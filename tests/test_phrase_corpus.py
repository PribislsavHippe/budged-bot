"""Набор фраз официантов через настоящий handlers.handle_text (аудит 20.11.2026).

Каждая фраза — одно из трёх: записалось именно это, бот спросил кнопками, ничего не записалось.
Новое правило разбора проверяется сразу на всех фразах: ошибка вида «в 23 → расход 23 ₽» больше не проскочит.
Бот здесь не ждёт ни времени смены, ни выходных — это худший случай для общего разбора."""
import os
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TODAY = '2026-11-20'
SAVED = {
    'стол 12 чай 700': [('income', 700, 'Чаевые', None)],
    'чай 1,5к на карту': [('income', 1500, 'Чаевые', None)],
    'такси 450': [('expense', -450, 'Такси', None)],
    'чай 500 нал': [('income', 500, 'Чаевые', None)],
    'кофе 150': [('expense', -150, 'Еда', None)],
    'гость оставил 500': [('income', 500, 'Чаевые', None)],
    'оставили 1000 на карту': [('income', 1000, 'Чаевые', None)],
    'чай 3500 на всех, моя доля 700': [('income', 700, 'Чаевые', None)],
    'штраф 500': [('expense', -500, 'Прочее', None)],
    'аванс 15000': [('income', 15000, 'Зарплата', None)],
    '2500 за смену': [('income', 2500, 'Подработка', None)],
    'вчера чай 800': [('income', 800, 'Чаевые', '2026-11-19')],
    'за вчера 800': [('income', 800, 'Чаевые', '2026-11-19')],
    '17 ноября чай 900': [('income', 900, 'Чаевые', '2026-11-17')],
    'за 17.11 чай 900': [('income', 900, 'Чаевые', '2026-11-17')],
    'чай 500 и такси 300': [('income', 500, 'Чаевые', None), ('expense', -300, 'Такси', None)],
    'чай 500, такси 300': [('income', 500, 'Чаевые', None), ('expense', -300, 'Такси', None)],
    # Повторная проверка 20.11: номера столов списком и месяц целым словом.
    'стол 5 и 6, чай 700': [('income', 700, 'Чаевые', None)],
    'чай 500 за 3 ноября': [('income', 500, 'Чаевые', '2026-11-03')],
    'такси 400 вчера и кофе 150': [('expense', -400, 'Такси', '2026-11-19'), ('expense', -150, 'Еда', None)],
}
ASKED = ('гостей было 150', 'обслужила 150 гостей', 'чек 2300', 'средний чек 2300', 'выручка 45000',
         'на карту 300', 'должен Ане 500', 'ставка 250 в час', '100%')
NOTHING = ('в 23', 'ушла в 23', '23.30', 'до 23', 'чай 20', 'такси 90', 'приду к 12', '5 столов', '12 часов',
           '10%', 'с 11 до 23', 'отпуск с 1 по 14', 'пробка, буду в 12:20', 'поменяемся 24 на 26',
           'за 25 ноября чай 500', 'привет', 'спасибо', 'чай 1.500',
           '5 мартини 1500', 'соус 5 майонез 300', '12 июльских 400', 'стол 12')
UNCLEAR = ('с 11 до 23', 'отпуск с 1 по 14', 'поменяемся 24 на 26', 'пробка, буду в 12:20')


class PhraseCorpus(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid', 'SUPABASE_KEY': 'test'}), \
                patch('supabase.create_client'):
            import handlers
            import research
        self.handlers, self.research = handlers, research
        handlers._MISSES.clear()

    async def send(self, text, private=False):
        handlers = self.handlers
        from workday import MSK
        message = NS(text=text, from_user=NS(id=5, first_name='Test'), forward_origin=None, answer=AsyncMock(),
                     chat=NS(id=5), bot=NS(id=1, delete_message=AsyncMock()), message_id=100,
                     date=datetime(2026, 11, 20, 15, 0, tzinfo=MSK))
        user = {'id': 5, 'onboarded': True, 'private_money_mode': private}
        state = NS(clear=AsyncMock(), update_data=AsyncMock(), get_data=AsyncMock(return_value={}), get_state=AsyncMock(return_value=None))
        saved = lambda uid, rows: [{**r, 'id': i} for i, r in enumerate(rows)]
        with patch.object(handlers.db, 'get_or_create_user', new=AsyncMock(return_value=user)), \
             patch.object(handlers.db, 'get_recent_entries', new=AsyncMock(return_value=[{'id': 1}])), \
             patch.object(handlers.db, 'add_entries', new=AsyncMock(side_effect=saved)) as entries, \
             patch.object(handlers.db, 'add_shifts', new=AsyncMock()), \
             patch.object(handlers.db, 'money_source_seen', new=AsyncMock(return_value=False)), \
             patch.object(handlers, '_send_private_record', new=AsyncMock(return_value=True)) as private_send, \
             patch.object(handlers, 'today_block', new=AsyncMock(return_value='Итог')), \
             patch('ux_chat.entry_confirmation', new=AsyncMock()), \
             patch('ux_chat.offer_wish', new=AsyncMock()), \
             patch('menu_chat.answer', new=AsyncMock(return_value=False)), \
             patch.object(self.research, 'record', new=AsyncMock(return_value={})):
            await handlers.handle_text(message, state)
        return message, entries, state, private_send

    def rows(self, entries):
        if not entries.await_count:
            return []
        rows = entries.await_args.args[1]
        return [(r['kind'], r['signed_amount'], r['category'], None if r['work_date'] == TODAY else r['work_date'])
                for r in rows]

    async def test_saved_exactly_as_meant(self):
        for text, expected in SAVED.items():
            with self.subTest(text=text):
                message, entries, state, _ = await self.send(text)
                self.assertEqual(self.rows(entries), expected)

    async def test_unknown_words_next_to_an_amount_are_asked_not_guessed(self):
        for text in ASKED:
            with self.subTest(text=text):
                message, entries, state, private_send = await self.send(text)
                entries.assert_not_awaited()
                private_send.assert_not_awaited()
                pending = state.update_data.await_args.kwargs['money_pending']
                self.assertTrue(pending['items'])
                buttons = [b.callback_data for row in message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
                self.assertEqual([b.split(':')[2] for b in buttons], ['tip', 'expense', 'none'])

    async def test_time_numbers_and_chatter_are_never_money(self):
        for private in (False, True):
            for text in NOTHING:
                with self.subTest(text=text, private=private):
                    message, entries, state, private_send = await self.send(text, private)
                    entries.assert_not_awaited()
                    private_send.assert_not_awaited()
                    self.assertFalse(any('money_pending' in c.kwargs for c in state.update_data.await_args_list))

    async def test_no_amount_talk_without_money_words(self):
        for text in UNCLEAR:
            with self.subTest(text=text):
                message, *_ = await self.send(text)
                self.assertEqual(message.answer.await_args_list[0].args[0], self.handlers.UNCLEAR_NUMBERS_TEXT)

    async def test_second_miss_in_a_row_offers_help_instead_of_another_retry(self):
        message, *_ = await self.send('с 11 до 23')
        self.assertEqual(message.answer.await_count, 1)
        message, *_ = await self.send('отпуск с 1 по 14')
        self.assertEqual(message.answer.await_args_list[-1].args[0], self.handlers.HELP_AFTER_MISSES)
        message, entries, *_ = await self.send('чай 500')
        self.assertEqual(self.rows(entries), [('income', 500, 'Чаевые', None)])
        self.assertNotIn(5, self.handlers._MISSES)


class AnswerTheQuestion(unittest.IsolatedAsyncioTestCase):
    """После кнопки запись идёт тем же путём, с ключом исходного сообщения и его датой."""
    async def test_tip_button_saves_the_original_message(self):
        with patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid', 'SUPABASE_KEY': 'test'}), \
                patch('supabase.create_client'):
            import handlers
        pending = {'nonce': 'abc', 'chat': 5, 'message_id': 100, 'date': '2026-11-20T15:00:00+03:00',
                   'items': [{'kind': 'expense', 'account': 'card', 'amount': 300.0, 'category': 'Прочее', 'note': 'на карту 300'}]}
        state = NS(get_data=AsyncMock(return_value={'money_pending': pending}), update_data=AsyncMock())
        reply = NS(chat=NS(id=5), bot=NS(id=1), answer=AsyncMock(), edit_reply_markup=AsyncMock())
        callback = NS(data='money:as:tip:abc', from_user=NS(id=5), message=reply, answer=AsyncMock())
        with patch.object(handlers.db, 'get_or_create_user', new=AsyncMock(return_value={'private_money_mode': False})), \
             patch.object(handlers, '_save_items', new=AsyncMock()) as save:
            await handlers.cb_money_kind(callback, state)
        origin, _, items = save.await_args.args
        self.assertEqual((origin.message_id, origin.chat.id), (100, 5))
        self.assertEqual(items, [{'kind': 'income', 'account': 'card', 'amount': 300.0, 'category': 'Чаевые', 'note': 'на карту 300'}])
        callback.answer.reset_mock()
        state.get_data = AsyncMock(return_value={'money_pending': None})
        await handlers.cb_money_kind(callback, state)
        callback.answer.assert_awaited_once_with('Этот вопрос уже закрыт.')


if __name__ == '__main__':
    unittest.main()
