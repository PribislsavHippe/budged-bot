"""Календарь выходных как виджет Telegram: запрос, запись тем же путём, ответ виджету, очередь."""
import os
import unittest
import zlib
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

with patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid', 'SUPABASE_KEY': 'test'}), patch('supabase.create_client'):
    import days_off_widget as w
    import roster_wishes

ROSTER = {'id': 'dddddddd-0000-0000-0000-000000000001', 'start_date': '2026-11-16', 'end_date': '2026-11-30',
          'collect_wishes_at': '2026-11-11T08:00:00Z', 'draft': {'people': [{'name': 'Аня', 'link': 'own'}]}}
HEX = 'dddddddd000000000000000000000001'
ID = HEX[:8]
TODAY = date(2026, 11, 18)


class Parts(unittest.TestCase):
    def test_markup_fits_telegram_limits(self):
        raw = w.HTML.encode()
        self.assertLessEqual(len(raw), 32768)
        self.assertLessEqual(len(zlib.compress(raw, 9)), 4096)
        self.assertTrue(w.shift_widget.fits(w.HTML))

    def test_state_holds_the_period_and_the_saved_choice_as_bits(self):
        data = w.state(ROSTER, ['2026-11-18', '2026-11-19'], ['2026-11-25'], TODAY, '18.11 в 14:05')
        self.assertLessEqual(len(data.encode()), 1024)
        self.assertEqual(data, '{"id":"%s","s":"2026-11-16","e":"2026-11-30","f":"2026-11-18","r":"c","p":"e8","ok":"18.11 в 14:05"}' % HEX)
        self.assertEqual((int('c', 36), int('e8', 36)), (0b1100, 1 << 9))

    def test_state_packs_hours_by_half_hours_from_six(self):
        data = w.state(ROSTER, [], [], TODAY, hours={'2026-11-20': [540, 1020], '2026-12-01': [540, 600]})
        self.assertIn('"h":"46m"', data)                                  # 20-е — день 4, 9:00 — 6, 17:00 — 22
        self.assertEqual(w._hours_text(date(2026, 11, 16), date(2026, 11, 30), {'2026-11-20': [555, 1380]}), '47y')

    def test_query_is_parsed_strictly(self):
        self.assertEqual(w.parse(f'o:{ID}:c:e8'),
                         {'roster': ID, 'none': False, 'required': 12, 'preferred': 512, 'hours': {}})
        self.assertEqual(w.parse(f'o:{ID}:c:e8:46m'),
                         {'roster': ID, 'none': False, 'required': 12, 'preferred': 512, 'hours': {4: (540, 1020)}})
        self.assertEqual(w.parse(f'n:{ID}'), {'roster': ID, 'none': True, 'required': 0, 'preferred': 0, 'hours': {}})
        longest = f'o:{ID}:{w._b36((1 << 31) - (1 << 12))}:0:' + ''.join(w._b36(d) + '6m' for d in range(12))
        self.assertLessEqual(len(longest), 64)
        self.assertIsNotNone(w.parse(longest))
        for bad in ('', None, 'refresh', f'o:{ID}:c', f'o:{ID}:c:c',            # день не бывает и тем, и другим
                    f'o:{ID.upper()}:c:0', f'o:{ID}:-1:0', f'x:{ID}', f'o:{ID}:c:0;drop', 'o:' + HEX + ':c:0',
                    f'o:{ID}:c:0:26m',                                   # часы на обязательный выходной
                    f'o:{ID}:0:0:4m6', f'o:{ID}:0:0:46z', f'o:{ID}:0:0:46m46m', f'o:{ID}:0:0:46',
                    longest + '06m'):
            self.assertIsNone(w.parse(bad), bad)

    def test_hours_go_to_future_days_only(self):
        query = w.parse(f'o:{ID}:0:0:06m46m')
        self.assertEqual(w.hours_for(ROSTER, query, TODAY), {'2026-11-20': [540, 1020]})
        self.assertIsNone(w.changes(ROSTER, w.parse(f'o:{ID}:0:0:f6m'), TODAY))     # 16-й день периода из 15

    def test_every_future_day_of_the_period_gets_a_mark(self):
        planned = w.changes(ROSTER, w.parse(f'o:{ID}:c:e8'), TODAY)
        self.assertEqual(list(planned)[0], '2026-11-18')                  # прошедшие дни не отправляются
        self.assertEqual(len(planned), 13)
        self.assertEqual((planned['2026-11-18'], planned['2026-11-19'], planned['2026-11-25'], planned['2026-11-20']),
                         ('required', 'required', 'preferred', 'none'))
        self.assertIsNone(w.changes(ROSTER, {'roster': ID, 'none': False, 'required': 1 << 15, 'preferred': 0, 'hours': {}}, TODAY))

    def test_switch(self):
        for value, admin, expected in (('0', False, False), ('1', False, True), ('admin', True, True), ('admin', False, False)):
            with self.subTest(value=value, admin=admin), patch.dict(os.environ, {'DAYS_OFF_WIDGETS': value}), \
                    patch('admin.is_admin', return_value=admin):
                self.assertEqual(w.enabled(7), expected)


class Flow(unittest.IsolatedAsyncioTestCase):
    def query(self, data, uid=7, chat=7):
        return {'id': 'q1', 'from': {'id': uid}, 'chat': {'id': chat, 'type': 'private'}, 'message_id': 42, 'data': data}

    async def run_handle(self, data, save=None, chat=7, employee={'id': 'own', 'restaurant_id': 'r'}, hours=None):
        import roster_time_store
        bot = AsyncMock()
        hours = hours or AsyncMock(return_value='ok')
        save = save or AsyncMock(return_value={'status': 'ok', 'days': ['2026-11-18', '2026-11-19'], 'desired_days': ['2026-11-25']})
        with patch.object(roster_wishes, 'employee', new=AsyncMock(return_value=employee)), \
                patch.object(roster_wishes, 'period_rows', new=AsyncMock(return_value=[ROSTER])), \
                patch.object(roster_wishes, 'save', new=save), patch('workday.op_today', return_value=TODAY), \
                patch.object(roster_time_store, 'write_period_hours', new=hours), patch('research.track') as track:
            await w.handle(bot, self.query(data, chat=chat))
        sent = [call.args[0] for call in bot.await_args_list]
        self.hours = hours
        return sent, save, track

    async def test_saving_writes_through_the_wish_path_updates_the_widget_and_answers(self):
        sent, save, track = await self.run_handle(f'o:{ID}:c:e8')
        args = save.await_args
        self.assertEqual(args.args[0], 7)
        self.assertEqual(args.args[4]['2026-11-18'], 'required')
        self.assertFalse(args.kwargs['clear'])
        edit, answer = sent
        self.assertIsInstance(edit, w.EditMessageWidgetData)
        self.assertEqual((edit.chat_id, edit.message_id), (7, 42))
        self.assertIn('"r":"c"', edit.chat_data)
        self.assertIn('"ok":', edit.chat_data)
        self.assertIsInstance(answer, w.AnswerWidgetQuery)
        self.assertEqual((answer.widget_query_id, answer.data), ('q1', 'ok'))
        track.assert_called_once_with(7, 'days_off_answered', source='bot', screen='schedule')
        # Повтор того же нажатия — та же операция: база вернёт прежний результат, второй записи не будет.
        self.assertEqual(self.hours.await_args.args[3], {})                # часов нет — часы периода очищаются
        _, again, _ = await self.run_handle(f'o:{ID}:c:e8')
        self.assertEqual(again.await_args.args[3], args.args[3])

    async def test_hours_are_written_and_shown_back(self):
        sent, _, _ = await self.run_handle(f'o:{ID}:c:e8:66m')
        uid, operation, roster, hours = self.hours.await_args.args
        self.assertEqual((uid, roster, hours), (7, ROSTER['id'], {'2026-11-22': [540, 1020]}))
        self.assertIn('"h":"66m"', sent[0].chat_data)
        self.assertEqual(sent[-1].data, 'ok')
        sent, _, _ = await self.run_handle(f'o:{ID}:c:e8:66m', hours=AsyncMock(return_value='access'))
        self.assertEqual(sent[-1].data, roster_wishes.NO_ACCESS)

    async def test_without_migration_45_days_off_still_save(self):
        from postgrest.exceptions import APIError
        missing = AsyncMock(side_effect=APIError({'code': 'PGRST202', 'message': 'no function'}))
        sent, save, _ = await self.run_handle(f'o:{ID}:c:e8', hours=missing)
        save.assert_awaited_once()
        self.assertEqual(sent[-1].data, 'ok')
        sent, _, _ = await self.run_handle(f'o:{ID}:c:e8:66m', hours=missing)
        self.assertIn('часы', sent[-1].data)

    async def test_no_wishes_clears_the_period(self):
        _, save, _ = await self.run_handle(f'n:{ID}')
        self.assertEqual((save.await_args.args[4], save.await_args.kwargs['clear']), ({}, True))

    async def test_refusals_still_answer_the_widget_and_never_write(self):
        cases = (('refresh', {}), (f'o:{ID}:c:e8', {'chat': 99}), (f'o:{ID}:c:e8', {'employee': None}),
                 (f'o:{"e" * 8}:c:0', {}))
        for data, extra in cases:
            with self.subTest(data=data, extra=extra):
                save = AsyncMock()
                sent, save, _ = await self.run_handle(data, save=save, **extra)
                save.assert_not_awaited()
                self.assertEqual(len(sent), 1)
                self.assertIsInstance(sent[0], w.AnswerWidgetQuery)
                self.assertNotEqual(sent[0].data, 'ok')

    async def test_a_database_problem_is_explained_to_the_widget(self):
        save = AsyncMock(return_value={'status': 'limit'})
        sent, _, track = await self.run_handle(f'o:{ID}:c:0', save=save)
        self.assertEqual(sent[-1].data, roster_wishes.problem({'status': 'limit'}, ROSTER))
        track.assert_not_called()

    async def test_a_failure_still_answers_within_the_query(self):
        sent, _, _ = await self.run_handle(f'o:{ID}:c:0', save=AsyncMock(side_effect=RuntimeError('db')))
        self.assertIsInstance(sent[-1], w.AnswerWidgetQuery)
        self.assertEqual(sent[-1].data, 'Не получилось записать. Нажми ещё раз.')

    async def test_the_reminder_falls_back_to_text_when_the_widget_is_off_or_refused(self):
        bot = AsyncMock()
        with patch.dict(os.environ, {'DAYS_OFF_WIDGETS': '0'}):
            self.assertIsNone(await w.send(bot, 7, ROSTER, 'текст'))
        bot.assert_not_awaited()
        bot = AsyncMock(side_effect=RuntimeError('unsupported'))
        with patch.dict(os.environ, {'DAYS_OFF_WIDGETS': '1'}), patch('workday.op_today', return_value=TODAY):
            self.assertIsNone(await w.send(bot, 7, ROSTER, 'текст'))
        long = {**ROSTER, 'end_date': '2026-12-20'}
        with patch.dict(os.environ, {'DAYS_OFF_WIDGETS': '1'}):
            self.assertIsNone(await w.send(AsyncMock(), 7, long, 'текст'))
        bot = AsyncMock(return_value=NS(message_id=42))
        with patch.dict(os.environ, {'DAYS_OFF_WIDGETS': '1'}), patch('workday.op_today', return_value=TODAY):
            await w.send(bot, 7, ROSTER, 'текст', ['2026-11-18'], hours={'2026-11-20': [540, 1020]})
        method = bot.await_args.args[0]
        self.assertEqual((method.chat_id, method.caption, method.allow_queries, method.protect_content), (7, 'текст', True, True))
        self.assertIn('"r":"4"', method.chat_data)
        self.assertIn('"h":"46m"', method.chat_data)
        self.assertEqual(w.short_caption(ROSTER), 'Собираем выходные с 16 по 30 ноября.')
        self.assertEqual(w.short_caption({**ROSTER, 'start_date': '2026-11-28', 'end_date': '2026-12-11'}),
                         'Собираем выходные с 28 ноября по 11 декабря.')


class Inbox(unittest.IsolatedAsyncioTestCase):
    async def test_widget_queries_bypass_the_dispatcher_and_keep_the_person_order(self):
        from telegram_inbox import TelegramInbox
        dispatcher = NS(feed_raw_update=AsyncMock())
        inbox = TelegramInbox(NS(token='123:abc'), dispatcher)
        raw = {'update_id': 5, 'widget_query': {'id': 'q', 'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'},
                                                'message_id': 42, 'data': f'n:{ID}'}}
        number, actor, sealed, _ = inbox.pack(raw)
        message = {'update_id': 6, 'message': {'message_id': 1, 'date': 0, 'chat': {'id': 7, 'type': 'private'},
                                                'from': {'id': 7, 'is_bot': False, 'first_name': 'A'}, 'text': 'привет'}}
        self.assertEqual(actor, inbox.pack(message)[1])                 # та же очередь, что у сообщений человека
        row = {'update_id': number, 'batch_id': None, 'payload': sealed, 'actor_key': actor}
        import db
        with patch.object(db, '_execute', new=AsyncMock(return_value=NS(data=[row]))), \
                patch.object(db, 'supabase', NS(rpc=lambda name, params: name)), \
                patch('days_off_widget.handle', new=AsyncMock()) as handle:
            await inbox.process([row])
        handle.assert_awaited_once()
        self.assertEqual(handle.await_args.args[1]['data'], f'n:{ID}')
        dispatcher.feed_raw_update.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
