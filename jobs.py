"""Планировщик: одно вечернее напоминание закрыть смену.

Шлём только тем, у кого сегодня был доход (была смена) — остальных не дёргаем.
"""
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import db

MSK = timezone(timedelta(hours=3))


async def evening_shift_prompt(bot):
    from handlers import shift_spend_kb
    day_start = datetime.now(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
    since = day_start.astimezone(timezone.utc).isoformat()
    try:
        user_ids = await db.get_onboarded_user_ids()
    except Exception as e:
        logging.error(f"evening prompt: users fetch failed: {e}")
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
            if income <= 0 or spent_today:
                continue
            await bot.send_message(
                user_id,
                f"Смена закончилась? За сегодня уже {income:,.0f} ₽.".replace(",", " ")
                + "\nЗакроем день — что потратил?",
                reply_markup=shift_spend_kb(),
            )
        except Exception as e:
            logging.warning(f"evening prompt failed for {user_id}: {e}")


def setup_scheduler(bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(evening_shift_prompt, "cron", hour=22, minute=30, args=[bot])
    return scheduler
