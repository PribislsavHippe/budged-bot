"""Планировщик: одно вечернее напоминание закрыть смену.

Шлём только тем, у кого сегодня был доход (была смена) — остальных не дёргаем.
"""
import logging
import os

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import db
from workday import op_day_start_utc_iso, op_today


async def self_ping():
    """Будит Render: бесплатный план засыпает через 15 минут без запросов."""
    host = os.getenv("WEBHOOK_HOST")
    if not host:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(host, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                logging.info(f"self-ping: {resp.status}")
    except Exception as e:
        logging.warning(f"self-ping failed: {e}")


async def evening_shift_prompt(bot):
    """Вечером спрашиваем про чай — в дни запланированных смен (или если был доход)."""
    from handlers import shift_spend_kb
    today = op_today()
    since = op_day_start_utc_iso(today)
    today_iso = today.isoformat()
    try:
        user_ids = await db.get_onboarded_user_ids()
        shift_users = set(await db.get_user_ids_with_shift_on(today_iso))
    except Exception as e:
        logging.error(f"evening prompt: fetch failed: {e}")
        return
    for user_id in user_ids:
        try:
            entries = await db.get_entries_since(user_id, since)
            income = sum(
                float(e["signed_amount"]) for e in entries if e["kind"] == "income"
            )
            spent_today = any(
                e["kind"] == "expense" and e.get("note") == "трата смены"
                for e in entries
            )
            is_shift = user_id in shift_users
            if spent_today or (not is_shift and income <= 0):
                continue
            if is_shift and income <= 0:
                text = ("Сегодня у тебя смена. Сколько вышло чая?\n"
                        "Запиши сумму или перешли уведомление банка, потом закрой смену.")
            else:
                text = (f"Смена закончилась? За сегодня уже {income:,.0f} ₽.".replace(",", " ")
                        + "\nЗакроем день — какие были траты?")
            await bot.send_message(user_id, text, reply_markup=shift_spend_kb())
        except Exception as e:
            logging.warning(f"evening prompt failed for {user_id}: {e}")


def setup_scheduler(bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(evening_shift_prompt, "cron", hour=22, minute=30, args=[bot])
    scheduler.add_job(self_ping, "interval", minutes=10)
    return scheduler
