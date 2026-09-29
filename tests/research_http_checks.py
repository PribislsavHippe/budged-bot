"""Integration at signed HTTP and bot boundaries; no live services."""
import asyncio,os,sys,unittest,logging,io
from unittest.mock import patch,AsyncMock
from types import SimpleNamespace as NS
from uuid import uuid4
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sales_http_checks import Store,signed,TOKEN
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from postgrest.exceptions import APIError

async def main():
    store=Store()
    with patch.dict(os.environ,{'SUPABASE_URL':'https://example.invalid','SUPABASE_KEY':'test','IDENTITY_ENABLED':'1','UX_RESEARCH_ENABLED':'1'}),patch('supabase.create_client',return_value=store):
        import db,webapp_api,restaurant_api,research,research_api,admin,identity,ux_chat,handlers
        app=web.Application();webapp_api.register_webapp_routes(app,TOKEN,'testbot')
        events=[]
        with patch.object(admin,'is_admin',side_effect=lambda uid:uid==1),patch.object(research,'track',side_effect=lambda *a,**kw:events.append((a,kw))):
            async with TestClient(TestServer(app)) as client:
                async def post(path,uid=1,expected=200,**body):
                    r=await client.post(path,json={'initData':signed(uid),**body});data=await r.json()
                    assert r.status==expected,(r.status,data);return data
                # Actual reported production failure, safe diagnostics and personal screens still work.
                missing=APIError({'code':'PGRST205','message':'SECRET restaurants absent','details':'TOKEN','hint':None})
                buf=io.StringIO();log=logging.StreamHandler(buf);logging.getLogger().addHandler(log)
                with patch.object(identity,'owner_restaurants',side_effect=missing):
                    d=await post('/api/restaurant/access',expected=503)
                    assert d['code']=='schema' and len(d['reference'])==12
                logging.getLogger().removeHandler(log)
                assert 'PGRST205' in buf.getvalue() and 'identity' in buf.getvalue()
                assert 'SECRET' not in buf.getvalue() and 'TOKEN' not in buf.getvalue()
                assert any(a[1]=='cabinet_load_error' and kw['error_code']=='schema' for a,kw in events)
                with patch.object(db,'get_all_entries',new=AsyncMock(return_value=[])),patch.object(db,'get_shift_goal',new=AsyncMock(return_value=None)),patch.object(db,'get_shift_dates',new=AsyncMock(return_value=[])),patch.object(db,'get_recent_entries',new=AsyncMock(return_value=[])):
                    await post('/api/stats');await post('/api/entries');await post('/api/month',year=2026,month=9)
                with patch.object(db,'get_all_entries',new=AsyncMock(side_effect=RuntimeError('financial SECRET'))):
                    d=await post('/api/stats',expected=503);assert 'SECRET' not in str(d)
                # Restored expense accepts only validated operations, trusts signed user, and preserves saved success.
                operation=str(uuid4())
                with patch.object(db,'get_or_create_user',new=AsyncMock()),patch.object(db.supabase,'rpc',create=True) as rpc,patch.object(db,'_execute',new=AsyncMock(return_value=NS(data={'id':31}))),patch.object(webapp_api,'_stats_payload',new=AsyncMock(side_effect=RuntimeError('SECRET'))):
                    d=await post('/api/shift_spend',uid=2,user_id=1,operation_id=operation,amount=430,category='Такси')
                    assert d['saved'] and d['stats'] is None
                    assert rpc.call_args.args[1]['actor']==2
                    assert rpc.call_args.args[1]['operation']==operation
                    for amount in (0,-1,'NaN',1.234):
                        await post('/api/shift_spend',expected=400,operation_id=str(uuid4()),amount=amount,category='Такси')
                    await post('/api/shift_spend',expected=400,operation_id='bad',amount=1,category='Такси')
                # Ownership alone does not grant research access; request body cannot override signed user.
                for action in ['overview','journey','feedback']:
                    await post('/api/research/'+action,uid=2,expected=403,user_id=1,subject=str(uuid4()))
                r=await client.post('/api/research/overview',json={'initData':'forged'});assert r.status==401
                await post('/api/research/event',uid=2,expected=400,event='first_value_action',screen='chat')
                await post('/api/research/event',uid=2,expected=400,event='tab_opened',screen='earnings',amount=1500)
                await post('/api/research/event',uid=2,expected=400,event='tab_opened',screen='secret user text')
                await post('/api/research/event',uid=2,expected=202,event='tab_opened',screen='earnings',operation=str(uuid4()))
                with patch.object(research,'pages',new=AsyncMock(return_value=[])) as pages:
                    d=await post('/api/research/overview',days=7)
                    assert d['new_users']==0 and d['retention']['d1']['rate'] is None
                    assert all('user_id' not in call.args[1] for call in pages.await_args_list)
                await post('/api/research/overview',days=999,expected=400)
                with patch.dict(os.environ,{'UX_RESEARCH_ENABLED':'0'}):
                    assert (await post('/api/research/access'))['available']
                    assert not (await post('/api/research/access',uid=2))['available']
                    await post('/api/research/overview',expected=404)
        # Persisted entry stays successful even when analytics fails. No financial content in RPC payload.
        message=NS(from_user=NS(id=2,first_name='Tester'),answer=AsyncMock())
        with patch.object(research,'execute',new=AsyncMock(side_effect=RuntimeError('SECRET'))):
            await ux_chat.value_saved(message,{'id':1,'kind':'income','signed_amount':1500,'note':'private'})
        payload=research.payload(2,'tip_added',operation=str(uuid4()))
        assert set(payload)=={'actor','kind','origin','screen_name','step_name','failure_code','release','operation','event_time'}
        # Existing user never forced; skip does not intercept ordinary text.
        with patch.object(db,'get_or_create_user',new=AsyncMock(return_value={'onboarded':True,'tutorial_step':None})):
            assert not await ux_chat.begin(message)
        with patch.object(db,'get_or_create_user',new=AsyncMock(return_value={'tutorial_step':'new'})),patch.object(research,'record',new=AsyncMock(return_value={'state':'started'})),patch.object(research,'track'):
            assert await ux_chat.begin(message)
        state=NS(clear=AsyncMock(),get_data=AsyncMock(return_value={'feedback_category':'idea','feedback_id':str(uuid4())}))
        message.text='A test idea';store.rows['users']=[{'id':2}]
        s={'id':str(uuid4())};captured=[]
        class Capture:
            def table(self,table):assert table=='feedback';return self
            def upsert(self,row,**kw):captured.append(row);return self
        with patch.object(research,'subject',new=AsyncMock(return_value=s)),patch.object(research,'client',return_value=Capture()),patch.object(research,'execute',new=AsyncMock()),patch.object(research,'track'):
            await ux_chat.feedback_text(message,state)
        assert captured[0]['body']=='A test idea' and 'user_id' not in captured[0]
        state.clear.assert_awaited()
        # Bounded non-blocking queue failures are swallowed.
        research._queue.clear();research._worker=None
        with patch.object(research,'execute',new=AsyncMock(side_effect=RuntimeError('SECRET'))):
            research.track(2,'help_opened');await research._worker
        print('Research HTTP/bot checks passed: missing schema, safe logs, stats/history, auth, privacy, errors, baseline, onboarding, feedback, fail-open tracking.')
if __name__=='__main__':asyncio.run(main())
