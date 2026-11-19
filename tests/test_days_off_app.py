"""Календарь выходных из виджета в мини-приложении: пример без сбора, доступ, запись тем же путём, что из виджета."""
import asyncio
import json
import os
import sys
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
patch('supabase.create_client').start()

import days_off_app  # noqa: E402
import days_off_widget  # noqa: E402


class Request(dict):
    def __init__(self, body):
        super().__init__()
        self.body, self.path, self.app = body, '/api/days_off/save', {'bot_token': 'x'}

    async def json(self):
        return self.body


class DaysOffApp(unittest.TestCase):
    def test_demo_period_is_the_next_half_month(self):
        self.assertEqual(days_off_app.demo_period(date(2026, 11, 19)),
                         {'id': days_off_app.DEMO_ID, 'start_date': '2026-12-01', 'end_date': '2026-12-15'})
        self.assertEqual(days_off_app.demo_period(date(2026, 2, 3))['end_date'], '2026-02-28')

    def test_access_switch(self):
        with patch.dict(os.environ, {'DAYS_OFF_MINIAPP': '1'}):
            self.assertTrue(days_off_app.allowed(5))
        with patch.dict(os.environ, {'DAYS_OFF_MINIAPP': '0'}):
            self.assertFalse(days_off_app.allowed(5))
        with patch.dict(os.environ, {'DAYS_OFF_MINIAPP': 'admin', 'ADMIN_ID': '7'}):
            self.assertFalse(days_off_app.allowed(5))
            self.assertTrue(days_off_app.allowed(7))
        with patch.dict(os.environ, {'DAYS_OFF_MINIAPP': '', 'ADMIN_ID': 'x'}):
            self.assertFalse(days_off_app.allowed(7))

    def test_not_an_employee_sees_an_example_that_saves_nothing(self):
        with patch('roster_wishes.employee', AsyncMock(return_value=None)), \
                patch('workday.op_today', return_value=date(2026, 11, 19)):
            result = asyncio.run(days_off_app.current(5))
        self.assertTrue(result['demo'])
        self.assertIn('пример', result['note'])
        self.assertEqual(result['caption'], 'Собираем выходные с 1 по 15 декабря.')
        self.assertEqual(json.loads(result['chat_data'])['id'][:8], '00000000')

    def test_save_goes_through_the_widget_path_with_a_nonce(self):
        request = Request({'initData': 'x', 'data': 'n:abcdef12', 'nonce': 'n1'})
        with patch('webapp_api._auth', AsyncMock(return_value=(7, request.body))), \
                patch.object(days_off_app, 'allowed', return_value=True), \
                patch.object(days_off_widget, 'apply', AsyncMock(return_value=('ok', '{"id":"x"}'))) as apply:
            response = asyncio.run(days_off_app.save_api(request))
        apply.assert_awaited_once_with(7, 'n:abcdef12', 'app:n1', source='miniapp')
        self.assertEqual(json.loads(response.text), {'result': 'ok', 'chat_data': '{"id":"x"}'})

    def test_save_without_nonce_or_access_is_refused(self):
        request = Request({'initData': 'x', 'data': 'n:abcdef12'})
        with patch('webapp_api._auth', AsyncMock(return_value=(7, request.body))), \
                patch.object(days_off_app, 'allowed', return_value=True):
            self.assertEqual(asyncio.run(days_off_app.save_api(request)).status, 400)
        with patch('webapp_api._auth', AsyncMock(return_value=(7, request.body))), \
                patch.object(days_off_app, 'allowed', return_value=False):
            self.assertEqual(asyncio.run(days_off_app.save_api(request)).status, 403)

    def test_page_defines_the_widget_bridge_before_the_widget_runs(self):
        self.assertIn("window.TelegramWidget=parent.makeWidget()<\\/script>'+s.html", days_off_app.PAGE)
        self.assertNotIn('</script>\'+', days_off_app.PAGE)


if __name__ == '__main__':
    unittest.main()
