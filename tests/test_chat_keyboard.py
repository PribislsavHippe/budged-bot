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

    async def test_inline_buttons_survive_and_cleanup_is_once(self):
        middleware = RemoveLegacyKeyboard()
        request = AsyncMock(return_value='sent')
        buttons = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='Отменить', callback_data='undo:1')]])
        method = SendMessage(chat_id=7, text='Записано 500 ₽', reply_markup=buttons)
        await middleware(request, None, method)
        self.assertEqual(request.await_count, 2)
        self.assertEqual(request.call_args_list[0].args[1].reply_markup, buttons)
        self.assertIsInstance(request.call_args_list[1].args[1].reply_markup, ReplyKeyboardRemove)
        await middleware(request, None, method)
        self.assertEqual(request.await_count, 3)

    async def test_cleanup_failure_does_not_fail_payment_response(self):
        middleware = RemoveLegacyKeyboard()
        request = AsyncMock(side_effect=['sent', RuntimeError('network')])
        method = SendMessage(chat_id=7, text='Записано', reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
        with self.assertLogs(level='ERROR'):
            self.assertEqual(await middleware(request, None, method), 'sent')
        self.assertNotIn(7, middleware.cleaned)

    async def test_failed_original_response_does_not_mark_cleaned(self):
        middleware = RemoveLegacyKeyboard()
        with self.assertRaises(RuntimeError):
            await middleware(AsyncMock(side_effect=RuntimeError()), None, SendMessage(chat_id=7, text='Ответ'))
        self.assertNotIn(7, middleware.cleaned)

    async def test_groups_and_callback_acknowledgments_untouched(self):
        middleware = RemoveLegacyKeyboard()
        for method in [SendMessage(chat_id=-7, text='Ответ'), AnswerCallbackQuery(callback_query_id='1')]:
            request = AsyncMock()
            await middleware(request, None, method)
            request.assert_awaited_once_with(None, method)
