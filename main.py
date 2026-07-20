"""Точка входа. Локально — polling, на Render (есть WEBHOOK_HOST) — webhook."""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from dotenv import load_dotenv

import handlers

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")  # пусто → polling
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("PORT", os.getenv("WEBAPP_PORT", "8080")))


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(handlers.router)
    return dp


async def run_polling(bot: Bot, dp: Dispatcher):
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Starting polling")
    await dp.start_polling(bot)


async def run_webhook(bot: Bot, dp: Dispatcher):
    app = web.Application()
    app.router.add_get("/", lambda _: web.Response(text="OK"))
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, WEBAPP_HOST, WEBAPP_PORT).start()

    await bot.set_webhook(f"{WEBHOOK_HOST}{WEBHOOK_PATH}")
    logging.info(f"Webhook set, listening on {WEBAPP_HOST}:{WEBAPP_PORT}")
    await asyncio.Event().wait()


async def main():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher()
    if WEBHOOK_HOST:
        await run_webhook(bot, dp)
    else:
        await run_polling(bot, dp)


if __name__ == "__main__":
    asyncio.run(main())
