"""Automatic report flow; synthetic fixtures, no services or staff data."""
import asyncio,copy,io,os,sys,unittest
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch,Mock
from uuid import uuid4
from PIL import Image
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with patch.dict(os.environ,{'SUPABASE_URL':'https://example.invalid','SUPABASE_KEY':'test'}),patch('supabase.create_client'):
 import report_auto as auto,report_photo as photo,category_groq as groq,categories,report_sheet_chat as sheets
R='11111111-1111-4111-8111-111111111111'
PROFILE={'restaurant_id':R,'restaurant_name':'Тест ресторан','report_name':'Тест Имя','name_key':'тест имя','status':'approved','has_admin':True,'requested_at':'2026-10-01T00:00:00Z'}
def cfg():return categories.discover(categories.empty_context(R,'2026-10',7),[{'name':'Гарниры','unit':'amount'}])
def draft():
 c=cfg();cid=c['definitions'][0]['id']
 return photo.Draft('n',str(uuid4()),'file',phase='reading',cutoff='2026-10-14',report={'category_config':c,'rows':[{'name':'Тест Имя','totals':{cid:1500}}]})
def message():return NS(chat=NS(id=7),from_user=NS(id=7),bot=NS(id=10,send_message=AsyncMock(),get_file=AsyncMock(return_value=NS(file_size=50,file_path='file')),download_file=AsyncMock()),message_id=100,photo=[NS(file_id='f',file_size=50)],answer=AsyncMock(),edit_reply_markup=AsyncMock())
def raw(name='Тест Имя'):
 return {'kind':'sales','cutoff':'2026-10-14','categories':[{'name':'Гарниры','unit':'amount'}],'rows':[{'name':name,'totals':{'Гарниры':1500}}]}

class AutoTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):photo.drafts.clear();auto._alerted.clear()
 async def test_no_model_call_before_one_time_consent(self):
  m=message()
  with patch.object(auto,'enabled',new=AsyncMock(return_value=True)),patch.object(auto.db,'_execute',new=AsyncMock(return_value=NS(data=[]))),patch.object(auto,'process_photo',new=AsyncMock()) as read:
   self.assertTrue(await auto.receive(m));read.assert_not_awaited()
   self.assertIn('Groq',m.answer.call_args.args[0]);self.assertNotIn('Что на фото',m.answer.call_args.args[0])
 async def test_saved_consent_starts_without_type_question(self):
  m=message()
  with patch.object(auto,'enabled',new=AsyncMock(return_value=True)),patch.object(auto.db,'_execute',new=AsyncMock(return_value=NS(data=[{'image_recognition_consent_at':'2026-10-01'}]))),patch.object(auto,'process_photo',new=AsyncMock()) as read:
   await auto.receive(m);read.assert_awaited_once();m.answer.assert_not_awaited()
 async def test_consent_callback_from_other_user_does_not_send_image(self):
  m=message();d=draft();d.phase='auto_consent';photo.drafts[7]=d
  q=NS(from_user=NS(id=8),data='ac:n:yes',message=m,answer=AsyncMock())
  with patch.object(auto,'process_photo',new=AsyncMock()) as read:
   await auto.consent(q);read.assert_not_awaited()
 async def test_targeted_read_finds_missing_row_with_fragments(self):
  c=categories.empty_context(R,'2026-10',7);missing=raw();missing['rows']=[]
  with patch.object(groq,'request_json',new=AsyncMock(side_effect=[missing,raw(),missing])) as read,patch.object(groq,'enlarged_fragments',return_value=[b'original',b'crop']) as crop:
   result=await groq.recognize_person(b'image',c,'Тест Имя')
   self.assertEqual(result['rows'][0]['name'],'Тест Имя');self.assertEqual(read.await_count,3)
   self.assertIn('Тест Имя',read.call_args.args[1]);crop.assert_called_once()
 async def test_wrong_or_duplicate_employee_is_never_saved(self):
  c=categories.empty_context(R,'2026-10',7)
  for response in [raw('Другой'),{**raw(),'rows':[raw()['rows'][0],raw()['rows'][0]]}]:
   with patch.object(groq,'request_json',new=AsyncMock(return_value=response)),patch.object(groq,'enlarged_fragments',return_value=[b'crop']):
    with self.assertRaises(ValueError):await groq.recognize_person(b'image',c,'Тест Имя')
 async def test_valid_target_does_not_scan_all_waiters_or_retry(self):
  with patch.object(groq,'request_json',new=AsyncMock(return_value=raw())) as read,patch.object(groq,'enlarged_fragments') as crop:
   await groq.recognize_person(b'image',categories.empty_context(R,'2026-10',7),'Тест Имя')
   read.assert_awaited_once();crop.assert_not_called()
 async def test_schedule_is_routed_without_sales_write(self):
  m=message();m.answer.return_value=m;d=draft()
  with patch.object(auto,'person',new=AsyncMock(return_value=PROFILE)),patch.object(auto,'configuration',new=AsyncMock(return_value=cfg())),patch.object(groq,'recognize_person',new=AsyncMock(return_value={'document_kind':'schedule'})),patch('schedule_chat.start_photo',new=AsyncMock()) as schedule,patch.object(categories,'save',new=AsyncMock()) as save:
   await auto.process_photo(m,d);schedule.assert_awaited_once();save.assert_not_awaited()
 async def test_auto_save_then_review_contains_only_own_result(self):
  d=draft();m=message()
  with patch.object(auto,'person',new=AsyncMock(return_value=PROFILE)),patch.object(categories,'save',new=AsyncMock()) as save,patch.object(auto,'own_report',new=AsyncMock(return_value={'review_status':'confirmed'})),patch('research.track'):
   await auto.finish(m,d,PROFILE,d.report['rows'])
   save.assert_awaited_once();text=m.answer.call_args.args[0]
   self.assertIn('Записал твой отчёт',text);self.assertIn('Тест ресторан',text);self.assertIn('1 500',text)
   self.assertEqual([b.text for b in m.answer.call_args.kwargs['reply_markup'].inline_keyboard[0]],['Всё верно','Есть ошибка'])
 async def test_missing_identity_or_date_does_not_save(self):
  for changed in [None,{**PROFILE,'restaurant_id':str(uuid4())}]:
   with patch.object(auto,'person',new=AsyncMock(return_value=changed)),patch.object(categories,'save',new=AsyncMock()) as save:
    with self.assertRaises(ValueError):await auto.save_and_show(message(),draft(),PROFILE)
    save.assert_not_awaited()
  d=draft();d.cutoff=None
  with patch.object(categories,'save',new=AsyncMock()) as save:
   with self.assertRaises(ValueError):await auto.finish(message(),d,PROFILE,d.report['rows'])
   save.assert_not_awaited()
 async def test_missing_row_runs_automatic_error_path_without_save(self):
  m=message();m.answer.return_value=m;d=draft();bad={**d.report,'cutoff':'2026-10-14','rows':[{'name':'Другой','totals':{d.report['category_config']['definitions'][0]['id']:999}}]}
  with patch.object(auto,'person',new=AsyncMock(return_value=PROFILE)),patch.object(auto,'configuration',new=AsyncMock(return_value=cfg())),patch.object(groq,'recognize_person',new=AsyncMock(return_value=bad)),patch.object(categories,'save',new=AsyncMock()) as save,patch.object(auto,'issue',new=AsyncMock()) as issue,self.assertLogs(level='ERROR'):
   await auto.process_photo(m,d);save.assert_not_awaited();issue.assert_awaited_once()
 async def test_uncertain_save_retains_original_operation_and_does_not_claim_success(self):
  m=message();m.answer.return_value=m;d=draft();result={**d.report,'cutoff':'2026-10-14'}
  with patch.object(auto,'person',new=AsyncMock(return_value=PROFILE)),patch.object(auto,'configuration',new=AsyncMock(return_value=cfg())),patch.object(groq,'recognize_person',new=AsyncMock(return_value=result)),patch.object(categories,'save',new=AsyncMock(side_effect=RuntimeError('network'))),self.assertLogs(level='ERROR'):
   await auto.process_photo(m,d)
   self.assertEqual(d.phase,'uncertain');self.assertEqual(d.report['auto_profile']['report_name'],'Тест Имя')
   self.assertFalse(any('Записал твой отчёт' in str(call) for call in m.answer.call_args_list))
   self.assertEqual(m.answer.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data,'ocr:n:save')
 async def test_duplicate_values_in_recovery_are_not_silently_combined(self):
  missing=raw();missing['rows']=[]
  with patch.object(groq,'request_json',new=AsyncMock(side_effect=[missing,raw(),raw()])),patch.object(groq,'enlarged_fragments',return_value=[b'a',b'b']):
   with self.assertRaisesRegex(ValueError,'ambiguous_employee_fragments'):
    await groq.recognize_person(b'image',categories.empty_context(R,'2026-10',7),'Тест Имя')

 async def test_unknown_employee_reports_to_owner_without_values(self):
  m=message()
  with patch('admin.ADMIN_ID',99):await auto.issue(m,str(uuid4()),'row')
  self.assertEqual(m.bot.send_message.call_args.args[0],99)
  self.assertNotIn('1500',m.bot.send_message.call_args.args[1]);self.assertIn('Уже пишу Лёше',m.answer.call_args.args[0])
 async def test_failed_owner_notice_never_promises_delivery(self):
  m=message();m.bot.send_message.side_effect=RuntimeError('private body')
  with patch('admin.ADMIN_ID',99),self.assertLogs(level='ERROR'):await auto.issue(m,str(uuid4()),'row')
  self.assertNotIn('Уже пишу',m.answer.call_args.args[0]);self.assertEqual(m.answer.call_args.kwargs['reply_markup'].inline_keyboard[0][0].url,'https://t.me/lechatsergeev')
 async def test_review_of_foreign_report_does_not_mutate(self):
  m=message();q=NS(from_user=NS(id=8),data=f'ar:{uuid4()}:error',message=m,answer=AsyncMock())
  with patch.object(auto,'own_report',new=AsyncMock(return_value=None)),patch.object(auto.db,'_execute',new=AsyncMock()) as execute:
   await auto.review(q);execute.assert_not_awaited()
 async def test_sheet_uses_profile_not_asks_restaurant_or_name(self):
  c=cfg();cid=c['definitions'][0]['id'];d=draft();d.report={'sheet_source':'a'*25}
  rows=[{'name':'Другой','category_values':{cid:{'value':999,'cutoff':'2026-10-14'}}},{'name':'Тест Имя','category_values':{cid:{'value':1500,'cutoff':'2026-10-14'}}}]
  with patch.object(auto,'person',new=AsyncMock(return_value=PROFILE)),patch.object(auto,'configuration',new=AsyncMock(return_value=c)),patch('report_sheet.fetch',new=AsyncMock(return_value='CSV')),patch('report_sheet.discover_rows',return_value=(rows,c)),patch.object(auto,'save_and_show',new=AsyncMock()) as save:
   await auto.load_sheet(message(),d,'Октябрь 26');save.assert_awaited_once()
   self.assertEqual(d.report['category_values'][cid]['value'],1500);self.assertNotIn('rows',d.report)
 def test_flagged_snapshot_cannot_override_prior_valid_result(self):
  c=cfg();cid=c['definitions'][0]['id'];old={'id':'a','created_at':'2026-10-14','definitions':c['definitions'],'metrics':{cid:{'value':100,'cutoff':'2026-10-14'}}}
  bad={**old,'id':'b','created_at':'2026-10-15','review_status':'flagged','metrics':{cid:{'value':999,'cutoff':'2026-10-15'}}}
  self.assertEqual(categories.summary([old,bad])[0]['total'],100)
 def test_crops_keep_full_width_and_bounded_size(self):
  data=io.BytesIO();Image.new('RGB',(1000,1200),'white').save(data,format='PNG')
  parts=groq.enlarged_fragments(data.getvalue());self.assertEqual(len(parts),2)
  for crop in parts:self.assertEqual(Image.open(io.BytesIO(crop)).width,2000)

if __name__=='__main__':unittest.main()
