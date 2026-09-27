"""Isolated from legacy tests that replace aiohttp modules."""
import asyncio
import copy
import json
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
with patch.dict(os.environ, {'SUPABASE_URL':'https://example.invalid', 'SUPABASE_KEY':'test'}), patch('supabase.create_client'):
    import report_photo as photo
import report_vision as vision

RAW={'cutoff':None,'targets':{'wine':143000},'rows':[{'name':'Алексей','totals':{'wine':73238,'cocktails':57,'postcards':None}},
                                                {'name':'Другой','totals':{'wine':100}}]}

class VisionTests(unittest.IsolatedAsyncioTestCase):
    def test_missing_and_invalid(self):
        result=vision.clean_result(RAW)
        self.assertNotIn('postcards',result['rows'][0]['totals'])
        self.assertIsNone(result['cutoff'])
        for totals in ({'cocktails':1.5},{'wine':True},{'wine':'NaN'},{'bonus':100},{'wine':-1}):
            raw=copy.deepcopy(RAW);raw['rows'][0]['totals']=totals
            with self.assertRaises(ValueError):vision.clean_result(raw)
        raw=copy.deepcopy(RAW);raw['rows'][0]['totals']={'wine':0}
        self.assertEqual(vision.clean_result(raw)['rows'][0]['totals'],{'wine':0})

    async def test_transport(self):
        async def response(request):
            payload=json.loads(request.content)
            self.assertEqual(payload['response_format'],{'type':'json_object'})
            self.assertTrue(payload['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,'))
            self.assertNotIn('api.telegram',request.content.decode())
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(RAW)}}]})
        client=httpx.AsyncClient(transport=httpx.MockTransport(response))
        with patch.dict(os.environ,{'GROQ_API_KEY':'test','REPORT_VISION_ENABLED':'1'}),patch.object(vision.httpx,'AsyncClient',return_value=client):
            self.assertEqual((await vision.recognize(b'\xff\xd8\xfftest'))['rows'][0]['totals']['wine'],73238)

    async def test_service_failures(self):
        for status in (401,403,429,500):
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status,text='SECRET')))
            with patch.dict(os.environ,{'GROQ_API_KEY':'test','REPORT_VISION_ENABLED':'1'}),patch.object(vision.httpx,'AsyncClient',return_value=client):
                with self.assertRaises(vision.VisionError) as error:await vision.recognize(b'\xff\xd8\xfftest')
                self.assertNotIn('SECRET',str(error.exception))

class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        photo.drafts.clear()
        self.bot=NS(id=10,get_file=AsyncMock(return_value=NS(file_path='private',file_size=10)),download_file=AsyncMock())
        self.message=NS(from_user=NS(id=1),chat=NS(id=1),message_id=5,photo=[NS(file_id='file',file_size=10)],
                        bot=self.bot,answer=AsyncMock(),edit_text=AsyncMock())

    def callback(self,draft,action,uid=1):
        return NS(from_user=NS(id=uid),data=f'ocr:{draft.nonce}:{action}',message=self.message,bot=self.bot,answer=AsyncMock())

    async def test_consent_selection_confirmation(self):
        with patch.object(vision,'configured',return_value=True),patch.object(vision,'recognize',new=AsyncMock(return_value=vision.clean_result(copy.deepcopy(RAW)))) as recognize,\
             patch.object(photo.db,'get_or_create_user',new=AsyncMock()),patch.object(photo,'chat_write',new=AsyncMock()) as plans,\
             patch.object(photo,'save_report',new=AsyncMock()) as save,patch.object(photo,'op_today',return_value=date(2026,9,27)):
            await photo.receive_photo(self.message)
            recognize.assert_not_awaited();save.assert_not_awaited()
            draft=photo.drafts[1]
            await photo.photo_callback(self.callback(draft,'read',uid=2))
            recognize.assert_not_awaited()
            await photo.photo_callback(self.callback(draft,'read'))
            recognize.assert_awaited_once();save.assert_not_awaited()
            await photo.photo_callback(self.callback(draft,'read'))
            recognize.assert_awaited_once()
            await photo.photo_callback(self.callback(draft,'row0'))
            self.assertEqual(draft.phase,'date');self.assertNotIn('rows',draft.report)
            self.message.text='2026-09-13';await photo.photo_text(self.message)
            self.assertEqual(draft.phase,'review');save.assert_not_awaited()
            await photo.photo_callback(self.callback(draft,'save'))
            save.assert_awaited_once_with(1,draft.oid,'2026-09','2026-09-13',{'wine':73238,'cocktails':57})
            plans.assert_awaited_once();self.assertNotIn(1,photo.drafts)
            await photo.photo_callback(self.callback(draft,'save'));save.assert_awaited_once()

    async def test_retry_and_expiry(self):
        draft=photo.Draft('n','fixed-id','file',phase='review',totals={'wine':10},cutoff='2026-09-13')
        photo.drafts[1]=draft
        with patch.object(photo.db,'get_or_create_user',new=AsyncMock()),patch.object(photo,'save_report',new=AsyncMock(side_effect=[RuntimeError(),None])) as save:
            await photo.photo_callback(self.callback(draft,'save'))
            self.assertEqual(draft.phase,'uncertain')
            await photo.photo_callback(self.callback(draft,'edit'));self.assertEqual(draft.phase,'uncertain')
            await photo.photo_callback(self.callback(draft,'save'))
            self.assertEqual(save.await_args_list[0],save.await_args_list[1])
        newer=photo.Draft('new','new-id','file');photo.drafts[1]=newer
        photo.expire(1,draft);self.assertIs(photo.drafts[1],newer)
        photo.expire(1,newer);self.assertNotIn(1,photo.drafts)

    async def test_edit_then_review(self):
        draft=photo.Draft('n','id','file',phase='review',totals={'wine':10},cutoff='2026-09-13',report={'targets':{'wine':100}})
        photo.drafts[1]=draft
        await photo.photo_callback(self.callback(draft,'edit'))
        self.message.text='отчет 2026-09-13 вино 20; коктейли 2'
        with patch.object(photo,'op_today',return_value=date(2026,9,27)):
            await photo.photo_text(self.message)
        self.assertEqual(draft.phase,'review');self.assertEqual(draft.totals,{'wine':20,'cocktails':2})
        self.assertEqual(draft.report['targets'],{})

    async def test_dispatch_and_commands(self):
        from aiogram import Bot, Dispatcher, Router
        from aiogram.types import Message
        dp=Dispatcher();dp.include_router(photo.router)
        fallback=Router();received=[]
        @fallback.message()
        async def handle(message):received.append(message.text)
        dp.include_router(fallback)
        bot=Bot('123456:test-only')
        update={'update_id':1,'message':{'message_id':1,'date':1790530000,
            'from':{'id':1,'is_bot':False,'first_name':'Test'},'chat':{'id':1,'type':'private'},
            'photo':[{'file_id':'file','file_unique_id':'unique','width':100,'height':100,'file_size':10}]}}
        with patch.object(vision,'configured',return_value=True),patch.object(Message,'answer',new=AsyncMock()):
            await dp.feed_raw_update(bot,update)
            self.assertIn(1,photo.drafts);self.assertFalse(received)
            update['update_id']=2;update['message'].pop('photo');update['message']['text']='/start'
            update['message']['entities']=[{'type':'bot_command','offset':0,'length':6}]
            await dp.feed_raw_update(bot,update)
            self.assertNotIn(1,photo.drafts);self.assertEqual(received,['/start'])
        await bot.session.close()

if __name__=='__main__':unittest.main()
