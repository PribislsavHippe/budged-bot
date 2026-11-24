"""Выходные только в приложении (решение владельца 20.11.2026): чат их не записывает, а даёт кнопку.

Прежний сбор сообщениями остаётся в коде и включается DAYS_OFF_CHAT=1 (его проверяют test_roster_api и ux_flow_checks)."""
import asyncio
import os
import sys
import unittest
from datetime import date, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
patch('supabase.create_client').start()

import days_off  # noqa: E402
import days_off_chat  # noqa: E402
import roster_wishes  # noqa: E402

HOST = {'WEBHOOK_HOST': 'https://bot.example', 'DAYS_OFF_CHAT': '0'}


def button(markup):
    return markup.inline_keyboard[0][0]


class AppOnly(unittest.TestCase):
    def test_chat_collection_is_off_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('DAYS_OFF_CHAT', None)
            self.assertFalse(days_off.chat_enabled())
        with patch.dict(os.environ, {'DAYS_OFF_CHAT': '1'}):
            self.assertTrue(days_off.chat_enabled())

    def test_days_off_phrases_get_the_app_button_and_money_or_time_do_not(self):
        catch = ('нужны выходные 17, 19', 'не могу 20 и 21', '17, 24', '2/2', 'выходные по вторникам', 'удобно с 12')
        skip = ('23', 'в 23', '10-22', 'чай 500', 'чай 500, такси 300', 'гостей было 150', 'привет')
        for text in catch:
            with self.subTest(text=text):
                self.assertTrue(days_off_chat.about_days_off(NS(text=text, forward_origin=None)))
        for text in skip:
            with self.subTest(text=text):
                self.assertFalse(days_off_chat.about_days_off(NS(text=text, forward_origin=None)))

    def test_button_opens_the_new_calendar_for_those_who_have_it(self):
        with patch.dict(os.environ, {**HOST, 'DAYS_OFF_MINIAPP': '1'}):
            self.assertEqual(button(days_off.app_markup(5)).web_app.url, 'https://bot.example/app/days-off')
        with patch.dict(os.environ, {**HOST, 'DAYS_OFF_MINIAPP': '0'}):
            self.assertEqual(button(days_off.app_markup(5)).web_app.url, 'https://bot.example/app')
            markup = days_off.app_markup(5, period_start=date(2026, 12, 1))
            self.assertEqual(button(markup).web_app.url, 'https://bot.example/app?calendar_month=2026-12#cal-grid')

    def test_point_to_app_answers_with_the_button(self):
        message = NS(text='нужны выходные 17, 19', from_user=NS(id=5), answer=AsyncMock())
        with patch.dict(os.environ, {**HOST, 'DAYS_OFF_MINIAPP': '1'}):
            asyncio.run(days_off_chat.point_to_app(message))
        text = message.answer.await_args.args[0]
        self.assertEqual(text, days_off.APP_TEXT)
        self.assertEqual(button(message.answer.await_args.kwargs['reply_markup']).text, 'Отметить выходные')

    def test_reminder_asks_to_mark_in_the_app_not_to_write(self):
        row = {'start_date': '2026-12-01', 'end_date': '2026-12-15', 'stage': 1}
        with patch.dict(os.environ, HOST):
            text = roster_wishes.reminder_text(row)
        self.assertIn('календаре приложения', text)
        self.assertNotIn('напиши', text.lower())
        self.assertNotIn('Можно написать', text)

    def test_manual_request_sends_period_button_with_beta_disabled(self):
        roster_id = 'abcdef12-0000-4000-8000-000000000001'
        claim = {'roster_id': roster_id, 'link_id': 'own', 'user_id': 5, 'stage': 1,
                 'start_date': '2026-12-01', 'end_date': '2026-12-15'}
        bot = NS(send_message=AsyncMock())
        with patch.dict(os.environ, {**HOST, 'DAYS_OFF_MINIAPP': '0', 'DAYS_OFF_CHAT': '1'}), \
                patch('identity_chat.enabled', return_value=True), \
                patch.object(roster_wishes, 'datetime') as clock, \
                patch.object(roster_wishes, '_widget', AsyncMock()) as widget, \
                patch('research.track'), \
                patch('db._execute', AsyncMock(return_value=NS())):
            clock.now.return_value = datetime(2026, 11, 24, 12)
            self.assertEqual(asyncio.run(roster_wishes.invite(bot, {**claim, 'id': roster_id}, {5: 'own'})), 1)
        widget.assert_not_awaited()
        message = bot.send_message.await_args
        self.assertIn('календаре приложения', message.args[1])
        markup = message.kwargs['reply_markup']
        self.assertEqual(button(markup).web_app.url, f'https://bot.example/app/days-off?roster={roster_id}')
        self.assertEqual(markup.inline_keyboard[1][0].text, 'Без изменений')


class HelpOffered(unittest.TestCase):
    def test_help_after_two_misses_is_recorded(self):
        with patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid', 'SUPABASE_KEY': 'test'}):
            import handlers
        handlers._MISSES[5] = [1.0, 2.0]
        message = NS(from_user=NS(id=5), answer=AsyncMock())
        with patch('research.track') as track:
            asyncio.run(handlers._offer_help_if_stuck(message))
        track.assert_called_once_with(5, 'help_offered')
        self.assertEqual(message.answer.await_args.args[0], handlers.HELP_AFTER_MISSES)


if __name__ == '__main__':
    unittest.main()
