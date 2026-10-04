"""Точка входа. Локально — polling, на Render (есть WEBHOOK_HOST) — webhook + мини-ап."""
import asyncio
import logging
import hashlib
import hmac
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import MenuButtonWebApp, WebAppInfo
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from dotenv import load_dotenv

import admin
import handlers
import report_photo
import identity_chat
from jobs import setup_scheduler
from webapp_api import register_webapp_routes

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# HTTP client INFO logs include full database URLs and Telegram IDs in filters.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")  # пусто → polling
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("PORT", os.getenv("WEBAPP_PORT", "8080")))


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    import ux_chat
    dp.message.outer_middleware(ux_chat.ActivityMiddleware())
    dp.callback_query.outer_middleware(ux_chat.ActivityMiddleware())
    dp.include_router(ux_chat.router)
    import schedule_chat
    dp.include_router(schedule_chat.router)
    # Админский роутер первым: иначе /admin и /broadcast перехватит
    # общий обработчик текста в handlers и попробует найти в них сумму.
    dp.include_router(admin.router)
    dp.include_router(identity_chat.router)
    dp.include_router(report_photo.router)
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
    secret = os.getenv("WEBHOOK_SECRET") or hmac.new(
        BOT_TOKEN.encode(), b"telegram-webhook", hashlib.sha256
    ).hexdigest()
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    register_webapp_routes(app, BOT_TOKEN, me.username)

    # Access logs would expose the private bearer URL used by iPhone calendars.
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, WEBAPP_HOST, WEBAPP_PORT).start()

    await bot.set_webhook(f"{WEBHOOK_HOST}{WEBHOOK_PATH}", secret_token=secret)
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
