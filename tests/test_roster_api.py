"""График на сайте: черновик, проверка правил, сборка, публикация и просмотр командой."""
import os
import sys
import unittest
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from postgrest.exceptions import APIError

import roster

START, END = date(2026, 11, 16), date(2026, 11, 22)
OWNER, MANAGER, WAITER, STRANGER = 1, 2, 3, 9
PLACE = 'aaaaaaaa-0000-0000-0000-000000000001'
OTHER = 'bbbbbbbb-0000-0000-0000-000000000002'


class Query:
    def __init__(self, store, name):
        self.store, self.name, self.filters, self.action, self.payload = store, name, [], 'select', None
        self.lo, self.hi = 0, None

    def select(self, *_):
        return self

    def eq(self, key, value):
        self.filters.append(lambda r: r.get(key) == value)
        return self

    def lte(self, key, value):
        self.filters.append(lambda r: r.get(key) <= value)
        return self

    def gte(self, key, value):
        self.filters.append(lambda r: r.get(key) >= value)
        return self

    def is_(self, key, _):
        self.filters.append(lambda r: r.get(key) is None)
        return self

    def delete(self):
        self.action = 'delete'
        return self

    def lt(self, key, value):
        self.filters.append(lambda r: r.get(key) < value)
        return self

    def in_(self, key, values):
        self.filters.append(lambda r: r.get(key) in values)
        return self

    @property
    def not_(self):
        query = self

        class Not:
            def is_(self, key, _):
                query.filters.append(lambda r: r.get(key) is not None)
                return query
        return Not()

    def order(self, key, desc=False):
        self.sort = (key, desc)
        return self

    def limit(self, n):
        self.hi = n
        return self

    def range(self, a, b):
        self.lo, self.hi = a, b + 1
        return self

    def insert(self, row):
        self.action, self.payload = 'insert', row
        return self

    def update(self, values):
        self.action, self.payload = 'update', values
        return self

    def execute(self):
        rows = self.store.setdefault(self.name, [])
        if self.action == 'insert':
            if self.name == 'rosters' and any(r['restaurant_id'] == self.payload['restaurant_id']
                                              and r['start_date'] == self.payload['start_date'] for r in rows):
                raise APIError({'code': '23505', 'message': 'duplicate'})
            row = {'revision': 1, 'published': None, 'published_at': None, 'updated_at': 'now', **self.payload}
            rows.append(row)
            return NS(data=[dict(row)])
        found = [r for r in rows if all(f(r) for f in self.filters)]
        if getattr(self, 'sort', None):
            found.sort(key=lambda r: str(r.get(self.sort[0])), reverse=self.sort[1])
        if self.action == 'delete':
            self.store[self.name] = [r for r in rows if r not in found]
            return NS(data=found)
        if self.action == 'update':
            for r in found:
                r.update(self.payload)
            return NS(data=[dict(r) for r in found])
        return NS(data=[dict(r) for r in found[self.lo:self.hi]])


class Fake:
    def __init__(self):
        self.rows = {}

    def table(self, name):
        return Query(self.rows, name)


class DraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid', 'SUPABASE_KEY': 'test'}), \
                patch('supabase.create_client'):
            import roster_api
        cls.api = roster_api

    def draft(self, **extra):
        people = [{'name': n} for n in ('Аня', 'Боря', 'Вика', 'Гоша', 'Даша', 'Ева', 'Женя', 'Зоя')]
        return self.api.normalize({'people': people, **extra}, START, END)

    def test_cells_accept_hours_intervals_and_marks_only(self):
        self.assertEqual(self.api.parse_cell('9'), (9, None, False))
        self.assertEqual(self.api.parse_cell('12-13*'), (12, 13, True))
        self.assertEqual(self.api.parse_cell('*'), (None, None, True))
        for bad in ('25', '9-8', 'девять', '3'):
            with self.assertRaises(ValueError):
                self.api.parse_cell(bad)

    def test_normalize_drops_unknown_people_and_rejects_dates_outside(self):
        draft = self.draft(cells={'Аня': {'2026-11-16': '9'}, 'Чужой': {'2026-11-16': '9'}},
                           days={'2026-11-17': {'count': 4, 'closers': 2, 'junk': 1}}, events={'2026-11-18': '  Генуборка '})
        self.assertEqual(draft['cells'], {'Аня': {'2026-11-16': '9'}})
        self.assertEqual(draft['days'], {'2026-11-17': {'count': 4, 'closers': 2}})
        self.assertEqual(draft['events'], {'2026-11-18': 'Генуборка'})
        with self.assertRaises(ValueError):
            self.draft(off={'Аня': ['2026-12-01']})
        with self.assertRaises(ValueError):
            self.api.normalize({'people': [{'name': 'Аня'}, {'name': 'аня'}]}, START, END)

    def test_day_settings_reach_the_builder_and_the_check(self):
        draft = self.draft(days={'2026-11-17': {'count': 6, 'closers': 4, 'openers': 2}}, off={'Аня': ['2026-11-17']})
        request = self.api.to_request(draft, START, END)
        tuesday = date(2026, 11, 17)
        self.assertEqual((request.count(tuesday), request.closing(tuesday), request.opening(tuesday)), (6, 4, 2))
        self.assertEqual(request.days_off['Аня'], {tuesday})
        cells = self.api.build_cells(draft, START, END, iterations=20000, restarts=1)
        built = {**draft, 'cells': cells}
        tuesday_starts = [int(row['2026-11-17']) for row in cells.values() if '2026-11-17' in row]
        self.assertEqual(len(tuesday_starts), 6)
        self.assertEqual(sum(h >= roster.LAST_CLOSER_START for h in tuesday_starts), 4)
        self.assertNotIn('2026-11-17', cells.get('Аня', {}))
        self.assertEqual(self.api.check(built, START, END)['days'], {})

    def test_short_interval_is_not_a_closer(self):
        friday = '2026-11-20'
        draft = self.draft(cells={'Аня': {friday: '9'}, 'Боря': {friday: '9'}, 'Вика': {friday: '11'},
                                  'Гоша': {friday: '12-13'}, 'Даша': {friday: '12'}, 'Ева': {friday: '10'}})
        found = roster.audit(self.api.to_request(draft, START, END), self.api.to_schedule(draft))
        self.assertIn((date(2026, 11, 20), 2, 3), found.closers)
        self.assertTrue(self.api.check(draft, START, END)['days'][friday])

    def test_problems_point_to_their_day_and_person(self):
        draft = self.draft(cells={'Аня': {d: '9' for d in ('2026-11-16', '2026-11-17', '2026-11-18', '2026-11-19')}})
        items = self.api.check(draft, START, END)['items']
        self.assertTrue(all(item['text'] and item['day'] for item in items))
        self.assertIn({'day': '2026-11-16', 'name': 'Аня', 'text': 'Аня: смен подряд с 16 ноября: 4.'}, items)
        self.assertEqual(len(items), self.api.check(draft, START, END)['problems'])

    def test_first_publication_tells_everyone_later_only_the_changed(self):
        new = self.draft(cells={'Аня': {'2026-11-17': '11', '2026-11-16': '9-17'}})
        for person in new['people'][:2]:
            person['link'] = 'link-' + person['name']
        first = dict(self.api.notices(None, new, START, END))
        self.assertEqual(set(first), {'link-Аня', 'link-Боря'})
        self.assertIn('пн 16 — с 9 до 17\nвт 17 — с 11', first['link-Аня'])
        self.assertIn('Смен у тебя в этом графике нет', first['link-Боря'])
        self.assertNotIn('Аня', first['link-Боря'])
        changed = dict(new, off={'Боря': ['2026-11-18']})
        self.assertEqual([link for link, _ in self.api.notices(new, changed, START, END)], ['link-Боря'])
        self.assertEqual(self.api.notices(new, new, START, END), [])

    def test_team_sees_only_the_published_snapshot_without_wishes(self):
        snapshot = self.draft(cells={'Аня': {'2026-11-16': '9'}}, off={'Боря': ['2026-11-16']},
                              wish_off={'Вика': ['2026-11-18']}, wish_starts={'Гоша': [9]})
        snapshot['people'][0]['student'] = True
        row = {'start_date': START.isoformat(), 'end_date': END.isoformat(), 'published': snapshot, 'published_at': 'x'}
        view = self.api.published_view(row, {'id': 'l', 'report_name': 'Аня'})
        text = str(view)
        self.assertNotIn('wish', text)
        self.assertNotIn('student', text)
        self.assertEqual(len(view['days']), 7)
        me = [p for p in view['people'] if p['me']]
        self.assertEqual([p['name'] for p in me], ['Аня'])
        self.assertEqual(view['people'][1]['off'], ['2026-11-16'])


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sales_http_checks import signed, TOKEN
        self.signed = signed
        self.fake = Fake()
        self.fake.rows = {
            'restaurants': [{'id': PLACE, 'name': 'Ресторан', 'owner_id': OWNER, 'created_at': '1'},
                            {'id': OTHER, 'name': 'Чужой', 'owner_id': 77, 'created_at': '2'}],
            'employee_links': [
                {'id': 'cccccccc-0000-0000-0000-000000000001', 'restaurant_id': PLACE, 'user_id': MANAGER,
                 'report_name': 'Менеджер', 'status': 'approved', 'can_schedule': True, 'requested_at': '1'},
                {'id': 'cccccccc-0000-0000-0000-000000000002', 'restaurant_id': PLACE, 'user_id': WAITER,
                 'report_name': 'Аня', 'status': 'approved', 'can_schedule': False, 'requested_at': '2'},
                {'id': 'cccccccc-0000-0000-0000-000000000003', 'restaurant_id': OTHER, 'user_id': STRANGER,
                 'report_name': 'Чужая', 'status': 'approved', 'can_schedule': True, 'requested_at': '3'}]}
        self.env = patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid', 'SUPABASE_KEY': 'test',
                                           'IDENTITY_ENABLED': '1', 'WEBHOOK_HOST': 'https://bot.example.test'})
        self.env.start()
        self.client_patch = patch('supabase.create_client', return_value=self.fake)
        self.client_patch.start()
        import db
        import webapp_api
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        self.db_patch = patch.object(db, 'supabase', self.fake)
        self.db_patch.start()
        app = web.Application()
        webapp_api.register_webapp_routes(app, TOKEN, 'testbot')
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        for p in (self.db_patch, self.client_patch, self.env):
            p.stop()

    async def post(self, path, uid, status=200, **body):
        response = await self.client.post(path, json={'initData': self.signed(uid), **body})
        data = await response.json()
        self.assertEqual(response.status, status, data)
        return data

    async def roster(self, action, uid, status=200, **body):
        return await self.post('/api/restaurant/roster/' + action, uid, status, **body)

    async def test_full_cycle_from_draft_to_the_team_tab(self):
        import roster_api
        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
        self.assertEqual([p['name'] for p in made['draft']['people']], ['Менеджер', 'Аня'])
        await self.roster('create', MANAGER, 400, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
        draft = made['draft']
        draft['people'] += [{'name': n} for n in ('Боря', 'Вика', 'Гоша', 'Даша', 'Ева')]
        draft['events'] = {'2026-11-18': 'Генуборка'}
        saved = await self.roster('save', MANAGER, id=made['id'], revision=made['revision'], draft=draft)
        self.assertEqual(saved['revision'], 2)
        # Старая версия из другого окна не перезаписывает новую.
        await self.roster('save', OWNER, 409, id=made['id'], revision=1, draft=draft)
        real = roster.build
        with patch.object(roster, 'build', lambda request, iterations, restarts: real(request, 15000, restarts=1)):
            built = await self.roster('build', OWNER, id=made['id'], revision=2, draft=saved['draft'])
        self.assertTrue(built['draft']['cells'])
        team_before = await self.post('/api/schedule/view', WAITER)
        self.assertIsNone(team_before['period'])
        self.assertFalse(team_before['can_edit'])
        published = await self.roster('publish', MANAGER, id=made['id'], revision=built['revision'])
        self.assertFalse(published['changed'])
        view = await self.post('/api/schedule/view', WAITER)
        self.assertEqual(view['period']['start'], '2026-11-16')
        self.assertEqual(view['period']['events'], {'2026-11-18': 'Генуборка'})
        self.assertEqual([p['name'] for p in view['period']['people'] if p['me']], ['Аня'])
        manager_view = await self.post('/api/schedule/view', MANAGER)
        self.assertTrue(manager_view['can_edit'])
        self.assertEqual(manager_view['site_url'], 'https://bot.example.test/restaurant#schedule')
        # Правка после публикации видна команде только после новой публикации.
        edited = dict(built['draft'], events={})
        await self.roster('save', MANAGER, id=made['id'], revision=published['revision'], draft=edited)
        self.assertEqual((await self.post('/api/schedule/view', WAITER))['period']['events'], {'2026-11-18': 'Генуборка'})

    async def test_next_schedule_keeps_order_and_settings_of_the_last_one(self):
        made = await self.roster('create', OWNER, restaurant_id=PLACE, start='2026-11-01', end='2026-11-15')
        draft = made['draft']
        draft['people'] = [draft['people'][1], {'name': 'Гость'}, draft['people'][0]]
        draft['people'][0]['student'] = True
        draft['wish_starts'] = {'Аня': [9, 10]}
        await self.roster('save', OWNER, id=made['id'], revision=1, draft=draft)
        self.fake.rows['employee_links'][0]['status'] = 'revoked'
        self.fake.rows['employee_links'].append({'id': 'cccccccc-0000-0000-0000-000000000009', 'restaurant_id': PLACE,
                                                 'user_id': 44, 'report_name': 'Новенький', 'status': 'approved', 'requested_at': '9'})
        later = await self.roster('create', OWNER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-30')
        people = later['draft']['people']
        self.assertEqual([p['name'] for p in people], ['Аня', 'Гость', 'Новенький'])
        self.assertTrue(people[0]['student'])
        self.assertEqual(later['draft']['wish_starts'], {'Аня': [9, 10]})

    async def test_publication_messages_each_person_in_the_bot(self):
        sent = []

        class Bot:
            async def send_message(self, chat, text):
                sent.append((chat, text))
        self.client.server.app['bot'] = Bot()
        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
        draft = dict(made['draft'], cells={'Аня': {'2026-11-16': '9'}})
        saved = await self.roster('save', MANAGER, id=made['id'], revision=1, draft=draft)
        published = await self.roster('publish', MANAGER, id=made['id'], revision=saved['revision'])
        import asyncio
        await asyncio.sleep(0.2)
        self.assertEqual(published['notified'], 1)
        self.assertEqual(sent[0][0], WAITER)
        self.assertIn('пн 16 — с 9', sent[0][1])

    async def test_periods_do_not_overlap_and_drafts_can_be_deleted(self):
        made = await self.roster('create', OWNER, restaurant_id=PLACE, start='2026-11-01', end='2026-11-15')
        clash = await self.roster('create', OWNER, 400, restaurant_id=PLACE, start='2026-11-10', end='2026-11-25')
        self.assertIn('пересекаются', clash['error'])
        await self.roster('delete', OWNER, id=made['id'])
        again = await self.roster('create', OWNER, restaurant_id=PLACE, start='2026-11-10', end='2026-11-25')
        await self.roster('publish', OWNER, id=again['id'], revision=1)
        await self.roster('delete', OWNER, 400, id=again['id'])
        await self.roster('open', OWNER, id=again['id'])

    async def test_name_written_by_hand_becomes_the_person_who_joined(self):
        made = await self.roster('create', OWNER, restaurant_id=PLACE, start='2026-11-01', end='2026-11-15')
        draft = dict(made['draft'], people=made['draft']['people'] + [{'name': 'Вика'}], cells={'Вика': {'2026-11-02': '9'}})
        saved = await self.roster('save', OWNER, id=made['id'], revision=1, draft=draft)
        self.fake.rows['employee_links'].append({'id': 'cccccccc-0000-0000-0000-000000000008', 'restaurant_id': PLACE,
                                                 'user_id': 48, 'report_name': 'Вика', 'status': 'approved', 'requested_at': '8'})
        sent = []

        class Bot:
            async def send_message(self, chat, text, **_):
                sent.append(chat)
        self.client.server.app['bot'] = Bot()
        await self.roster('publish', OWNER, id=made['id'], revision=saved['revision'])
        import asyncio
        await asyncio.sleep(0.2)
        self.assertIn(48, sent)
        later = await self.roster('create', OWNER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-30')
        names = [(p['name'], p['link']) for p in later['draft']['people']]
        self.assertEqual(names.count(('Вика', 'cccccccc-0000-0000-0000-000000000008')), 1)
        self.assertEqual(len(names), 3)

    async def test_new_schedule_manager_gets_the_site_link(self):
        sent = []

        class Bot:
            async def send_message(self, chat, text, **_):
                sent.append((chat, text))
        self.client.server.app['bot'] = Bot()
        await self.post('/api/restaurant/scheduler', OWNER, id='cccccccc-0000-0000-0000-000000000002', value=True, restaurant_id=PLACE)
        self.assertEqual(sent, [(WAITER, 'Тебе открыли составление графика в «Ресторан».\nhttps://bot.example.test/restaurant')])
        await self.post('/api/restaurant/scheduler', OWNER, id='cccccccc-0000-0000-0000-000000000002', value=False, restaurant_id=PLACE)
        self.assertEqual(len(sent), 1)

    async def test_only_owner_and_schedule_manager_build(self):
        made = await self.roster('create', OWNER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
        for uid in (WAITER, 555):
            await self.roster('list', uid, 403)
            await self.roster('open', uid, 403, id=made['id'])
        # У менеджера чужого ресторана есть право на свой, но не на этот.
        await self.roster('open', STRANGER, 404, id=made['id'])
        await self.roster('create', STRANGER, 403, restaurant_id=PLACE, start='2026-12-01', end='2026-12-15')
        self.assertFalse((await self.post('/api/schedule/view', 555))['available'])
        await self.post('/api/schedule/view', STRANGER, 403, restaurant_id=PLACE)

    async def test_owner_grants_and_removes_the_schedule_right(self):
        access = await self.post('/api/restaurant/access', WAITER)
        self.assertFalse(access['available'])
        link = 'cccccccc-0000-0000-0000-000000000002'
        await self.post('/api/restaurant/scheduler', MANAGER, 403, id=link, value=True)
        await self.post('/api/restaurant/scheduler', OWNER, id=link, value=True, restaurant_id=PLACE)
        access = await self.post('/api/restaurant/access', WAITER)
        self.assertTrue(access['available'])
        self.assertEqual([r['id'] for r in access['schedule']], [PLACE])
        self.assertEqual(access['restaurants'], [])
        await self.post('/api/restaurant/view', WAITER, 403, restaurant_id=PLACE)
        await self.post('/api/restaurant/scheduler', OWNER, id=link, value=False, restaurant_id=PLACE)
        self.assertFalse((await self.post('/api/restaurant/access', WAITER))['available'])


if __name__ == '__main__':
    unittest.main()
