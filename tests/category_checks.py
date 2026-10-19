"""Monthly categories: only mocked DB/provider, no production services."""
import asyncio,copy,json,os,sys,unittest
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from uuid import uuid4
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with patch.dict(os.environ,{'SUPABASE_URL':'https://example.invalid','SUPABASE_KEY':'test'}),patch('supabase.create_client'):
 import categories,category_chat as chat,category_groq,report_photo as photo,report_sheet
R='11111111-1111-4111-8111-111111111111'
RAW={'month':'2026-10','categories':[{'name':'Хлеб','unit':'amount'},{'name':'Шоты','unit':'count','target':30}]}
def cfg(raw=RAW):return {'restaurant_id':R,'month':raw['month'],'revision':1,'definitions':categories.clean(raw,R)['categories']}

class CategoryTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.auto=patch('report_auto.enabled',new=AsyncMock(return_value=False))
  self.auto.start();self.addCleanup(self.auto.stop)
 def test_ids_history_and_unit_validation(self):
  a=cfg();b=cfg({'month':'2026-11','categories':RAW['categories']})
  self.assertNotEqual(a['definitions'][0]['id'],b['definitions'][0]['id'])
  for item in [{'name':'X','unit':'invalid'},{'name':'<b>X</b>','unit':'amount'},{'name':'X','unit':'count','target':1.5},{'name':'X','unit':'amount','target':float('nan')}]:
   with self.assertRaises(ValueError):categories.clean({'month':'2026-10','categories':[item]},R)
  with self.assertRaises(ValueError):categories.clean({'month':'2026-10','categories':[{'name':'ХЛЕБ','unit':'amount'},RAW['categories'][0]]},R)
  old={'id':'1','created_at':'2026-10-01','definitions':a['definitions'],'metrics':{a['definitions'][0]['id']:{'value':100,'cutoff':'2026-10-07'}}}
  newer=cfg({'month':'2026-10','categories':[{'name':'Гарниры','unit':'amount'}]})
  summary=categories.summary([old],newer)
  self.assertEqual({v['name'] for v in summary},{'Хлеб','Гарниры'})
  self.assertEqual(next(v['total'] for v in summary if v['name']=='Хлеб'),100)

 async def test_only_admin_can_configure_even_owner(self):
  with patch('admin.is_admin',return_value=False),patch.object(categories.db,'_execute',new=AsyncMock()) as write:
   with self.assertRaises(PermissionError):await categories.configure(42,R,RAW,0,str(uuid4()))
   write.assert_not_awaited()
  with patch('admin.is_admin',return_value=True),patch('identity.owner_restaurant',new=AsyncMock(return_value=None)),patch.object(categories.db,'_execute',new=AsyncMock()) as write:
   with self.assertRaises(PermissionError):await categories.configure(42,R,RAW,0,str(uuid4()))
   write.assert_not_awaited()

 def test_named_csv_blocks_and_decimal_counts(self):
  configuration=cfg();rows=[['Официант','ХЛЕБ','Продажи 1-7','Осталось до плана','Продажи 1-14'],
   ['Тест Имя','1000','100,25','899,75','200,50'],['Официант','Шоты','Продажи 1-7','Осталось до плана','Продажи 1-14'],
   ['Тест Имя','30','2','28','3']]
  people=report_sheet.parse_configured(rows,configuration,date(2026,10,19))
  self.assertEqual(len(people),1)
  values=people[0]['category_values'];bread,shots=[d['id'] for d in configuration['definitions']]
  self.assertEqual(values[bread],{'value':200.5,'target':1000,'cutoff':'2026-10-14'})
  self.assertEqual(values[shots]['value'],3)
  rows[-1][-1]='3,5'
  with self.assertRaises(ValueError):report_sheet.parse_configured(rows,configuration,date(2026,10,19))

 async def test_groq_discovers_unknown_category_but_rejects_unlabeled_value(self):
  configuration=cfg();cid=configuration['definitions'][0]['id']
  response={'cutoff':'2026-10-14','categories':[{'name':'Хлеб','unit':'amount'},{'name':'Гарниры','unit':None}],
            'rows':[{'name':'Тест','totals':{'Хлеб':100,'Гарниры':1500}}]}
  with patch.object(category_groq,'request_json',new=AsyncMock(return_value=response)) as read:
   data=await category_groq.recognize(b'photo',configuration)
   self.assertEqual(data['rows'][0]['totals'][cid],100)
   definitions=data['category_config']['definitions']
   unknown=next(c for c in definitions if c['name']=='Гарниры')
   self.assertEqual(unknown['unit'],'number')
   self.assertEqual(data['rows'][0]['totals'][unknown['id']],1500)
   self.assertTrue(data['category_config']['discovered'])
   response['rows'][0]['totals']={'Неподписанное число':500}
   with self.assertRaises(ValueError):await category_groq.recognize(b'photo',configuration)

 def test_missing_date_unit_require_explicit_correction(self):
  data=categories.clean({'month':None,'categories':[{'name':'Гарниры','unit':None}]})
  self.assertIsNone(data['month']);self.assertIsNone(data['categories'][0]['unit'])
  parsed=chat.manual('месяц: 2026-10\nГарниры; руб; 50000\nШоты; шт')
  self.assertEqual(parsed['categories'][0]['target'],50000)

 async def test_admin_callback_confirmation_no_bypass_and_retry(self):
  chat._drafts.clear();uid=42
  message=NS(from_user=NS(id=uid),chat=NS(id=uid),message_id=9,bot=NS(id=10),answer=AsyncMock(),edit_text=AsyncMock())
  with patch.object(chat,'permitted',side_effect=lambda uid:uid==42),patch('identity.owner_restaurants',new=AsyncMock(return_value=[{'id':R,'name':'Тест ресторан'}])),patch.object(categories,'config',new=AsyncMock(return_value=None)),patch.object(category_groq,'parse_message',new=AsyncMock(return_value=copy.deepcopy(RAW))),patch.object(categories,'configure',new=AsyncMock(return_value={})) as save:
   await chat.begin(message,'Менеджерский план на октябрь 2026')
   draft=chat._drafts[uid];save.assert_not_awaited()
   q=NS(data=f"cat:{draft['nonce']}:save",from_user=NS(id=43),message=message,answer=AsyncMock())
   await chat.callback(q);save.assert_not_awaited()
   q.from_user.id=42
   await chat.callback(q);save.assert_awaited_once();self.assertNotIn(42,chat._drafts)
   await chat.callback(q);self.assertEqual(save.await_count,1)

 async def test_photo_dynamic_preview_and_save_not_legacy_wine(self):
  configuration=cfg();cid=configuration['definitions'][0]['id']
  draft=photo.Draft('n',str(uuid4()),'',phase='row',cutoff='2026-10-14',report={'category_config':configuration,'rows':[{'name':'Тест','totals':{cid:100}}]})
  message=NS(chat=NS(id=42),answer=AsyncMock(),edit_text=AsyncMock())
  with patch.object(photo,'op_today',return_value=date(2026,10,19)),patch.object(categories,'save',new=AsyncMock()) as save,patch.object(photo,'save_report',new=AsyncMock()) as legacy,patch.object(photo.db,'get_or_create_user',new=AsyncMock()),patch('research.track'):
   self.assertTrue(await photo.select_row(message,draft,0))
   await photo.ask_date_or_preview(message,draft)
   self.assertEqual(draft.phase,'review');self.assertIn('Хлеб',photo.preview(draft));self.assertNotIn('Вино',photo.preview(draft))
   photo.drafts[42]=draft
   q=NS(from_user=NS(id=42),data='ocr:n:save',answer=AsyncMock(),message=message)
   await photo.photo_callback(q);save.assert_awaited_once();legacy.assert_not_awaited()

 def test_natural_correction_is_local_and_understands_word_forms(self):
  configuration=cfg();bread,shots=[c['id'] for c in configuration['definitions']]
  self.assertEqual(categories.parse_correction('хлеба на 1500, шотов 3',configuration),{bread:1500,shots:3})
  self.assertEqual(categories.parse_correction('хлебом 1200',configuration),{bread:1200})
  with self.assertRaises(ValueError):categories.parse_correction('неизвестное 1500',configuration)

 async def test_sheet_dynamic_flow_does_not_write_legacy_metrics(self):
  import report_sheet_chat as sheets
  configuration=cfg();cid=configuration['definitions'][0]['id']
  rows=[{'name':'Тест','category_values':{cid:{'value':123,'cutoff':'2026-10-14'}}}]
  draft=photo.Draft('n',str(uuid4()),'',phase='sheet_tabs',report={'sheet_source':'a'*25,'sheet_names':['Октябрь 26']})
  photo.drafts[42]=draft
  message=NS(chat=NS(id=42),answer=AsyncMock(),edit_text=AsyncMock())
  with patch.object(categories,'context',new=AsyncMock(return_value=configuration)),patch.object(report_sheet,'fetch',new=AsyncMock(return_value='CSV')),patch.object(report_sheet,'discover_rows',return_value=(rows,configuration)),patch('research.track'),patch.object(photo,'op_today',return_value=date(2026,10,19)):
   self.assertTrue(await sheets.load_rows(message,draft,'Октябрь 26'))
   query=NS(from_user=NS(id=42),data='rs:n:row0',message=message,answer=AsyncMock())
   await sheets.action(query)
   self.assertEqual(draft.phase,'review');self.assertIn('Хлеб',photo.preview(draft))
   self.assertEqual(draft.report['category_values'][cid]['value'],123)

 async def test_configured_restaurant_schedule_link_still_routes_to_calendar(self):
  import report_sheet_chat as sheets
  photo.drafts.clear()
  message=NS(from_user=NS(id=42),chat=NS(id=42),bot=NS(id=10),message_id=20,
    text='https://docs.google.com/spreadsheets/d/'+'a'*25+'/edit',answer=AsyncMock())
  with patch.object(categories,'context',new=AsyncMock(return_value=cfg())),patch.object(report_sheet,'tabs',new=AsyncMock(return_value=['Октябрь 26'])),patch.object(report_sheet,'fetch',new=AsyncMock(return_value='CSV')),patch.object(report_sheet,'discover_rows',side_effect=report_sheet.SheetParseError('no_matching_categories')),patch('schedule_sheet.parse_rows',return_value=[{'name':'Тест'}]),patch('schedule.enabled',return_value=True),patch('schedule_chat.sheet_link',new=AsyncMock()) as calendar:
   await sheets.receive_link(message)
   calendar.assert_awaited_once_with(message)

 async def test_admin_natural_edit_preserves_other_categories_before_save(self):
  message=NS(from_user=NS(id=42),chat=NS(id=42),text='хлеба цель 60000',answer=AsyncMock())
  previous=copy.deepcopy(RAW);updated=copy.deepcopy(RAW);updated['categories'][0]['target']=60000
  draft={'nonce':'n','lock':asyncio.Lock(),'phase':'edit','raw':previous,'restaurant':{'id':R,'name':'Тест'}}
  chat._drafts[42]=draft
  with patch.object(chat,'permitted',return_value=True),patch.object(category_groq,'parse_setup_edit',new=AsyncMock(return_value=updated)) as edit,patch.object(categories,'config',new=AsyncMock(return_value=None)),patch.object(categories,'configure',new=AsyncMock()) as save:
   await chat.body(message)
   edit.assert_awaited_once_with('хлеба цель 60000',previous)
   self.assertEqual(len(draft['raw']['categories']),2);save.assert_not_awaited()

 def test_csv_without_setup_imports_new_labels_and_neutral_units(self):
  context=categories.empty_context(R,'2026-10',42)
  rows=[['Официант','Гарниры','Продажи 1-7','Осталось','Продажи 1-14'],['Тест','10000','500','9500','1500']]
  people,discovered=report_sheet.discover_rows(rows,context,date(2026,10,19))
  self.assertEqual(discovered['definitions'][0]['name'],'Гарниры')
  self.assertEqual(discovered['definitions'][0]['unit'],'number')
  cid=discovered['definitions'][0]['id']
  self.assertEqual(people[0]['category_values'][cid]['value'],1500)

 async def test_photo_date_rebases_discovered_categories_to_selected_month(self):
  observed=categories.discover(categories.empty_context(R,'2026-10',42),[{'name':'Гарниры','unit':'amount'}])
  old=observed['definitions'][0]['id']
  draft=photo.Draft('n',str(uuid4()),'photo',phase='date',cutoff='2026-09-14',report={'category_config':observed,'category_values':{old:{'value':1500,'cutoff':None}}})
  message=NS(chat=NS(id=42),answer=AsyncMock())
  with patch.object(categories,'config',new=AsyncMock(return_value=None)),patch.object(categories,'validate_values',wraps=categories.validate_values):
   await photo.ask_date_or_preview(message,draft)
  new=draft.report['category_config']
  self.assertEqual(new['month'],'2026-09');self.assertNotEqual(new['definitions'][0]['id'],old)
  self.assertEqual(new['definitions'][0]['unit'],'amount')
  self.assertEqual(next(iter(draft.report['category_values'].values())),{'value':1500,'cutoff':'2026-09-14'})
  self.assertEqual(draft.phase,'review')

 async def test_missing_migration_keeps_legacy_import_route(self):
  from postgrest.exceptions import APIError
  with patch.object(categories,'_ready_until',0),patch.object(categories.db,'_execute',new=AsyncMock(side_effect=APIError({'code':'PGRST205','message':'missing table','details':None,'hint':None}))) as execute:
   self.assertIsNone(await categories.context(42,'2026-10'))
   self.assertEqual(await categories.options(42,'2026-10'),[])
   execute.assert_awaited_once()
  categories._ready_until=0

 async def test_discovered_save_does_not_change_admin_configuration(self):
  observed=categories.discover(categories.empty_context(R,'2026-10',42),[{'name':'Гарниры','unit':None}])
  cid=observed['definitions'][0]['id'];values={cid:{'value':1500,'cutoff':'2026-10-14'}}
  with patch.object(categories.db,'_execute',new=AsyncMock()) as execute:
   await categories.save(42,str(uuid4()),observed,values)
   method=categories.db.supabase.rpc.call_args.args[0]
   self.assertEqual(method,'save_discovered_category_report');execute.assert_awaited_once()

 async def test_unknown_notification_is_after_save_owner_only_and_retries(self):
  import category_notifications as notices
  alert={'alert_id':str(uuid4()),'category_name':'Новые закуски','restaurant_name':'Тест','period':'2026-10','unit':'number'}
  bot=NS(send_message=AsyncMock())
  with patch('admin.ADMIN_ID',99),patch.object(notices.db,'_execute',new=AsyncMock(side_effect=[NS(data=[alert]),NS(data=[]),NS(data=[])])) as execute:
   await notices.send_pending(bot);await notices.send_pending(bot)
   bot.send_message.assert_awaited_once()
   self.assertEqual(bot.send_message.call_args.args[0],99)
   self.assertIn('Новые закуски',bot.send_message.call_args.args[1])
   self.assertNotIn('1450',bot.send_message.call_args.args[1])
   self.assertEqual(execute.await_count,3)
  bot.send_message=AsyncMock(side_effect=RuntimeError('network'))
  with patch('admin.ADMIN_ID',99),patch.object(notices.db,'_execute',new=AsyncMock(return_value=NS(data=[alert]))) as execute,self.assertLogs(level='ERROR'):
   await notices.send_pending(bot)
   self.assertEqual(execute.await_count,1) # Failed delivery must remain unacknowledged.

 def test_known_import_includes_chat_sales_after_cutoff_without_double_count(self):
  row={'id':'x','name':'Вино','unit':'amount','official':1500,'total':1500,'cutoff':'2026-10-14','target':5000}
  events=[{'kind':'bottle','value':100,'work_date':'2026-10-15'}, {'kind':'bottle','value':999,'work_date':'2026-10-13'}]
  result=categories.include_legacy_sales([row],'2026-10',{},events)[0]
  self.assertEqual(result['total'],1600);self.assertEqual(result['pct'],32)
  self.assertEqual(result['legacy_metric'],'wine')

if __name__=='__main__':unittest.main()
