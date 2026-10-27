"""手动触发一次日报（跳过定时调度），用于验收 LLM→渲染→发图 链路。"""
import asyncio
import time

import db
import config
from telegram.ext import Application
from telegram.request import HTTPXRequest

from bot import process_chat


def build_app():
    builder = Application.builder().token(config.BOT_TOKEN)
    if config.PROXY_URL:
        builder = (
            builder.get_updates_request(HTTPXRequest(proxy=config.PROXY_URL))
            .request(HTTPXRequest(proxy=config.PROXY_URL))
        )
    return builder.build()


class Ctx:  # process_chat 只用到 .bot
    def __init__(self, bot):
        self.bot = bot


async def main():
    counters = {"ok": 0, "skip": 0, "fail": 0, "kick": 0}
    app = build_app()
    async with app:
        await app.initialize()
        chats = db.active_chats(7 * 86400, int(time.time()))
        for row in chats:
            chat_id, title = row["chat_id"], row["title"] or str(row["chat_id"])
            print(f"处理群 {title} ({chat_id}) ...")
            await process_chat(Ctx(app.bot), chat_id, title, counters)
        print("结果:", counters)


if __name__ == "__main__":
    asyncio.run(main())
