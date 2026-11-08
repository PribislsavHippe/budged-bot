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


if __name__ == '__main__':
    unittest.main()
