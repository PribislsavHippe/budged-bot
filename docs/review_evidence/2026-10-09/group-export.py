import asyncio
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from aiogram import Bot
from aiogram.types import Message

with patch.dict(os.environ,{'BOT_TOKEN':'123456789:test-token','SUPABASE_URL':'https://example.invalid','SUPABASE_KEY':'test-key'}),patch('supabase.create_client'):
    import handlers
    import sales_db
    import identity_chat
    import research
    import schedule

async def main():
    bot=Bot(token='123456789:test-token')
    message=Message(message_id=1,date=datetime.now(timezone.utc),
      chat={'id':-1001234567,'type':'supergroup','title':'Synthetic group'},
      from_user={'id':7,'is_bot':False,'first_name':'Test'},text='/export',
      entities=[{'type':'bot_command','offset':0,'length':7}]).as_(bot)
    entry={'id':1,'kind':'income','account':'cash','category':'Чаевые',
      'signed_amount':5000,'created_at':'2026-10-09T12:00:00+03:00','work_date':'2026-10-09'}
    with patch('db.get_or_create_user',AsyncMock(return_value={'id':7})), \
      patch('db.get_all_entries',AsyncMock(return_value=[entry])), \
      patch('db.get_shift_dates',AsyncMock(return_value=[])), \
      patch('sales_db.export',AsyncMock(return_value={})), \
      patch('identity_chat.enabled',return_value=False), \
      patch('research.enabled',return_value=False), \
      patch('schedule.enabled',return_value=False), \
      patch.object(Bot,'__call__',AsyncMock(return_value=True)) as send:
        await handlers.router.propagate_event('message',message,bot=bot)
        methods=[call.args[0] for call in send.await_args_list]
        doc=next(method for method in methods if type(method).__name__=='SendDocument')
        assert doc.chat_id==-1001234567
        assert b'5000,00' in doc.document.data
        print('CONFIRMED group export: private CSV with synthetic amount 5000 sent to group chat',doc.chat_id)
    await bot.session.close()

asyncio.run(main())
