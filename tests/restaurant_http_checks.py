"""Signed HTTP requests against a test-only database boundary."""
import asyncio,os,sys
from unittest.mock import patch,AsyncMock
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sales_http_checks import Store,signed,TOKEN
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer

class TrackedStore(Store):
    def __init__(self):super().__init__();self.tables=[]
    def table(self,name):self.tables.append(name);return super().table(name)

async def main():
    store=TrackedStore()
    ids=['00000000-0000-0000-0000-00000000000'+str(i) for i in range(1,6)]
    store.rows={'restaurants':[{'id':'aaaaaaaa-0000-0000-0000-000000000001','name':'A','owner_id':1,'created_at':'2026-09-01'},{'id':'bbbbbbbb-0000-0000-0000-000000000002','name':'B','owner_id':2,'created_at':'2026-09-01'}],
       'employee_links':[{'id':ids[0],'restaurant_id':'aaaaaaaa-0000-0000-0000-000000000001','user_id':3,'report_name':'A Employee','status':'approved','requested_at':'2026-09-10T00:00:00Z'},
                         {'id':ids[1],'restaurant_id':'aaaaaaaa-0000-0000-0000-000000000001','user_id':4,'report_name':'Pending','status':'pending','requested_at':'2026-09-10T00:00:00Z'},
                         {'id':ids[2],'restaurant_id':'bbbbbbbb-0000-0000-0000-000000000002','user_id':5,'report_name':'Other Restaurant','status':'approved','requested_at':'2026-09-10T00:00:00Z'}],
       'sales_months':[{'user_id':3,'month':'2026-09','targets':{'wine':1000},'created_at':'2026-09-11T00:00:00Z'}],
       'sales_events':[{'id':'e','user_id':3,'month':'2026-09','kind':'bottle','value':100,'work_date':'2026-09-12','created_at':'2026-09-12T00:00:00Z'},
                       {'id':'old','user_id':3,'month':'2026-09','kind':'bottle','value':99999,'work_date':'2026-09-01','created_at':'2026-09-01T00:00:00Z'},
                       {'id':'other','user_id':5,'month':'2026-09','kind':'bottle','value':77777,'work_date':'2026-09-12','created_at':'2026-09-12T00:00:00Z'}]}
    with patch.dict(os.environ,{'SUPABASE_URL':'https://example.invalid','SUPABASE_KEY':'test','IDENTITY_ENABLED':'1'}),patch('supabase.create_client',return_value=store):
        import db,identity,admin,webapp_api
        app=web.Application();webapp_api.register_webapp_routes(app,TOKEN,'testbot')
        with patch.object(admin,'is_admin',side_effect=lambda uid:uid==1):
            async with TestClient(TestServer(app)) as client:
                async def post(action,uid=1,status=200,**args):
                    response=await client.post('/api/restaurant/'+action,json={'initData':signed(uid),'month':'2026-09',**args})
                    data=await response.json();assert response.status==status,(response.status,data)
                    assert response.headers.get('Cache-Control')=='no-store, no-cache, must-revalidate'
                    return data
                for uid in (3,5,999):
                    assert not (await post('access',uid))['available']
                    await post('view',uid,status=403,user_id=1,restaurant_id='aaaaaaaa-0000-0000-0000-000000000001')
                    with patch.object(identity,'action',new=AsyncMock()) as mutation:
                        await post('approve',uid,status=403,id=ids[0]);mutation.assert_not_awaited()
                response=await client.post('/api/restaurant/view',json={'initData':'forged'});assert response.status==401
                data=await post('view');assert len(data['rows'])==1
                assert data['rows'][0]['sales']['metrics']['wine']['total']==100
                assert data['rows'][0]['sales']['metrics']['wine']['target']==1000
                assert data['counts']['pending']==1
                text=str(data);assert '99999' not in text and '77777' not in text and 'Other Restaurant' not in text
                response=await client.post('/api/restaurant/view',json={'initData':signed(1),'month':'2026-09','status':'pending'})
                data=await response.json();assert 'sales' not in data['rows'][0]
                await post('view',page=-1,status=400)
                await post('create',uid=3,status=400,name='Unauthorized')
                with patch.object(identity,'action',new=AsyncMock(return_value={'status':'approved'})) as mutation:
                    await post('approve',id=ids[0],user_id=2)
                    mutation.assert_awaited_once_with(1,'approve',{'id':ids[0],'restaurant_id':'aaaaaaaa-0000-0000-0000-000000000001'})
                # Multiple venues: explicit selection and no cross-owner access.
                second='cccccccc-0000-0000-0000-000000000003'
                store.rows['restaurants'].append({'id':second,'name':'Second A','owner_id':1,'created_at':'2026-09-02'})
                assert len((await post('access'))['restaurants'])==2
                await post('view',status=400)
                selected=await post('view',restaurant_id=second)
                assert selected['restaurant']['id']==second and not selected['rows']
                await post('view',restaurant_id='bbbbbbbb-0000-0000-0000-000000000002',status=403)
                with patch.object(identity,'transfer',new=AsyncMock(return_value={})) as transfer:
                    moved=await post('transfer',restaurant_id='aaaaaaaa-0000-0000-0000-000000000001',
                                     target_restaurant_id=second,id=ids[0])
                    assert moved['restaurant']['id']==second
                    transfer.assert_awaited_once_with(1,ids[0],
                        'aaaaaaaa-0000-0000-0000-000000000001',second)
                    await post('transfer',uid=3,status=403,restaurant_id='aaaaaaaa-0000-0000-0000-000000000001',
                               target_restaurant_id=second,id=ids[0])
                    await post('transfer',status=403,restaurant_id='aaaaaaaa-0000-0000-0000-000000000001',
                               target_restaurant_id='bbbbbbbb-0000-0000-0000-000000000002',id=ids[0])
                    await post('transfer',status=400,restaurant_id='aaaaaaaa-0000-0000-0000-000000000001',
                               target_restaurant_id='aaaaaaaa-0000-0000-0000-000000000001',id=ids[0])
                    transfer.assert_awaited_once()
                with patch.dict(os.environ,{'IDENTITY_ENABLED':'0'}):await post('view',status=404)
                assert set(store.tables)<={'restaurants','employee_links','sales_months','sales_events','sales_reports'},store.tables
    print('Restaurant HTTP checks passed: signed identity, owner isolation, membership boundaries, no tips access, actions and feature flag.')
if __name__=='__main__':asyncio.run(main())
