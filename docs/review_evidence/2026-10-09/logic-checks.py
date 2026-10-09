import asyncio
import io
import json
import logging
import os
import time
import parser
from datetime import datetime,timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from postgrest.exceptions import APIError

with patch.dict(os.environ,{'BOT_TOKEN':'123456789:test-token','SUPABASE_URL':'https://example.invalid','SUPABASE_KEY':'test-key'}),patch('supabase.create_client'):
    import handlers,webapp_api,google_calendar,schedule_chat,jobs

class State:
    def __init__(self):
        self.status=schedule_chat.Work.end.state
        self.data={'work_day':'2026-10-02','work_start':'09:00',
          'work_created':time.time(),'work_nonce':'test'}
    async def get_state(self):return self.status
    async def get_data(self):return self.data.copy()
    async def update_data(self,**values):self.data.update(values)
    async def set_state(self,status):self.status=status.state

async def main():
    assert parser.parse_transactions('чай -500')[0]['amount']==500
    assert parser.parse_transactions('чай 12.345')[0]['amount']==12.34
    assert [r['amount'] for r in parser.parse_transactions('чай 123,456')]==[123,456]
    print('CONFIRMED amount parser: negative sign ignored; 3 decimals truncated; comma with 3 digits creates two entries')

    message=NS(text='кофе 200, такси 350',forward_origin=None,
      from_user=NS(id=7),chat=NS(id=7),message_id=1,answer=AsyncMock())
    persisted=[]
    async def add(*args,**kwargs):
        if persisted:raise RuntimeError('synthetic database failure on second entry')
        entry={'id':1,'kind':args[1],'signed_amount':args[3],
          'account':args[2],**kwargs}
        persisted.append(entry);return entry
    with patch('db.get_or_create_user',AsyncMock(return_value={'id':7,'onboarded':True})), \
      patch('db.get_recent_entries',AsyncMock(return_value=[])), \
      patch('db.add_entry',side_effect=add):
        try:await handlers.handle_text(message,NS())
        except RuntimeError:pass
        else:raise AssertionError('expected write error')
    assert len(persisted)==1 and not message.answer.await_count
    print('CONFIRMED multi-entry write: first expense persisted; second failed; no result sent to user')
    callback=handlers.undo_kb(list(range(1,25))).inline_keyboard[-1][0].callback_data
    assert len(callback.encode())>64
    print('CONFIRMED oversized cancel button:',len(callback.encode()),'bytes for 24 entries (Telegram limit 64)')

    entered=asyncio.Event()
    class Slow:
        def execute(self):
            time.sleep(.12)
            return NS(data=[])
    class Fast:
        def execute(self):return NS(data=[])
    slow=asyncio.create_task(handlers.db._execute(Slow()))
    await asyncio.sleep(.015)
    started=time.monotonic()
    await handlers.db._execute(Fast())
    delay=time.monotonic()-started
    await slow
    assert delay>.07
    print('CONFIRMED shared database queue: independent instant query waited',round(delay,3),'seconds behind one .12 second synthetic query')

    # An explicitly replied-to day must not be silently replaced by an older FSM day.
    state=State()
    message=NS(text='23:30',from_user=NS(id=7),bot=NS(id=123),answer=AsyncMock(),
      reply_to_message=NS(from_user=NS(id=123),
        reply_markup=NS(inline_keyboard=[[NS(callback_data='work:close:2026-10-09')]])))
    await schedule_chat.time_message(message,state)
    assert state.data['actual_end'].startswith('2026-10-02')
    print('CONFIRMED time entry: reply explicitly selects 9 October; pending FSM records confirmation for 2 October')

    request=NS()
    with patch('webapp_api._auth',AsyncMock(return_value=(7,{'date':'2026-10-02','action':'shift_delete'}))), \
      patch('db.delete_shift',AsyncMock(return_value=True)) as delete, \
      patch('google_calendar.is_connected',AsyncMock(return_value=True)), \
      patch('google_calendar.delete_shift_event',AsyncMock(side_effect=google_calendar.CalendarError('temporary'))) as google_delete:
        response=await webapp_api.api_calendar_edit(request)
        data=json.loads(response.text)
        assert data['saved'] and data['warning']
        delete.assert_awaited_once();google_delete.assert_awaited_once()
    print('CONFIRMED Google deletion failure: local deletion committed; external deletion failed; response saved=true with warning; retry queue stores only remaining shifts')

    claimed={'value':False}
    class Query:
        def table(self,*args):return self
        def update(self,values):claimed['value']=values['start_reminder_sent'];return self
        def eq(self,*args):return self
    shift={'id':1,'user_id':7,'starts_at':'09:00','ends_at':'21:00'}
    entered=asyncio.Event()
    async def send(*args):entered.set();await asyncio.Event().wait()
    with patch('schedule.enabled',return_value=True),patch('db._pages',AsyncMock(return_value=[shift])), \
      patch('db.supabase',Query()),patch('db._execute',AsyncMock(return_value=NS(data=[shift]))):
        task=asyncio.create_task(jobs.tomorrow_shift_reminder(NS(send_message=send)))
        await asyncio.wait_for(entered.wait(),timeout=2);task.cancel()
        await asyncio.gather(task,return_exceptions=True)
    assert claimed['value']
    print('CONFIRMED reminder cancellation: database sent flag remains true before Telegram send completes')

    message=NS(message_id=1,answer=AsyncMock())
    key=('synthetic',7,'bank')
    handlers._forward_batches[key]={'messages':{1:(message,{'amount':5000})}}
    output=io.StringIO();sink=logging.StreamHandler(output)
    logging.getLogger().addHandler(sink)
    provider_error=APIError({'code':'23514','message':'constraint violation',
      'details':'Failing row contains (synthetic uid=7, amount=5000, private note)', 'hint':None})
    try:
        with patch('handlers._save_bank_tips',AsyncMock(side_effect=provider_error)):
            await handlers._flush_forwarded_tips(key)
    finally:logging.getLogger().removeHandler(sink)
    assert 'amount=5000' in output.getvalue() and 'private note' in output.getvalue()
    print('CONFIRMED log exposure: provider error details with synthetic financial row copied to application log')

asyncio.run(main())
