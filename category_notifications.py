"""Durable owner-only notifications after a discovered category is saved."""
import asyncio
import html
from datetime import datetime,timezone
from uuid import uuid4
import db


async def send_pending(bot):
    from admin import ADMIN_ID
    if not ADMIN_ID:return
    token=str(uuid4())
    try:
        pending=(await db._execute(db.supabase.rpc('claim_category_alerts',{'claim':token}))).data or []
    except Exception as error:
        from postgrest.exceptions import APIError
        if isinstance(error,APIError) and error.code in ('42P01','PGRST202','PGRST205'):return
        from diagnostics import failure
        failure(error,area='category_notice',stage='claim')
        return
    for item in pending:
        try:
            text=('Новая категория: '+html.escape(item['category_name'])+'\n'+
                  html.escape(item['restaurant_name'])+' · '+item['period']+'\n\n'+
                  'Результат сохранён и участвует в расчёте плана. '
                  'Нужно добавить распознавание добавления записей для этой категории.')
            await asyncio.wait_for(bot.send_message(ADMIN_ID,text,disable_web_page_preview=True),timeout=5)
            await db._execute(db.supabase.table('category_alerts').update({
                'notified_at':datetime.now(timezone.utc).isoformat(),'claimed_until':None
            }).eq('id',item['alert_id']).eq('claim_token',token))
            await asyncio.sleep(0.04)
        except Exception as error:
            from diagnostics import failure
            failure(error,area='category_notice',stage='send')
