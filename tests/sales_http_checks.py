"""Real aiohttp routes + sales repository, with an in-memory PostgREST boundary.
Run in a separate process: legacy test_api intentionally replaces aiohttp modules.
"""
import asyncio
import copy
import hashlib
import hmac
import json
import os
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import patch, AsyncMock
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


class Query:
    def __init__(self, owner, table):
        self.owner=owner; self.table=table; self.filters=[]; self.orders=[]
        self.action='select'; self.data=None; self.start=0; self.end=None; self.conflict=''; self.ignore=False
    def select(self,*a,**kw): return self
    def eq(self,k,v): self.filters.append(lambda r,k=k,v=v:r.get(k)==v);return self
    def gte(self,k,v): self.filters.append(lambda r,k=k,v=v:r[k]>=v);return self
    def order(self,k,desc=False):self.orders.append((k,desc));return self
    def limit(self,n):self.end=n;return self
    def range(self,a,b):self.start=a;self.end=b+1;return self
    def upsert(self,data,on_conflict='',ignore_duplicates=False):
        self.action='upsert';self.data=data;self.conflict=on_conflict;self.ignore=ignore_duplicates;return self
    def update(self,data):self.action='update';self.data=data;return self
    def execute(self):
        rows=self.owner.rows.setdefault(self.table,[])
        if self.action=='upsert':
            keys=self.conflict.split(',')
            old=next((r for r in rows if all(r.get(k)==self.data.get(k) for k in keys)),None)
            if old:
                if not self.ignore:old.update(copy.deepcopy(self.data))
                return SimpleNamespace(data=[copy.deepcopy(old)])
            row=copy.deepcopy(self.data);row.setdefault('created_at',f'2026-09-26T00:00:{len(rows):02}+00:00')
            if self.table=='sales_events':row['voided']=False
            rows.append(row);return SimpleNamespace(data=[copy.deepcopy(row)])
        found=[r for r in rows if all(f(r) for f in self.filters)]
        if self.action=='update':
            for r in found:r.update(self.data)
        for key,desc in reversed(self.orders):found.sort(key=lambda r:r[key],reverse=desc)
        # Simulate a service cap smaller than the requested page.
        end=min(self.end if self.end is not None else self.start+1000,self.start+1000)
        return SimpleNamespace(data=copy.deepcopy(found[self.start:end]),count=len(found))


class Store:
    def __init__(self):self.rows={}
    def table(self,name):return Query(self,name)


TOKEN='123456:test-only'
def signed(uid=1, age=0):
    pairs={'user':json.dumps({'id':uid}),'auth_date':str(int(time.time())-age)}
    check='\n'.join(f'{k}={v}' for k,v in sorted(pairs.items()))
    secret=hmac.new(b'WebAppData',TOKEN.encode(),hashlib.sha256).digest()
    return urlencode({**pairs,'hash':hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()})


async def main():
    store=Store()
    with patch.dict(os.environ,{'SUPABASE_URL':'https://example.invalid','SUPABASE_KEY':'test'}),patch('supabase.create_client',return_value=store):
        import db
    import sales_api
    import sales_db
    import webapp_api
    from datetime import date
    with patch.object(sales_api,'op_today',return_value=date(2026,9,26)):
        app=web.Application();webapp_api.register_webapp_routes(app,TOKEN,'test')
        async with TestClient(TestServer(app)) as client:
            async def post(action,body,uid=1,status=200,auth=None):
                r=await client.post('/api/sales/'+action,json={'month':'2026-09','initData':signed(uid) if auth is None else auth,**body})
                payload=await r.json();assert r.status==status,(r.status,payload)
                return payload
            await post('view',{},auth=signed(age=86401),status=401)
            await post('view',{},auth=signed(age=-120),status=401)
            await post('view',{},auth='forged',status=401)
            r=await client.post('/api/sales/view',json=[]);assert r.status==400
            # Configuration failures must not look like uncertain sale writes.
            from postgrest.exceptions import APIError
            for code, expected in [('42501','sales_database_permissions'), ('42P01','sales_database_schema'),
                                   ('PGRST205','sales_database_schema'), ('PGRST204','sales_database_schema'),
                                   ('08006','sales_database_unavailable')]:
                error=APIError({'code':code,'message':'private database details','details':None,'hint':None})
                with patch.object(sales_db,'load',side_effect=error):
                    result=await post('view',{},status=503)
                    assert result['code']==expected
                    assert 'private' not in result['error'] and 'продажу' not in result['error']
            with patch.object(sales_db,'load',side_effect=RuntimeError('private failure')):
                result=await post('view',{},status=503)
                assert result['error']=='Не удалось загрузить план. Повтори загрузку.'
            result=await post('view',{})
            assert result['has_settings'] is False  # An empty month is not an error.
            from sales_chat import parse_sales_message
            from sales_service import chat_write
            await db.get_or_create_user(1)
            await post('save',{'operation_id':str(uuid4()),'kind':'glass','value':1,'work_date':'2026-09-14'},status=405)
            await post('settings',{'targets':{'wine':143000},'glass_price':850},status=409)
            await chat_write(1,str(uuid4()),parse_sales_message('план продаж вино 143000; коктейли 110',date(2026,9,26)))
            await post('settings',{'targets':{'wine':143000,'cocktails':110,'desserts':82000,'turnover':1570000},'glass_price':850})
            oid=str(uuid4());sale={'action':'save','month':'2026-09','kind':'glass','value':2,'work_date':'2026-09-14'}
            await chat_write(1,oid,sale)
            result=await post('view',{});assert result['metrics']['wine']['total']==1700
            await chat_write(1,oid,sale);assert len(store.rows['sales_events'])==1
            try:await chat_write(1,oid,{**sale,'value':3})
            except ValueError:pass
            else:raise AssertionError('Conflicting id must not overwrite')
            await post('edit',{'operation_id':oid,'value':3,'work_date':'2026-09-14'},uid=2,status=404)
            await post('edit',{'operation_id':oid,'value':1.5,'work_date':'2026-09-14'},status=400)
            await post('edit',{'operation_id':oid,'value':2,'work_date':'2026-09-14'})
            result=await post('view',{},uid=2);assert result['metrics']['wine']['total'] is None
            await post('undo',{'operation_id':oid},uid=2,status=404)
            await post('report',{'operation_id':str(uuid4()),'cutoff':'2026-09-27','totals':{'wine':1}},status=400)
            totals={'wine':73238,'cocktails':57,'desserts':33724,'turnover':919783.5,'postcards':5,'dvd':2}
            rid=str(uuid4());report={'operation_id':rid,'cutoff':'2026-09-13','totals':totals}
            await post('report',report,status=409)
            await chat_write(1,rid,{'action':'report','month':'2026-09','cutoff':'2026-09-13','totals':totals})
            result=await post('view',{});assert result['metrics']['wine']['total']==74938
            assert store.rows['sales_reports'][0]['forecast']['metrics']['wine']['total'] is None
            await post('report',report);assert len(store.rows['sales_reports'])==1
            await post('report',{**report,'operation_id':str(uuid4()),'totals':{**totals,'wine':74000}})
            assert len(store.rows['sales_reports'])==2
            result=await post('undo',{'operation_id':oid});assert result['metrics']['wine']['total']==74000
            await chat_write(1,oid,sale)
            result=await post('view',{});assert result['metrics']['wine']['total']==74000
            result=await post('view',{'month':'2026-10'});assert result['metrics']['wine']['total'] is None
            exported=await sales_db.export(1);assert len(exported['sales_reports'])==2
            assert all(not rows for rows in (await sales_db.export(2)).values())
            # Real chat handler routes sales before the personal expense parser.
            import handlers
            store.rows['users'][0]['onboarded']=True
            message=SimpleNamespace(from_user=SimpleNamespace(id=1),text='бокал',forward_origin=None,
                bot=SimpleNamespace(id=123),chat=SimpleNamespace(id=1),message_id=100,answer=AsyncMock())
            await handlers.handle_text(message,None)
            assert message.answer.await_args.args[0].startswith('✓ Бокалы: 1')
            sales_count=len(store.rows['sales_events'])
            await handlers.handle_text(message,None)
            assert len(store.rows['sales_events'])==sales_count
            message.text='бутылка';message.message_id=101
            await handlers.handle_text(message,None)
            assert len(store.rows['sales_events'])==sales_count
            # Authenticated tips comparison is owner-scoped too.
            store.rows['entries']=[{'id':1,'user_id':1,'kind':'income','category':'Чаевые','signed_amount':2000,'created_at':'2026-09-02T12:00:00+03:00'},
                {'id':2,'user_id':2,'kind':'income','category':'Чаевые','signed_amount':999999,'created_at':'2026-09-02T12:00:00+03:00'}]
            r=await client.post('/api/tips_compare',json={'initData':signed(1),'anchor':'2026-09-01'})
            j=await r.json();assert r.status==200 and j['a']['gross']==2000
            # More records than a single response permits must remain complete.
            store.rows['entries']=[{'id':i,'user_id':1,'created_at':str(i).zfill(5)} for i in range(1207)]
            assert len(await db.get_all_entries(1))==1207
            assert not await db.get_all_entries(2)
            for path in ['/app','/app/sales.js','/app/sales.css','/app/tips.js','/app/tips.css']:
                r=await client.get(path);assert r.status==200
    print('HTTP checks passed: auth, validation, ownership, retries, undo, reconciliation, export, pagination, assets')

if __name__=='__main__':asyncio.run(main())
