"""SQLite 存储：消息、日报记录、被踢记录。看完即焚：消息发完即删。"""
import json
import sqlite3

import config

_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute(
            """CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                chat_title TEXT,
                user_name TEXT,
                text TEXT,
                ts INTEGER NOT NULL)"""
        )
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_chat_ts ON messages(chat_id, ts)")
        _conn.execute(
            """CREATE TABLE IF NOT EXISTS summaries(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                ts INTEGER NOT NULL,
                lang TEXT,
                skipped INTEGER DEFAULT 0)"""
        )
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_sum_chat ON summaries(chat_id, ts)")
        _conn.execute(
            """CREATE TABLE IF NOT EXISTS kicks(
                chat_id INTEGER PRIMARY KEY,
                ts INTEGER NOT NULL)"""
        )
        _conn.execute(
            """CREATE TABLE IF NOT EXISTS chat_stats(
                chat_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                chat_title TEXT,
                msgs INTEGER DEFAULT 0,
                hours TEXT DEFAULT '[]',
                updated INTEGER DEFAULT 0,
                PRIMARY KEY(chat_id, day))"""
        )
    return _conn


def save_message(chat_id: int, title: str, user: str, text: str, ts: int):
    conn().execute(
        "INSERT INTO messages(chat_id, chat_title, user_name, text, ts) VALUES(?,?,?,?,?)",
        (chat_id, title, user, text, ts),
    )
    conn().commit()


def chat_recent(chat_id: int, window: int, now_ts: int) -> bool:
    r = conn().execute(
        "SELECT 1 FROM messages WHERE chat_id=? AND ts>? LIMIT 1", (chat_id, now_ts - window)
    ).fetchone()
    if r:
        return True
    r = conn().execute(
        "SELECT 1 FROM chat_stats WHERE chat_id=? AND updated>? LIMIT 1", (chat_id, now_ts - window)
    ).fetchone()
    return r is not None


def active_count(window: int, now_ts: int) -> int:
    r = conn().execute(
        """SELECT COUNT(DISTINCT chat_id) c FROM (
             SELECT chat_id FROM messages WHERE ts>?
             UNION
             SELECT chat_id FROM chat_stats WHERE updated>?
           )""",
        (now_ts - window, now_ts - window),
    ).fetchone()
    return r["c"]


def active_chats(window: int, now_ts: int):
    """活跃群 = 消息表 ∪ 统计存档表（消息看完即焚后靠存档保持记忆）。"""
    return conn().execute(
        """SELECT chat_id, MAX(title) AS title, SUM(cnt) AS cnt FROM (
             SELECT chat_id, chat_title AS title, COUNT(*) AS cnt
               FROM messages WHERE ts>? GROUP BY chat_id
             UNION ALL
             SELECT chat_id, MAX(chat_title) AS title, SUM(msgs) AS cnt
               FROM chat_stats WHERE updated>? GROUP BY chat_id
           ) t GROUP BY chat_id""",
        (now_ts - window, now_ts - window),
    ).fetchall()


def messages_since(chat_id: int, since: int):
    return conn().execute(
        "SELECT user_name, text, ts FROM messages WHERE chat_id=? AND ts>=? ORDER BY ts",
        (chat_id, since),
    ).fetchall()


def recent_texts(chat_id: int, limit: int = 200):
    """最近的纯文本消息，用于语言探测。"""
    rows = conn().execute(
        "SELECT text FROM messages WHERE chat_id=? ORDER BY ts DESC LIMIT ?",
        (chat_id, limit),
    ).fetchall()
    return [r["text"] for r in rows]


def record_stats(chat_id: int, title: str, since: int, cutoff: int, day: str, now_ts: int):
    """删除消息前，把本窗口的小时分布并入当天的存档。"""
    rows = conn().execute(
        """SELECT (ts/3600)%24 AS h, COUNT(*) AS c FROM messages
           WHERE chat_id=? AND ts>=? AND ts<=? GROUP BY h""",
        (chat_id, since, cutoff),
    ).fetchall()
    counts = [0] * 24
    for r in rows:
        counts[r["h"]] = r["c"]
    n = sum(counts)
    row = conn().execute(
        "SELECT msgs, hours FROM chat_stats WHERE chat_id=? AND day=?", (chat_id, day)
    ).fetchone()
    if row:
        old = json.loads(row["hours"] or "[]")
        merged = [a + b for a, b in zip(old + [0] * 24, counts)]
        conn().execute(
            "UPDATE chat_stats SET msgs=msgs+?, hours=?, chat_title=?, updated=? WHERE chat_id=? AND day=?",
            (n, json.dumps(merged), title, now_ts, chat_id, day),
        )
    else:
        conn().execute(
            "INSERT INTO chat_stats(chat_id, day, chat_title, msgs, hours, updated) VALUES(?,?,?,?,?,?)",
            (chat_id, day, title, n, json.dumps(counts), now_ts),
        )
    conn().commit()


def stats_histogram(chat_id: int, since: int):
    """存档表里的 7 天小时分布（UTC 小时）。"""
    rows = conn().execute(
        "SELECT hours FROM chat_stats WHERE chat_id=? AND updated>?", (chat_id, since)
    ).fetchall()
    counts = [0] * 24
    for r in rows:
        for h, c in enumerate(json.loads(r["hours"] or "[]")):
            counts[h] += c
    return counts


def histogram(chat_id: int, since: int):
    """近 N 天 24 小时直方图（UTC 小时），= 在存消息 + 存档合并。"""
    rows = conn().execute(
        """SELECT (ts/3600)%24 AS h, COUNT(*) AS c
           FROM messages WHERE chat_id=? AND ts>? GROUP BY h""",
        (chat_id, since),
    ).fetchall()
    counts = stats_histogram(chat_id, since)
    for r in rows:
        counts[r["h"]] += r["c"]
    return counts


def top_members(chat_id: int, since: int, until: int | None = None, limit: int = 8):
    if until is None:
        until = 1 << 62
    return conn().execute(
        """SELECT user_name, COUNT(*) AS c FROM messages
           WHERE chat_id=? AND ts>=? AND ts<? GROUP BY user_name
           ORDER BY c DESC LIMIT ?""",
        (chat_id, since, until, limit),
    ).fetchall()


def topic_counts(chat_id: int, since: int) -> int:
    return conn().execute(
        "SELECT COUNT(DISTINCT user_name) c FROM messages WHERE chat_id=? AND ts>=?",
        (chat_id, since),
    ).fetchone()["c"]


def last_summary_ts(chat_id: int) -> int | None:
    r = conn().execute(
        "SELECT MAX(ts) AS t FROM summaries WHERE chat_id=? AND skipped=0", (chat_id,)
    ).fetchone()
    return r["t"] if r and r["t"] else None


def any_summary_today(chat_id: int, since: int) -> bool:
    r = conn().execute(
        "SELECT 1 FROM summaries WHERE chat_id=? AND ts>=? LIMIT 1", (chat_id, since)
    ).fetchone()
    return r is not None


def record_summary(chat_id: int, ts: int, lang: str, skipped: int):
    conn().execute(
        "INSERT INTO summaries(chat_id, ts, lang, skipped) VALUES(?,?,?,?)",
        (chat_id, ts, lang, skipped),
    )
    conn().commit()


def delete_since(chat_id: int, before_ts: int):
    conn().execute("DELETE FROM messages WHERE chat_id=? AND ts<=?", (chat_id, before_ts))
    conn().commit()


def delete_all(chat_id: int):
    conn().execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
    conn().commit()


def record_kick(chat_id: int, ts: int):
    conn().execute(
        "INSERT OR REPLACE INTO kicks(chat_id, ts) VALUES(?,?)", (chat_id, ts)
    )
    conn().commit()


def kick_count(since: int) -> int:
    return conn().execute("SELECT COUNT(*) c FROM kicks WHERE ts>=?", (since,)).fetchone()["c"]


def has_chat(chat_id: int) -> bool:
    r = conn().execute("SELECT 1 FROM messages WHERE chat_id=? LIMIT 1", (chat_id,)).fetchone()
    return r is not None


def distinct_chat_count() -> int:
    return conn().execute("SELECT COUNT(DISTINCT chat_id) c FROM messages").fetchone()["c"]
