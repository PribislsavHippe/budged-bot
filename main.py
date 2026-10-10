"""Точка входа. Локально — polling, на Render (есть WEBHOOK_HOST) — webhook + мини-ап."""
import asyncio
import logging
import hashlib
import hmac
import os
import signal
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import MenuButtonWebApp, WebAppInfo
from aiohttp import web
from aiogram.webhook.aiohttp_server import setup_application
from dotenv import load_dotenv

import admin
import handlers
import report_photo
import identity_chat
from jobs import setup_scheduler
from webapp_api import register_webapp_routes
from telegram_inbox import TelegramInbox,replay_safe_responses
from chat_keyboard import RemoveLegacyKeyboard

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


async def db_ready():
    import db
    try:
        for table,column in [('telegram_inbox','update_id'),('entries','revision'),
                             ('research_subjects','first_started_at'),('money_source_receipts','source_key'),
                             ('telegram_actor_state','actor_key')]:
            await db._execute(db.supabase.table(table).select(column).limit(1))
    except Exception as error:
        from diagnostics import failure
        failure(error,area='startup',stage='migrations')
        raise RuntimeError('Примените миграции v24–v26 перед запуском этой версии.') from None


async def run_polling(bot: Bot, dp: Dispatcher):
    await db_ready()
    await bot.delete_webhook(drop_pending_updates=False)
    inbox=TelegramInbox(bot,dp)
    await dp.emit_startup(bot=bot)
    inbox.start();offset=None
    try:
        while True:
            try:
                updates=await bot.get_updates(offset=offset,timeout=20,allowed_updates=dp.resolve_used_update_types())
                for update in updates:
                    await inbox.enqueue(update.model_dump(mode='json',exclude_none=True))
                    offset=update.update_id+1
            except Exception as error:
                from diagnostics import failure
                failure(error,area='telegram_inbox',stage='poll')
                await asyncio.sleep(3)
    finally:
        await inbox.stop()
        await dp.emit_shutdown(bot=bot)


async def run_webhook(bot: Bot, dp: Dispatcher):
    await db_ready()
    me = await bot.get_me()
    app = web.Application(client_max_size=8 * 1024 ** 2)
    app.router.add_get("/", lambda _: web.Response(text="OK"))
    secret = os.getenv("WEBHOOK_SECRET") or hmac.new(
        BOT_TOKEN.encode(), b"telegram-webhook", hashlib.sha256
    ).hexdigest()
    app['webhook_secret']=secret
    inbox=TelegramInbox(bot,dp)
    app.router.add_post(WEBHOOK_PATH,inbox.webhook)
    async def start_inbox(app):inbox.start()
    async def stop_inbox(app):await inbox.stop()
    app.on_startup.append(start_inbox)
    app.on_shutdown.append(stop_inbox)
    setup_application(app, dp, bot=bot)
    register_webapp_routes(app, BOT_TOKEN, me.username)
    app["bot"] = bot

    # Access logs would expose the private bearer URL used by iPhone calendars.
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, WEBAPP_HOST, WEBAPP_PORT).start()

    stopped=asyncio.Event()
    loop=asyncio.get_running_loop()
    with suppress(NotImplementedError,RuntimeError):loop.add_signal_handler(signal.SIGTERM,stopped.set)
    try:
        await bot.set_webhook(f"{WEBHOOK_HOST}{WEBHOOK_PATH}", secret_token=secret)
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Статистика", web_app=WebAppInfo(url=f"{WEBHOOK_HOST}/app")
                )
            )
        except Exception as error:
            from diagnostics import failure
            failure(error,area='menu_button',stage='setup')
        logging.info("Webhook listening")
        await stopped.wait()
    finally:
        with suppress(NotImplementedError,RuntimeError):loop.remove_signal_handler(signal.SIGTERM)
        await runner.cleanup()


async def main():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session.middleware(replay_safe_responses)
    bot.session.middleware(RemoveLegacyKeyboard())
    dp = build_dispatcher()

    scheduler = setup_scheduler(bot)
    scheduler.start()

    try:
        if WEBHOOK_HOST:
            await run_webhook(bot, dp)
        else:
            await run_polling(bot, dp)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
