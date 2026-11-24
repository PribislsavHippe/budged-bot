"""Календарь выходных из виджета в мини-приложении: пример без сбора, доступ, запись тем же путём, что из виджета."""
import asyncio
import json
import os
import sys
import unittest
from datetime import date
from types import SimpleNamespace
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
        apply.assert_awaited_once_with(7, 'n:abcdef12', 'app:n1', source='miniapp', roster_id=None)
        self.assertEqual(json.loads(response.text), {'result': 'ok', 'chat_data': '{"id":"x"}'})

    def test_save_without_nonce_or_access_is_refused(self):
        request = Request({'initData': 'x', 'data': 'n:abcdef12'})
        with patch('webapp_api._auth', AsyncMock(return_value=(7, request.body))), \
                patch.object(days_off_app, 'allowed', return_value=True):
            self.assertEqual(asyncio.run(days_off_app.save_api(request)).status, 400)
        with patch('webapp_api._auth', AsyncMock(return_value=(7, request.body))), \
                patch.object(days_off_app, 'allowed', return_value=False):
            self.assertEqual(asyncio.run(days_off_app.save_api(request)).status, 403)

    def test_invitation_opens_the_requested_period_for_its_members(self):
        from db import supabase
        earlier = {'id': 'abcdef12-0000-4000-8000-000000000000', 'start_date': '2026-12-01',
                   'end_date': '2026-12-15', 'collect_wishes_at': 'now', 'draft': {'people': [{'link': 'own'}]}}
        target = {**earlier, 'id': 'abcdef12-0000-4000-8000-000000000001', 'start_date': '2026-12-16',
                  'end_date': '2026-12-31'}
        request = Request({'roster': target['id']})
        with patch('webapp_api._auth', AsyncMock(return_value=(7, request.body))), \
                patch.object(days_off_app, 'allowed', return_value=False), \
                patch('roster_wishes.employee', AsyncMock(return_value={'id': 'own'})) as employee, \
                patch('roster_wishes.period_rows', AsyncMock(return_value=[earlier, target])), \
                patch('workday.op_today', return_value=date(2026, 11, 24)), \
                patch('db._execute', AsyncMock(return_value=SimpleNamespace(data=[]))), \
                patch('roster_time_store.period_hours', AsyncMock(return_value={})), \
                patch.object(supabase, 'table'):
            response = asyncio.run(days_off_app.state_api(request))
            self.assertEqual(json.loads(json.loads(response.text)['chat_data'])['s'], '2026-12-16')
            employee.return_value = {'id': 'other'}
            self.assertEqual(asyncio.run(days_off_app.state_api(request)).status, 404)

    def test_invitation_cannot_save_to_another_period_with_the_same_short_id(self):
        first = {'id': 'abcdef12-0000-4000-8000-000000000000', 'collect_wishes_at': 'now',
                 'draft': {'people': [{'link': 'own'}]}}
        requested = 'abcdef12-0000-4000-8000-000000000001'
        request = Request({'roster': requested, 'data': 'n:abcdef12', 'nonce': 'n1'})
        with patch('webapp_api._auth', AsyncMock(return_value=(7, request.body))), \
                patch.object(days_off_app, 'allowed', return_value=False), \
                patch('roster_wishes.employee', AsyncMock(return_value={'id': 'own'})), \
                patch('roster_wishes.period_rows', AsyncMock(return_value=[first])), \
                patch('roster_wishes.save', AsyncMock()) as save:
            response = asyncio.run(days_off_app.save_api(request))
        save.assert_not_awaited()
        self.assertIsNone(json.loads(response.text)['chat_data'])

    def test_page_defines_the_widget_bridge_before_the_widget_runs(self):
        self.assertIn("window.TelegramWidget=parent.makeWidget()<\\/script>'+s.html", days_off_app.PAGE)
        self.assertNotIn('</script>\'+', days_off_app.PAGE)

    def test_link_in_the_mini_app_is_not_inside_the_hidden_month_summary(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'webapp', 'index.html'),
                  encoding='utf-8') as f:
            page = f.read()
        summary = page.index('id="cal-sum"')
        self.assertIn('#cal-sum,#cal-hint { display:none; }', page)
        link = page.index('id="days-off-beta"')
        self.assertGreater(link, page.index('</p>', summary))
        self.assertIn('/app/days-off', page[link:link + 200])


if __name__ == '__main__':
    unittest.main()
