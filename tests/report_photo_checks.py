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
RealClient=httpx.AsyncClient
REAL_SLEEP=asyncio.sleep
# Реальный шлюз лимита здесь не ждёт: его работу проверяют отдельные тесты ниже.
vision._pacer.limit=vision._pacer.tokens=1e12

RAW={'cutoff':None,'targets':{'wine':143000},'rows':[{'name':'Алексей','totals':{'wine':73238,'cocktails':57,'postcards':None}},
                                                {'name':'Другой','totals':{'wine':100}}]}

class VisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_quota_is_distinct_from_oversized_request(self):
        for text,quota in [('Rate limit reached for private account',True),('Request too large for private account',False)]:
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(429,headers={'retry-after':'2'},json={'error':{'message':text}})))
            with patch.dict(os.environ,{'GROQ_API_KEY':'test','REPORT_VISION_ENABLED':'1'}),patch.object(vision.httpx,'AsyncClient',return_value=client),\
                 patch.object(vision.asyncio,'sleep',new=AsyncMock()):
                with self.assertRaises(vision.VisionError) as caught:await vision.request_json(b'\xff\xd8\xfftest','test')
                self.assertEqual(isinstance(caught.exception,vision.VisionQuotaError),quota)
                self.assertNotIn('private account',str(caught.exception))
                if quota:self.assertEqual(caught.exception.retry_after,2)

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

    async def test_oversized_request_is_not_temporary_quota(self):
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
            httpx.Response(429,json={'error':{'message':'Request too large for SECRET account'}})))
        with patch.dict(os.environ,{'GROQ_API_KEY':'test','REPORT_VISION_ENABLED':'1'}),patch.object(vision.httpx,'AsyncClient',return_value=client):
            with self.assertRaises(vision.VisionError) as error:await vision.recognize(b'\xff\xd8\xfftest')
            self.assertIn('по размеру',str(error.exception))
            self.assertNotIn('SECRET',str(error.exception))

def ok(content=None,headers=None):
    return httpx.Response(200,headers=headers or {},json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(content or {'rows':[]})}}]})


def quota(message='Rate limit reached on tokens per minute (TPM): Limit 8000, Used 6519, Requested 3350.',retry='14'):
    return httpx.Response(429,headers={'retry-after':retry,'x-ratelimit-limit-tokens':'8000','x-ratelimit-remaining-tokens':'1481',
                                       'x-ratelimit-limit-requests':'1000','x-ratelimit-remaining-requests':'998'},json={'error':{'message':message}})


class RateLimitTests(unittest.IsolatedAsyncioTestCase):
    """Минутный лимит токенов: ждём и повторяем чтение, не отдаём 429 человеку сразу."""
    def setUp(self):
        self.saved=(vision._pacer,vision._clock)
        self.now=[1000.0];self.sleeps=[]
        vision._clock=lambda:self.now[0]
        vision._pacer=vision._Pacer()
        async def sleep(seconds):
            self.sleeps.append(seconds);self.now[0]+=seconds
        self.sleep=patch.object(vision.asyncio,'sleep',new=sleep);self.sleep.start()
        self.env=patch.dict(os.environ,{'GROQ_API_KEY':'test','REPORT_VISION_ENABLED':'1'});self.env.start()
    def tearDown(self):
        self.sleep.stop();self.env.stop();vision._pacer,vision._clock=self.saved

    def client(self,*responses):
        calls=[]
        def handler(request):
            calls.append(json.loads(request.content));return responses[min(len(calls),len(responses))-1]
        # httpx-клиент нельзя открыть второй раз: каждый запрос получает свой.
        return (lambda *args,**kwargs:RealClient(transport=httpx.MockTransport(handler))),calls

    async def test_429_with_retry_after_waits_and_succeeds(self):
        client,calls=self.client(quota(),ok({'rows':[{'name':'А'}]}))
        with patch.object(vision.httpx,'AsyncClient',side_effect=client):
            self.assertEqual(await vision.request_json(b'\xff\xd8\xfftest','p',900),{'rows':[{'name':'А'}]})
        self.assertEqual(len(calls),2);self.assertEqual(self.sleeps,[15])

    async def test_long_or_daily_limit_is_not_retried(self):
        for response in (quota(retry='600'),quota('Rate limit on tokens per day (TPD): Limit 200000, Used 199999',retry='10')):
            vision._pacer=vision._Pacer()
            client,calls=self.client(response)
            with patch.object(vision.httpx,'AsyncClient',side_effect=client):
                with self.assertRaises(vision.VisionQuotaError):await vision.request_json(b'\xff\xd8\xfftest','p')
            self.assertEqual(len(calls),1)
        self.assertEqual(self.sleeps,[])

    async def test_retries_are_bounded_and_diagnostic_has_only_numbers(self):
        client,calls=self.client(quota('Org org_SECRET on tokens per minute (TPM): Limit 8000, Used 6519, Requested 3350. SECRETTEXT'))
        with patch.object(vision.httpx,'AsyncClient',side_effect=client):
            with self.assertRaises(vision.VisionQuotaError) as caught:await vision.request_json(b'\xff\xd8\xfftest','p')
        self.assertEqual(len(calls),1+vision.MAX_RETRIES)
        note=caught.exception.diagnostic
        self.assertIn('TPM',note);self.assertIn('лимит 8000',note);self.assertIn('занято 6519',note);self.assertIn('запрошено 3350',note)
        self.assertNotIn('SECRET',note);self.assertNotIn('SECRET',str(caught.exception))

    async def test_requests_queue_instead_of_exceeding_the_minute_budget(self):
        client,calls=self.client(ok())
        with patch.object(vision.httpx,'AsyncClient',side_effect=client):
            await vision.request_json(b'\xff\xd8\xfftest','p',1800)   # около 3900 токенов
            await vision.request_json(b'\xff\xd8\xfftest','p',1800)
            self.assertEqual(self.sleeps,[])
            await vision.request_json(b'\xff\xd8\xfftest','p',1800)   # третий не помещается в 8000
        self.assertEqual(len(self.sleeps),1);self.assertGreater(self.sleeps[0],0)
        self.assertEqual(len(calls),3)

    async def test_pacer_follows_provider_remaining_tokens(self):
        client,calls=self.client(ok(headers={'x-ratelimit-limit-tokens':'8000','x-ratelimit-remaining-tokens':'1481'}))
        with patch.object(vision.httpx,'AsyncClient',side_effect=client):
            await vision.request_json(b'\xff\xd8\xfftest','p',1800)
            await vision.request_json(b'\xff\xd8\xfftest','p',1800)
        self.assertEqual(len(self.sleeps),1)
        self.assertAlmostEqual(self.sleeps[0],(vision.IMAGE_TOKENS+1+40+1800-1481)*60/8000,delta=1)

    async def test_wait_longer_than_budget_fails_without_calling_provider(self):
        vision._pacer.tokens=0;vision._pacer.limit=100.0
        client,calls=self.client(ok())
        with patch.object(vision.httpx,'AsyncClient',side_effect=client),patch.object(vision,'MAX_WAIT',30):
            with self.assertRaises(vision.VisionQuotaError) as caught:await vision.request_json(b'\xff\xd8\xfftest','p',50)
        self.assertEqual(calls,[]);self.assertIn('очередь лимита',caught.exception.diagnostic)

    async def test_truncated_answer_is_asked_again_once_with_more_room(self):
        truncated=httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{"rows":['}}]})
        client,calls=self.client(truncated,ok({'rows':[]}))
        with patch.object(vision.httpx,'AsyncClient',side_effect=client):
            self.assertEqual(await vision.request_json(b'\xff\xd8\xfftest','p',900),{'rows':[]})
        self.assertEqual([c['max_completion_tokens'] for c in calls],[900,1800])
        client,calls=self.client(truncated)
        with patch.object(vision.httpx,'AsyncClient',side_effect=client):
            with self.assertRaises(vision.VisionError):await vision.request_json(b'\xff\xd8\xfftest','p',900)
        self.assertEqual(len(calls),2)

    async def test_length_retry_never_asks_for_more_than_the_minute_budget_allows(self):
        truncated=httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{"rows":['}}]})
        prompt='p'*1000   # около 2588 токенов вместе с картинкой
        client,calls=self.client(truncated,ok({'rows':[]}))
        with patch.object(vision.httpx,'AsyncClient',side_effect=client):
            self.assertEqual(await vision.request_json(b'\xff\xd8\xfftest',prompt,3000),{'rows':[]})
        sizes=[c['max_completion_tokens'] for c in calls]
        self.assertEqual(sizes[0],3000);self.assertGreater(sizes[1],3000)
        self.assertLessEqual(vision.IMAGE_TOKENS+500+40+sizes[1],8000)
        # Удвоение не поместилось бы в лимит вообще: повтора нет, ответ честно считается неполным.
        client,calls=self.client(truncated,ok({'rows':[]}))
        with patch.object(vision.httpx,'AsyncClient',side_effect=client):
            with self.assertRaises(vision.VisionError):await vision.request_json(b'\xff\xd8\xfftest',prompt,6000)
        self.assertEqual(len(calls),1)

    async def test_long_wait_does_not_hold_the_queue_lock(self):
        # Пока первый запрос спит в ожидании места, второй уже занимает очередь за ним.
        pacer=vision._Pacer();pacer.tokens=0
        gate=asyncio.Event();sleeping=[]
        async def blocking(seconds):
            sleeping.append(round(seconds));await gate.wait()
        with patch.object(vision.asyncio,'sleep',new=blocking):
            first=asyncio.create_task(pacer.admit(3000,self.now[0]+1000))
            second=asyncio.create_task(pacer.admit(1000,self.now[0]+1000))
            for _ in range(5):await REAL_SLEEP(0)
            self.assertEqual(len(sleeping),2)
            self.assertLess(sleeping[0],sleeping[1])   # один общий баланс: вторая очередь дольше первой
            gate.set();await asyncio.gather(first,second)

    async def test_cancelled_wait_returns_its_reserved_tokens(self):
        pacer=vision._Pacer();pacer.tokens=0
        gate=asyncio.Event()
        async def blocking(seconds):await gate.wait()
        with patch.object(vision.asyncio,'sleep',new=blocking):
            task=asyncio.create_task(pacer.admit(3000,self.now[0]+1000))
            for _ in range(3):await REAL_SLEEP(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(pacer.inflight,0);self.assertAlmostEqual(pacer.tokens,0,delta=1)

    async def test_schedule_steps_survive_one_429(self):
        import schedule_reader
        client,calls=self.client(quota(),ok({'rows':[{'name':'Алексей'}]}))
        with patch.object(vision.httpx,'AsyncClient',side_effect=client),\
             patch.object(schedule_reader.layout,'directory',side_effect=ValueError()),\
             patch.object(schedule_reader.layout,'whole_image',return_value=b'\xff\xd8\xfftest'):
            result=await schedule_reader.read_directory(b'ignored')
        self.assertEqual(result['rows'][0]['name'],'Алексей');self.assertEqual(len(calls),2)

    def test_scheduler_reserve_stays_below_minute_budget(self):
        # Имена и строка графика подряд должны помещаться в 8000 токенов в минуту.
        import inspect,schedule_reader
        source=inspect.getsource(schedule_reader)
        self.assertIn('request_json(image,prompt,1200)',source)
        self.assertIn("1600 if mode=='whole' else 1100",source)


class RowVisionTests(unittest.IsolatedAsyncioTestCase):
    def test_grid_rejects_unknown_and_tilted_layouts(self):
        from PIL import Image, ImageDraw
        import report_layout as layout
        image=Image.new('RGB',(1280,346),'white');draw=ImageDraw.Draw(image)
        for y in range(67,324,16):draw.line((20,y,1279,y),fill='black')
        for x in layout.EDGES:draw.line((x,67,x,323),fill='black')
        _,_,bands=layout.detect(layout.encode(image))
        self.assertEqual(len(bands),16)
        for invalid in (Image.new('RGB',(1280,346),'white'),image.rotate(5,fillcolor='white')):
            with self.assertRaises(ValueError):layout.detect(layout.encode(invalid))
        doubled=image.resize((2560,692))
        self.assertEqual(len(layout.detect(layout.encode(doubled))[2]),16)

    def test_recalibration_preserves_row_with_white_margins(self):
        from PIL import Image,ImageDraw,ImageOps
        import report_layout as layout
        image=Image.new('RGB',(1280,346),'white');draw=ImageDraw.Draw(image)
        draw.rectangle((0,0,1279,345),outline='black')
        for y in range(67,324,16):draw.line((20,y,1279,y),fill='black')
        for x in layout.EDGES:draw.line((x,67,x,323),fill='black')
        raw=layout.encode(image);padded=layout.encode(ImageOps.expand(image,border=40,fill='white'))
        self.assertEqual(layout.row_image(raw,6,relaxed=True),layout.row_image(padded,6,relaxed=True))
        with self.assertRaises(ValueError):layout.relaxed_detect(layout.encode(image.resize((640,173))))

    def test_report_cropped_at_top_and_left_keeps_columns_aligned(self):
        from PIL import Image,ImageDraw
        import report_layout as layout
        image=Image.new('RGB',(1280,346),'white');draw=ImageDraw.Draw(image)
        draw.rectangle((0,0,1279,345),outline='black')
        for y in range(67,324,16):draw.line((20,y,1279,y),fill='black')
        for x in layout.EDGES:draw.line((x,67,x,323),fill='black')
        cropped=image.crop((11,9,1280,346)).resize((1280,340))
        data=layout.encode(cropped)
        with self.assertRaises(ValueError):layout.detect(data)
        normalized,scale,bands=layout.relaxed_detect(data)
        self.assertEqual(scale,1);self.assertEqual(len(bands),17)
        pixels=normalized.convert('L').load();y=(bands[6][0]+bands[6][1])//2
        for edge in layout.EDGES:
            self.assertLess(min(pixels[edge+d,y] for d in range(-2,3)),170)
        self.assertTrue(layout.row_image(data,6,relaxed=True).startswith(b'\x89PNG'))

    def test_printed_number_rejects_ambiguous_formats(self):
        self.assertEqual(vision.printed_number('73 238,00'),73238)
        for value in ('1 2','1,234,56','NaN','1e6','-1','about 10'):
            with self.assertRaises(ValueError):vision.printed_number(value)

    async def test_directory_rejects_duplicate_indexes(self):
        with patch('report_layout.directory',return_value=(b'image',3)),patch.object(vision,'request_json',new=AsyncMock(return_value={'rows':[{'index':0,'name':'Тест Имя'},{'index':0,'name':'Другой'}]})):
            with self.assertRaises(vision.VisionError):await vision.recognize_directory(b'photo')

    async def test_recalibrated_grid_always_uses_verified_row_crop(self):
        with patch('report_layout.directory',side_effect=[ValueError('grid'),(b'prepared',3)]) as directory,patch.object(vision,'request_json',new=AsyncMock(return_value={'rows':[{'index':0,'name':'Тест Имя'}]})) as read:
            result=await vision.recognize_directory(b'whole photo')
            self.assertTrue(result['relaxed_grid']);self.assertEqual(read.await_args.args[0],b'prepared')
            self.assertTrue(directory.call_args.kwargs['relaxed'])
        raw={'name':'Тест Имя','totals':{'wine':100},'percent':{}}
        with patch('report_layout.row_image',return_value=b'one verified row') as crop,patch.object(vision,'request_json',new=AsyncMock(return_value=raw)) as read:
            result=await vision.recognize_row(b'whole photo',0,'Тест Имя',relaxed_grid=True)
            crop.assert_called_once_with(b'whole photo',0,relaxed=True)
            self.assertEqual(read.await_args.args[0],b'one verified row')
        with patch('report_layout.directory',side_effect=ValueError('unknown grid')),patch.object(vision,'request_json',new=AsyncMock()) as read:
            with self.assertRaises(vision.VisionError):await vision.recognize_directory(b'whole photo')
            read.assert_not_awaited()

    async def test_row_name_and_decimal_percentage(self):
        raw={'name':'Тест Имя','totals':{'cocktails':'39,00'},'percent':{'cocktails':'35,45%'}}
        with patch('report_layout.row_image',return_value=b'image'),patch.object(vision,'request_json',new=AsyncMock(return_value=raw)):
            row=await vision.recognize_row(b'photo',0,'Тест Имя')
            self.assertEqual(row['percent']['cocktails'],35.45)
            with self.assertRaises(vision.VisionError):await vision.recognize_row(b'photo',0,'Другой Человек')

class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_flexible_shift_goal(self):
        import handlers
        message=NS(from_user=NS(id=1),text='план 2,5 тыс',answer=AsyncMock())
        self.assertIsNotNone(handlers.PLAN_RE.fullmatch(message.text))
        self.assertIsNone(handlers.PLAN_RE.fullmatch('план на октябрь вино 143 тыс'))
        with patch.object(handlers.db,'set_shift_goal',new=AsyncMock()) as save:
            await handlers.shift_plan(message)
            save.assert_awaited_once_with(1,2500)

    async def test_welcome_has_no_presentation(self):
        import handlers
        message=NS(from_user=NS(id=1),answer=AsyncMock(),answer_media_group=AsyncMock())
        with patch.object(handlers.db,'set_onboarded',new=AsyncMock()):
            await handlers._greet(message,'<Имя>')
        message.answer_media_group.assert_not_awaited()
        text=message.answer.call_args.args[0]
        self.assertIn('чай 1500',text)
        self.assertIn('&lt;Имя&gt;',text)

    def test_preview_does_not_round_large_amounts(self):
        draft=photo.Draft('n','id','file',totals={'turnover':919783.5})
        self.assertIn('919 783,5',photo.preview(draft))

    async def asyncSetUp(self):
        self.auto=patch('report_auto.enabled',new=AsyncMock(return_value=False))
        self.auto.start();self.addCleanup(self.auto.stop)
        photo.drafts.clear()
        self.category_context=patch('categories.context',new=AsyncMock(return_value=None))
        self.category_context.start();self.addCleanup(self.category_context.stop)
        self.category_options=patch('categories.options',new=AsyncMock(return_value=[]))
        self.category_options.start();self.addCleanup(self.category_options.stop)
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

    async def test_rate_limit_preserves_draft_without_writes(self):
        client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request:httpx.Response(429,headers={'retry-after':'60'},text='private provider details')))
        with patch.dict(os.environ,{'GROQ_API_KEY':'test','REPORT_VISION_ENABLED':'1'}),\
             patch.object(vision.httpx,'AsyncClient',return_value=client),\
             patch.object(photo,'chat_write',new=AsyncMock()) as plans,\
             patch.object(photo,'save_report',new=AsyncMock()) as save:
            await photo.receive_photo(self.message)
            draft=photo.drafts[1]
            async def download(path,destination):destination.write(b'\xff\xd8\xfftest')
            self.bot.download_file.side_effect=download
            await photo.photo_callback(self.callback(draft,'read'))
            self.assertIs(photo.drafts[1],draft)
            self.assertEqual(draft.phase,'consent')
            self.assertEqual(self.message.edit_text.call_args.args[0],
                             'Сервис пока не принимает новые фото. Попробуй чуть позже.')
            save.assert_not_awaited();plans.assert_not_awaited()
            with patch.object(vision,'recognize',new=AsyncMock(return_value=vision.clean_result(copy.deepcopy(RAW)))):
                await photo.photo_callback(self.callback(draft,'read'))
                self.assertEqual(draft.phase,'row')
                save.assert_not_awaited();plans.assert_not_awaited()
            photo.expire(1,draft)

    async def test_row_mode_confirmation_and_plan_preservation(self):
        draft=photo.Draft('n','id','file',phase='row',image=b'photo',report={
            'row_mode':True,'targets':{},'rows':[{'index':6,'name':'Тест Имя'}]})
        photo.drafts[1]=draft
        self.message.chat=NS(id=1)
        result={'name':'Тест Имя','totals':{'wine':100,'postcards':2},'percent':{'wine':50}}
        with patch.object(vision,'recognize_row',new=AsyncMock(return_value=result)),patch.object(photo.db,'_execute',new=AsyncMock(return_value=NS(data=[{'targets':{'wine':400}}]))),patch.object(photo,'chat_write',new=AsyncMock()) as plans,patch.object(photo,'save_report',new=AsyncMock()) as save,patch.object(photo.db,'get_or_create_user',new=AsyncMock()):
            await photo.photo_callback(self.callback(draft,'row0'))
            self.assertIsNone(draft.image);self.assertEqual(draft.phase,'date')
            self.message.text='2026-09-13';await photo.photo_text(self.message)
            self.assertEqual(draft.report['warnings'],['wine'])
            self.assertIn('⚠ Вино',photo.preview(draft))
            save.assert_not_awaited();plans.assert_not_awaited()
            await photo.photo_callback(self.callback(draft,'save'))
            save.assert_awaited_once();plans.assert_not_awaited()

    async def test_failed_row_keeps_retry_and_expiry_releases_image(self):
        draft=photo.Draft('n','id','file',phase='row',image=b'photo',report={
            'row_mode':True,'rows':[{'index':6,'name':'Тест Имя'}]})
        photo.drafts[1]=draft
        with patch.object(vision,'recognize_row',new=AsyncMock(side_effect=vision.VisionError('test'))):
            await photo.photo_callback(self.callback(draft,'row0'))
            self.assertEqual(draft.phase,'row');self.assertEqual(draft.image,b'photo')
            self.assertIn('rows',draft.report)
        photo.expire(1,draft);self.assertIsNone(draft.image)

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
