"""Сторож пользовательских текстов (PRODUCT_PRINCIPLES §16).

1. В текстах бота и приложения нет устаревших обещаний (фото графика, Google Таблица) и команд через «/».
2. Обычные фразы о выходных и пожелания никогда не записываются как деньги.
"""
import ast
import os
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STALE = re.compile(r'фото графика|пришли его фото|фото своего графика|Google Таблиц|ссылку на открытую', re.I)
COMMAND = re.compile(r'(?<![\w?=])/(?:export|delete|feedback|learn|privacy|reset|help|start|profile|team|hours|calendar|'
                     r'undo|categories|sheet|reminders|work|rate|roster|invite|restaurant|broadcast|admin|onboarding)\b')


def python_texts():
    """Строки-предложения из кода бота: то, что видит человек. Сравнения вида == '/cancel' не в счёт."""
    for path in sorted(ROOT.glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        docs = {id(node.body[0].value) for node in ast.walk(tree)
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs and ' ' in node.value.strip():
                yield f'{path.name}:{node.lineno}', node.value


def web_texts():
    for path in sorted((ROOT / 'webapp').glob('*.html')) + sorted((ROOT / 'webapp').glob('*.js')):
        text = path.read_text(encoding='utf-8')
        # В разметке и в строках скриптов; адреса API (/api/...) командами не считаются.
        for number, line in enumerate(text.splitlines(), 1):
            yield f'webapp/{path.name}:{number}', line


class UserTexts(unittest.TestCase):
    def test_no_stale_promises_or_slash_commands_in_texts(self):
        found = [f'{where}: {text[:90]!r}' for where, text in list(python_texts()) + list(web_texts())
                 if STALE.search(text) or COMMAND.search(text)]
        # Распознавание фото отчёта и Google Таблицы остаются в коде (решение владельца), но не предлагаются.
        # Инструменты только для владельца бота (админка, прототип сборки) — старые команды, пока их не перенесли.
        allowed = ('report_photo.py', 'report_auto.py', 'report_vision.py', 'schedule_reader.py', 'schedule_sheet.py',
                   'schedule_image', 'legacy', 'admin.py', 'roster.py', 'roster_chat.py')
        found = [f for f in found if not f.startswith(allowed)]
        self.assertEqual(found, [], '\n'.join(found))


class NeverMoney(unittest.IsolatedAsyncioTestCase):
    """Фразы, которые раньше могли стать расходом или ошибкой про сумму."""
    PHRASES = ('нужны выходные 17, 19', 'выходной 17 и 19', 'по вторникам не могу',
               'Желательные воскресенья а так мне 2/2 нравится',
               'Слушай, ну я хочу по понедельникам, ещё нужно 17 и 19',
               'хочу отдыхать в субботу и воскресенье', 'хочу воскресенья и 2/2', '2/2', 'График', 'Блять',
               'НЕ могу работать: 16,17,20, 24,27\n\nМогу работать: все остальные могу\n\nРабочие неполные ->18 ноября  -> С 9:00-17:00 !')

    async def test_day_off_phrases_and_wishes_are_not_recorded_as_money(self):
        with patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid', 'SUPABASE_KEY': 'test'}), patch('supabase.create_client'):
            import handlers
        for text in self.PHRASES:
            message = NS(text=text, from_user=NS(id=5, first_name='Test'), forward_origin=None, answer=AsyncMock(),
                         chat=NS(id=5), bot=NS(id=1), message_id=100)
            store = {}
            async def update_data(**kw): store.update(kw)
            state = NS(clear=AsyncMock(), update_data=update_data, get_data=AsyncMock(return_value={}))
            with patch.object(handlers.db, 'get_or_create_user', new=AsyncMock(return_value={'id': 5, 'onboarded': True})), \
                 patch.object(handlers.db, 'add_entries', new=AsyncMock()) as entries, \
                 patch.object(handlers.db, 'add_entry', new=AsyncMock()) as entry, \
                 patch.object(handlers.db, 'add_shifts', new=AsyncMock()), \
                 patch.object(handlers.db, 'money_source_seen', new=AsyncMock(return_value=False)):
                await handlers.handle_text(message, state)
            entries.assert_not_awaited()
            entry.assert_not_awaited()
            reply = message.answer.await_args.args[0]
            self.assertNotIn('сумма неоднозначна', reply, text)
            self.assertNotIn('Не нашёл сумму', reply, text)
            self.assertNotIn('✓', reply, text)


class LoneShortNumbers(unittest.IsolatedAsyncioTestCase):
    """Одно короткое число без слов не деньги (решение владельца 13.11.2026). Деньги — от 100 ₽ (решение 20.11.2026):
    «чай 20» и «такси 20» больше не записываются, «в 23» и «23.30» — время, а не расход и не чаевые."""
    NOT_MONEY = ('0', '1', '12', '23', '28', '31', '32', '99', ' 12 ')
    MONEY = (('100', 100), ('300', 300), ('2000', 2000), ('чай 500', 500), ('такси 350', -350), ('кофе 120', -120))
    SMALL = ('чай 20', 'такси 20', 'кофе 12', 'чай 99')
    TIME_LIKE = ('в 23', 'ушла в 23', '23.30', 'до 23')

    def setUp(self):
        with patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid', 'SUPABASE_KEY': 'test'}), patch('supabase.create_client'):
            import handlers, research
        self.handlers, self.research = handlers, research

    async def send(self, text, user):
        handlers = self.handlers
        message = NS(text=text, from_user=NS(id=5, first_name='Test'), forward_origin=None, answer=AsyncMock(),
                     chat=NS(id=5), bot=NS(id=1, delete_message=AsyncMock()), message_id=100)
        entry = {'id': 10, 'kind': 'income', 'account': 'cash', 'signed_amount': 1, 'category': 'Чаевые', 'note': None}
        state = NS(clear=AsyncMock(), update_data=AsyncMock(), get_data=AsyncMock(return_value={}))
        with patch.object(handlers.db, 'get_or_create_user', new=AsyncMock(return_value=user)), \
             patch.object(handlers.db, 'get_recent_entries', new=AsyncMock(return_value=[])), \
             patch.object(handlers.db, 'add_entries', new=AsyncMock(return_value=[entry])) as entries, \
             patch.object(handlers.db, 'add_entry', new=AsyncMock(return_value=entry)) as single, \
             patch.object(handlers.db, 'add_shifts', new=AsyncMock()) as shifts, \
             patch.object(handlers.db, 'money_source_seen', new=AsyncMock(return_value=False)), \
             patch.object(handlers, '_send_private_record', new=AsyncMock(return_value=True)) as private, \
             patch.object(handlers, 'today_block', new=AsyncMock(return_value='Итог')), \
             patch.object(self.research, 'record', new=AsyncMock(return_value={})):
            await handlers.handle_text(message, state)
        return message, entries, single, shifts, private

    async def test_short_numbers_are_never_recorded_in_either_money_mode(self):
        for private_mode in (False, True):
            user = {'id': 5, 'onboarded': True, 'private_money_mode': private_mode}
            for text in self.NOT_MONEY:
                with self.subTest(text=text, private=private_mode):
                    message, entries, single, shifts, private = await self.send(text, user)
                    entries.assert_not_awaited()
                    single.assert_not_awaited()
                    shifts.assert_not_awaited()
                    private.assert_not_awaited()
                    message.answer.assert_awaited_once_with(self.handlers.SHORT_NUMBER_TEXT)
                    reply = message.answer.await_args.args[0]
                    for forbidden in ('✓', 'Не нашёл сумму', 'сумма неоднозначна', 'Записал'):
                        self.assertNotIn(forbidden, reply)

    async def test_amounts_below_100_and_time_like_phrases_are_never_money(self):
        for private_mode in (False, True):
            user = {'id': 5, 'onboarded': True, 'private_money_mode': private_mode}
            for text, reply in [(t, self.handlers.SMALL_AMOUNT_TEXT) for t in self.SMALL] + \
                               [(t, self.handlers.SMALL_TIME_TEXT) for t in self.TIME_LIKE]:
                with self.subTest(text=text, private=private_mode):
                    message, entries, single, shifts, private = await self.send(text, user)
                    entries.assert_not_awaited()
                    single.assert_not_awaited()
                    private.assert_not_awaited()
                    message.answer.assert_awaited_once_with(reply)

    async def test_bigger_and_marked_amounts_are_still_recorded(self):
        user = {'id': 5, 'onboarded': True, 'private_money_mode': False}
        for text, signed in self.MONEY:
            with self.subTest(text=text):
                message, entries, *_ = await self.send(text, user)
                entries.assert_awaited_once()
                self.assertEqual(entries.await_args.args[1][0]['signed_amount'], signed)

    async def test_amount_in_an_open_expense_form_is_still_an_expense(self):
        # Открытый вопрос «Сколько ушло на…?» сам задаёт контекст суммы: «20» здесь — расход.
        handlers = self.handlers
        message = NS(text='20', from_user=NS(id=5), chat=NS(id=5), message_id=100, answer=AsyncMock())
        import time
        state = NS(clear=AsyncMock(), get_data=AsyncMock(return_value={'shift_category': 'Кофе', 'shift_asked_at': time.time()}))
        entry = {'id': 10, 'kind': 'expense', 'account': 'cash', 'signed_amount': -20, 'category': 'Кофе', 'note': None}
        with patch.object(handlers.db, 'get_or_create_user', new=AsyncMock(return_value={'id': 5, 'onboarded': True})), \
             patch.object(handlers.db, 'add_entry', new=AsyncMock(return_value=entry)) as single, \
             patch('ux_chat.value_saved', new=AsyncMock()):
            await handlers.shift_spend_amount(message, state)
        self.assertEqual(single.await_args.args[:3], (5, 'expense', handlers.db.CASH))
        self.assertEqual(single.await_args.args[3], -20)
        state.clear.assert_awaited()

    async def test_a_forgotten_expense_question_does_not_capture_a_later_number(self):
        # Ревью 15.11.2026: вопрос «Сколько ушло на мойку?» без ответа перехватывал «500» на следующий день.
        import time
        handlers = self.handlers
        for asked in (time.time() - handlers.SHIFT_SPEND_TTL - 60, None):
            with self.subTest(asked=asked):
                message = NS(text='500', from_user=NS(id=5), chat=NS(id=5), message_id=100, answer=AsyncMock())
                state = NS(clear=AsyncMock(), get_data=AsyncMock(return_value={'shift_category': 'Мойка', 'shift_asked_at': asked}))
                with patch.object(handlers, 'handle_text', new=AsyncMock()) as ordinary, \
                        patch.object(handlers.db, 'add_entry', new=AsyncMock()) as single:
                    await handlers.shift_spend_amount(message, state)
                single.assert_not_awaited()
                ordinary.assert_awaited_once_with(message, state)
                state.clear.assert_awaited()

    async def test_bigger_and_marked_amounts_still_reach_the_private_journal(self):
        user = {'id': 5, 'onboarded': True, 'private_money_mode': True}
        for text, signed in self.MONEY:
            with self.subTest(text=text):
                message, entries, single, shifts, private = await self.send(text, user)
                entries.assert_not_awaited()
                self.assertEqual(private.await_args.args[1]['signed_amount'], signed)


if __name__ == '__main__':
    unittest.main()


class ImpossibleShiftDays(unittest.IsolatedAsyncioTestCase):
    """«работаю 31» в ноябре и «работаю 5 500»: ни смены, ни денег (ревью 15.11.2026, P1 №8)."""
    setUp = LoneShortNumbers.setUp
    send = LoneShortNumbers.send

    async def test_a_number_that_is_not_a_day_records_nothing(self):
        from datetime import date
        user = {'id': 5, 'onboarded': True, 'private_money_mode': False}
        for text, bad in (('работаю 31', '31'), ('работаю 5 500', '500'), ('работаю 29 30 31', '31')):
            with self.subTest(text=text), patch.object(self.handlers, 'op_today', return_value=date(2026, 11, 15)):
                message, entries, single, shifts, private = await self.send(text, user)
                entries.assert_not_awaited()
                single.assert_not_awaited()
                shifts.assert_not_awaited()
                reply = message.answer.await_args.args[0]
                self.assertEqual(reply, self.handlers.bad_shift_days_text([int(bad)]))

    async def test_real_days_are_still_saved_as_shifts(self):
        from datetime import date
        user = {'id': 5, 'onboarded': True, 'private_money_mode': False}
        with patch.object(self.handlers, 'op_today', return_value=date(2026, 11, 15)), \
                patch('ux_chat.schedule_saved', new=AsyncMock()):
            message, entries, single, shifts, private = await self.send('работаю 22 24 26', user)
        shifts.assert_awaited_once_with(5, ['2026-11-22', '2026-11-24', '2026-11-26'])
        entries.assert_not_awaited()
