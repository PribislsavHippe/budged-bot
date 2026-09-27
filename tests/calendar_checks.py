import sys,os,unittest
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with patch.dict(os.environ,{'SUPABASE_URL':'https://example.invalid','SUPABASE_KEY':'test'}),patch('supabase.create_client'):
    import google_calendar as gcal
import httpx


def response(code,data):return httpx.Response(code,json=data,request=httpx.Request('POST','https://example.invalid'))


class CalendarTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db=SimpleNamespace(get_google_token=AsyncMock(),mark_google_reconnect=AsyncMock(),save_google_token=AsyncMock(),mark_google_shift=AsyncMock(),pending_google_shifts=AsyncMock())
        self.client=AsyncMock();self.client.__aenter__.return_value=self.client
        self.patches=[patch.object(gcal,'db',self.db),patch.object(gcal.httpx,'AsyncClient',return_value=self.client),patch.object(gcal,'is_configured',return_value=True)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.db.get_google_token.return_value={'google_access_token':'test-access','google_refresh_token':'test-refresh','google_token_expiry':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
    async def test_valid_access_without_refresh(self):
        self.db.get_google_token.return_value['google_refresh_token']=None
        self.assertEqual(await gcal._valid_token(1),'test-access');self.client.post.assert_not_awaited()
    async def test_expired_refresh_reports_reconnect(self):
        self.db.get_google_token.return_value['google_token_expiry']='2020-01-01T00:00:00Z'
        self.client.post.return_value=response(400,{'error':'invalid_grant'})
        with self.assertRaises(gcal.CalendarError) as err:await gcal._valid_token(1)
        self.assertEqual(err.exception.code,'reconnect');self.db.mark_google_reconnect.assert_awaited_once_with(1,True)
    async def test_refresh_preserves_returned_rotation(self):
        self.db.get_google_token.return_value['google_token_expiry']='2020-01-01T00:00:00Z'
        self.client.post.return_value=response(200,{'access_token':'fresh','refresh_token':'rotated','expires_in':3600})
        self.assertEqual(await gcal._valid_token(1),'fresh')
        self.assertEqual(self.db.save_google_token.await_args.args[:3],(1,'fresh','rotated'))
    async def test_legacy_event_deduplicated(self):
        self.client.get.return_value=response(200,{'items':[{'id':'old','start':{'date':'2026-09-27'}}]})
        self.assertTrue(await gcal.create_shift_event(1,'2026-09-27'));self.client.post.assert_not_awaited()
    async def test_new_event_stable_id_and_inclusive_date(self):
        self.client.get.return_value=response(200,{'items':[]});self.client.post.side_effect=[response(201,{}),response(409,{})]
        self.assertTrue(await gcal.create_shift_event(1,'2026-09-27'))
        self.assertTrue(await gcal.create_shift_event(1,'2026-09-27'))
        first,second=[call.kwargs['json'] for call in self.client.post.await_args_list]
        self.assertEqual(first['id'],second['id']);self.assertEqual(first['end']['date'],'2026-09-28')
    async def test_401_refresh_and_retry(self):
        self.client.get.side_effect=[response(401,{}),response(200,{'items':[{'start':{'date':'2026-09-27'}}]})]
        self.client.post.return_value=response(200,{'access_token':'fresh'})
        self.assertTrue(await gcal.create_shift_event(1,'2026-09-27'))
        self.assertEqual(self.client.get.await_count,2)
        self.assertEqual(self.client.get.await_args.kwargs['headers']['Authorization'],'Bearer fresh')
    async def test_network_failure_remains_pending(self):
        self.client.get.side_effect=httpx.ConnectError('offline')
        r=await gcal.sync_shifts(1,['2026-09-27'])
        self.assertEqual((r['synced'],r['pending'],r['error']),(0,1,'temporary'))
        self.db.mark_google_shift.assert_awaited_once_with(1,'2026-09-27',False,'temporary')
    async def test_pending_recovered(self):
        self.db.pending_google_shifts.return_value=[{'user_id':1,'shift_date':'2026-09-27'}]
        self.client.get.return_value=response(200,{'items':[]});self.client.post.return_value=response(201,{})
        r=await gcal.sync_pending(1);self.assertEqual(r['synced'],1)
        self.db.mark_google_shift.assert_awaited_once_with(1,'2026-09-27',True,None)
    async def test_stored_reconnect_not_shown_as_connected(self):
        self.db.get_google_token.return_value['google_reconnect_required']=True
        self.assertFalse(await gcal.is_connected(1));r=await gcal.connection_status(1)
        self.assertFalse(r['connected']);self.assertEqual(r['error'],'reconnect')

if __name__=='__main__':unittest.main()
