"""
run_all.py — запускает бота и мини-апп API одновременно

ВМЕСТО:  python telegram_bot.py
ТЕПЕРЬ:  python run_all.py

Бот и API работают в одном процессе через asyncio.gather
"""

import asyncio
import uvicorn
from dotenv import load_dotenv

load_dotenv()

from mini_app_api import app as fastapi_app


async def run_api():
    config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=8000,
        log_level="warning"
    )
    server = uvicorn.Server(config)
    await server.serve()


async def run_bot():
    # импортируем dp и bot из твоего telegram_bot.py
    from telegram_bot import dp, bot
    await dp.start_polling(bot)


async def main():
    print("🍌 Nano Banano запускается...")
    print("   Bot + Mini App API (port 8000)")
    await asyncio.gather(
        run_api(),
        run_bot(),
    )


if __name__ == "__main__":
    asyncio.run(main())
