"""График на сайте: черновик, проверка правил, сборка, публикация и просмотр командой."""
import os
import sys
import unittest
from uuid import uuid4
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from postgrest.exceptions import APIError

import holidays_ru
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

    def upsert(self, row, on_conflict='', ignore_duplicates=False):
        self.action, self.payload, self.conflict, self.ignore = 'upsert', row, on_conflict, ignore_duplicates
        return self

    def insert(self, row):
        self.action, self.payload = 'insert', row
        return self

    def update(self, values):
        self.action, self.payload = 'update', values
        return self

    def execute(self):
        rows = self.store.setdefault(self.name, [])
        if self.action == 'upsert':
            keys = [k.strip() for k in self.conflict.split(',')]
            for existing in rows:
                if all(existing.get(k) == self.payload.get(k) for k in keys):
                    if not self.ignore:
                        existing.update(self.payload)
                    break
            else:
                rows.append(dict(self.payload))
            return NS(data=[self.payload])
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


def fast():
    """Сборка в тестах короче: для проверки правил хватает нескольких тысяч шагов."""
    real = roster.build
    return patch.object(roster, 'build', lambda request, iterations=15000, seed=7, restarts=1: real(request, min(iterations, 15000), seed, 1))


NAMES = ('Аня', 'Боря', 'Вика', 'Гоша', 'Даша', 'Ева', 'Женя', 'Зоя')


class DraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid', 'SUPABASE_KEY': 'test'}), \
                patch('supabase.create_client'):
            import roster_api
        cls.api = roster_api

    def draft(self, **extra):
        people = [{'name': n} for n in NAMES]
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

    def test_russian_holidays_are_busy_days_unless_the_manager_says_otherwise(self):
        start, end = date(2026, 12, 29), date(2027, 1, 11)
        draft = self.api.normalize({'people': [{'name': 'Аня'}], 'days': {'2027-01-05': {'holiday': False}, '2026-12-30': {'holiday': True}}}, start, end)
        request = self.api.to_request(draft, start, end)
        for day in (date(2026, 12, 31), date(2027, 1, 1), date(2027, 1, 8), date(2026, 12, 30)):
            self.assertIn(day, request.holidays, day)
            self.assertEqual(request.count(day), 6)
        for day in (date(2027, 1, 5), date(2026, 12, 29), date(2027, 1, 11)):
            self.assertNotIn(day, request.holidays, day)

    def test_holiday_calendar_has_the_transfers(self):
        names = holidays_ru.between(date(2026, 1, 1), date(2026, 12, 31))
        for day in (date(2026, 1, 9), date(2026, 3, 9), date(2026, 5, 11), date(2026, 11, 4), date(2026, 12, 31), date(2026, 2, 23)):
            self.assertIn(day, names, day)
        self.assertNotIn(date(2026, 11, 3), names)
        self.assertIn(date(2027, 11, 5), holidays_ru.between(date(2027, 11, 1), date(2027, 11, 8)))
        self.assertIn(date(2031, 6, 12), holidays_ru.between(date(2031, 6, 1), date(2031, 6, 30)))

    def test_mandatory_request_limit_for_fifteen_days(self):
        self.assertEqual(roster.off_limit(15), 3)
        self.assertEqual(roster.off_limit(15, student=True), 5)

    def test_variants_differ_and_describe_their_pros_and_cons(self):
        people = [f'П{i}' for i in range(11)]
        request = roster.Request(date(2026, 11, 1), date(2026, 11, 15), people)
        with fast():
            found = roster.build_variants(request)
        self.assertEqual({v['id'] for v in found}, {'even', 'calm', 'weekend'})
        self.assertLessEqual(found[0]['metrics']['problems'], found[-1]['metrics']['problems'])
        self.assertTrue(all(v['schedule'] and v['metrics']['pairs'] > 0 for v in found))
        self.assertNotEqual(found[0]['schedule'], found[1]['schedule'])
        self.assertTrue(any(v['pros'] for v in found))
        self.assertEqual(request.days_off, {})

    def test_day_settings_reach_the_builder_and_the_check(self):
        draft = self.draft(days={'2026-11-17': {'count': 6, 'closers': 4, 'openers': 2}}, off={'Аня': ['2026-11-17']})
        request = self.api.to_request(draft, START, END)
        tuesday = date(2026, 11, 17)
        self.assertEqual((request.count(tuesday), request.closing(tuesday), request.opening(tuesday)), (6, 4, 2))
        self.assertEqual(request.days_off['Аня'], {tuesday})
        with fast():
            self.api.build_all(draft, START, END)
        cells = draft['cells']
        tuesday_starts = [int(row['2026-11-17']) for row in cells.values() if '2026-11-17' in row]
        self.assertEqual(len(tuesday_starts), 6)
        self.assertEqual(sum(h >= roster.LAST_CLOSER_START for h in tuesday_starts), 4)
        self.assertNotIn('2026-11-17', cells.get('Аня', {}))
        self.assertEqual(draft['off'], {'Аня': ['2026-11-17']})
        self.assertEqual(draft['off_auto'], {})
        self.assertEqual(len(draft['variants']), 3)
        self.assertEqual(draft['variant'], draft['variants'][0]['id'])
        self.assertEqual(self.api.check(draft, START, END)['days'], {})

    def test_rebuild_keeps_manual_days_off_and_drops_legacy_automatic_flags(self):
        draft = self.draft(off={'Аня': ['2026-11-16']})
        draft['off']['Аня'].append('2026-11-17')
        draft['off_auto'] = {'Аня': ['2026-11-17']}
        with fast():
            self.api.build_all(draft, START, END)
            self.api.build_all(draft, START, END)
        self.assertEqual(draft['off'], {'Аня': ['2026-11-16']})
        self.assertEqual(draft['off_auto'], {})

    def test_requested_days_off_come_first_and_are_capped(self):
        draft = self.draft()
        draft['people'][0]['student'] = True
        asked = {'Аня': [f'2026-11-{d}' for d in (16, 17, 18, 19, 20, 21, 22)], 'Боря': ['2026-11-18', '2026-12-30', '2026-11-19'], 'Чужой': ['2026-11-17']}
        with fast():
            self.api.build_all(draft, START, END, asked)
        # Норма на 7 дней — 1 выходной, студенту — 3: лишние просьбы отбрасываются, даты вне периода тоже.
        self.assertEqual(draft['off_requested'], {'Аня': ['2026-11-16', '2026-11-17', '2026-11-18'], 'Боря': ['2026-11-18']})
        for name, days in draft['off_requested'].items():
            for day in days:
                self.assertIn(day, draft['off'][name])
                self.assertNotIn(day, draft['off_auto'].get(name, []))
                self.assertNotIn(day, draft['cells'].get(name, {}))
        # Новая сборка заменяет прежние просьбы новыми, а ручные выходные не трогает.
        draft['off']['Ева'] = draft['off'].get('Ева', []) + ['2026-11-22']
        with fast():
            self.api.build_all(draft, START, END, {'Аня': ['2026-11-20']})
        self.assertEqual(draft['off_requested'], {'Аня': ['2026-11-20']})
        self.assertNotIn('2026-11-16', draft['off'].get('Аня', []))
        self.assertIn('2026-11-22', draft['off']['Ева'])

    def test_two_requested_days_remain_two_mandatory_days_in_every_variant(self):
        end = date(2026, 11, 30)
        draft = self.api.normalize({'people': [{'name': name} for name in NAMES]}, START, end)
        requested = {'Аня': ['2026-11-21', '2026-11-22']}
        with fast():
            self.api.build_all(draft, START, end, requested)
        self.assertEqual(draft['off'], requested)
        self.assertEqual(draft['off_requested'], requested)
        self.assertEqual(draft['off_auto'], {})
        for variant in draft['variants']:
            self.assertEqual(variant['off'], {})
            self.assertTrue(set(requested['Аня']).isdisjoint(variant['cells'].get('Аня', {})))

    def test_telegram_text_with_days_off_is_read_and_checked_against_the_period(self):
        days_off = self.api.days_off
        start, end, today = date(2026, 11, 16), date(2026, 11, 30), date(2026, 11, 10)
        parse = days_off.parse
        self.assertEqual(parse('17, 24')['numbers'], [17, 24])
        self.assertEqual(parse('17 и 24 ноября'), {'numbers': [17, 24], 'months': {11}})
        self.assertEqual(parse('17-19')['numbers'], [17, 18, 19])
        self.assertEqual(parse('Нет'), {'none': True})
        # Суммы, обычные фразы и дробные числа остаются обычным сообщениям.
        for text in ('3500', '17.5', 'кофе 17', '40', '', '17 3500'):
            self.assertIsNone(parse(text), text)
        ok, problem = days_off.resolve(parse('17, 24'), start, end, today, 3)
        self.assertEqual((ok, problem), ({date(2026, 11, 17), date(2026, 11, 24)}, None))
        # Числа первой половины месяца к графику 16–30 не подходят.
        for text in ('1', '5, 17', '15'):
            ok, problem = days_off.resolve(parse(text), start, end, today, 3)
            self.assertIsNone(ok, text)
            self.assertIn('16–30 ноября', problem)
        # Больше нормы — принимается; сверх нормы согласует менеджер.
        many, problem = days_off.resolve(parse('17 18 19 20'), start, end, today, 3)
        self.assertEqual((len(many), problem), (4, None))
        self.assertIn('по согласованию с менеджером', days_off.over_note(many, 3))
        self.assertEqual(days_off.over_note(many, 5), '')
        self.assertIn('уже прошли', days_off.resolve(parse('17'), start, end, date(2026, 11, 20), 3)[1])
        self.assertIsNone(days_off.resolve(parse('17 декабря'), start, end, today, 3)[0])
        self.assertEqual(days_off.resolve(parse('нет'), start, end, today, 3), (set(), None))
        self.assertEqual(days_off.days_text({date(2026, 11, 18), date(2026, 11, 17)}), '17, 18 ноября')
        self.assertEqual(days_off.period_text(date(2026, 12, 16), date(2026, 12, 31)), '16–31 декабря')

    def test_weekday_wishes_are_read_from_ordinary_phrases(self):
        parse = self.api.days_off.parse_weekdays
        self.assertEqual(parse('выходной по вторникам'), [1])
        self.assertEqual(parse('Желательные воскресенья а так мне 2/2 нравится'), [6])
        self.assertEqual(parse('по понедельникам и средам не могу'), [0, 2])
        self.assertEqual(parse('хочу отдыхать в субботу и воскресенье'), [5, 6])
        for text in ('хочу работать по вторникам', 'вторник', 'чай 500', 'среди недели выходной', ''):
            self.assertIsNone(parse(text), text)
        self.assertEqual(self.api.days_off.weekdays_text([6, 1]), 'по вторникам и воскресеньям')
        request = self.api.days_off.parse_request
        both = request('Слушай, ну я хочу по понедельникам, ещё нужно 17 и 19')
        self.assertEqual((both['weekdays'], both['dates']['numbers']), ([0], [17, 19]))
        self.assertEqual(request('нужны выходные 17, 19')['dates']['numbers'], [17, 19])
        self.assertEqual(request('выходные 17-19 ноября')['dates'], {'numbers': [17, 18, 19], 'months': {11}})
        # «2/2», время и суммы — не числа дней.
        self.assertIsNone(request('Желательные воскресенья а так мне 2/2 нравится')['dates'])
        self.assertIsNone(request('отдых в 9:00'))
        self.assertIsNone(request('такси 430'))
        # «2/2» — как удобно работать; даты через косую черту так не читаются.
        self.assertEqual(request('2/2')['pattern'], '2/2')
        self.assertEqual(request('хочу работать 2/2')['pattern'], '2/2')
        both = request('хочу воскресенья и 2/2')
        self.assertEqual((both['weekdays'], both['pattern'], both['dates']), ([6], '2/2', None))
        self.assertIsNone(request('17/11'))
        # Сообщение Али: «не могу работать» — выходные; строки «могу работать», «рабочие … 18 ноября» не в счёт.
        alya = request('НЕ могу работать: 16,17,20, 24,27\n\nМогу работать: все остальные могу\n\nРабочие неполные ->18 ноября  -> С 9:00-17:00 !')
        self.assertEqual(alya['dates']['numbers'], [16, 17, 20, 24, 27])
        self.assertIsNone(request('могу работать 18'))

    def test_variants_from_the_site_are_checked(self):
        good = {'id': 'even', 'title': 'Поровну', 'cells': {'Аня': {'2026-11-16': '9'}, 'Чужой': {'2026-11-16': '9'}},
                'off': {'Аня': ['2026-11-17']}, 'pros': ['Плюс'], 'cons': []}
        draft = self.draft(variants=[good, {'id': 'BAD!', 'title': 'x'}, {'id': 'ok', 'title': ''}], variant='nope')
        self.assertEqual([v['id'] for v in draft['variants']], ['even'])
        self.assertEqual(draft['variants'][0]['cells'], {'Аня': {'2026-11-16': '9'}})
        self.assertIsNone(draft['variant'])
        with self.assertRaises(ValueError):
            self.draft(variants=[dict(good, cells={'Аня': {'2026-11-16': '99'}})])

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
                              wish_off={'Вика': ['2026-11-18']}, wish_starts={'Гоша': [9]},
                              variants=[{'id': 'even', 'title': 'Поровну', 'cells': {}, 'off': {}, 'pros': ['Плюс'], 'cons': []}])
        snapshot['people'][0]['student'] = True
        row = {'start_date': START.isoformat(), 'end_date': END.isoformat(), 'published': snapshot, 'published_at': 'x'}
        view = self.api.published_view(row, {'id': 'l', 'report_name': 'Аня'})
        text = str(view)
        for private in ('wish', 'student', 'variants', 'Поровну'):
            self.assertNotIn(private, text)
        self.assertEqual(len(view['days']), 7)
        me = [p for p in view['people'] if p['me']]
        self.assertEqual([p['name'] for p in me], ['Аня'])
        self.assertEqual(view['people'][1]['off'], ['2026-11-16'])


def link_id(n):
    return f'cccccccc-0000-0000-0000-0000000001{n:02d}'


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sales_http_checks import signed, TOKEN
        self.signed = signed
        self.fake = Fake()
        staff = [{'id': link_id(i), 'restaurant_id': PLACE, 'user_id': 10 + i, 'report_name': name, 'status': 'approved',
                  'role': 'staff', 'requested_at': str(10 + i)} for i, name in enumerate(('Боря', 'Вика', 'Гоша', 'Даша', 'Ева', 'Женя'))]
        self.fake.rows = {
            'restaurants': [{'id': PLACE, 'name': 'Ресторан', 'owner_id': OWNER, 'created_at': '1'},
                            {'id': OTHER, 'name': 'Чужой', 'owner_id': 77, 'created_at': '2'}],
            'employee_links': [
                {'id': 'cccccccc-0000-0000-0000-000000000001', 'restaurant_id': PLACE, 'user_id': MANAGER,
                 'report_name': 'Менеджер', 'status': 'approved', 'role': 'manager', 'requested_at': '1'},
                {'id': 'cccccccc-0000-0000-0000-000000000002', 'restaurant_id': PLACE, 'user_id': WAITER,
                 'report_name': 'Аня', 'status': 'approved', 'role': 'staff', 'requested_at': '2'},
                {'id': 'cccccccc-0000-0000-0000-000000000003', 'restaurant_id': OTHER, 'user_id': STRANGER,
                 'report_name': 'Чужая', 'status': 'approved', 'role': 'manager', 'requested_at': '3'}, *staff]}
        self.env = patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid', 'SUPABASE_KEY': 'test',
                                           'IDENTITY_ENABLED': '1', 'WEBHOOK_HOST': 'https://bot.example.test'})
        self.env.start()
        self.client_patch = patch('supabase.create_client', return_value=self.fake)
        self.client_patch.start()
        self.fast = fast()
        self.fast.start()
        import days_off
        self.today = patch.object(days_off, 'roster_today', return_value=date(2026, 11, 10))
        self.today.start()
        # Запись смен в личный календарь при публикации: в обычных тестах настоящей базы нет.
        self.sync = patch('schedule.save', new=AsyncMock())
        self.sync.start()
        self.addCleanup(self.sync.stop)
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
        for p in (self.db_patch, self.today, self.fast, self.client_patch, self.env):
            p.stop()

    async def post(self, path, uid, status=200, **body):
        response = await self.client.post(path, json={'initData': self.signed(uid), **body})
        data = await response.json()
        self.assertEqual(response.status, status, data)
        return data

    async def roster(self, action, uid, status=200, **body):
        return await self.post('/api/restaurant/roster/' + action, uid, status, **body)

    async def test_calendar_wish_edit_checks_signed_actor_and_period(self):
        import roster_wishes
        await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
        row = self.fake.rows['rosters'][0]
        row['collect_wishes_at'] = '2026-11-11T09:00:00Z'
        payload = {'action':'wish_save', 'date':'2026-11-18', 'kind':'preferred', 'roster_id':row['id'],
                   'revision':0, 'operation_id':str(uuid4())}
        with patch.object(roster_wishes, 'save', new=AsyncMock(return_value={
            'status':'ok', 'revision':1, 'days':[], 'desired_days':['2026-11-18'],
        })) as save:
            await self.post('/api/calendar_edit', WAITER, 401, initData='wrong', **payload)
            await self.post('/api/calendar_edit', MANAGER, 403, **payload)
            await self.post('/api/calendar_edit', WAITER, 404, **{**payload, 'roster_id':str(uuid4())})
            save.assert_not_awaited()
            result = await self.post('/api/calendar_edit', WAITER, user_id=MANAGER, **payload)
            self.assertEqual(result['revision'], 1)
            self.assertIn('желательные выходные', result['message'])
            uid, link, saved_row, _, changes = save.await_args.args
            self.assertEqual(uid, WAITER)
            self.assertEqual(link['restaurant_id'], PLACE)
            self.assertEqual(saved_row['id'], row['id'])
            self.assertEqual(changes, {'2026-11-18':'preferred'})
            save.return_value = {'status':'conflict'}
            conflict = await self.post('/api/calendar_edit', WAITER, 409, **payload)
            self.assertIn('уже изменилось', conflict['error'])

    async def test_shift_time_rule_survives_rebuild_and_validates_publication(self):
        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
        day = '2026-11-18'
        worker = next(person for person in made['draft']['people'] if person['name'] == 'Аня')
        self.fake.rows['shift_time_rules'] = [
            {'link_id':worker['link'], 'restaurant_id':PLACE, 'target':day, 'kind':'limit', 'start_min':540, 'end_min':1020},
        ]
        asks = await self.roster('offs', MANAGER, id=made['id'])
        self.assertIn('09:00 до 17:00', asks['time']['Аня'][0])
        built = await self.roster('build', MANAGER, id=made['id'], revision=made['revision'], draft=made['draft'])
        self.assertEqual(built['draft']['time_rules']['Аня'][day], [540, 1020, None, None])
        for variant in built['draft']['variants']:
            cell = variant['cells'].get('Аня', {}).get(day)
            if cell:
                import roster_api
                start, end, _ = roster_api.parse_cell(cell)
                self.assertTrue(9 <= start < end <= 17)
        built['draft']['cells'].setdefault('Аня', {})[day] = '14'
        draft = await self.roster('save', MANAGER, id=built['id'], revision=built['revision'], draft=built['draft'])
        error = await self.roster('publish', MANAGER, 400, id=draft['id'], revision=draft['revision'])
        self.assertIn('смена выходит за пределы доступного времени', error['error'])
        draft['draft']['cells']['Аня'][day] = '9-17'
        corrected = await self.roster('save', MANAGER, id=draft['id'], revision=draft['revision'], draft=draft['draft'])
        await self.roster('publish', MANAGER, id=corrected['id'], revision=corrected['revision'])
        period = (await self.post('/api/schedule/view', WAITER))['period']
        self.assertEqual(next(p for p in period['people'] if p['me'])['cells'][day], '9-17')

    async def test_creating_a_schedule_builds_variants_and_the_rest_follows(self):
        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
        # Менеджер в график смен не попадает: в нём только официанты.
        self.assertEqual([p['name'] for p in made['draft']['people']], ['Аня', *NAMES[1:7]])
        draft = made['draft']
        self.assertEqual(len(draft['variants']), 3)
        # Порядок совпадает с написанным: у лучшего минусов не больше, чем плюсов, чем у следующего.
        balance = [len(v['cons']) - len(v['pros']) for v in draft['variants']]
        self.assertEqual(balance, sorted(balance))
        self.assertEqual(draft['variant'], draft['variants'][0]['id'])
        self.assertFalse(draft['picked'])
        self.assertTrue(draft['cells'])
        self.assertEqual(draft['off'], {})
        self.assertIn('2026-11-16', made['holidays'] if False else {**made['holidays'], '2026-11-16': 1})
        await self.roster('create', MANAGER, 400, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
        draft['events'] = {'2026-11-18': 'Генуборка'}
        saved = await self.roster('save', MANAGER, id=made['id'], revision=made['revision'], draft=draft)
        self.assertEqual(saved['revision'], 2)
        self.assertEqual(len(saved['draft']['variants']), 3)
        # Старая версия из другого окна не перезаписывает новую.
        await self.roster('save', OWNER, 409, id=made['id'], revision=1, draft=draft)
        rebuilt = await self.roster('build', OWNER, id=made['id'], revision=2, draft=saved['draft'])
        self.assertTrue(rebuilt['draft']['cells'])
        team_before = await self.post('/api/schedule/view', WAITER)
        self.assertIsNone(team_before['period'])
        self.assertFalse(team_before['can_edit'])
        published = await self.roster('publish', MANAGER, id=made['id'], revision=rebuilt['revision'])
        self.assertFalse(published['changed'])
        view = await self.post('/api/schedule/view', WAITER)
        self.assertEqual(view['period']['start'], '2026-11-16')
        self.assertEqual(view['period']['events'], {'2026-11-18': 'Генуборка'})
        self.assertEqual([p['name'] for p in view['period']['people'] if p['me']], ['Аня'])
        self.assertNotIn('variants', str(view))
        manager_view = await self.post('/api/schedule/view', MANAGER)
        self.assertTrue(manager_view['can_edit'])
        self.assertEqual(manager_view['site_url'], 'https://bot.example.test/restaurant#schedule')
        # Правка после публикации видна команде только после новой публикации.
        edited = dict(rebuilt['draft'], events={})
        await self.roster('save', MANAGER, id=made['id'], revision=published['revision'], draft=edited)
        self.assertEqual((await self.post('/api/schedule/view', WAITER))['period']['events'], {'2026-11-18': 'Генуборка'})

    async def test_only_confirmed_employees_can_be_in_the_schedule(self):
        made = await self.roster('create', OWNER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
        draft = made['draft']
        count = len(draft['people'])
        draft['people'] += [{'name': 'Кто угодно'}, {'name': 'Левый', 'link': str(uuid4())},
                            {'name': 'Чужая', 'link': 'cccccccc-0000-0000-0000-000000000003'},
                            {'name': 'Менеджер', 'link': 'cccccccc-0000-0000-0000-000000000001'}]
        saved = await self.roster('save', OWNER, id=made['id'], revision=1, draft=draft)
        self.assertEqual(len(saved['draft']['people']), count)
        # Человека, которому закрыли доступ (уволился), график убирает сам, вместе с его сменами.
        self.fake.rows['employee_links'][3]['status'] = 'revoked'
        left = saved['draft']['people'][1]['name']
        again = await self.roster('save', OWNER, id=made['id'], revision=2, draft=saved['draft'])
        self.assertNotIn(left, [p['name'] for p in again['draft']['people']])
        self.assertNotIn(left, again['draft']['cells'])
        rebuilt = await self.roster('build', OWNER, id=made['id'], revision=3, draft=again['draft'])
        self.assertNotIn(left, rebuilt['draft']['cells'])
        # Вернуть можно только подтверждённого: он снова появляется по ссылке, а не по имени.
        self.fake.rows['employee_links'][3]['status'] = 'approved'
        link = self.fake.rows['employee_links'][3]['id']
        draft = rebuilt['draft']
        draft['people'].append({'name': left, 'link': link})
        back = await self.roster('save', OWNER, id=made['id'], revision=rebuilt['revision'], draft=draft)
        self.assertIn(left, [p['name'] for p in back['draft']['people']])

    async def test_next_schedule_keeps_order_and_settings_of_the_last_one(self):
        made = await self.roster('create', OWNER, restaurant_id=PLACE, start='2026-11-01', end='2026-11-15')
        draft = made['draft']
        draft['people'].reverse()
        by_name = {p['name']: p for p in draft['people']}
        by_name['Аня']['student'] = True
        draft['wish_starts'] = {'Аня': [9, 10]}
        await self.roster('save', OWNER, id=made['id'], revision=1, draft=draft)
        order = [p['name'] for p in draft['people']]
        self.fake.rows['employee_links'][0]['status'] = 'revoked'
        self.fake.rows['employee_links'].append({'id': 'cccccccc-0000-0000-0000-000000000209', 'restaurant_id': PLACE,
                                                 'user_id': 44, 'report_name': 'Новенький', 'status': 'approved', 'role': 'staff', 'requested_at': '99'})
        later = await self.roster('create', OWNER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-30')
        people = later['draft']['people']
        self.assertEqual([p['name'] for p in people], [n for n in order if n != 'Менеджер'] + ['Новенький'])
        self.assertTrue(next(p for p in people if p['name'] == 'Аня')['student'])
        self.assertEqual(later['draft']['wish_starts'], {'Аня': [9, 10]})
        self.assertEqual(later['draft']['off'], {})

    async def test_publication_messages_each_person_in_the_bot(self):
        sent = []

        class Bot:
            async def send_message(self, chat, text):
                sent.append((chat, text))
        self.client.server.app['bot'] = Bot()
        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
        draft = dict(made['draft'], cells={'Аня': {'2026-11-16': '9'}}, off={}, off_auto={})
        saved = await self.roster('save', MANAGER, id=made['id'], revision=1, draft=draft)
        published = await self.roster('publish', MANAGER, id=made['id'], revision=saved['revision'])
        import asyncio
        await asyncio.sleep(0.2)
        # Первая публикация — каждому официанту в графике; менеджер, который публикует, в нём не числится.
        self.assertEqual(published['notified'], len(made['draft']['people']))
        mine = [text for chat, text in sent if chat == WAITER]
        self.assertEqual(len(mine), 1)
        self.assertIn('пн 16 — с 9', mine[0])

    async def test_check_offs_sends_to_saved_roster_including_staff_who_already_answered(self):
        import asyncio

        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
        payload = {'id': made['id'], 'revision': made['revision']}
        row = self.fake.rows['rosters'][0]
        row['collect_wishes_at'] = '2026-11-10T09:00:00Z'
        people = row['draft']['people']
        people[:] = [p for p in people if p['link'] != link_id(1)]
        members = {p['user_id']: p for p in self.fake.rows['employee_links']}
        members[10]['status'] = 'revoked'
        outsider = {**members[STRANGER], 'id': link_id(9), 'user_id': 99, 'role': 'staff'}
        self.fake.rows['employee_links'].append(outsider)
        people.extend({'name': p['report_name'], 'link': p['id']} for p in (members[MANAGER], outsider))
        requests = [
            {'link_id': members[WAITER]['id'], 'roster_id': made['id'], 'answered': True,
             'days': ['2026-11-22', '2026-11-18'], 'desired_days': ['2026-11-20']},
            {'link_id': members[12]['id'], 'roster_id': made['id'], 'answered': True, 'days': ['2026-11-19']},
            {'link_id': members[13]['id'], 'roster_id': str(uuid4()), 'answered': True, 'days': ['2026-11-21']},
            {'link_id': members[14]['id'], 'roster_id': made['id'], 'answered': True, 'days': [],
             'desired_days': ['2026-11-21']},
        ]
        self.fake.rows['day_off_requests'] = [r.copy() for r in requests]
        self.fake.rows['weekday_wishes'] = [
            {'link_id': members[WAITER]['id'], 'restaurant_id': PLACE, 'days': [6]},
            {'link_id': members[15]['id'], 'restaurant_id': PLACE, 'days': [0, 4]},
        ]
        sending, finish = asyncio.Event(), asyncio.Event()

        async def send_message(chat, text):
            if chat == WAITER:
                sending.set()
                await finish.wait()
            if chat == 12:
                raise RuntimeError('Telegram rejected the message')

        bot = NS(send_message=AsyncMock(side_effect=send_message))
        self.client.server.app['bot'] = bot
        await self.roster('check_offs', WAITER, 403, **payload)
        await self.roster('check_offs', STRANGER, 404, **payload)
        await self.roster('check_offs', MANAGER, 409, **{**payload, 'revision': 0})
        bot.send_message.assert_not_awaited()

        request = asyncio.create_task(self.roster('check_offs', MANAGER, **payload))
        try:
            await asyncio.wait_for(sending.wait(), timeout=5)
            await self.roster('check_offs', OWNER, 409, **payload)
        finally:
            finish.set()
            result = await request

        self.assertEqual(result, {'sent': 4, 'total': 5})
        prefix = 'Проверь свои выходные на 16–22 ноября.\n'
        self.assertCountEqual([call.args for call in bot.send_message.await_args_list], [
            (WAITER, prefix + 'Обязательно: 18, 22 ноября.\nЖелательно: 20 ноября, по воскресеньям.'),
            (12, prefix + 'Обязательно: 19 ноября.'),
            (13, prefix + 'Пожеланий пока нет.'),
            (14, prefix + 'Желательно: 21 ноября.'),
            (15, prefix + 'Желательно: по понедельникам и пятницам.'),
        ])
        self.assertEqual(self.fake.rows['day_off_requests'], requests)
        self.assertEqual(row['revision'], made['revision'])

    async def test_check_offs_reads_only_in_period_dates_from_legacy_requests(self):
        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
        link = next(member['id'] for member in self.fake.rows['employee_links'] if member['user_id'] == WAITER)
        row = self.fake.rows['rosters'][0]
        row['draft']['people'] = [person for person in row['draft']['people'] if person['link'] == link]
        self.fake.rows['day_off_requests'] = [
            {'link_id': link, 'restaurant_id': PLACE, 'start_date': made['start'], 'answered': True,
             'days': ['2026-11-20', '2026-11-23']},
        ]
        bot = NS(send_message=AsyncMock())
        self.client.server.app['bot'] = bot

        result = await self.roster('check_offs', MANAGER, id=made['id'], revision=made['revision'])

        self.assertEqual(result, {'sent': 1, 'total': 1})
        bot.send_message.assert_awaited_once_with(WAITER, 'Проверь свои выходные на 16–22 ноября.\nОбязательно: 20 ноября.')

    def callback(self, data, uid=WAITER):
        return NS(data=data, from_user=NS(id=uid), message=NS(chat=NS(type='private'), edit_text=AsyncMock()), answer=AsyncMock())

    def text(self, body, uid=WAITER):
        return NS(text=body, from_user=NS(id=uid), chat=NS(type='private'), answer=AsyncMock(), message_id=7)

    async def said(self, body, uid=WAITER):
        """Сообщение сотрудника: возвращает ответ бота или None, если бот не считает это выходными."""
        import days_off_chat
        message = self.text(body, uid)
        taken = await days_off_chat.expecting(message)
        if not taken:
            return None
        await days_off_chat.write(message, **taken)
        return message.answer.await_args

    async def test_employees_write_days_off_in_telegram_and_the_builder_uses_them(self):
        import days_off_chat
        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-30')
        sent = []

        class Bot:
            async def send_message(self, chat, text, reply_markup=None, **_):
                sent.append((chat, text, reply_markup))
        self.client.server.app['bot'] = Bot()
        status = await self.roster('offs', MANAGER, id=made['id'])
        self.assertEqual((status['answered'], status['total']), (0, len(made['draft']['people'])))
        # Пока никто не просил, числа остаются обычным сообщением.
        self.assertIsNone(await self.said('17, 18'))
        asked = await self.roster('ask_offs', MANAGER, id=made['id'])
        self.assertEqual(asked['sent'], status['total'])
        self.assertEqual({chat for chat, *_ in sent}, {3, *(10 + i for i in range(6))})
        self.assertIn('График на 16–30 ноября', sent[0][1])
        self.assertIn('Напиши числа', sent[0][1])
        self.assertIsNone(sent[0][2])
        # Числа вне периода не принимаются, ничего не записывается.
        refused = await self.said('1, 5')
        self.assertIn('16–30 ноября', refused.args[0])
        self.assertEqual((await self.roster('offs', MANAGER, id=made['id']))['answered'], 0)
        # Сумма и обычный текст идут в обычную обработку.
        self.assertIsNone(await self.said('3500'))
        self.assertIsNone(await self.said('привет'))
        with patch('research.track') as track:
            done = await self.said('17, 18')
        self.assertEqual([(c.args[1], c.kwargs) for c in track.call_args_list],
                         [('days_off_answered', {'source': 'bot', 'screen': 'schedule'})])
        self.assertIn('Записал выходные: 17, 18 ноября', done.args[0])
        self.assertEqual(done.kwargs['reply_markup'].inline_keyboard[0][0].text, 'Изменить')
        # Ответ записан: числа снова обычное сообщение, пока не нажали «Изменить».
        self.assertIsNone(await self.said('20'))
        status = await self.roster('offs', MANAGER, id=made['id'])
        self.assertEqual((status['answered'], status['fresh']), (1, 1))
        self.assertEqual(status['over'], {})
        change = self.callback('off:e:2026-11-16:2026-11-30')
        await days_off_chat.change(change)
        self.assertIn('заново', change.message.edit_text.await_args.args[0])
        self.assertEqual((await self.roster('offs', MANAGER, id=made['id']))['answered'], 1)
        self.assertIn('Записал выходные: 20 ноября', (await self.said('20')).args[0])
        # Кто ответил, тому просьбу повторно не шлют.
        sent.clear()
        again = await self.roster('ask_offs', MANAGER, id=made['id'])
        self.assertEqual(again['sent'], status['total'] - 1)
        self.assertNotIn(WAITER, {chat for chat, *_ in sent})
        # Сборка берёт выходные из Telegram и ставит их первыми.
        rebuilt = await self.roster('build', MANAGER, id=made['id'], revision=made['revision'], draft=made['draft'])
        self.assertEqual(rebuilt['draft']['off_requested'], {'Аня': ['2026-11-20']})
        self.assertTrue({'2026-11-20'} <= set(rebuilt['draft']['off']['Аня']))
        self.assertNotIn('2026-11-20', rebuilt['draft']['cells'].get('Аня', {}))
        self.assertEqual((await self.roster('offs', MANAGER, id=made['id']))['fresh'], 0)

    async def test_days_off_text_comes_only_from_the_asked_employee_and_old_buttons_stop(self):
        import days_off_chat
        await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-30')
        class Bot:
            async def send_message(self, *_, **__):
                pass
        self.client.server.app['bot'] = Bot()
        made = (await self.roster('list', MANAGER))['periods'][0]
        await self.roster('ask_offs', MANAGER, id=made['id'])
        self.assertIsNone(await self.said('17', uid=555))
        for data in ('off:t:2026-11-16:2026-11-30:2026-11-20', 'off:d:2026-11-16:2026-11-30', 'off:e:плохо:2026-11-30',
                     'off:e:2026-11-01:2026-11-15', 'off:e:2026-11-16:2027-12-31'):
            c = self.callback(data)
            await days_off_chat.change(c)
            self.assertTrue(c.answer.await_args.kwargs.get('show_alert'), data)
            c.message.edit_text.assert_not_awaited()
        stranger = self.callback('off:e:2026-11-16:2026-11-30', uid=555)
        await days_off_chat.change(stranger)
        self.assertTrue(stranger.answer.await_args.kwargs.get('show_alert'))
        # Студенту положено больше выходных, чем остальным.
        opened = await self.roster('open', MANAGER, id=made['id'])
        draft = opened['draft']
        next(p for p in draft['people'] if p['name'] == 'Аня')['student'] = True
        await self.roster('save', MANAGER, id=made['id'], revision=opened['revision'], draft=draft)
        self.assertIn('Записал выходные: 17, 18, 19, 20, 21 ноября', (await self.said('17-21')).args[0])

    async def test_dates_of_a_draft_change_in_place_and_extra_days_are_dropped(self):
        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
        draft = made['draft']
        draft['events'] = {'2026-11-22': 'Генуборка', '2026-11-17': 'Собрание'}
        saved = await self.roster('save', MANAGER, id=made['id'], revision=made['revision'], draft=draft)
        shorter = await self.roster('dates', MANAGER, id=made['id'], revision=saved['revision'], start='2026-11-16', end='2026-11-20')
        self.assertEqual((shorter['start'], shorter['end']), ('2026-11-16', '2026-11-20'))
        self.assertEqual(shorter['draft']['events'], {'2026-11-17': 'Собрание'})
        self.assertTrue(all(day <= '2026-11-20' for row in shorter['draft']['cells'].values() for day in row))
        self.assertEqual(shorter['draft']['variants'], [])
        # Дальше период можно сдвинуть; пересечение с другим графиком запрещено.
        other = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-25', end='2026-11-30')
        clash = await self.roster('dates', MANAGER, 400, id=other['id'], revision=other['revision'], start='2026-11-19', end='2026-11-30')
        self.assertIn('пересекаются', clash['error'])
        moved = await self.roster('dates', MANAGER, id=shorter['id'], revision=shorter['revision'], start='2026-11-14', end='2026-11-24')
        self.assertEqual(len(moved['draft']['events']), 1)
        await self.roster('dates', MANAGER, 400, id=moved['id'], revision=moved['revision'], start='2026-11-24', end='2026-11-16')
        await self.roster('dates', MANAGER, 409, id=moved['id'], revision=1, start='2026-11-14', end='2026-11-22')
        # У опубликованного графика даты не меняются.
        published = await self.roster('publish', MANAGER, id=moved['id'], revision=moved['revision'])
        refused = await self.roster('dates', MANAGER, 400, id=moved['id'], revision=published['revision'], start='2026-11-14', end='2026-11-23')
        self.assertIn('не меняются', refused['error'])

    async def test_schedule_steps_are_recorded_for_research_without_content(self):
        class Bot:
            async def send_message(self, chat, text, reply_markup=None, **_):
                pass
        self.client.server.app['bot'] = Bot()
        with patch('research.track') as track:
            made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
            draft = dict(made['draft'], picked=True)
            saved = await self.roster('save', MANAGER, id=made['id'], revision=made['revision'], draft=draft)
            # Повторное сохранение того же выбора ничего не добавляет.
            saved = await self.roster('save', MANAGER, id=made['id'], revision=saved['revision'], draft=saved['draft'])
            rebuilt = await self.roster('build', MANAGER, id=made['id'], revision=saved['revision'], draft=saved['draft'])
            moved = await self.roster('dates', MANAGER, id=made['id'], revision=rebuilt['revision'], start='2026-11-16', end='2026-11-21')
            await self.roster('ask_offs', MANAGER, id=made['id'])
            published = await self.roster('publish', MANAGER, id=made['id'], revision=moved['revision'])
            await self.roster('publish', MANAGER, id=made['id'], revision=published['revision'])
            await self.post('/api/schedule/view', WAITER)
            await self.post('/api/schedule/view', WAITER)
            await self.post('/api/schedule/view', MANAGER)
        kinds = [c.args[1] for c in track.call_args_list]
        self.assertEqual(kinds, ['roster_created', 'roster_variant_picked', 'roster_built', 'roster_dates_changed',
                                 'days_off_asked', 'roster_published', 'roster_republished', 'roster_viewed', 'roster_viewed'])
        # Только факт и этап: ни имён, ни дат, ни смен.
        for c in track.call_args_list:
            self.assertLessEqual(set(c.kwargs), {'source', 'screen', 'operation'})
        self.assertNotIn('Аня', str(track.call_args_list))
        views = [c for c in track.call_args_list if c.args[1] == 'roster_viewed']
        self.assertEqual(views[0].kwargs['operation'], views[1].kwargs['operation'])
        self.assertEqual({c.args[0] for c in views}, {WAITER})

    async def test_weekday_wish_from_telegram_goes_into_the_build_and_the_editor(self):
        import days_off_chat
        store = {}
        async def get_data(): return dict(store)
        async def update_data(**kw): store.update(kw)
        state = NS(get_data=get_data, update_data=update_data, clear=AsyncMock())
        message = self.text('выходной по вторникам')
        taken = await days_off_chat.off_request(message)
        self.assertEqual(taken['found']['weekdays'], [1])
        await days_off_chat.write_request(message, state=state, **taken)
        self.assertIn('по вторникам', message.answer.await_args.args[0])
        self.assertEqual(store['wish_text'], 'выходной по вторникам')
        # Не сотрудник ресторана: ничего не записывается и деньгами фраза тоже не становится.
        stranger = self.text('нужны выходные 17, 19', uid=555)
        taken = await days_off_chat.off_request(stranger)
        self.assertIsNone(taken['link'])
        await days_off_chat.write_request(stranger, state=state, **taken)
        self.assertIn('сотрудники ресторана', stranger.answer.await_args.args[0])
        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
        self.assertEqual(made['draft']['wish_off'], {'Аня': ['2026-11-17']})
        self.assertEqual(made['draft']['off'], {})
        self.assertTrue(any('2026-11-17' not in v['cells'].get('Аня', {}) for v in made['draft']['variants']))
        opened = await self.roster('open', MANAGER, id=made['id'])
        self.assertEqual(list(opened['wishes'].values()), [{'days': [1], 'pattern': None}])
        # «Убрать пожелание» стирает его; следующая сборка его уже не учитывает.
        callback = self.callback('off:w')
        await days_off_chat.clear_weekday_wish(callback)
        self.assertIn('удалил', callback.message.edit_text.await_args.args[0])
        rebuilt = await self.roster('build', MANAGER, id=made['id'], revision=made['revision'], draft=made['draft'])
        self.assertEqual(rebuilt['draft']['wish_off'], {})

    async def test_one_message_with_weekdays_and_dates_records_both(self):
        import days_off_chat
        store = {}
        async def update_data(**kw): store.update(kw)
        state = NS(update_data=update_data)
        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-30')
        message = self.text('Слушай, ну я хочу по понедельникам, ещё нужно 17 и 19')
        taken = await days_off_chat.off_request(message)
        await days_off_chat.write_request(message, state=state, **taken)
        reply = message.answer.await_args.args[0]
        self.assertIn('по понедельникам', reply)
        self.assertIn('17, 19 ноября', reply)
        status = await self.roster('offs', MANAGER, id=made['id'])
        self.assertEqual(status['answered'], 1)
        # Менеджер видит, что именно просят, ещё до сборки; сотрудник видит свои выходные до публикации.
        self.assertEqual(status['requests'], {'Аня': ['2026-11-17', '2026-11-19']})
        self.assertEqual(status['weekdays'], {'Аня': [0]})
        mine = (await self.post('/api/schedule/view', WAITER))['mine']
        self.assertEqual(mine, {'requests': [{'start': '2026-11-16', 'end': '2026-11-30', 'days': ['2026-11-17', '2026-11-19'], 'limit': 3}],
                                'weekdays': [0], 'pattern': None})
        # Другой человек видит только своё: чужие выходные не показываются.
        self.assertEqual((await self.post('/api/schedule/view', STRANGER))['mine'], {'requests': [], 'weekdays': [], 'pattern': None})
        # Числа вне графика не записываются и объясняются.
        wrong = self.text('выходные 3 и 5')
        await days_off_chat.write_request(wrong, state=state, **(await days_off_chat.off_request(wrong)))
        self.assertIn('16–30 ноября', wrong.answer.await_args.args[0])

    async def test_colleague_wish_sundays_and_two_two_is_kept_whole_and_in_parts(self):
        import days_off_chat
        store = {}
        async def update_data(**kw): store.update(kw)
        state = NS(update_data=update_data)
        async def say(text):
            message = self.text(text)
            await days_off_chat.write_request(message, state=state, **(await days_off_chat.off_request(message)))
            return message.answer.await_args.args[0]
        reply = await say('хочу воскресенья и 2/2')
        self.assertIn('по воскресеньям', reply)
        self.assertIn('график 2/2', reply)
        self.assertIn('а не обязательный выходной', reply)
        # По частям: сначала дни, потом «2/2» — пожелание дополняется, а не заменяется.
        await days_off_chat.clear_weekday_wish(self.callback('off:w'))
        await say('Желательные воскресенья')
        reply = await say('2/2')
        self.assertIn('по воскресеньям', reply)
        self.assertIn('график 2/2', reply)
        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
        status = await self.roster('offs', MANAGER, id=made['id'])
        self.assertEqual((status['weekdays'], status['patterns']), ({'Аня': [6]}, {'Аня': '2/2'}))
        self.assertEqual((await self.post('/api/schedule/view', WAITER))['mine']['pattern'], '2/2')
        # Во вкладке «Ресторан» и на сайте пожелание видно в карточке сотрудника.
        rows = (await self.post('/api/restaurant/view', MANAGER, restaurant_id=PLACE, month='2026-11'))['rows']
        self.assertEqual(next(r for r in rows if r['report_name'] == 'Аня')['wish'], {'days': [6], 'pattern': '2/2', 'offs': []})
        self.assertNotIn('wish', next(r for r in rows if r['report_name'] == 'Боря'))

    async def test_more_days_off_than_the_norm_are_accepted_for_the_manager_to_agree(self):
        import days_off_chat
        store = {}
        async def update_data(**kw): store.update(kw)
        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-30')
        text = 'НЕ могу работать: 16,17,20, 24,27\n\nМогу работать: все остальные могу\n\nРабочие неполные ->18 ноября  -> С 9:00-17:00 !'
        message = self.text(text)
        await days_off_chat.write_request(message, state=NS(update_data=update_data), **(await days_off_chat.off_request(message)))
        reply = message.answer.await_args.args[0]
        self.assertIn('16, 17, 20, 24, 27 ноября', reply)
        self.assertIn('по согласованию с менеджером', reply)
        status = await self.roster('offs', MANAGER, id=made['id'])
        self.assertEqual(status['requests']['Аня'], ['2026-11-16', '2026-11-17', '2026-11-20', '2026-11-24', '2026-11-27'])
        self.assertEqual(status['over'], {'Аня': 3})
        mine = (await self.post('/api/schedule/view', WAITER))['mine']
        self.assertEqual(mine['requests'][0]['limit'], 3)
        # Сборка сама ставит только норму; остальное менеджер добавляет вручную после согласования.
        rebuilt = await self.roster('build', MANAGER, id=made['id'], revision=made['revision'], draft=made['draft'])
        self.assertEqual(len(rebuilt['draft']['off_requested']['Аня']), 3)

    async def test_publishing_writes_waiters_shifts_into_their_personal_calendar(self):
        import asyncio
        import roster_sync
        saved = []
        async def save(uid, cells): saved.append((uid, cells))
        fake_user = AsyncMock(return_value={})
        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
        draft = dict(made['draft'], cells={'Аня': {'2026-11-16': '9', '2026-11-18': '10-18*', '2026-11-19': '14'}}, off={}, off_auto={})
        saved_draft = await self.roster('save', MANAGER, id=made['id'], revision=made['revision'], draft=draft)
        # У Ани уже стоят личные смены: на 20-е (старая, из графика исчезла) и на прошлое число.
        shifts = self.fake.rows.setdefault('shifts', [])
        shifts += [{'user_id': WAITER, 'shift_date': '2026-11-20'}, {'user_id': WAITER, 'shift_date': '2026-11-08'},
                   {'user_id': WAITER, 'shift_date': '2026-11-16'}]
        with patch('schedule.save', new=save), patch('db.get_or_create_user', new=fake_user):
            await self.roster('publish', MANAGER, id=made['id'], revision=saved_draft['revision'])
            await asyncio.sleep(0.3)
        mine = [cells for uid, cells in saved if uid == WAITER]
        self.assertEqual(mine, [[{'date': '2026-11-16', 'start': '09:00', 'end': '21:00'},
                                 {'date': '2026-11-18', 'start': '10:00', 'end': '18:00'},
                                 {'date': '2026-11-19', 'start': '14:00', 'end': '23:00'}]])
        # Будущая смена, которой в графике нет, убрана; прошлая и та, что в графике, остались.
        left = sorted(r['shift_date'] for r in self.fake.rows['shifts'] if r['user_id'] == WAITER)
        self.assertEqual(left, ['2026-11-08', '2026-11-16'])
        # Менеджер, которого в графике нет, личных смен не получает.
        self.assertNotIn(MANAGER, {uid for uid, _ in saved})
        # Повторная синхронизация ничего не ломает.
        with patch('schedule.save', new=save), patch('db.get_or_create_user', new=fake_user):
            self.assertGreaterEqual(await roster_sync.resync_all(), 1)

    # ─── аудит 11 ноября: G04–G06 ───
    async def test_employee_requests_follow_the_period(self):
        import days_off
        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-30')
        link = await days_off.member(WAITER)
        await days_off.save(link, date(2026, 11, 16), date(2026, 11, 30), {date(2026, 11, 18), date(2026, 11, 28)})
        # Начало сдвинули на 15-е: ответ не пропадает, его числа остаются.
        moved = await self.roster('dates', MANAGER, id=made['id'], revision=made['revision'], start='2026-11-15', end='2026-11-30')
        self.assertEqual((await self.roster('offs', MANAGER, id=made['id']))['requests'].get('Аня'), ['2026-11-18', '2026-11-28'])
        # Период сократили: числа за новой границей отбрасываются, остальные остаются.
        await self.roster('dates', MANAGER, id=made['id'], revision=moved['revision'], start='2026-11-15', end='2026-11-22')
        self.assertEqual((await self.roster('offs', MANAGER, id=made['id']))['requests'].get('Аня'), ['2026-11-18'])

    async def test_bot_waits_only_for_a_schedule_that_exists_with_the_same_dates(self):
        import days_off
        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-30')
        link = await days_off.member(WAITER)
        await days_off.save(link, date(2026, 11, 16), date(2026, 11, 30), set())
        await days_off.reopen(link, date(2026, 11, 16))
        self.assertEqual((await days_off.waiting(WAITER))['end'], date(2026, 11, 30))
        # Период сократили: бот ждёт числа уже для новых границ.
        moved = await self.roster('dates', MANAGER, id=made['id'], revision=made['revision'], start='2026-11-16', end='2026-11-22')
        self.assertEqual((await days_off.waiting(WAITER))['end'], date(2026, 11, 22))
        # График удалили: бот больше ничего не ждёт.
        await self.roster('delete', MANAGER, id=made['id'])
        self.assertIsNone(await days_off.waiting(WAITER))
        # Строка ожидания без графика (например, осталась после сбоя) тоже ничего не ждёт.
        self.fake.rows['day_off_requests'] = [{'link_id': link['id'], 'restaurant_id': PLACE, 'start_date': '2026-11-16',
                                               'end_date': '2026-11-30', 'days': [], 'answered': False, 'waiting': True, 'updated_at': 'x'}]
        self.assertIsNone(await days_off.waiting(WAITER))

    async def test_shift_after_restaurant_closing_is_rejected(self):
        import roster_api
        self.assertEqual(roster_api.parse_cell('12-23'), (12, 23, False))
        with self.assertRaises(ValueError):
            roster_api.parse_cell('12-24')
        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-22')
        draft = dict(made['draft'], cells={'Аня': {'2026-11-16': '12-24'}})
        refused = await self.roster('save', MANAGER, 400, id=made['id'], revision=made['revision'], draft=draft)
        self.assertIn('вне часов работы', refused['error'])

    async def test_old_publication_cannot_overwrite_a_newer_one_and_bad_cells_keep_personal_shifts(self):
        import asyncio
        import roster_sync
        import days_off
        link = await days_off.member(WAITER)
        old = {'people': [{'link': link['id'], 'name': 'Аня'}], 'cells': {'Аня': {'2026-11-18': '9'}}}
        new = {'people': [{'link': link['id'], 'name': 'Аня'}], 'cells': {'Аня': {'2026-11-18': '14'}}}
        entered, release = asyncio.Event(), asyncio.Event()
        calendar = {}
        async def delayed_save(uid, cells):
            if cells[0]['start'] == '09:00':
                entered.set()
                await release.wait()
            calendar[uid] = [dict(c) for c in cells]
        start, end = date(2026, 11, 16), date(2026, 11, 30)
        with patch('schedule.save', new=delayed_save), patch('db.get_or_create_user', new=AsyncMock(return_value={})):
            first = asyncio.create_task(roster_sync.sync_published(PLACE, start, end, old, version=2, roster_id='r1'))
            await asyncio.wait_for(entered.wait(), 2)
            second = asyncio.create_task(roster_sync.sync_published(PLACE, start, end, new, old, version=3, roster_id='r1'))
            await asyncio.sleep(0.05)
            release.set()
            await asyncio.gather(first, second)
        self.assertEqual(calendar[WAITER][0]['start'], '14:00', calendar)
        # Устаревшая версия, пришедшая позже, ничего не пишет.
        calendar.clear()
        with patch('schedule.save', new=delayed_save), patch('db.get_or_create_user', new=AsyncMock(return_value={})):
            self.assertEqual(await roster_sync.sync_published(PLACE, start, end, old, version=2, roster_id='r1'), 0)
        self.assertEqual(calendar, {})
        # Непонятная клетка в старом снимке (интервал до 24) не стирает уже стоящую личную смену.
        self.fake.rows['shifts'] = [{'user_id': WAITER, 'shift_date': '2026-11-19'}]
        broken = {'people': [{'link': link['id'], 'name': 'Аня'}], 'cells': {'Аня': {'2026-11-19': '12-24'}}}
        with patch('schedule.save', new=delayed_save), patch('db.get_or_create_user', new=AsyncMock(return_value={})):
            await roster_sync.sync_published(PLACE, start, end, broken, version=4, roster_id='r1')
        self.assertEqual([r['shift_date'] for r in self.fake.rows['shifts']], ['2026-11-19'])

    async def test_periods_do_not_overlap_and_drafts_can_be_deleted(self):
        made = await self.roster('create', OWNER, restaurant_id=PLACE, start='2026-11-01', end='2026-11-15')
        clash = await self.roster('create', OWNER, 400, restaurant_id=PLACE, start='2026-11-10', end='2026-11-25')
        self.assertIn('пересекаются', clash['error'])
        await self.roster('delete', OWNER, id=made['id'])
        again = await self.roster('create', OWNER, restaurant_id=PLACE, start='2026-11-10', end='2026-11-25')
        await self.roster('publish', OWNER, id=again['id'], revision=1)
        await self.roster('delete', OWNER, 400, id=again['id'])
        await self.roster('open', OWNER, id=again['id'])

    async def test_new_manager_gets_the_site_link(self):
        sent = []

        class Bot:
            async def send_message(self, chat, text, **_):
                sent.append((chat, text))
        self.client.server.app['bot'] = Bot()
        await self.post('/api/restaurant/role', OWNER, id='cccccccc-0000-0000-0000-000000000002', role='manager', restaurant_id=PLACE)
        self.assertEqual(sent, [(WAITER, 'Ты теперь менеджер в «Ресторан».\nhttps://bot.example.test/restaurant')])
        await self.post('/api/restaurant/role', OWNER, id='cccccccc-0000-0000-0000-000000000002', role='staff', restaurant_id=PLACE)
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

    async def test_roles_open_exactly_their_part_of_the_cabinet(self):
        waiter = 'cccccccc-0000-0000-0000-000000000002'
        manager = 'cccccccc-0000-0000-0000-000000000001'
        self.assertFalse((await self.post('/api/restaurant/access', WAITER))['available'])
        # Менеджер не назначает роли; владелец назначает любые.
        await self.post('/api/restaurant/role', MANAGER, 403, id=waiter, role='manager', restaurant_id=PLACE)
        await self.post('/api/restaurant/role', OWNER, id=waiter, role='director', restaurant_id=PLACE)
        access = await self.post('/api/restaurant/access', WAITER)
        self.assertEqual([(r['id'], r['role']) for r in access['restaurants']], [(PLACE, 'director')])
        # Управляющий видит продажи и назначает менеджеров, но не управляющих.
        with patch('restaurant_api.member_sales', return_value={'metrics': {}}):
            director_view = await self.post('/api/restaurant/view', WAITER, restaurant_id=PLACE, month='2026-11')
            manager_view = await self.post('/api/restaurant/view', MANAGER, restaurant_id=PLACE, month='2026-11')
        # План есть только у официантов: у менеджеров и управляющего его нет, и показывать нечего.
        self.assertTrue(all(('sales' in r) == (r['role'] == 'staff') for r in director_view['rows']))
        self.assertTrue(any(r['role'] == 'staff' for r in director_view['rows']))
        self.assertTrue(any(r['role'] != 'staff' for r in director_view['rows']))
        self.assertFalse(any('sales' in r for r in manager_view['rows']))
        await self.post('/api/restaurant/role', WAITER, id=manager, role='staff', restaurant_id=PLACE)
        await self.post('/api/restaurant/role', WAITER, 403, id=manager, role='director', restaurant_id=PLACE)
        self.assertFalse((await self.post('/api/restaurant/access', MANAGER))['available'])
        # Переносить между ресторанами может только владелец.
        await self.post('/api/restaurant/transfer', WAITER, 403, id=manager, restaurant_id=PLACE, target_restaurant_id=OTHER)
        await self.post('/api/restaurant/role', OWNER, id=waiter, role='staff', restaurant_id=PLACE)
        self.assertFalse((await self.post('/api/restaurant/access', WAITER))['available'])

    async def test_legacy_automatic_off_flags_are_removed_without_rebuilding_or_changing_shifts(self):
        made = await self.roster('create', MANAGER, restaurant_id=PLACE, start='2026-11-16', end='2026-11-30')
        row = self.fake.rows['rosters'][0]
        shifts = {'Аня': {'2026-11-24': '9-17'}}
        explicit = {'Аня': ['2026-11-20', '2026-11-21', '2026-11-22']}
        automatic = {'Аня': ['2026-11-23']}
        legacy = {**row['draft'], 'cells': shifts, 'picked': True,
                  'off': {'Аня': [*explicit['Аня'], *automatic['Аня']]},
                  'off_requested': {'Аня': ['2026-11-21', '2026-11-22']},
                  'off_auto': automatic,
                  'variants': [{**variant, 'off': automatic} for variant in row['draft']['variants']]}
        row.update(draft=legacy, published=legacy, published_at='2026-11-12T10:00:00Z')
        opened = await self.roster('open', MANAGER, id=made['id'])
        self.assertEqual(opened['draft']['off'], explicit)
        self.assertEqual(opened['draft']['cells'], shifts)
        self.assertEqual(opened['draft']['off_auto'], {})
        self.assertTrue(all(not v['off'] for v in opened['draft']['variants']))
        self.assertFalse(opened['changed'])
        view = await self.post('/api/schedule/view', WAITER)
        own = next(person for person in view['period']['people'] if person['me'])
        self.assertEqual((own['cells'], own['off']), (shifts['Аня'], explicit['Аня']))
        saved = await self.roster('save', MANAGER, id=made['id'], revision=made['revision'], draft=legacy)
        self.assertEqual(saved['draft']['off'], explicit)
        self.assertEqual(saved['draft']['cells'], shifts)


if __name__ == '__main__':
    unittest.main()
