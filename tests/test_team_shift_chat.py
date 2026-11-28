import os
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

with patch.dict(os.environ, SUPABASE_URL='https://example.invalid', SUPABASE_KEY='test.test.test'):
    import team_shift_chat as chat


class TeamShiftTests(unittest.IsolatedAsyncioTestCase):
    def test_dates(self):
        friday = date(2026, 12, 4)
        self.assertEqual(chat.asked_date('Кто работает?', friday), friday)
        self.assertEqual(chat.asked_date('Кто работает сегодня?', friday), friday)
        self.assertEqual(chat.asked_date('Кто работает завтра?', friday), date(2026, 12, 5))
        self.assertEqual(chat.asked_date('Кто работает в пятницу?', friday), friday)
        self.assertEqual(chat.asked_date('Кто работает в пятницу?', date(2026, 12, 5)), date(2026, 12, 11))
        self.assertEqual(chat.asked_date('Кто из официантов работает во вторник?', friday), date(2026, 12, 8))
        self.assertIsNone(chat.asked_date('чай 1500', friday))

    def test_shifts_and_contact_links(self):
        day = date(2026, 12, 4)
        snapshot = {
            'people': [{'name': 'Боря', 'link': 'b'}, {'name': 'Аня & Ко', 'link': 'a'},
                       {'name': 'Вика', 'link': 'v'}],
            'cells': {'Боря': {str(day): '12'}, 'Аня & Ко': {str(day): '9:30-17'},
                      'Вика': {str(day): '*'}},
        }
        text = chat.format_day('Итальянская', day, snapshot, {'a': 123}, 321)
        self.assertEqual(text.splitlines()[1:], [
            '09:30–17:00 — <a href="tg://user?id=123">Аня &amp; Ко</a>',
            '12:00–23:00 — Боря',
        ])
        self.assertIn('ещё не опубликован', chat.format_day('Ресторан', day, None, {}, 321))
        self.assertIn('нет смен', chat.format_day('Ресторан', day, {}, {}, 321))

    async def test_request_without_restaurant_access_reads_no_roster(self):
        message = SimpleNamespace(text='Кто работает завтра?', from_user=SimpleNamespace(id=321),
                                  answer=AsyncMock())
        with patch.object(chat.roster_api, 'team_places', AsyncMock(return_value=([], None))), \
                patch.object(chat.roles, 'places', AsyncMock(return_value=[])), \
                patch.object(chat, 'place_day', AsyncMock()) as load:
            await chat.who_works(message, date(2026, 12, 4))
        load.assert_not_awaited()
        message.answer.assert_not_awaited()

    async def test_manager_gets_one_answer_with_each_available_restaurant_once(self):
        message = SimpleNamespace(from_user=SimpleNamespace(id=321), answer=AsyncMock())
        day = date(2026, 12, 4)
        italian = {'id': '1', 'name': 'Итальянская'}
        other = {'id': '2', 'name': 'Другой'}
        with patch.object(chat.roster_api, 'team_places', AsyncMock(return_value=([italian], None))), \
                patch.object(chat.roles, 'places', AsyncMock(return_value=[italian, other])), \
                patch.object(chat, 'place_day', AsyncMock(side_effect=['Смены 1', 'Смены 2'])) as load:
            await chat.who_works(message, day)
        self.assertEqual([call.args for call in load.await_args_list],
                         [(italian, day, 321), (other, day, 321)])
        message.answer.assert_awaited_once_with('Смены 1\n\nСмены 2', disable_web_page_preview=True)
