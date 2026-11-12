import os
import unittest
from datetime import date, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import roster


START = date(2026, 11, 16)
PERIOD = {'id': 'test-period', 'start_date': '2026-11-16', 'end_date': '2026-11-30',
          'collect_wishes_at': '2026-11-11T08:00:00Z', 'draft': {'people': [{'name': 'Аня', 'link': 'own'}]},
          'published': None, 'published_at': None}
EMPLOYEE = {'id': 'own', 'restaurant_id': 'restaurant'}


def modules():
    with patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid', 'SUPABASE_KEY': 'test'}), patch('supabase.create_client'):
        import roster_wish_chat as chat
        import roster_wishes as wishes
    return chat, wishes


class WishParsing(unittest.TestCase):
    def test_mixed_text_and_pattern_have_independent_meanings(self):
        chat, _ = modules()
        parsed = chat.parse_wish('Не могу работать 18 ноября, желательно выходной 20 ноября, удобно 3/3')
        self.assertEqual(parsed['pattern'], '3/3')
        self.assertEqual(chat.resolve_dates(parsed['dates'], PERIOD, START),
                         {'2026-11-18': 'required', '2026-11-20': 'preferred'})
        plain = chat.parse_wish('выходные 18, 19')
        self.assertEqual(chat.resolve_dates(plain['dates'], PERIOD, START), {'2026-11-18': None, '2026-11-19': None})

    def test_named_month_is_never_replaced_with_the_wrong_month(self):
        chat, _ = modules()
        period = {**PERIOD, 'end_date': '2026-12-15'}
        parsed = chat.parse_wish('Желательный выходной 20 декабря')
        self.assertIsNone(chat.resolve_dates(parsed['dates'], period, START))
        parsed = chat.parse_wish('Желательные 29, 30 ноября и 3 декабря')
        self.assertEqual(chat.resolve_dates(parsed['dates'], period, START),
                         {'2026-11-29':'preferred','2026-11-30':'preferred','2026-12-03':'preferred'})

    def test_date_formats_and_money_do_not_become_work_patterns(self):
        chat, _ = modules()
        parsed = chat.parse_wish('Обязательные 18-го, 20.11 и 2026-11-23.')
        self.assertEqual(chat.resolve_dates(parsed['dates'], PERIOD, START),
                         {'2026-11-18':'required', '2026-11-20':'required', '2026-11-23':'required'})
        for message in ('хочу записать чаевые 20', 'хочу работать 20 ноября', 'такси 30'):
            self.assertIsNone(chat.parse_wish(message))
        parsed = chat.parse_wish('не могу работать 3/3')
        self.assertIsNone(parsed['pattern'])
        self.assertEqual(parsed['dates'][0][:3], [3, 3, 3])
        parsed = chat.parse_wish('хочу воскресенья и 2/2')
        self.assertEqual((parsed['weekdays'], parsed['pattern']), ([6], '2/2'))

    def test_pattern_scoring_is_soft_and_can_continue_a_cycle(self):
        cycle = roster.pattern_masks('2/2', 4, '1100')
        self.assertEqual(roster.pattern_misses(0b0011, cycle), 0)
        self.assertGreater(roster.pattern_misses(0b1010, cycle), 0)
        request = roster.Request(START, START + timedelta(days=3), ['Аня'],
                                 counts={START + timedelta(days=i):1 for i in range(4)},
                                 wish_dates={'Аня': {START}}, wish_patterns={'Аня':'2/2'})
        shifts = roster.as_schedule(request, roster.build(request, iterations=100, restarts=1))
        self.assertEqual(len(shifts['Аня']), 4)
        self.assertEqual(roster.audit(request, shifts).missed_off, [('Аня', START)])
        request.people.extend(['Боря', 'Вика'])
        self.assertNotIn(START, roster.auto_days_off(request).get('Аня', set()))


class WishFlow(unittest.IsolatedAsyncioTestCase):
    async def test_chat_confirms_required_and_preferred_dates_after_saving(self):
        chat, wishes = modules()
        message = NS(
            text='Не могу работать 23 ноября, желательно выходной 25 ноября.',
            from_user=NS(id=7), chat=NS(id=7), message_id=42, answer=AsyncMock(),
        )
        state = NS(update_data=AsyncMock())
        with patch('identity_chat.enabled', return_value=True), \
                patch.object(wishes, 'supported', new=AsyncMock(return_value=True)), \
                patch.object(wishes, 'employee', new=AsyncMock(return_value=EMPLOYEE)), \
                patch.object(wishes, 'period_rows', new=AsyncMock(return_value=[PERIOD])), \
                patch.object(chat, 'active_period', new=AsyncMock(return_value=PERIOD)), \
                patch.object(chat.days_off, 'roster_today', return_value=START), \
                patch.object(wishes, 'save', new=AsyncMock(return_value={
                    'status': 'ok', 'days': ['2026-11-23'], 'desired_days': ['2026-11-25'],
                })) as save:
            matched = await chat.message_filter(message)
            await chat.write_reply(message, state, **matched)
        self.assertEqual(save.await_args.args[4], {'2026-11-23': 'required', '2026-11-25': 'preferred'})
        state.update_data.assert_not_awaited()
        message.answer.assert_awaited_once_with(
            'Записал пожелания на 16–30 ноября.\n'
            'Обязательные выходные: 23 ноября.\n'
            'Желательные выходные: 25 ноября.', reply_markup=None,
        )

    def test_mixed_reply_includes_requested_pattern(self):
        chat, _ = modules()
        for pattern in ('2/2', '3/3'):
            with self.subTest(pattern=pattern):
                parsed = chat.parse_wish(f'Не могу работать 23 ноября, удобно {pattern}')
                changes = chat.resolve_dates(parsed['dates'], PERIOD, START)
                reply = chat.summary(PERIOD, parsed, changes, {'days': ['2026-11-23']}, EMPLOYEE['id'])
                self.assertTrue(reply.endswith(f'\nРежим: {pattern}.'), reply)

    async def test_reminder_context_does_not_capture_a_bare_tip(self):
        chat, wishes = modules()
        message = NS(from_user=NS(id=7), text='30')
        with patch('identity_chat.enabled', return_value=True), \
                patch.object(wishes, 'supported', new=AsyncMock(return_value=True)), \
                patch.object(wishes, 'employee', new=AsyncMock(return_value=EMPLOYEE)), \
                patch.object(wishes, 'period_rows', new=AsyncMock(return_value=[PERIOD])), \
                patch.object(chat.days_off, 'waiting', new=AsyncMock(return_value=None)), \
                patch.object(chat, 'active_period', new=AsyncMock(return_value=PERIOD)) as pending:
            self.assertFalse(await chat.message_filter(message))
            pending.assert_not_awaited()
            message.text = 'желательно выходной 20-го'
            result = await chat.message_filter(message)
        self.assertEqual(result['wish_rows'], [PERIOD])
        self.assertEqual(chat.resolve_dates(result['parsed_wish']['dates'], PERIOD, START), {'2026-11-20':'preferred'})

    async def test_chat_confirms_ambiguous_dates_before_atomic_write(self):
        chat, wishes = modules()
        message = NS(from_user=NS(id=7), chat=NS(id=7), message_id=42, answer=AsyncMock())
        state = NS(update_data=AsyncMock(), set_data=AsyncMock())
        parsed = chat.parse_wish('выходные 18, 19')
        with patch.object(wishes, 'save', new=AsyncMock()) as save, patch.object(chat.days_off, 'roster_today', return_value=START):
            await chat.write_reply(message, state, parsed, EMPLOYEE, [PERIOD])
        save.assert_not_awaited()
        pending = state.update_data.await_args.kwargs['roster_wish_pending']
        state.get_data = AsyncMock(return_value={'roster_wish_pending': pending})
        message.edit_reply_markup = AsyncMock()
        callback = NS(from_user=NS(id=7), data=message.answer.await_args.kwargs['reply_markup'].inline_keyboard[0][1].callback_data,
                      answer=AsyncMock(), message=message)
        with patch.object(wishes, 'save', new=AsyncMock(return_value={'status':'ok','days':[], 'weekdays':[]})) as save, \
                patch.object(wishes, 'employee', new=AsyncMock(return_value=EMPLOYEE)), \
                patch.object(wishes, 'period_rows', new=AsyncMock(return_value=[PERIOD])):
            await chat.callback_kind(callback, state)
        self.assertEqual(save.await_args.args[0], 7)
        self.assertEqual(save.await_args.args[4], {'2026-11-18':'preferred','2026-11-19':'preferred'})
        self.assertIn('Записал желательные выходные', message.answer.await_args.args[0])

    async def test_calendar_exposes_only_own_wishes_and_publication_outcome(self):
        _, wishes = modules()
        published = {**PERIOD['draft'], 'cells': {'Аня': {'2026-11-18':'9'}},
                     'wish_dates': {'Аня': ['2026-11-18']}}
        row = {**PERIOD, 'published': published, 'published_at': '2026-11-15T08:00Z'}
        fake = NS(select=lambda *_: fake, eq=lambda *_: fake, in_=lambda *_: fake)
        with patch('identity_chat.enabled', return_value=True), \
                patch.object(wishes, 'employee', new=AsyncMock(return_value=EMPLOYEE)), \
                patch.object(wishes, 'period_rows', new=AsyncMock(return_value=[row])), \
                patch.object(wishes.db.supabase, 'table', return_value=fake), \
                patch.object(wishes.db, '_execute', new=AsyncMock(return_value=NS(data=[
                    {'roster_id':row['id'], 'days':['2026-11-19'], 'desired_days':['2026-11-18'], 'revision':3}]))), \
                patch.object(wishes, 'op_today', return_value=START):
            days = (await wishes.calendar(7, START, date(2026, 11, 30)))['days']
        self.assertEqual(days['2026-11-18']['kind'], 'preferred')
        self.assertTrue(days['2026-11-18']['working'])
        self.assertFalse(days['2026-11-18']['changed'])
        self.assertTrue(days['2026-11-19']['published'])
        self.assertFalse(days['2026-11-19']['working'])
        self.assertEqual(days['2026-11-19']['revision'], 3)
