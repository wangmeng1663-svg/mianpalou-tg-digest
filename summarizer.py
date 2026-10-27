"""LLM 调用：DeepSeek（OpenAI 兼容）/ Gemini 双支持，JSON 输出，超长分段 map-reduce。"""
import asyncio
import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

import config
import prompts

log = logging.getLogger("mianpalou.summarizer")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
TZ = ZoneInfo(config.TIMEZONE)
MAX_CHARS = 18000  # 单次请求的记录文本上限


def _build_transcript(rows) -> str:
    lines = []
    for r in rows:
        t = datetime.fromtimestamp(r["ts"], TZ).strftime("%H:%M")
        lines.append(f"[{t}] {r['user_name']}: {r['text']}")
    return "\n".join(lines)


_TAGS = {"行情", "技术", "公告", "求助", "闲聊", "其他"}
SRC_MIN = 4  # src 太短（如「好的」）没有证据价值，但仍算通过，只计入未验证


def _flatten(s: str) -> str:
    """去掉所有空白与常见标点差异，便于逐字比对原文。"""
    s = (s or "").lower()
    return re.sub(r"[\s\"'`“”‘’「」『』，。、；：！？,.;:!?()（）\[\]…~～—\-]+", "", s)


def _normalize(d, transcript: str = ""):
    """字段名纠偏 + src 原话校验。

    src 能在 transcript 里逐字找到 → 保留（verified）
    src 存在但对不上 → 判定为编造，**丢弃该要点**
    src 缺失或过短 → 保留但记为 unverified（不因模型漏填字段而丢信息）
    """
    if not isinstance(d, dict):
        return None
    d.setdefault("lang", "zh")
    if not isinstance(d.get("topics"), list):
        d["topics"], d["skip"] = [], True
        return d
    flat = _flatten(transcript)
    st = {"kept": 0, "dropped": 0, "unverified": 0}
    fixed = []
    for t in d["topics"]:
        if not isinstance(t, dict):
            continue
        name = str(t.get("name") or t.get("title") or t.get("topic") or "").strip()
        if not name:
            continue
        pts = t.get("points")
        if not isinstance(pts, list) or not pts:
            if isinstance(t.get("summary"), str) and t["summary"].strip():
                pts = [{"text": t["summary"], "who": "", "src": ""}]
            elif isinstance(t.get("evidence"), list):
                pts = [{"text": str(x), "who": "", "src": str(x)} for x in t["evidence"]]
            else:
                pts = []
        points = []
        for p in pts[:4]:
            if isinstance(p, dict):
                txt = str(p.get("text") or p.get("summary") or "").strip()
                who = str(p.get("who") or p.get("user") or "").strip()
                src = str(p.get("src") or p.get("evidence") or "").strip()
            else:
                txt, who, src = str(p).strip(), "", str(p).strip()
            if not txt:
                continue
            if src and flat:
                if _flatten(src) and _flatten(src) in flat:
                    st["kept"] += 1
                else:
                    st["dropped"] += 1
                    log.info("src 对不上原文，丢弃要点: text=%.30s src=%.30s", txt, src)
                    continue
            else:
                st["unverified"] += 1
            points.append({"text": txt[:60], "who": who[:20]})
        if not points:
            continue
        try:
            heat = max(1, min(10, int(t.get("heat") or 5)))
        except Exception:
            heat = 5
        tag = str(t.get("tag") or "").strip()
        fixed.append({"name": name[:20], "tag": tag if tag in _TAGS else "其他",
                      "heat": heat, "points": points})
    fixed.sort(key=lambda x: x["heat"], reverse=True)
    d["topics"] = fixed[:5]
    if not d["topics"]:
        d["skip"] = True
    d["_src"] = st  # 置信度指标，渲染前会被剥离
    log.info("src 校验: 命中 %s / 丢弃 %s / 未验证 %s", st["kept"], st["dropped"], st["unverified"])
    return d


def _parse(raw: str, transcript: str = ""):
    """宽松解析 JSON，失败返回 None（静默跳过，不炸流程）。"""
    if not raw:
        return None
    try:
        data = json.loads(raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
    except Exception:
        log.warning("LLM 输出 JSON 解析失败: %.200s", raw)
        return None
    return _normalize(data, transcript)


async def _post_with_retry(url: str, headers: dict, body: dict, params: dict | None = None):
    async with httpx.AsyncClient(timeout=120) as client:
        r = None
        for attempt in (1, 2):  # 429/5xx 重试一次
            r = await client.post(url, headers=headers, json=body, params=params)
            if r.status_code in (429, 500, 502, 503) and attempt == 1:
                await asyncio.sleep(6)
                continue
            r.raise_for_status()
            return r
    return r


async def _call(system: str, user: str) -> str:
    """按 LLM_PROVIDER 分发，返回模型文本。"""
    if config.LLM_PROVIDER == "deepseek":
        r = await _post_with_retry(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"},
            body={
                "model": config.DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
                "max_tokens": 4096,
                "response_format": {"type": "json_object"},
            },
        )
        return r.json()["choices"][0]["message"]["content"]

    r = await _post_with_retry(
        GEMINI_URL.format(model=config.LLM_MODEL),
        headers={},
        params={"key": config.GEMINI_API_KEY},
        body={
            "contents": [{"parts": [{"text": system + "\n\n" + user}]}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 4096,
                "responseMimeType": "application/json",
            },
        },
    )
    parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


def _chunk(text: str, size: int) -> list[str]:
    lines, chunks, buf = text.split("\n"), [], []
    cur = 0
    for ln in lines:
        if cur + len(ln) > size and buf:
            chunks.append("\n".join(buf))
            buf, cur = [], 0
        buf.append(ln)
        cur += len(ln) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks[:5]


async def generate_digest(chat_title: str, rows) -> dict | None:
    """返回日报 dict；LLM 失败返回 None（调用方静默跳过）。"""
    transcript = _build_transcript(rows)
    if len(transcript) <= MAX_CHARS:
        user = f"群名：{chat_title}\n\n【消息记录】\n{transcript}"
        return _parse(await _call(prompts.SYSTEM_PROMPT, user), transcript)

    # 超长：分段提取 → 合并
    chunks = _chunk(transcript, MAX_CHARS)
    maps = []
    for i, c in enumerate(chunks, 1):
        raw = await _call(
            prompts.SYSTEM_RULES_SHORT,
            prompts.MAP_PROMPT.format(
                idx=i, total=len(chunks), chunk=c,
                rules=prompts.SYSTEM_RULES_SHORT, spec=prompts.OUTPUT_SPEC,
            ),
        )
        maps.append(raw)
    merged = await _call(
        "你是群聊日报主编，按 <task> 里的规则合并 JSON，只输出最终 JSON。",
        prompts.MERGE_PROMPT.format(maps="\n---\n".join(maps), spec=prompts.OUTPUT_SPEC),
    )
    return _parse(merged, transcript)
