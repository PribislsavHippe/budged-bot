import asyncio
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram import Bot
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

with patch.dict(os.environ,{'BOT_TOKEN':'123456789:test-token','SUPABASE_URL':'https://example.invalid','SUPABASE_KEY':'test-key'}),patch('supabase.create_client'):
    import webapp_api
    import google_calendar
import research_stats

async def main():
    bot=Bot(token='123456789:test-token')
    entered=asyncio.Event()
    release=asyncio.Event()
    persisted=[]
    async def process(bot,update,**kwargs):
        entered.set()
        await release.wait()
        persisted.append(update)
    dp=SimpleNamespace(feed_raw_update=process)
    handler=SimpleRequestHandler(dp,bot,secret_token='test')
    request=SimpleNamespace(json=AsyncMock(return_value={'update_id':1}))
    response=await handler._handle_request_background(bot,request)
    await entered.wait()
    assert response.status==200 and not persisted
    for task in list(handler._background_feed_update_tasks):
        task.cancel()
    await asyncio.gather(*list(handler._background_feed_update_tasks),return_exceptions=True)
    assert not persisted
    print('CONFIRMED webhook: HTTP 200 before processing/saving; cancelled process leaves no persisted update')

    state=google_calendar._sign(7)
    assert google_calendar.verify_state(state)==7
    assert google_calendar.verify_state(state)==7
    request=SimpleNamespace(query={'state':state,'code':'synthetic-code-from-another-google-account'})
    with patch('google_calendar.exchange_code',AsyncMock()) as exchange, \
      patch('google_calendar.sync_pending',AsyncMock(return_value={'synced':0,'pending':0,'message':'Test'})):
        await webapp_api.google_callback(request)
        exchange.assert_awaited_once_with(7,'synthetic-code-from-another-google-account')
    print('CONFIRMED OAuth: signed state reusable; callback links supplied Google authorization code to uid from state without browser/Telegram user confirmation')

    subject={'id':'synthetic','label':2,'cohort':'new','onboarding_version':3}
    event=lambda date,number:{'id':number,'subject_id':'synthetic','event':'user_started',
      'occurred_at':date,'onboarding_version':3,'source':'bot'}
    old=event('2026-08-01T12:00:00+03:00',1)
    recent=event('2026-10-09T10:00:00+03:00',2)
    now=datetime(2026,10,9,12,tzinfo=timezone.utc)
    complete=research_stats.summarize([subject],[old,recent],30,now=now)
    limited=research_stats.summarize([subject],[recent],30,now=now)
    assert complete['new_users']==0 and limited['new_users']==1
    print('CONFIRMED research cohort: older employee repeats /start; full history new_users=0; window fetched by API new_users=1')
    await bot.session.close()

asyncio.run(main())
