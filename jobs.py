"""Планировщик смен и напоминаний по московскому времени."""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import db
from workday import op_today


async def tomorrow_shift_reminder(bot):
    """One evening notice for tomorrow's planned shift, with an atomic claim."""
    import schedule
    if not schedule.enabled():return
    day=(op_today()+timedelta(days=1)).isoformat()
    try:
        shifts=await db._pages(lambda:db.supabase.table('shifts')
                               .select('id,user_id,starts_at,ends_at')
                               .eq('shift_date',day).eq('start_reminder_sent',False).order('id'))
    except Exception as error:
        from diagnostics import failure
        failure(error,area='shift_reminder',stage='fetch')
        return
    for shift in shifts:
        uid=shift['user_id']
        try:
            from notices import send_shift_notice
            start=shift.get('starts_at')
            when=f" с {start[:5]}" if start else ''
            await send_shift_notice(shift['id'],'start',lambda:bot.send_message(
                uid,f'Завтра у тебя смена{when}. Хорошего вечера!'))
            await asyncio.sleep(0.04)
        except Exception as error:
            from diagnostics import failure
            failure(error,area='shift_reminder',stage='send')


async def retry_tomorrow_shift_reminder(bot):
    """Catch up after a restart or a transient failure on the same evening."""
    from schedule import TZ
    if datetime.now(TZ).hour >= 19:
        await tomorrow_shift_reminder(bot)


async def self_ping():
    """Будит Render: бесплатный план засыпает через 15 минут без запросов."""
    host = os.getenv("WEBHOOK_HOST")
    if not host:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(host, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                logging.info(f"self-ping: {resp.status}")
    except Exception as error:
        from diagnostics import failure
        failure(error,area='self_ping',stage='request')


async def prune_private_money_backups():
    """Remove recovery ciphertext after its 14-day minimum retention."""
    try:
        await db.prune_private_backups()
    except Exception as error:
        from diagnostics import failure
        failure(error,area='private_money',stage='prune')


def setup_scheduler(bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(tomorrow_shift_reminder, "cron", hour=19, minute=0, args=[bot],max_instances=1)
    scheduler.add_job(retry_tomorrow_shift_reminder, "interval", minutes=10, args=[bot],max_instances=1)
    scheduler.add_job(retry_tomorrow_shift_reminder, args=[bot],max_instances=1)
    from schedule_chat import prompt_work_end
    scheduler.add_job(prompt_work_end,"interval",minutes=5,args=[bot],max_instances=1)
    scheduler.add_job(self_ping, "interval", minutes=10)
    scheduler.add_job(prune_private_money_backups, "interval", hours=1, max_instances=1)
    scheduler.add_job(prune_private_money_backups, max_instances=1)
    async def prune_finished_inbox():
        try:
            await db._execute(db.supabase.table('telegram_inbox').delete().lt(
                'finished_at',(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()))
        except Exception as error:
            from diagnostics import failure
            failure(error,area='telegram_inbox',stage='prune')
    scheduler.add_job(prune_finished_inbox,'interval',hours=24,max_instances=1)
    return scheduler
