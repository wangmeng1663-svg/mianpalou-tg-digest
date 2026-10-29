"""免爬楼 · TG 群日报机器人主程序。透明人进群，到点发图，发完即焚。"""
import asyncio
import datetime
import logging
import re
import time
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.error import Forbidden
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import db
import render
from config import brand
from prompts import CAPTION, START_REPLY
from summarizer import generate_digest

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("mianpalou")

MIN_RENDER_MB = 250  # 可用内存低于此值不渲染（这台机器基线可用仅 ~320MB，阈值太严会永不渲染）


def _mem_available_mb() -> float | None:
    """读 Linux MemAvailable；非 Linux（本机开发）返回 None = 不设闸。"""
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass
    return None
TZ = ZoneInfo(config.TIMEZONE)


# ---------- 消息收集 ----------

async def notify_owner(bot, text: str):
    if config.OWNER_ID:
        try:
            await bot.send_message(config.OWNER_ID, text)
        except Exception as e:
            log.warning("owner 通知失败: %s", e)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, chat = update.effective_message, update.effective_chat
    if not msg or not msg.text or chat.type not in ("group", "supergroup"):
        return
    user = msg.from_user
    if not user or user.is_bot:  # bot 收不到别的 bot 的消息，双保险
        return
    now = int(time.time())
    is_new_chat = not db.has_chat(chat.id)
    # 群数上限：新群且当前已满 → 忽略（防滥用）
    if is_new_chat and db.active_count(86400, now) >= config.MAX_GROUPS:
        return
    db.save_message(chat.id, chat.title or "", (user.first_name or "?")[:32], msg.text[:500], ts=now)
    if is_new_chat:
        log.info("新群入驻: %s (id=%s)", chat.title or chat.id, chat.id)
        await notify_owner(
            context.bot,
            f"🎉 新群报喜：{chat.title or chat.id}\n当前共 {db.distinct_chat_count()} 个群",
        )


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """被移出群 → 立即清空该群全部数据。"""
    mc = update.my_chat_member
    if not mc:
        return
    if mc.new_chat_member.status in ("member", "administrator"):
        log.info("bot 被加入群: %s (id=%s) 状态=%s", mc.chat.title or "", mc.chat.id,
                 mc.new_chat_member.status)
    if mc.new_chat_member.status in ("left", "kicked"):
        db.delete_all(mc.chat.id)
        db.record_kick(mc.chat.id, int(time.time()))
        log.info("已退出群 %s (%s)，状态=%s，数据已清空", mc.chat.title or "", mc.chat.id,
                 mc.new_chat_member.status)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    u = update.effective_user
    log.info("有人私聊 /start: %s (@%s) id=%s",
             u.first_name if u else "?", u.username if u and u.username else "-",
             u.id if u else "-")
    await notify_owner(
        context.bot,
        f"👋 有人来了：{u.first_name if u else '?'}"
        + (f" (@{u.username})" if u and u.username else ""),
    )
    await update.effective_message.reply_text(
        START_REPLY.format(brand="免爬楼", bot=config.BOT_USERNAME),
        disable_web_page_preview=True,
    )


# ---------- 发送时间（按语言预估时区，当地时间 20:00 发送） ----------

def detect_group_lang(chat_id: int) -> str:
    """按近期消息的字符特征判断群语言（代码判定，不耗 LLM）。"""
    import re
    counts = {"ja": 0, "ko": 0, "ru": 0, "zh": 0, "en": 0}
    patterns = {
        "ja": re.compile(r"[\u3040-\u30ff]"),
        "ko": re.compile(r"[\uac00-\ud7af]"),
        "ru": re.compile(r"[\u0400-\u04ff]"),
        "zh": re.compile(r"[\u4e00-\u9fff]"),
        "en": re.compile(r"[A-Za-z]"),
    }
    for text in db.recent_texts(chat_id):
        for lang, pat in patterns.items():
            if pat.search(text):
                counts[lang] += 1
                break
    if not any(counts.values()):
        return config.DEFAULT_LANG
    return max(counts, key=counts.get)


def compute_send_target_utc(chat_id: int, now_ts: int) -> datetime.datetime:
    """今天当地 20:00 对应的 UTC 时刻。"""
    lang = detect_group_lang(chat_id)
    tzname = config.LANG_TZ.get(lang, config.LANG_TZ[config.DEFAULT_LANG])
    local = datetime.datetime.fromtimestamp(now_ts, ZoneInfo(tzname))
    target_local = local.replace(
        hour=config.SEND_LOCAL_HOUR, minute=0, second=0, microsecond=0
    )
    return target_local.astimezone(datetime.timezone.utc)


# ---------- 日报生成与发送 ----------

async def process_chat(context, chat_id: int, title: str, counters: dict):
    now_ts = int(time.time())
    last = db.last_summary_ts(chat_id)
    since = last or now_ts - 86400
    rows = db.messages_since(chat_id, since)
    if len(rows) < config.MIN_MESSAGES:  # 冷场不发，消息留着滚入明天
        return
    cutoff = now_ts  # 生成期间新到的消息留给下一期

    # 内存闸门：可用内存不足时跳过本轮（不删消息，留给下一期），防止渲染把 1G 小机拖死
    avail = _mem_available_mb()
    if avail is not None and avail < MIN_RENDER_MB:
        log.warning("群 %s 内存不足 %.0fMB < %dMB，跳过本轮，消息留待下期", chat_id, avail, MIN_RENDER_MB)
        counters["fail"] += 1
        return

    try:
        digest = await generate_digest(title, rows)
    except Exception as e:
        log.error("群 %s LLM 失败: %s", chat_id, e)
        counters["fail"] += 1
        return
    if digest is None or digest.get("skip"):
        db.record_summary(chat_id, cutoff, lang=str((digest or {}).get("lang", "")), skipped=1)
        day = datetime.datetime.fromtimestamp(cutoff, TZ).strftime("%Y-%m-%d")
        db.record_stats(chat_id, title, since, cutoff, day, cutoff)
        db.delete_since(chat_id, cutoff)
        counters["skip"] += 1
        return

    lang = digest.get("lang", "zh")
    src_stat = digest.pop("_src", None)  # 证据校验结果，仅日志用，不进渲染
    if src_stat:
        log.info("群 %s 证据校验 命中%s/丢弃%s/未验证%s", chat_id,
                 src_stat.get("kept"), src_stat.get("dropped"), src_stat.get("unverified"))
    topics = digest.get("topics", [])[:5]
    prev_since = since - (now_ts - since)
    prev_cnt = {m["user_name"]: m["c"] for m in db.top_members(chat_id, prev_since, since)}
    cur = db.top_members(chat_id, since)
    members = [
        {"name": m["user_name"], "count": m["c"],
         "delta": m["c"] - prev_cnt.get(m["user_name"], 0)}
        for m in cur
    ]
    start_dt = datetime.datetime.fromtimestamp(since, TZ)
    data = {
        "lang": lang,
        "group": title,
        "date_range": f"{start_dt:%m.%d %H:%M} - {datetime.datetime.fromtimestamp(cutoff, TZ):%m.%d %H:%M}",
        "stats": {
            "msgs": len(rows),
            "topics_n": len(topics),
            "members": db.topic_counts(chat_id, since),
            "digest_n": len(topics),
        },
        "top_topics": [
            {"name": t["name"], "count": int(t.get("heat") or sum(len(p) for p in t.get("points", [])) or 1)}
            for t in topics
        ],
        "topics": topics,
        "members": members,
        "hours": db.histogram(chat_id, since=now_ts - config.HISTOGRAM_DAYS * 86400),
        "bot_username": config.BOT_USERNAME,
    }
    try:
        png = await asyncio.to_thread(render.render_png, data)
    except Exception as e:
        log.error("群 %s 渲染失败: %s", chat_id, e)
        counters["fail"] += 1
        return
    b = brand(lang)
    try:
        await context.bot.send_photo(
            chat_id=chat_id, photo=png,
            caption=CAPTION.format(brand=b, bot=config.BOT_USERNAME),
        )
    except Forbidden:
        log.info("群 %s 已拒绝消息，清空数据", chat_id)
        db.record_stats(chat_id, title, since, cutoff,
                        datetime.datetime.fromtimestamp(cutoff, TZ).strftime("%Y-%m-%d"), cutoff)
        db.delete_all(chat_id)
        db.record_kick(chat_id, cutoff)
        counters["kick"] += 1
        return
    except Exception as e:
        msg = str(e)
        if "migrated to supergroup" in msg.lower():
            # 群升级成超级群：TG 换了 chat_id，把数据迁过去并立即补发一次
            m = re.search(r"New chat id:\s*(-?\d+)", msg)
            if m:
                new_id = int(m.group(1))
                log.warning("群 %s 升级为超级群，迁移数据 → %s", chat_id, new_id)
                db.migrate_chat(chat_id, new_id)
                try:
                    await context.bot.send_photo(
                        chat_id=new_id, photo=png,
                        caption=CAPTION.format(brand=b, bot=config.BOT_USERNAME),
                    )
                    db.record_summary(new_id, cutoff, lang=lang, skipped=0)
                    db.delete_since(new_id, cutoff)
                    counters["ok"] += 1
                    return
                except Exception as e2:
                    log.error("群 %s 迁移后补发仍失败: %s", new_id, e2)
        log.error("群 %s 发送失败: %s", chat_id, e)
        counters["fail"] += 1
        return
    db.record_summary(chat_id, cutoff, lang=lang, skipped=0)
    db.record_stats(chat_id, title, since, cutoff,
                    datetime.datetime.fromtimestamp(cutoff, TZ).strftime("%Y-%m-%d"), cutoff)
    db.delete_since(chat_id, cutoff)  # 看完即焚
    counters["ok"] += 1


async def daily_sweep(context: ContextTypes.DEFAULT_TYPE):
    """每 15 分钟扫一次：到点（每群自己的峰值前 1h）且今天未发 → 生成。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    now_ts = int(now.timestamp())
    counters = {"ok": 0, "skip": 0, "fail": 0, "kick": 0}
    chats = db.active_chats(config.HISTOGRAM_DAYS * 86400, now_ts)
    for row in chats:
        chat_id, title = row["chat_id"], row["title"] or str(row["chat_id"])
        target = compute_send_target_utc(chat_id, now_ts)
        last_any = db.any_summary_today(chat_id, int(target.timestamp()) - 7200)
        if last_any:
            continue
        if target <= now < target + datetime.timedelta(minutes=40):
            await process_chat(context, chat_id, title, counters)
    # 运营日报：只有实际发生动作才发，全 0 静默（修复 15 分钟刷屏）
    if config.OWNER_ID and any(counters.values()):
        text = (
            f"📊 免爬楼运营日报\n"
            f"活跃群（7天）：{len(chats)}\n"
            f"发图成功：{counters['ok']}　水聊跳过：{counters['skip']}\n"
            f"失败：{counters['fail']}　被移出：{counters['kick']}"
        )
        try:
            await context.bot.send_message(config.OWNER_ID, text)
        except Exception as e:
            log.error("运营日报发送失败: %s", e)


# ---------- 启动 ----------

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """兜底：任何 handler 崩了，用户至少收到一句回应，不再死寂。"""
    log.error("未捕获异常: %s | update=%s", context.error,
              str(update)[:200] if update else None)
    try:
        if isinstance(update, Update) and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="😅 出了点小故障，请稍后再试一次。",
            )
    except Exception:
        pass  # 兜底不能再炸


def main():
    if not config.BOT_TOKEN:
        raise SystemExit("请在 .env 里填写 BOT_TOKEN")
    builder = Application.builder().token(config.BOT_TOKEN)
    # 图片上传走 media_write_timeout：默认 20s 在新加坡回源时不够，容易 TimedOut
    req_kw = dict(connect_timeout=20, read_timeout=60, write_timeout=60, media_write_timeout=180)
    if config.PROXY_URL:  # 本机测试需梯子时启用；海外 VPS 上留空
        from telegram.request import HTTPXRequest
        builder = (
            builder.get_updates_request(HTTPXRequest(proxy=config.PROXY_URL, **req_kw))
            .request(HTTPXRequest(proxy=config.PROXY_URL, **req_kw))
        )
    else:
        from telegram.request import HTTPXRequest
        builder = builder.request(HTTPXRequest(**req_kw))
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, on_message))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_error_handler(on_error)
    app.job_queue.run_repeating(daily_sweep, interval=900, first=20)
    log.info("免爬楼启动，发送基点 %s %d 点", config.TIMEZONE, config.DEFAULT_SEND_HOUR)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
