"""Isolated time, schedule import, confirmations and private persistence checks."""
import sys,os,unittest
from datetime import date,datetime,timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sales_http_checks import Store
store=Store()
with patch.dict(os.environ,{'SUPABASE_URL':'https://example.invalid','SUPABASE_KEY':'test'}),patch('supabase.create_client',return_value=store):
    import db,schedule,schedule_chat as chat,report_photo as photo,jobs

class Times(unittest.TestCase):
    def test_google_sheet_rows_and_links(self):
        import schedule_sheet as sheets
        sheet_id='1gx13NYuISndRuCzG6I-DpD3zlkmfzc_S4LF1Pk0_dwU'
        self.assertEqual(sheets.link(f'https://docs.google.com/spreadsheets/d/{sheet_id}/edit?usp=drivesdk'),(sheet_id,None))
        self.assertEqual(sheets.link(f'https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid=1843113945'),(sheet_id,'1843113945'))
        with self.assertRaises(ValueError):sheets.link(f'https://evil.example/spreadsheets/d/{sheet_id}/edit')
        rows=[['Менеджеры']+[str(n) for n in range(1,9)],
              ['Даша','15-01','x','9-16','','14-23','п','8-15',''],
              ['Официанты']+[str(n) for n in range(1,9)],
              ['Лёша','10-22','х','','10-23:30','и','11-23','','п'],
              ['Бар','',*[item for n in range(1,9) for item in (str(n),'')]],
              ['', 'Лиза', *[item for n in range(1,9) for item in (('8-16' if n%2 else 'x'),'8')]]]
        people=sheets.parse_rows(rows)
        self.assertEqual([p['name'] for p in people],['Даша','Лёша','Лиза'])
        self.assertEqual(people[0]['cells'][0],{'day':1,'text':'15-01'})
        self.assertEqual([c['day'] for c in people[1]['cells']],[1,4,6])
        self.assertEqual([c['day'] for c in people[2]['cells']],[1,3,5,7])

    def test_scheduler_has_evening_retry_and_startup_catchup(self):
        scheduler=jobs.setup_scheduler(NS())
        names=[job.func.__name__ for job in scheduler.get_jobs()]
        self.assertEqual(names.count('retry_tomorrow_shift_reminder'),2)
        self.assertIn('tomorrow_shift_reminder',names)
        self.assertEqual(str(scheduler.timezone),'Europe/Moscow')

    def test_cells_and_dates(self):
        for text,want in [('10',('10:00','22:00')),('11',('11:00','23:00')),('14',('14:00','23:30')),('9–17',('09:00','17:00')),('10-23',('10:00','23:00')),('10-23:30',('10:00','23:30')),('23-02',('23:00','02:00'))]:
            self.assertEqual(schedule.cell(text),want)
        for text in ('','в','-'):self.assertIsNone(schedule.cell(text))
        for bad in ('?','24','11-24','10-10','23:45'):
            with self.assertRaises(ValueError):schedule.cell(bad)
        self.assertEqual(schedule.month('Октябрь 2026'),'2026-10')
        with self.assertRaises(ValueError):schedule.month('октябрь')
        for raw in ([{'day':31,'text':'10'}],[{'day':1,'text':'10'},{'day':1,'text':'11'}],[None],[{'day':True,'text':'10'}]):
            with self.assertRaises(ValueError):schedule.clean_cells(raw,'2026-09')
    def test_actual_overtime_and_rate(self):
        a,b=schedule.actual('2026-09-28','23:30','10:00:00')
        self.assertEqual(schedule.earned(a,b,350),{'hours':13.5,'income':4725})
        a,b=schedule.actual('2026-10-05','В 21:40','10:00:00')
        self.assertEqual(b.strftime('%H:%M'),'21:40')
        self.assertEqual(schedule.hours_text(schedule.earned(a,b,None)['hours']),'11 ч 40 мин')
        self.assertEqual(schedule.hours_text(13.5),'13 ч 30 мин')
        self.assertEqual(schedule.hours_text(0.5),'30 мин')
        with self.assertRaises(ValueError):schedule.actual('2026-10-05','в 25:40','10:00:00')
        a,b=schedule.actual('2026-09-28','00:30','11:00')
        self.assertEqual(b.date().isoformat(),'2026-09-29')
        self.assertIsNone(schedule.earned(a,b,None)['income'])
    def test_unknown_grid_rejected(self):
        from PIL import Image
        import schedule_layout as layout
        from report_layout import encode
        with self.assertRaises(ValueError):layout.directory(encode(Image.new('RGB',(1280,256),'white')))

class Flow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        photo.drafts.clear();store.rows.clear()
        self.message=NS(text='октябрь 2026',from_user=NS(id=7),answer=AsyncMock(),edit_text=AsyncMock())
    def cb(self,data,uid=7):return NS(data=data,from_user=NS(id=uid),message=self.message,answer=AsyncMock())
    async def test_sheet_link_requires_preview_and_saves_selected_row_only(self):
        sheet_id='1gx13NYuISndRuCzG6I-DpD3zlkmfzc_S4LF1Pk0_dwU'
        self.message.text=f'https://docs.google.com/spreadsheets/d/{sheet_id}/edit?usp=drivesdk'
        with patch.object(chat.research,'track') as started:
            await chat.sheet_link(self.message)
        started.assert_called_once_with(7,'schedule_import_started',screen='calendar')
        draft=photo.drafts[7]
        self.assertEqual(draft.phase,'schedule_sheet_month')
        self.message.text='октябрь 2026'
        rows=[{'name':'Лёша','row':8,'cells':[{'day':5,'text':'14-23'}],'invalid':[]},
              {'name':'Коллега','row':9,'cells':[{'day':6,'text':'9-16'}],'invalid':[]}]
        with patch('schedule_sheet.read',new=AsyncMock(return_value=rows)) as read,patch.object(schedule,'save',new=AsyncMock()) as save,patch('google_calendar.is_connected',new=AsyncMock(return_value=False)),patch.object(chat.research,'track') as tracked:
            await chat.sheet_month(self.message)
            read.assert_awaited_once_with(sheet_id,'2026-10',None)
            self.assertEqual(draft.phase,'schedule_sheet_name')
            await chat.photo_action(self.cb(f'sch:{draft.nonce}:row0'))
            self.assertEqual(draft.phase,'schedule_review')
            save.assert_not_awaited()
            await chat.photo_action(self.cb(f'sch:{draft.nonce}:save',8))
            save.assert_not_awaited()
            await chat.photo_action(self.cb(f'sch:{draft.nonce}:save'))
            save.assert_awaited_once_with(7,[{'date':'2026-10-05','start':'14:00','end':'23:00'}])
            self.assertEqual([c.args[1] for c in tracked.call_args_list],['schedule_previewed','schedule_imported'])
    async def test_read_confirm_no_other_user_write(self):
        draft=photo.Draft('nonce','id','file',phase='schedule_month',image=b'photo',report={'name':'Алексей','index':7})
        photo.drafts[7]=draft
        raw={'name':'Алексей','cells':[{'day':2,'text':'10'},{'day':7,'text':'9-17'}]}
        with patch('schedule_layout.row_image',return_value=b'crop'),patch.object(chat.vision,'request_json',new=AsyncMock(return_value=raw)),patch.object(schedule,'save',new=AsyncMock()) as save,patch('google_calendar.is_connected',new=AsyncMock(return_value=False)),patch.object(chat.research,'track'):
            await chat.photo_month(self.message)
            self.assertEqual(draft.phase,'schedule_review');save.assert_not_awaited()
            self.assertEqual(draft.report['cells'][0],{'date':'2026-10-02','start':'10:00','end':'22:00'})
            await chat.photo_action(self.cb('sch:nonce:save',8));save.assert_not_awaited()
            await chat.photo_action(self.cb('sch:nonce:save'));save.assert_awaited_once_with(7,draft.report['cells'])
            await chat.photo_action(self.cb('sch:nonce:save'));self.assertEqual(save.await_count,1)
    async def test_wrong_row_does_not_advance(self):
        draft=photo.Draft('n','id','file',phase='schedule_month',image=b'photo',report={'name':'Алексей','index':7})
        photo.drafts[7]=draft
        with patch('schedule_layout.row_image',return_value=b'crop'),patch.object(chat.vision,'request_json',new=AsyncMock(return_value={'name':'Другой','cells':[{'day':1,'text':'10'}]})):
            await chat.photo_month(self.message)
            self.assertEqual(draft.phase,'schedule_month');self.assertNotIn('cells',draft.report)
    async def test_actual_save_failure_retry_preserves_state(self):
        data={'work_day':'2026-09-28','work_nonce':'n','actual_start':'2026-09-28T10:00:00+03:00','actual_end':'2026-09-28T23:30:00+03:00'}
        state=NS(get_data=AsyncMock(return_value=data),get_state=AsyncMock(return_value=chat.Work.confirm.state),clear=AsyncMock())
        with patch.object(schedule,'save_work',new=AsyncMock(side_effect=[RuntimeError('secret'),{'hours':13.5,'income':4725}])),patch.object(chat.research,'track'):
            await chat.save_actual(self.cb('work:save:n'),state);state.clear.assert_not_awaited()
            self.assertNotIn('secret',self.message.answer.await_args.args[0])
            await chat.save_actual(self.cb('work:save:n'),state);state.clear.assert_awaited_once()
            self.assertIn('Записал 13 ч 30 мин.',self.message.edit_text.await_args.args[0])
            self.assertIn('4725',self.message.edit_text.await_args.args[0])
    async def test_private_rate_snapshot_and_export(self):
        store.rows={'users':[{'id':7,'hourly_rate':350},{'id':8,'hourly_rate':999}], 'worked_shifts':[], 'shifts':[]}
        a,b=schedule.actual('2026-09-28','23:30','10')
        self.assertEqual((await schedule.save_work(7,'2026-09-28',a,b))['income'],4725)
        store.rows['users'][0]['hourly_rate']=400
        self.assertEqual((await schedule.save_work(7,'2026-09-28',a,b))['income'],4725)
        self.assertEqual((await schedule.export(8))['worked_shifts'],[])
        self.assertEqual(len(store.rows['worked_shifts']),1)
    async def test_due_reminder_no_early_or_duplicate(self):
        now=datetime(2026,9,28,23,30,tzinfo=schedule.TZ)
        store.rows={'shifts':[{'id':1,'user_id':7,'shift_date':'2026-09-28','starts_at':'10:00:00','ends_at':'22:00:00','time_prompt_sent':False,'time_prompt_at':None},
                             {'id':2,'user_id':8,'shift_date':'2026-09-29','starts_at':'11:00:00','ends_at':'23:00:00','time_prompt_sent':False,'time_prompt_at':None}], 'worked_shifts':[]}
        bot=NS(send_message=AsyncMock())
        with patch.object(schedule,'enabled',return_value=True),patch.object(chat,'datetime') as clock:
            clock.now.return_value=now;clock.fromisoformat=datetime.fromisoformat;clock.combine=datetime.combine
            await chat.prompt_work_end(bot);await chat.prompt_work_end(bot)
        bot.send_message.assert_awaited_once();self.assertEqual(bot.send_message.await_args.args[0],7)

    async def test_evening_reminder_sends_to_every_scheduled_user_once(self):
        store.rows={'users':[{'id':7,'shift_reminders_enabled':True},
                             {'id':8,'shift_reminders_enabled':False}],
                    'shifts':[{'id':1,'user_id':7,'shift_date':'2026-10-05','starts_at':'10:00:00','ends_at':'22:00:00','start_reminder_sent':False},
                              {'id':2,'user_id':8,'shift_date':'2026-10-05','starts_at':None,'ends_at':None,'start_reminder_sent':False}]}
        bot=NS(send_message=AsyncMock())
        with patch.object(schedule,'enabled',return_value=True),patch.object(jobs,'op_today',return_value=date(2026,10,4)):
            await jobs.tomorrow_shift_reminder(bot)
            await jobs.tomorrow_shift_reminder(bot)
        self.assertEqual(bot.send_message.await_count,2)
        self.assertEqual({call.args[0] for call in bot.send_message.await_args_list},{7,8})
        self.assertIn('10:00',bot.send_message.await_args_list[0].args[1])
        self.assertTrue(store.rows['shifts'][0]['start_reminder_sent'])
        self.assertTrue(store.rows['shifts'][1]['start_reminder_sent'])

    async def test_old_reminder_setting_button_cannot_opt_out(self):
        store.rows={'users':[{'id':7,'shift_reminders_enabled':True},{'id':8,'shift_reminders_enabled':True}]}
        self.message.edit_text=AsyncMock()
        await chat.set_reminder_settings(self.cb('work:reminders:off'))
        self.assertTrue(store.rows['users'][0]['shift_reminders_enabled'])
        self.assertTrue(store.rows['users'][1]['shift_reminders_enabled'])

    async def test_failed_delivery_releases_reminder_claim(self):
        store.rows={'users':[{'id':7,'shift_reminders_enabled':True}],
                    'shifts':[{'id':1,'user_id':7,'shift_date':'2026-10-05','starts_at':'10:00:00','ends_at':'22:00:00','start_reminder_sent':False}]}
        bot=NS(send_message=AsyncMock(side_effect=RuntimeError('blocked')))
        with patch.object(schedule,'enabled',return_value=True),patch.object(jobs,'op_today',return_value=date(2026,10,4)):
            await jobs.tomorrow_shift_reminder(bot)
        self.assertFalse(store.rows['shifts'][0]['start_reminder_sent'])

    async def test_evening_retry_sends_after_temporary_failure(self):
        store.rows={'users':[{'id':7,'shift_reminders_enabled':True}],
                    'shifts':[{'id':1,'user_id':7,'shift_date':'2026-10-05','starts_at':'10:00:00','ends_at':'22:00:00','start_reminder_sent':False}]}
        bot=NS(send_message=AsyncMock(side_effect=[RuntimeError('temporary'),None]))
        with patch.object(schedule,'enabled',return_value=True),patch.object(jobs,'op_today',return_value=date(2026,10,4)),patch.object(jobs,'datetime') as clock:
            clock.now.return_value=datetime(2026,10,4,19,10,tzinfo=schedule.TZ)
            await jobs.retry_tomorrow_shift_reminder(bot)
            await jobs.retry_tomorrow_shift_reminder(bot)
        self.assertEqual(bot.send_message.await_count,2)
        self.assertTrue(store.rows['shifts'][0]['start_reminder_sent'])

if __name__=='__main__':unittest.main()
