import unittest
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import roster
from roster_time import Rule

DAY = date(2026, 11, 23)
PERIOD = {'id': 'p', 'start_date': '2026-11-16', 'end_date': '2026-11-30',
          'draft': {'people': [{'name': 'Аля', 'link': 'employee'}]}}


def chat_module():
    with patch('supabase.create_client'):
        import roster_time_chat
    return roster_time_chat


class TimeRules(unittest.TestCase):
    def test_same_window_in_different_word_orders(self):
        chat = chat_module()
        phrases = ('23-го и 25-го могу только с 9 до 17', 'с 9 до 17 могу 23 и 25 ноября',
                   'Я доступна с 09:00 до 17:00: 23 и 25 ноября', 'Только 09:00–17:00 на 23.11 и 25.11',
                   '23 и 25 ноября только 9–17')
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                result = chat.parse(phrase)
                self.assertEqual((result['kind'], result['start'], result['end']), ('limit', 540, 1020))
                self.assertEqual(list(chat.off.resolve_dates(result['dates'], PERIOD, DAY)), ['2026-11-23', '2026-11-25'])

    def test_preference_scope_and_ambiguous_phrases(self):
        chat = chat_module()
        for text in ('Каждое воскресенье лучше с двух дня', 'по вс хотелось бы начинать в 14', 'по воскресеньям с 14:00'):
            result = chat.parse(text)
            self.assertEqual((result['kind'], result['start'], result['weekdays'], result['recurring']),
                             ('wish', 840, [6], True))
        self.assertFalse(chat.parse('в воскресенье хочу с 14')['recurring'])
        self.assertEqual(chat.parse('с 12 26 числа')['dates'][0][0], 26)
        self.assertEqual(chat.parse('поставьте с двенадцати 26-го')['start'], 720)
        self.assertEqual(chat.parse('хочу с 14')['question'], chat.ASK_DATE)
        self.assertEqual(chat.parse('26-го могу только с 9 до 24')['question'], chat.BAD_CLOCK)
        self.assertIsNone(chat.parse('такси 430 в 14:00'))
        self.assertIsNone(chat.parse('коктейль 2 в 14'))
        self.assertEqual(chat.parse('завтра могу с 12')['relative'], ['завтра'])
        self.assertEqual(chat.parse('26-го готова выйти в 12')['start'], 720)
        self.assertEqual(chat.parse('26-го подойдёт с 12')['kind'], 'wish')
        self.assertIsNone(chat.parse('не могу работать 26 ноября'))
        self.assertIsNone(chat.parse('не могу работать 18–20'))
        self.assertEqual(chat.parse('23 желательный выходной, 25-го хочу с 14')['question'], chat.ASK_TIME)

    def test_build_preserves_coverage_when_possible_and_reports_gap(self):
        names = ['Аля', 'Боря', 'Вика', 'Гоша']
        request = roster.Request(DAY, DAY, names, counts={DAY: 4}, openers={DAY: 1}, closers={DAY: 3},
                                 time_rules={'Аля': {DAY: Rule(9, 17)}, 'Боря': {DAY: Rule(preferred_start=14)}})
        schedule = roster.as_schedule(request, roster.build(request, iterations=200, restarts=1))
        self.assertEqual(schedule['Аля'][DAY], (9, 17))
        self.assertEqual(roster.start_of(schedule['Боря'][DAY]), 14)
        self.assertFalse(roster.audit(request, schedule).closers)
        request.time_rules['Боря'] = {DAY: Rule(9.5, 17.75)}
        schedule = roster.as_schedule(request, roster.build(request, iterations=200, restarts=1))
        audit = roster.audit(request, schedule)
        self.assertEqual(audit.closers, [(DAY, 2, 3)])
        self.assertEqual(roster.end_of(schedule['Боря'][DAY]), 17.75)
        self.assertFalse(audit.unavailable)
        schedule['Аля'][DAY] = 14
        self.assertEqual(roster.audit(request, schedule).unavailable[0][:2], ('Аля', DAY))


class TimeConversation(unittest.IsolatedAsyncioTestCase):
    async def test_confirmation_and_remove_are_scoped_to_sender(self):
        chat = chat_module()
        message = NS(text='23 и 25 ноября могу только с 9 до 17', from_user=NS(id=7), message_id=42,
                     answer=AsyncMock(), edit_text=AsyncMock())
        state = NS(update_data=AsyncMock(), get_data=AsyncMock())
        with patch('identity_chat.enabled', return_value=True), \
                patch.object(chat.wishes, 'employee', new=AsyncMock(return_value={'id':'employee'})), \
                patch.object(chat.store, 'supported', new=AsyncMock(return_value=True)), \
                patch.object(chat.wishes, 'period_rows', new=AsyncMock(return_value=[PERIOD])), \
                patch.object(chat.days_off, 'roster_today', return_value=DAY), \
                patch.object(chat.store, 'write', new=AsyncMock(return_value='ok')) as write:
            await chat.begin(message, state, chat.parse(message.text))
            write.assert_not_awaited()
            pending = state.update_data.await_args.kwargs['shift_time']
            state.get_data.return_value = {'shift_time': pending}
            token = message.answer.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
            callback = NS(data=token, from_user=NS(id=8), message=message, answer=AsyncMock())
            await chat.action(callback, state)
            write.assert_not_awaited()
            callback.from_user.id = 7
            await chat.action(callback, state)
            write.assert_awaited_once_with(7, pending)
            self.assertEqual(pending['keys'], ['2026-11-23', '2026-11-25'])
            callback.data = message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
            await chat.action(callback, state)
            self.assertEqual(write.await_args.kwargs['remove'], pending['op'])
