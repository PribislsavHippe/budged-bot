"""Планировщик смен и напоминаний по московскому времени."""
import asyncio
import logging
import os
from datetime import datetime, timedelta

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
        claimed=None
        try:
            claimed=(await db._execute(db.supabase.table('shifts')
                     .update({'start_reminder_sent':True}).eq('id',shift['id'])
                     .eq('start_reminder_sent',False))).data
            if not claimed:continue
            start=shift.get('starts_at')
            when=f" с {start[:5]}" if start else ''
            await bot.send_message(uid,f'Завтра у тебя смена{when}. Хорошего вечера!')
            await asyncio.sleep(0.04)
        except Exception as error:
            if claimed:
                try:await db._execute(db.supabase.table('shifts').update({'start_reminder_sent':False}).eq('id',shift['id']))
                except Exception:pass
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
    except Exception as e:
        logging.warning(f"self-ping failed: {e}")


def setup_scheduler(bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(tomorrow_shift_reminder, "cron", hour=19, minute=0, args=[bot],max_instances=1)
    scheduler.add_job(retry_tomorrow_shift_reminder, "interval", minutes=10, args=[bot],max_instances=1)
    scheduler.add_job(retry_tomorrow_shift_reminder, args=[bot],max_instances=1)
    from google_calendar import retry_pending_shifts
    scheduler.add_job(retry_pending_shifts, "interval", minutes=10, max_instances=1)
    from schedule_chat import prompt_work_end
    scheduler.add_job(prompt_work_end,"interval",minutes=5,args=[bot],max_instances=1)
    scheduler.add_job(self_ping, "interval", minutes=10)
    return scheduler
