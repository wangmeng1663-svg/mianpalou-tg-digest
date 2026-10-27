"""P0 自检 + 长群模拟：验证超长记录的分段精炼（map-reduce）路径能跑通。

用法：
    python selftest_long.py           # 离线检查（不花 token，走 mock）
    python selftest_long.py --real    # 真实调用 LLM + 渲染出图
"""
import asyncio
import json
import sys

import config
import prompts
import render
import summarizer

MAXC = summarizer.MAX_CHARS

# ---------------- 假数据：一个「话很多」的群，约 2.5 万字符 ----------------
NAMES = ["老王", "阿May", "Kai", "小周", "tony", "夜猫", "阿哲", "Lily"]
TOPICS = [
    ("量化脚本跑回测", "把参数从 5 改成 8 之后，回撤小了一半，但胜率掉到 41%"),
    ("VPS 续费", "新加坡那台还有 12 天到期，年付比月付便宜 40 块"),
    ("域名解析", "Cloudflare 开了橙云之后反而变慢了，关掉好一些"),
    ("爬虫被封", "昨天又被 403 了，加了随机 UA 才通过"),
    ("显卡价格", "二手 3090 现在 3600 能拿下，比上个月跌了 300"),
    ("入门教程", "那个讲 asyncio 的系列不错，第 3 集讲 gather 讲得清楚"),
    ("机器人部署", "systemd 挂了自动重启要配 Restart=always，别忘了 daemon-reload"),
    ("改图工具", "这个批量裁成 3:4 的脚本挺好用，一行命令处理整个文件夹"),
]


def fake_rows(n_lines: int):
    rows, t = [], 20 * 3600
    for i in range(n_lines):
        name = NAMES[i % len(NAMES)]
        topic, detail = TOPICS[i % len(TOPICS)]
        text = f"{topic}这事我昨天又试了一遍：{detail}，第{i // len(TOPICS) + 2}次了。"
        rows.append({"user_name": name, "text": text, "ts": t + i * 60})
    return rows


def build():
    rows = fake_rows(700)
    tr = summarizer._build_transcript(rows)
    return rows, tr


# ---------------- 离线检查：结构 + 模板占位符 ----------------
def build_render_data(digest, rows):
    """按 bot.py 里 process_chat 的方式拼渲染数据（除数据库字段用假值）。"""
    topics = (digest.get("topics") or [])[:5]
    names = [r["user_name"] for r in rows]
    members = [{"name": n, "count": names.count(n), "delta": i % 3 - 1}
               for i, n in enumerate(dict.fromkeys(names))]
    return {
        "lang": digest.get("lang", "zh"),
        "group": "币圈摸鱼群",
        "date_range": "10.26 20:00 - 10.27 20:00",
        "stats": {
            "msgs": len(rows),
            "topics_n": len(topics),
            "members": len(members),
            "digest_n": len(topics),
        },
        "top_topics": [{"name": t["name"], "count": int(t.get("heat") or 1)} for t in topics],
        "topics": topics,
        "members": members,
        "hours": [len(rows) // 24 or 1] * 24,
        "bot_username": config.BOT_USERNAME,
    }


def check_offline():
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(("  [PASS] " if cond else "  [FAIL] ") + msg)
        ok = ok and cond

    print("【1】模板占位符")
    user = prompts.MAP_PROMPT.format(idx=1, total=2, chunk="[20:00] a: hi", rules=prompts.SYSTEM_RULES_SHORT, spec=prompts.OUTPUT_SPEC)
    chk(not any(k in user for k in ("{spec}", "{rules}", "{chunk}", "{idx}", "{total}")),
        "MAP_PROMPT 格式化后无残留占位符")
    chk(prompts.SYSTEM_RULES_SHORT in user, "规则正文已嵌入 MAP_PROMPT")
    chk("1/2" in user, "分段序号正确")
    chk('"name"' in user and '"heat"' in user and '"points"' in user,
        "字段规范已嵌入（name/heat/points）")
    chk("title" not in user.split("<output_format>")[-1].split("</output_format>")[0].replace("不要用 title", ""),
        "字段规范里没有 title/summary/evidence 之类错误字段")

    print("【2】假数据长度触发分段")
    rows, tr = build()
    print(f"  消息 {len(rows)} 条 / {len(tr)} 字符 / 阈值 {MAXC}")
    chk(len(tr) > MAXC, "记录超过单次上限，会走分段分支")
    chunks = summarizer._chunk(tr, MAXC)
    chk(len(chunks) >= 2, f"被切成 {len(chunks)} 段")
    chk(all(len(c) <= MAXC + 200 for c in chunks), "每段不超上限")
    chk(sum(len(c) for c in chunks) + len(chunks) - 1 >= len(tr) - 5, "切分无内容丢失")

    print("【3】每段的提示词都能正常生成（原 P0 崩溃点）")
    try:
        for i, c in enumerate(chunks, 1):
            prompts.MAP_PROMPT.format(idx=i, total=len(chunks), chunk=c,
                                     rules=prompts.SYSTEM_RULES_SHORT, spec=prompts.OUTPUT_SPEC)
        chk(True, f"{len(chunks)} 段全部格式化成功，无 KeyError")
    except Exception as e:
        chk(False, f"格式化仍报错 {type(e).__name__}: {e}")

    print("【4】合并提示词")
    try:
        m = prompts.MERGE_PROMPT.format(maps='{"lang":"zh","skip":false,"topics":[]}', spec=prompts.OUTPUT_SPEC)
        chk("{" in m, "MERGE_PROMPT 正常")
    except Exception as e:
        chk(False, f"MERGE_PROMPT 报错 {e}")

    print("【5】mock 跑完整个 map-reduce 流程")
    fake_topic = {
        "lang": "zh", "skip": False,
        "topics": [{"name": "量化回测", "tag": "技术", "heat": 8,
                    "points": [{"text": "参数从5改到8后回撤减半但胜率降到41%", "who": "老王"}]}],
    }
    calls = {"n": 0}
    orig = summarizer._call

    async def mock_call(system, user):
        calls["n"] += 1
        return json.dumps(fake_topic, ensure_ascii=False)

    summarizer._call = mock_call
    try:
        got = asyncio.run(summarizer.generate_digest("测试群", rows))
    finally:
        summarizer._call = orig
    chk(got is not None, "generate_digest 返回非空")
    chk(calls["n"] == len(chunks) + 1, f"调用次数 = 分段数+合并 = {calls['n']}")
    chk(isinstance(got, dict) and "topics" in got, "返回结构含 topics")

    print("【6】渲染（不需要网络）")
    try:
        png = render.render_png(build_render_data(got, rows))
        chk(len(png) > 10000, f"出图 {len(png)} 字节")
        open("selftest_mock.png", "wb").write(png)
    except Exception as e:
        chk(False, f"渲染失败 {type(e).__name__}: {e}")

    print()
    print("OFFLINE_OK" if ok else "OFFLINE_FAIL")
    return ok


# ---------------- 真实调用 ----------------
async def _gen(rows):
    return await summarizer.generate_digest("币圈摸鱼群", rows)


def check_real():
    rows, tr = build()
    print(f"真实调用：{len(rows)} 条 / {len(tr)} 字符，分段 {len(summarizer._chunk(tr, MAXC))} 段")
    d = asyncio.run(_gen(rows))
    if not d:
        print("REAL_FAIL：LLM 没返回可解析 JSON")
        return False
    if d.get("skip") or not d.get("topics"):
        print("REAL_FAIL：被判 skip 或 topics 为空")
        return False
    print("字段校验：", [list(t.keys()) for t in d["topics"]][:2])
    print(json.dumps(d, ensure_ascii=False, indent=1)[:1400])
    png = render.render_png(build_render_data(d, rows))
    open("selftest_long.png", "wb").write(png)
    print(f"出图 selftest_long.png {len(png)} 字节")
    print("REAL_OK")
    return True


if __name__ == "__main__":
    good = check_offline()
    if "--real" in sys.argv and good:
        good = check_real()
    sys.exit(0 if good else 1)
