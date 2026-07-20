"""Точка входа. Локально — polling, на Render (есть WEBHOOK_HOST) — webhook + мини-ап."""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import MenuButtonWebApp, WebAppInfo
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from dotenv import load_dotenv

import handlers
from jobs import setup_scheduler
from webapp_api import register_webapp_routes

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
    me = await bot.get_me()
    app = web.Application()
    app.router.add_get("/", lambda _: web.Response(text="OK"))
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    register_webapp_routes(app, BOT_TOKEN, me.username)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, WEBAPP_HOST, WEBAPP_PORT).start()

    await bot.set_webhook(f"{WEBHOOK_HOST}{WEBHOOK_PATH}")
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Статистика", web_app=WebAppInfo(url=f"{WEBHOOK_HOST}/app")
            )
        )
    except Exception as e:
        logging.warning(f"menu button setup failed: {e}")
    logging.info(f"Webhook set, listening on {WEBAPP_HOST}:{WEBAPP_PORT}")
    await asyncio.Event().wait()


async def main():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher()

    scheduler = setup_scheduler(bot)
    scheduler.start()

    if WEBHOOK_HOST:
        await run_webhook(bot, dp)
    else:
        await run_polling(bot, dp)


if __name__ == "__main__":
    asyncio.run(main())
