"""Unknown layouts, blind proof, periods, units, completeness; no live services."""
import copy,io,os,sys,unittest
from datetime import date
from unittest.mock import AsyncMock,patch
from PIL import Image,ImageDraw
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with patch.dict(os.environ,{'SUPABASE_URL':'https://example.invalid','SUPABASE_KEY':'test'}),patch('supabase.create_client'):
 import categories,report_table as table
R='11111111-1111-4111-8111-111111111111';NAME='Тест Имя'
LAYOUT={'kind':'sales','blocks':[{'h':[0,100],'r':[500,560],'nx':[0,190],'n':NAME,'x':[[0,190],[200,400],[400,500],[550,700],[700,800]]}]}
READ={'date_text':'Октябрь 1–18','names':[NAME],
 'cols':[[0,0,'Официант',NAME],[0,1,'10000 Гарниры','1 500,00'],[0,2,'% выполнения','15%'],[0,3,'Супы шт.','3'],[0,4,'План Супы','10']],
 'facts':[[0,1,'Гарниры',None,'10000',2],[0,3,'Супы','count',4,None]],'skip':[[0,0,'name'],[0,2,'percent'],[0,4,'goal']]}
def transcriptions():
 result=[]
 for start in range(0,len(READ['cols']),2):
  result.append({'date_text':READ['date_text'],'names':READ['names'],
                 'cols':{f'B{b}C{c}':[h,v] for b,c,h,v in READ['cols'][start:start+2]}})
 return copy.deepcopy(result)
def cfg():return categories.empty_context(R,'2026-10',7)
def image():
 output=io.BytesIO();Image.new('RGB',(1000,500),'white').save(output,format='PNG');return output.getvalue()
def result(layout=LAYOUT,read=READ):
 return table.verified_result(copy.deepcopy(read),table.clean_layout(copy.deepcopy(layout),NAME),cfg(),NAME)
class TableTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.clock=patch('workday.op_today',return_value=date(2026,10,19));self.clock.start();self.addCleanup(self.clock.stop)
  self.today=patch.object(table,'op_today',return_value=date(2026,10,19));self.today.start();self.addCleanup(self.today.stop)
 def test_period_without_year_uses_import_context(self):
  for text in ['Октябрь 1–18','1-18 октября','18 октября 2026','18.10.2026','2026-10-18']:
   self.assertEqual(table.period(text,'2026-10'),'2026-10-18')
  for text in ['18 октября 2017','18 сентября','вчера','85000','Октябрь 1–31','октябрь 1–18, сентябрь 1–17']:
   with self.assertRaises(ValueError,msg=text):table.period(text,'2026-10')
 def test_goals_percentage_and_actuals_separate(self):
  self.assertEqual(list(result()['rows'][0]['category_values'].values()),[{'value':1500,'cutoff':'2026-10-18','target':10000},{'value':3,'cutoff':'2026-10-18','target':10}])
 def test_no_unit_inference_from_cocktails_label(self):
  read=copy.deepcopy(READ);read['cols'][1][2]='10000 Коктейли';read['facts'][0][2:4]=['Коктейли','count']
  self.assertEqual(result(read=read)['category_config']['definitions'][0]['unit'],'number')
 def test_new_category_preserves_label(self):
  read=copy.deepcopy(READ);read['cols'][1][2]='10000 Пироги месяца';read['facts'][0][2]='Пироги месяца'
  self.assertEqual(result(read=read)['category_config']['definitions'][0]['name'],'Пироги месяца')
 def test_reordered_columns_have_no_template_dependency(self):
  layout=copy.deepcopy(LAYOUT);layout['blocks'][0]['nx']=[700,800]
  read=copy.deepcopy(READ)
  read['cols']=[[0,4,'Официант',NAME],[0,3,'10000 Гарниры','1500'],[0,2,'% выполнения','15%'],[0,1,'Супы шт.','3'],[0,0,'План Супы','10']]
  read['facts']=[[0,3,'Гарниры',None,'10000',2],[0,1,'Супы','count',0,None]];read['skip']=[[0,4,'name'],[0,2,'percent'],[0,0,'goal']]
  self.assertEqual(list(result(layout,read)['rows'][0]['totals'].values()),[1500,3])
 def test_missing_column_or_role_rejects_partial_zero_success(self):
  for key in ['cols','facts','skip']:
   read=copy.deepcopy(READ);read[key]=read[key][:-1]
   with self.assertRaises(ValueError):result(read=read)
  read=copy.deepcopy(READ);read['facts']=read['facts'][:1];read['skip'].append([0,3,'average'])
  with self.assertRaisesRegex(ValueError,'unverified_skipped_column'):result(read=read)
 def test_blank_is_not_zero(self):
  read=copy.deepcopy(READ);read['cols'][1][3]='0';read['cols'][2][3]='0%'
  self.assertEqual(list(result(read=read)['rows'][0]['totals'].values())[0],0)
  for empty in [None,'','не видно']:
   read['cols'][1][3]=empty
   with self.assertRaises(ValueError):result(read=read)
 def test_percentage_detects_wrong_actual(self):
  for wrong in ['10000','15','150']:
   read=copy.deepcopy(READ);read['cols'][1][3]=wrong
   with self.assertRaisesRegex(ValueError,'percentage_mismatch'):result(read=read)
 def test_blind_name_cannot_match_wrong_employee(self):
  read=copy.deepcopy(READ);read['names']=['Сосед']
  with self.assertRaisesRegex(ValueError,'wrong_employee'):result(read=read)
 def test_wrong_header_or_bonus_not_actual(self):
  read=copy.deepcopy(READ);read['cols'][1][2]='Премия'
  with self.assertRaisesRegex(ValueError,'wrong_header'):result(read=read)
  read['facts'][0][2]='Премия'
  with self.assertRaisesRegex(ValueError,'wrong_value_role'):result(read=read)
 def test_invented_goal_rejected(self):
  read=copy.deepcopy(READ);read['cols'][1][2]='Гарниры 50000'
  with self.assertRaisesRegex(ValueError,'unverified_goal'):result(read=read)
 def test_duplicate_cells_or_overlapping_columns_rejected(self):
  read=copy.deepcopy(READ);read['cols'][1]=read['cols'][0]
  with self.assertRaisesRegex(ValueError,'duplicate_cell'):result(read=read)
  layout=copy.deepcopy(LAYOUT);layout['blocks'][0]['x'][1]=[100,400]
  with self.assertRaisesRegex(ValueError,'overlapping_columns'):result(layout)
 def test_invalid_geometry_rejected(self):
  for box in [[-1,20],[30,20],[True,10],[0,float('inf')],[20,20]]:
   layout=copy.deepcopy(LAYOUT);layout['blocks'][0]['r']=box
   with self.assertRaises(ValueError):result(layout)
 async def test_blind_verifier_gets_no_expected_name_or_numbers(self):
  with patch.object(table.vision,'request_json',new=AsyncMock(side_effect=[copy.deepcopy(LAYOUT),*transcriptions()])) as read:
   r=await table.recognize(image(),cfg(),NAME)
   self.assertEqual(r['cutoff'],'2026-10-18');self.assertEqual(read.await_count,4)
   second=read.await_args_list[1].args[1]
   self.assertNotIn(NAME,second);self.assertNotIn('1500',second);self.assertNotIn('10000',second)
 async def test_missing_employee_retries_enlarged_source_once(self):
  with patch.object(table.vision,'request_json',new=AsyncMock(side_effect=[{'kind':'sales','blocks':[]},{'kind':'sales'},copy.deepcopy(LAYOUT),*transcriptions()])) as read:
   await table.recognize(image(),cfg(),NAME);self.assertEqual(read.await_count,6)
 async def test_network_no_retry_but_short_explicit_quota_wait_is_bounded(self):
  error=table.vision.VisionError('quota')
  with patch.object(table.vision,'request_json',new=AsyncMock(side_effect=error)) as read:
   with self.assertRaises(table.vision.VisionError):await table.recognize(image(),cfg(),NAME)
   read.assert_awaited_once()
  error.retry_after=1
  with patch.object(table.vision,'request_json',new=AsyncMock(side_effect=[error,{'ok':True}])) as read,patch.object(table.asyncio,'sleep',new=AsyncMock()) as sleep:
   self.assertEqual(await table.request(b'image','prompt'),{'ok':True});self.assertEqual(read.await_count,2);sleep.assert_awaited_once()
 async def test_schedule_bypasses_sales_write(self):
  with patch.object(table.vision,'request_json',new=AsyncMock(return_value={'kind':'schedule'})) as read:
   self.assertEqual(await table.recognize(image(),cfg(),NAME),{'document_kind':'schedule'});read.assert_awaited_once()
 def test_caption_date_cannot_override_conflicting_image(self):
  blocks=table.clean_layout(copy.deepcopy(LAYOUT),NAME);read=copy.deepcopy(READ);read['date_text']=None
  self.assertEqual(table.verified_result(read,blocks,cfg(),NAME,'итог 1–18 октября')['cutoff'],'2026-10-18')
  for text in ['1–17 октября','18 октября 2017']:
   with self.assertRaises(ValueError):table.verified_result(copy.deepcopy(READ),blocks,cfg(),NAME,text)
 def test_generic_grid_detects_arbitrary_column_widths(self):
  canvas=Image.new('RGB',(1100,400),'white');draw=ImageDraw.Draw(canvas)
  for x in [0,130,510,720,1099]:draw.line((x,0,x,399),fill='black')
  for y in range(0,400,25):draw.line((0,y,1099,y),fill='black')
  output=io.BytesIO();canvas.save(output,format='PNG')
  directory,columns,rows,name_index,w,h=table.grid_directory(output.getvalue())
  self.assertEqual(name_index,1);self.assertEqual(w,1100);self.assertGreater(len(rows),10)
  self.assertTrue(directory.startswith(b'\xff\xd8'))
 async def test_header_keeps_merged_rows_above_first_continuous_grid_line(self):
  directory=b'jpeg';cols=[(0,80),(81,300),(301,400)];rows=[(40,60),(61,80),(81,100)]
  with patch.object(table,'grid_directory',return_value=(directory,cols,rows,1,400,120)),patch.object(table,'request',new=AsyncMock(return_value={'kind':'sales','first_employee_row':1,'matches':[{'r':2,'name':NAME}]})):
   r=await table.locate(image(),NAME)
   self.assertEqual(r['blocks'][0]['h'][0],0)
   self.assertEqual(r['blocks'][0]['h'][1],61*1000/120)
 def test_strict_object_transcription_is_checked_like_array_form(self):
  read=copy.deepcopy(READ);read['cols']={f'B{b}C{c}':[h,v] for b,c,h,v in read['cols']}
  self.assertEqual(list(result(read=read)['rows'][0]['totals'].values()),[1500,3])

 def test_transcribed_labels_classify_without_restaurant_vocabulary(self):
  raw={'names':[NAME],'cols':{f'B{b}C{c}':[h,v] for b,c,h,v in READ['cols']}}
  checked=table.classify_cells(raw)
  self.assertEqual(checked['facts'],READ['facts'])
  raw['cols']['B0C1'][0]='Пироги сезона'
  checked=table.classify_cells(raw)
  self.assertEqual(checked['facts'][0][2],'Пироги сезона')
 async def test_ruled_schedule_is_routed_after_sales_validation_fails(self):
  with patch.object(table,'attempt',new=AsyncMock(side_effect=ValueError)),patch.object(table,'request',new=AsyncMock(return_value={'kind':'schedule'})):
   self.assertEqual(await table.recognize(image(),cfg(),NAME),{'document_kind':'schedule'})

 async def test_excel_index_band_can_be_removed_without_cropping_category_labels(self):
  with patch.object(table,'grid_directory',return_value=(b'jpeg',[(0,80),(81,300),(301,400)],[(40,60),(61,80),(81,100)],1,400,120)),patch.object(table,'header_bands',return_value=[(1,20),(21,40),(41,60)]),patch.object(table,'request',new=AsyncMock(return_value={'kind':'sales','first_employee_row':1,'header_row':1,'matches':[{'r':2,'name':NAME}]})):
   r=await table.locate(image(),NAME)
   self.assertEqual(r['blocks'][0]['h'][0],21*1000/120)
 def test_digits_inside_category_name_are_not_a_sales_goal(self):
  raw={'names':[NAME],'cols':{'B0C0':['Сет365','12']}}
  fact=table.classify_cells(raw)['facts'][0]
  self.assertEqual(fact[2],'Сет365');self.assertIsNone(fact[4])

 def test_long_caption_extracts_date_without_treating_sales_numbers_as_days(self):
  caption='Промежуточный итог 1–18 октября. Средний чек 2072, продажи 85000. '+('Пожалуйста, проверьте результаты. '*25)
  self.assertEqual(table.caption_period(caption,'2026-10'),'2026-10-18')
  for caption in ['отчёт 1–18 октября 2017','итог 18 октября и 17 октября']:
   with self.assertRaises(ValueError):table.caption_period(caption,'2026-10')

 def test_product_names_are_not_silently_classified_by_substring(self):
  for name in ['Планета вкуса','Премиум десерты','Часовой сет']:
   raw={'names':[NAME],'cols':{'B0C0':[name,'12']}}
   checked=table.classify_cells(raw)
   self.assertEqual(checked['facts'][0][2],name)
   self.assertEqual(checked['skip'],[])

 def test_proof_has_no_fixed_original_resolution(self):
  picture=Image.open(io.BytesIO(table.proof_image(image(),table.clean_layout(copy.deepcopy(LAYOUT),NAME))))
  self.assertEqual(picture.width,1600);self.assertGreater(picture.height,200)

 async def test_unlabelled_numeric_header_is_reread_without_repeating_other_columns(self):
  batches=transcriptions();bad=copy.deepcopy(batches[0]);bad['cols']['B0C1'][0]='10000'
  with patch.object(table.vision,'request_json',new=AsyncMock(side_effect=[copy.deepcopy(LAYOUT),bad,{'headers':{'B0C0':['Официант'],'B0C1':['10000','Гарниры']}},*batches[1:]])) as read:
   answer=await table.recognize(image(),cfg(),NAME)
   self.assertEqual(list(answer['rows'][0]['totals'].values()),[1500,3])
   self.assertEqual(read.await_count,5)
   first=Image.open(io.BytesIO(read.await_args_list[1].args[0]))
   retry=Image.open(io.BytesIO(read.await_args_list[2].args[0]))
   self.assertEqual((first.width,retry.width),(1600,800))

 async def test_missing_label_after_targeted_reread_stops_import(self):
  bad=transcriptions()[0];bad['cols']['B0C1'][0]='10000'
  with patch.object(table.vision,'request_json',new=AsyncMock(side_effect=[copy.deepcopy(LAYOUT),bad,{'headers':{'B0C0':['Официант'],'B0C1':['10000']}}])) as read:
   with self.assertRaisesRegex(ValueError,'missing_header_proof'):
    await table.recognize(image(),cfg(),NAME)
   self.assertEqual(read.await_count,3)

 def test_large_physical_row_in_short_grid_table_is_valid(self):
  layout=copy.deepcopy(LAYOUT);block=layout['blocks'][0];block['r']=[400,650]
  with self.assertRaisesRegex(ValueError,'ambiguous_row_geometry'):
   table.clean_layout(layout,NAME)
  block['grid']=True
  with self.assertRaisesRegex(ValueError,'ambiguous_row_geometry'):
   table.clean_layout(layout,NAME)
  layout['blocks'][0]=table._GridBlock(block)
  self.assertEqual(len(table.clean_layout(layout,NAME)),1)

 async def test_namesakes_cannot_be_replaced_by_a_model_choice(self):
  directory=(b'jpeg',[(0,80),(81,300),(301,400)],[(40,60),(61,80),(81,100)],1,400,120)
  answer={'kind':'sales','first_employee_row':1,'header_row':0,'matches':[{'r':1,'name':NAME},{'r':2,'name':NAME}]}
  with patch.object(table,'grid_directory',return_value=directory),patch.object(table,'request',new=AsyncMock(return_value=answer)) as read:
   with self.assertRaisesRegex(ValueError,'duplicate_employee'):
    await table.recognize(image(),cfg(),NAME)
   read.assert_awaited_once()

 async def test_ruled_schedule_is_routed_before_financial_cell_reads(self):
  raw={'date_text':None,'names':[NAME],'cols':{'B0C0':['Сотрудник',NAME],'B0C1':['Пн 19 октября','9']}}
  with patch.object(table,'locate',new=AsyncMock(return_value=copy.deepcopy(LAYOUT))),patch.object(table.vision,'request_json',new=AsyncMock(side_effect=[raw,{'kind':'schedule'}])) as read:
   self.assertEqual(await table.recognize(image(),cfg(),NAME),{'document_kind':'schedule'})
   self.assertEqual(read.await_count,2)
if __name__=='__main__':unittest.main()
