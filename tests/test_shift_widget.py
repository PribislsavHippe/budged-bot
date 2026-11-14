"""Карточка смены как HTML-виджет Telegram: размер, текст, откат на обычное сообщение."""
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import shift_widget as w


class ShiftWidget(unittest.IsolatedAsyncioTestCase):
    def test_card_shows_day_time_and_length_within_telegram_limits(self):
        html = w.card('2026-11-17', '11:00:00', '23:00:00')
        for part in ('Завтра, вторник 17 ноября', '11:00', 'до 23:00 · 12 ч'):
            self.assertIn(part, html)
        self.assertNotIn('<script', html)
        self.assertTrue(w.fits(html))
        self.assertIn('до 17:00 · 8 ч', w.card('2026-11-18', '09:00:00', '17:00:00'))
        self.assertIn('уточни у менеджера', w.card('2026-11-19'))
        self.assertFalse(w.fits('x' * 40000))
        self.assertFalse(w.fits('a\x00b'))

    def test_method_goes_to_send_widget(self):
        method = w.SendWidget(chat_id=5, html='<b>x</b>', caption='т', height=0)
        self.assertEqual(method.__api_method__, 'sendWidget')
        self.assertEqual(method.model_dump(exclude_none=True), {'chat_id': 5, 'html': '<b>x</b>', 'caption': 'т', 'height': 0})

    async def test_widget_is_off_by_default_and_falls_back_to_text(self):
        bot = AsyncMock()
        with patch.dict(os.environ, {'SHIFT_WIDGETS': '0'}):
            await w.send_or_text(bot, 5, 'Завтра смена', '2026-11-17', '11:00:00', '23:00:00')
        bot.send_message.assert_awaited_once_with(5, 'Завтра смена')
        bot.assert_not_awaited()
        # Включено и принято: уходит карточка с тем же текстом подписью, обычного сообщения нет.
        bot = AsyncMock()
        with patch.dict(os.environ, {'SHIFT_WIDGETS': '1'}):
            await w.send_or_text(bot, 5, 'Завтра смена', '2026-11-17', '11:00:00', '23:00:00')
        sent = bot.await_args.args[0]
        self.assertEqual((sent.chat_id, sent.caption), (5, 'Завтра смена'))
        self.assertIn('11:00', sent.html)
        bot.send_message.assert_not_awaited()
        # Telegram не принял виджет — человек всё равно получает напоминание.
        bot = AsyncMock(side_effect=RuntimeError('unsupported'))
        bot.send_message = AsyncMock()
        with patch.dict(os.environ, {'SHIFT_WIDGETS': '1'}):
            await w.send_or_text(bot, 5, 'Завтра смена', '2026-11-17', '11:00:00', '23:00:00')
        bot.send_message.assert_awaited_once_with(5, 'Завтра смена')


if __name__ == '__main__':
    unittest.main()
