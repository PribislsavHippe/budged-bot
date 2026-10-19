import unittest
from unittest.mock import AsyncMock

from aiogram.methods import SendMessage, AnswerCallbackQuery
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove

from chat_keyboard import RemoveLegacyKeyboard


class ChatKeyboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_regular_response_removes_saved_keyboard(self):
        middleware = RemoveLegacyKeyboard()
        request = AsyncMock(return_value='sent')
        method = SendMessage(chat_id=7, text='Записано 500 ₽')
        self.assertEqual(await middleware(request, None, method), 'sent')
        sent = request.call_args.args[1]
        self.assertIsInstance(sent.reply_markup, ReplyKeyboardRemove)
        self.assertEqual(sent.text, method.text)
        self.assertIsNone(method.reply_markup)

    async def test_inline_buttons_survive_without_extra_messages(self):
        request = AsyncMock(return_value='sent')
        buttons = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='Отменить', callback_data='undo:1')]])
        method = SendMessage(chat_id=7, text='Записано 500 ₽', reply_markup=buttons)
        # Restarting the middleware must never trigger an announcement.
        for _ in range(2):
            middleware = RemoveLegacyKeyboard()
            self.assertEqual(await middleware(request, None, method), 'sent')
        self.assertEqual(request.await_count, 2)
        for call in request.call_args_list:
            self.assertEqual(call.args[1], method)

    async def test_original_failure_is_propagated_without_extra_requests(self):
        request = AsyncMock(side_effect=RuntimeError('network'))
        with self.assertRaises(RuntimeError):
            await RemoveLegacyKeyboard()(request, None, SendMessage(chat_id=7, text='Ответ'))
        self.assertEqual(request.await_count, 1)

    async def test_groups_and_callback_acknowledgments_untouched(self):
        middleware = RemoveLegacyKeyboard()
        for method in [SendMessage(chat_id=-7, text='Ответ'), AnswerCallbackQuery(callback_query_id='1')]:
            request = AsyncMock()
            await middleware(request, None, method)
            request.assert_awaited_once_with(None, method)
