"""No live network, staff fixtures or production DB."""
import asyncio
import os
import sys
import unittest
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with patch.dict(os.environ, {'SUPABASE_URL':'https://example.invalid','SUPABASE_KEY':'test'}), patch('supabase.create_client'):
    import report_sheet as sheet
    import report_sheet_chat as chat
    import report_photo as photo
import httpx

TODAY = date(2026,10,18)
ROWS = [
 ['Официант','Часы план','План продаж сентябрь 2026','Продажи 1-7','Осталось до плана','Продажи 8-14','Осталось до плана','Продажи 15-21'],
 ['Тест Один','100','100000','1\u00a0234,50','90000','500,25','80000','999'],
 ['Тест Два','120','100000','0','100000','100','99900',''],
 ['Остальные','','','888','','777','',''],
 ['План оборот/час = 10000'],
 ['Официант'], ['Тест Один','111','999','10000']]

class ParserTests(unittest.IsolatedAsyncioTestCase):
    def test_periods_are_summed_and_future_and_unlabeled_excluded(self):
        rows=sheet.parse_rows(ROWS,'2026-10',TODAY)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0],{'name':'Тест Один','totals':{'turnover':1734.75},'cutoff':'2026-10-14'})
        self.assertEqual(rows[1]['totals'],{'turnover':100})

    def test_cumulative_snapshot_is_not_summed(self):
        rows=[r[:] for r in ROWS];rows[0][5]='Продажи 1-14';rows[0][7]='Продажи 1-21'
        self.assertEqual(sheet.parse_rows(rows,'2026-10',TODAY)[0]['totals']['turnover'],500.25)

    def test_gaps_overlaps_duplicates_and_non_numbers_rejected(self):
        for mutation in ('gap','overlap','duplicate','bad','negative'):
            rows=[r[:] for r in ROWS]
            if mutation=='gap':rows[1][3]=''
            elif mutation=='overlap':rows[0][5]='Продажи 7-14'
            elif mutation=='duplicate':rows[2][0]='тест один'
            elif mutation=='negative':rows[1][3]='-1'
            else:rows[1][3]='#REF!'
            with self.assertRaises(ValueError,msg=mutation):sheet.parse_rows(rows,'2026-10',TODAY)

    def test_no_guessing_unidentified_metrics_and_explicit_zero(self):
        rows=[r[:] for r in ROWS];rows[4]=['без подписи']
        self.assertEqual(sheet.parse_rows(rows,'2026-10',TODAY),[])
        rows=[r[:] for r in ROWS];rows[2][5]='0'
        self.assertEqual(sheet.parse_rows(rows,'2026-10',TODAY)[1]['totals'],{'turnover':0})
        rows[2][3]='';rows[2][5]=''
        self.assertEqual(len(sheet.parse_rows(rows,'2026-10',TODAY)),1)

    def test_month_labels_and_tabs(self):
        self.assertEqual(sheet.sheet_month('Октябрь 26'),'2026-10')
        self.assertEqual(sheet.sheet_month('Июль 2026'),'2026-07')
        self.assertIsNone(sheet.sheet_month('Архив'))
        parser=sheet.Tabs();parser.feed('<div class="docs-sheet-tab-caption">Октябрь 26</div>')
        self.assertEqual(parser.names,['Октябрь 26'])

    async def test_fetch_rejects_redirects_html_oversize_and_untrusted_ids(self):
        for status, body in [(302,b''),(403,b''),(200,b'x'*(sheet.MAX_BYTES+1))]:
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(status,content=body)))
            with patch.object(sheet.httpx,'AsyncClient',return_value=client):
                with self.assertRaises(ValueError):await sheet.fetch('a'*25,sheet='Октябрь 26')
        with self.assertRaises(ValueError):await sheet.fetch('https://evil.example')
        with patch.object(sheet,'fetch',new=AsyncMock(return_value='<html>Login</html>')):
            with self.assertRaises(ValueError):await sheet.read('a'*25,'Октябрь 26',TODAY)

class ExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_preserves_text_in_numeric_columns_and_selected_tab(self):
        import json
        record=[9,0,'123456',[{'1':[[0,0,'Октябрь 26']]}]]
        metadata='[21350203,'+json.dumps(json.dumps(record,ensure_ascii=False),ensure_ascii=False)+']'
        exported='Официант,ХЛЕБ\nТест,10000\n'
        seen=[]
        def response(request):
            seen.append(str(request.url))
            if request.url.path.endswith('/edit'):return httpx.Response(200,text=metadata)
            if request.url.path.endswith('/export'):
                self.assertEqual(request.url.params['gid'],'123456')
                return httpx.Response(307,headers={'Location':'https://doc-0g-5k-sheets.googleusercontent.com/export/test'})
            return httpx.Response(200,text=exported)
        make_client=httpx.AsyncClient
        with patch.object(sheet.httpx,'AsyncClient',side_effect=lambda **kwargs:make_client(transport=httpx.MockTransport(response),**kwargs)):
            self.assertEqual(await sheet.fetch('a'*25,sheet='Октябрь 26'),exported)
        self.assertEqual(len(seen),3)

    def test_redirect_allowlist_and_metadata_fail_closed(self):
        for url in ['http://docs.google.com/a','https://evil.example/a','https://docs.google.com.evil.example/a','https://user:pass@docs.google.com/a','https://docs.google.com:8000/a','https://sheets.googleusercontent.com.evil.example/a','https://accounts.google.com/a']:
            self.assertFalse(sheet.allowed_download(url),url)
        self.assertTrue(sheet.allowed_download('https://doc-0g-5k-sheets.googleusercontent.com/a'))
        self.assertEqual(sheet.tab_ids('<html>changed</html>'),{})

class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.auto=patch('report_auto.enabled',new=AsyncMock(return_value=False))
        self.auto.start();self.addCleanup(self.auto.stop)
        photo.drafts.clear()
        chat._alerts.clear()
        self.context_patch=patch('categories.context',new=AsyncMock(return_value=None))
        self.context_patch.start();self.addCleanup(self.context_patch.stop)
        self.options_patch=patch('categories.options',new=AsyncMock(return_value=[]))
        self.options_patch.start();self.addCleanup(self.options_patch.stop)
        self.message=NS(from_user=NS(id=42),chat=NS(id=42),bot=NS(id=99),message_id=7,
                        text='https://docs.google.com/spreadsheets/d/'+'a'*25+'/edit?usp=drivesdk',answer=AsyncMock(),edit_text=AsyncMock())
    def callback(self,draft,action,uid=42):
        return NS(data=f'rs:{draft.nonce}:{action}',from_user=NS(id=uid),message=self.message,answer=AsyncMock())

    async def test_link_select_preview_restart_cancel_and_ownership(self):
        rows=sheet.parse_rows(ROWS,'2026-10',TODAY)
        with patch.object(chat,'op_today',return_value=TODAY),patch.object(sheet,'tabs',new=AsyncMock(return_value=['Январь 26','Октябрь 26'])),patch.object(sheet,'read',new=AsyncMock(return_value=rows)),patch.object(chat.research,'track'),patch.object(photo,'save_report',new=AsyncMock()) as save:
            await chat.receive_link(self.message)
            draft=photo.drafts[42]
            self.assertEqual(draft.report['sheet_name'],'Октябрь 26')
            await chat.action(self.callback(draft,'row0',uid=43))
            self.assertEqual(draft.phase,'sheet_rows')
            with patch.object(photo,'op_today',return_value=TODAY):await chat.action(self.callback(draft,'row0'))
            self.assertEqual(draft.phase,'review');self.assertNotIn('rows',draft.report)
            self.assertEqual(draft.totals,{'turnover':1734.75});save.assert_not_awaited()
            markup=self.message.answer.call_args.kwargs['reply_markup']
            self.assertTrue(any(b.text=='Начать заново' for row in markup.inline_keyboard for b in row))
            await chat.action(self.callback(draft,'restart'))
            self.assertEqual(draft.phase,'sheet_rows');self.assertEqual(draft.totals,{})
            await chat.action(self.callback(draft,'cancel'));self.assertNotIn(42,photo.drafts)
            save.assert_not_awaited()

    async def test_ambiguous_month_requires_tab_and_explicit_save_is_idempotent(self):
        rows=sheet.parse_rows(ROWS,'2026-10',TODAY)
        with patch.object(chat,'op_today',return_value=TODAY),patch.object(sheet,'tabs',new=AsyncMock(return_value=['Октябрь 26','Октябрь 2026'])),patch.object(sheet,'read',new=AsyncMock(return_value=rows)),patch.object(sheet,'fetch',new=AsyncMock(return_value='')),patch('schedule.enabled',return_value=False),patch.object(chat.research,'track'),patch.object(photo.db,'get_or_create_user',new=AsyncMock()),patch.object(photo,'save_report',new=AsyncMock()) as save:
            await chat.receive_link(self.message);draft=photo.drafts[42]
            self.assertEqual(draft.phase,'sheet_tabs')
            await chat.action(self.callback(draft,'tab1'))
            with patch.object(photo,'op_today',return_value=TODAY):await chat.action(self.callback(draft,'row1'))
            save.assert_not_awaited()
            cb=self.callback(draft,'save');cb.data=f'ocr:{draft.nonce}:save'
            await photo.photo_callback(cb)
            save.assert_awaited_once_with(42,draft.oid,'2026-10','2026-10-14',{'turnover':100})
            await photo.photo_callback(cb);self.assertEqual(save.await_count,1)

    async def test_dispatch_and_reset_command(self):
        from aiogram import Bot, Dispatcher, Router
        from aiogram.types import Message
        dp=Dispatcher();dp.include_router(chat.router);dp.include_router(photo.router)
        fallback=Router();received=[]
        @fallback.message()
        async def handle(message):received.append(message.text)
        dp.include_router(fallback)
        bot=Bot('123456:test-only')
        update={'update_id':1,'message':{'message_id':1,'date':1790530000,
            'from':{'id':42,'is_bot':False,'first_name':'Test'},'chat':{'id':42,'type':'private'},
            'text':self.message.text}}
        try:
            with patch.object(chat,'op_today',return_value=TODAY),patch.object(sheet,'tabs',new=AsyncMock(return_value=['Октябрь 26'])),patch.object(sheet,'read',new=AsyncMock(return_value=sheet.parse_rows(ROWS,'2026-10',TODAY))),patch.object(chat.research,'track'),patch.object(Message,'answer',new=AsyncMock()):
                await dp.feed_raw_update(bot,update)
                self.assertEqual(photo.drafts[42].phase,'sheet_rows');self.assertFalse(received)
                update['update_id']=2;update['message']['text']='/cancel'
                update['message']['entities']=[{'type':'bot_command','offset':0,'length':7}]
                await dp.feed_raw_update(bot,update)
                self.assertNotIn(42,photo.drafts);self.assertFalse(received)
        finally:
            await bot.session.close()

    async def test_closed_table_retry_and_uncertain_save_cannot_restart(self):
        with patch.object(sheet,'tabs',new=AsyncMock(side_effect=ValueError(sheet.ACCESS_ERROR))):
            await chat.receive_link(self.message)
        draft=photo.drafts[42];self.assertEqual(draft.phase,'sheet_retry')
        draft.phase='uncertain'
        await chat.action(self.callback(draft,'restart'))
        self.assertEqual(draft.phase,'uncertain')

    async def test_parse_error_notifies_owner_without_content_and_is_rate_limited(self):
        self.message.bot.send_message=AsyncMock()
        with patch.object(chat,'op_today',return_value=TODAY),patch.object(sheet,'tabs',new=AsyncMock(return_value=['Октябрь 26'])),patch.object(sheet,'read',new=AsyncMock(side_effect=sheet.SheetParseError('duplicate_employee_names'))),patch('admin.ADMIN_ID',99):
            await chat.receive_link(self.message)
            self.message.bot.send_message.assert_awaited_once()
            call=self.message.bot.send_message.call_args
            self.assertEqual(call.args[0],99)
            self.assertIn('duplicate_employee_names',call.args[1])
            self.assertNotIn('docs.google',call.args[1]);self.assertNotIn('Тест Один',call.args[1])
            self.assertIn('Уже пишу Лёше',self.message.answer.call_args.args[0])
            await chat.receive_link(self.message)
            self.assertEqual(self.message.bot.send_message.await_count,1)

    async def test_failed_owner_notification_does_not_claim_delivery(self):
        self.message.bot.send_message=AsyncMock(side_effect=RuntimeError('private error'))
        draft=photo.Draft('nonce','oid','',phase='sheet_loading')
        with patch('admin.ADMIN_ID',99),self.assertLogs(level='ERROR') as logs:
            await chat.report_failure(self.message,draft,sheet.SheetParseError('invalid_amount'))
        self.assertNotIn('Уже пишу',self.message.answer.call_args.args[0])
        self.assertNotIn('private error',' '.join(logs.output))

    async def test_schedule_links_keep_existing_flow(self):
        with patch.object(chat,'op_today',return_value=TODAY),patch.object(sheet,'tabs',new=AsyncMock(return_value=['Октябрь 26'])),patch.object(sheet,'read',new=AsyncMock(return_value=[])),patch.object(sheet,'fetch',new=AsyncMock(return_value='grid')),patch('schedule_sheet.parse_rows',return_value=[{'name':'Тест'}]),patch('schedule.enabled',return_value=True),patch('schedule_chat.sheet_link',new=AsyncMock()) as old:
            await chat.receive_link(self.message)
            old.assert_awaited_once_with(self.message)

if __name__=='__main__':unittest.main()
