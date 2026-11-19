"""Ответ о конце смены в свободной форме и серии сообщений без результата (20.11.2026).

В выгрузке исследования 18.11 человек отправил 12 сообщений ради одной записи времени: бот понимал только «23:30»
ответом на вопрос, а «в 23» записывал расходом 23 ₽. Здесь — что теперь понимается и что видит аналитика."""
import asyncio
import os
import sys
import unittest
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
patch('supabase.create_client').start()

import research  # noqa: E402
import schedule_chat  # noqa: E402


class TimeOf(unittest.TestCase):
    def test_free_form_times(self):
        for text, value in (('23', '23:00'), ('в 23', '23:00'), ('23.30', '23:30'), ('23 30', '23:30'),
                            ('закончила в 23:30', '23:30'), ('ушла в 23', '23:00'), ('где-то в 23', '23:00'),
                            ('ушла в 23 часа', '23:00'), ('24', '00:00'), ('в 9', '09:00')):
            with self.subTest(text=text):
                self.assertEqual(schedule_chat.time_of(text), value)

    def test_money_days_and_ranges_are_not_time(self):
        for text in ('чай 50', 'такси 90', 'кофе 20', 'смена 2500', '1500', '17, 19', '17 и 19', '10-22', '45', 'привет', ''):
            with self.subTest(text=text):
                self.assertIsNone(schedule_chat.time_of(text))


def run(coro):
    return asyncio.run(coro)


class PendingDay(unittest.TestCase):
    def shifts(self, rows):
        async def execute(query):
            day = query.day
            return NS(data=[rows[day]] if day in rows else [])

        class Query:
            def __init__(self):
                self.day = None

            def select(self, *_):
                return self

            def eq(self, field, value):
                if field == 'shift_date':
                    self.day = value
                return self

        table = NS(table=lambda name: Query())
        return patch.object(schedule_chat.db, 'supabase', table), patch.object(schedule_chat.db, '_execute', new=execute)

    def test_next_day_answer_goes_to_yesterday_when_today_has_no_asked_shift(self):
        rows = {'2026-11-17': {'starts_at': '11:00', 'time_prompt_sent': True}}
        a, b = self.shifts(rows)
        with a, b, patch.object(schedule_chat, 'op_today', return_value=date(2026, 11, 18)), \
                patch.object(schedule_chat.db, 'get_worked_shift_details', new=AsyncMock(return_value=[])):
            self.assertEqual(run(schedule_chat.pending_end_day(5)), '2026-11-17')

    def test_recorded_or_not_asked_shifts_are_not_pending(self):
        rows = {'2026-11-18': {'starts_at': '11:00', 'time_prompt_sent': False},
                '2026-11-17': {'starts_at': '11:00', 'time_prompt_sent': True}}
        a, b = self.shifts(rows)
        with a, b, patch.object(schedule_chat, 'op_today', return_value=date(2026, 11, 18)), \
                patch.object(schedule_chat.db, 'get_worked_shift_details', new=AsyncMock(return_value=[{'shift_date': '2026-11-17'}])):
            self.assertIsNone(run(schedule_chat.pending_end_day(5)))


class EndAnswerFilter(unittest.TestCase):
    def message(self, text, reply=None):
        return NS(text=text, forward_origin=None, reply_to_message=reply, from_user=NS(id=5), bot=NS(id=1))

    def test_reply_to_the_question_uses_its_day(self):
        button = NS(callback_data='work:close:2026-11-17')
        reply = NS(from_user=NS(id=1), reply_markup=NS(inline_keyboard=[[button]]))
        with patch.object(schedule_chat, 'pending_end_day', new=AsyncMock()) as pending:
            self.assertEqual(run(schedule_chat.end_answer(self.message('в 23', reply))), {'day': '2026-11-17', 'value': '23:00'})
        pending.assert_not_awaited()

    def test_without_a_pending_shift_the_message_goes_on(self):
        with patch.object(schedule_chat, 'pending_end_day', new=AsyncMock(return_value=None)):
            self.assertFalse(run(schedule_chat.end_answer(self.message('23'))))
        with patch.object(schedule_chat, 'pending_end_day', new=AsyncMock(return_value='2026-11-18')):
            self.assertEqual(run(schedule_chat.end_answer(self.message('23.30'))), {'day': '2026-11-18', 'value': '23:30'})
            self.assertFalse(run(schedule_chat.end_answer(self.message('чай 1500'))))


class Bursts(unittest.TestCase):
    def setUp(self):
        research._bursts.clear()

    def test_three_messages_without_a_save_report_the_last_problem(self):
        self.assertIsNone(research.burst(5, 'activity', 'message', now=0))
        self.assertIsNone(research.burst(5, 'message_not_understood', 'small_amount', now=1))
        self.assertIsNone(research.burst(5, 'activity', 'message', now=20))
        self.assertIsNone(research.burst(5, 'shift_end_answer', 'time_format', now=21))
        self.assertEqual(research.burst(5, 'activity', 'message', now=40), 'time_format')
        self.assertIsNone(research.burst(5, 'activity', 'message', now=50))   # одна серия — одно событие

    def test_buttons_saves_and_pauses_do_not_make_a_burst(self):
        for now in (0, 5, 10):
            self.assertIsNone(research.burst(5, 'activity', 'button', now=now))
        research.burst(5, 'activity', 'message', now=0)
        research.burst(5, 'activity', 'message', now=10)
        research.burst(5, 'hours_recorded', None, now=11)
        self.assertIsNone(research.burst(5, 'activity', 'message', now=12))
        research.burst(6, 'activity', 'message', now=0)
        research.burst(6, 'activity', 'message', now=10)
        self.assertIsNone(research.burst(6, 'activity', 'message', now=400))   # после паузы счёт заново

    def test_burst_is_queued_as_its_own_event(self):
        research._queue.clear()
        with patch.object(research, 'enabled', return_value=True), patch('admin.is_admin', return_value=False), \
                patch.object(research, 'drain', new=AsyncMock()), patch.object(research.asyncio, 'create_task', side_effect=lambda coro: coro.close()):
            for _ in range(3):
                research.track(5, 'activity', step='message')
        kinds = [(r['kind'], r['step_name']) for r in research._queue]
        self.assertEqual(kinds.count(('activity', 'message')), 3)
        self.assertIn(('message_burst', 'unknown'), kinds)
        research._queue.clear()


if __name__ == '__main__':
    unittest.main()
