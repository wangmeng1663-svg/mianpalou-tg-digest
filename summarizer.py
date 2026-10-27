"""LLM 调用：DeepSeek（OpenAI 兼容）/ Gemini 双支持，JSON 输出，超长分段 map-reduce。"""
import asyncio
import json
import logging
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


def _parse(raw: str):
    """宽松解析 JSON，失败返回 None（静默跳过，不炸流程）。"""
    if not raw:
        return None
    try:
        return json.loads(raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
    except Exception:
        log.warning("LLM 输出 JSON 解析失败: %.200s", raw)
        return None


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
        return _parse(await _call(prompts.SYSTEM_PROMPT, user))

    # 超长：分段提取 → 合并
    chunks = _chunk(transcript, MAX_CHARS)
    maps = []
    for i, c in enumerate(chunks, 1):
        raw = await _call(
            prompts.SYSTEM_RULES_SHORT,
            prompts.MAP_PROMPT.format(idx=i, total=len(chunks), chunk=c),
        )
        maps.append(raw)
    merged = await _call(
        "你是群聊日报主编，按 <task> 里的规则合并 JSON，只输出最终 JSON。",
        prompts.MERGE_PROMPT.format(maps="\n---\n".join(maps)),
    )
    return _parse(merged)
