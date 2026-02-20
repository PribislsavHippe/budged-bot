import asyncio
import os
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from dotenv import load_dotenv

import start, transactions, payments, budget
from scheduler.jobs import setup_scheduler
from services.google_calendar import exchange_code

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("PORT", 8080))
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"


async def on_startup(bot: Bot):
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook set to {WEBHOOK_URL}")


async def on_shutdown(bot: Bot):
    await bot.delete_webhook()


# ─── Google OAuth callback ────────────────────────────────

async def google_oauth_callback(request: web.Request) -> web.Response:
    """Обрабатывает редирект от Google после авторизации"""
    code = request.query.get("code")
    state = request.query.get("state")  # это user_id

    if not code or not state:
        return web.Response(text="Ошибка авторизации", status=400)

    user_id = int(state)
    bot: Bot = request.app["bot"]

    try:
        await exchange_code(user_id, code)
        await bot.send_message(
            user_id,
            "✅ <b>Google Calendar подключён!</b>\n\n"
            "Теперь я буду создавать события для платежей и напоминаний прямо в твоём календаре.",
            parse_mode="HTML"
        )
        return web.Response(
            text="<h2>✅ Успешно!</h2><p>Google Calendar подключён. Можешь вернуться в Telegram.</p>",
            content_type="text/html"
        )
    except Exception as e:
        logging.error(f"Google OAuth error: {e}")
        return web.Response(text="Ошибка при подключении Google Calendar", status=500)


def create_app(bot: Bot, dp: Dispatcher) -> web.Application:
    app = web.Application()
    app["bot"] = bot

    # Webhook handler
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)

    # Google OAuth callback
    app.router.add_get("/google/callback", google_oauth_callback)

    setup_application(app, dp, bot=bot)
    app.on_startup.append(lambda _: on_startup(bot))
    app.on_shutdown.append(lambda _: on_shutdown(bot))

    return app


async def main():
    bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
    dp = Dispatcher()

    # Регистрируем роутеры
    dp.include_router(start.router)
    dp.include_router(transactions.router)
    dp.include_router(payments.router)
    dp.include_router(budget.router)

    # Запускаем планировщик
    scheduler = setup_scheduler(bot)
    scheduler.start()
    logging.info("Scheduler started")

    # Запускаем веб-сервер
    app = create_app(bot, dp)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEBAPP_HOST, WEBAPP_PORT)
    await site.start()

    logging.info(f"Bot started on {WEBAPP_HOST}:{WEBAPP_PORT}")

    # Держим приложение живым
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
