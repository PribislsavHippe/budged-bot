"""Прототип сборки графика: правила владельца, разбор и доступ. Имена вымышленные."""
import os
import sys
import unittest
from datetime import date, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import roster

START = date(2026, 11, 2)  # понедельник
NAMES = [f'Имя{i}' for i in range(1, 12)]


def fortnight(off=None, **kwargs):
    end = START + timedelta(days=13)
    request = roster.Request(START, end, list(NAMES), **kwargs)
    for name, days in (off or {}).items():
        request.days_off[name] = {START + timedelta(days=d) for d in days}
    return request


def solved(request):
    return roster.as_schedule(request, roster.build(request))


class SlotTests(unittest.TestCase):
    def test_slots_follow_the_owner_rules_for_every_weekday(self):
        expected = {0: [9, 10, 11, 12, 14], 3: [9, 10, 11, 12, 14],
                    4: [9, 9, 10, 11, 12, 12], 5: [8, 8, 9, 11, 12, 14], 6: [8, 8, 9, 11, 12, 14]}
        for offset, hours in expected.items():
            day = START + timedelta(days=offset)
            count = roster.WEEKEND_COUNT if day.weekday() >= 4 else roster.WEEKDAY_COUNT
            self.assertEqual(sorted(roster.day_slots(day, count)), hours)

    def test_extra_people_never_change_closers_or_openers(self):
        for offset in range(7):
            day = START + timedelta(days=offset)
            for count in (5, 6, 7, 8, 9):
                slots = roster.day_slots(day, count)
                self.assertEqual(len(slots), count)
                self.assertEqual(sum(h >= 11 for h in slots), 3)
                self.assertEqual(slots.count(roster.open_hour(day)), roster.openers_needed(day))

    def test_shift_is_twelve_hours_but_ends_at_closing(self):
        self.assertEqual([roster.shift_hours(h) for h in (8, 11, 12, 14)], [12, 12, 11, 9])


class BuildTests(unittest.TestCase):
    def test_all_rules_hold_and_money_days_are_even(self):
        request = fortnight({NAMES[0]: [0, 1], NAMES[1]: [2, 3, 4], NAMES[2]: [5, 6], NAMES[3]: [9]})
        schedule = solved(request)
        found = roster.audit(request, schedule)
        for problems in (found.shortages, found.closers, found.openers, found.rises, found.forced):
            self.assertEqual(problems, [])
        money = [stats['money'] for stats in found.people.values()]
        self.assertLessEqual(max(money) - min(money), 1)
        hours = [stats['hours'] for stats in found.people.values()]
        self.assertLessEqual(max(hours) - min(hours), 12)

    def test_same_input_gives_the_same_schedule(self):
        request = fortnight({NAMES[0]: [0, 1]})
        self.assertEqual(solved(request), solved(request))

    def test_count_override_and_holiday(self):
        day = START + timedelta(days=2)
        request = fortnight(counts={day: 7}, holidays={day})
        schedule = solved(request)
        working = [p[day] for p in schedule.values() if day in p]
        self.assertEqual(len(working), 7)
        self.assertEqual(working.count(8), 2)
        self.assertEqual(sum(h >= 11 for h in working), 3)
        self.assertIn(day, [d for d in request.days if request.is_money(d)])

    def test_shortage_is_reported_and_keeps_openers_and_closers(self):
        everyone_else = {name: [0] for name in NAMES[4:]}
        request = fortnight(everyone_else)
        schedule = solved(request)
        working = sorted(p[START] for p in schedule.values() if START in p)
        self.assertEqual(working, [9, 11, 12, 14])
        found = roster.audit(request, schedule)
        self.assertEqual(found.shortages[0][:3], (START, 4, 5))
        self.assertEqual(found.shortages[0][3], NAMES[4:])
        self.assertIn('не хватает людей, 4 из 5', roster.report(request, schedule, found))
        self.assertIn(START, found.no_reserve)

    def test_nobody_available_does_not_crash(self):
        request = roster.Request(START, START, ['А'], {'А': {START}})
        self.assertEqual(roster.as_schedule(request, roster.build(request)), {'А': {}})


class AuditTests(unittest.TestCase):
    def test_finds_problems_in_a_manual_schedule(self):
        request = roster.Request(START, START + timedelta(days=4), ['А', 'Б'], {'Б': {START}})
        day = lambda n: START + timedelta(days=n)
        schedule = {'А': {day(0): 11, day(1): 9, day(2): 9, day(3): 9, day(4): 9}, 'Б': {day(0): 9}}
        found = roster.audit(request, schedule)
        self.assertEqual(found.rises, [('А', day(1), 11, 9)])
        self.assertEqual(found.forced, [('Б', day(0))])
        self.assertEqual(found.streaks, [('А', day(0), 5)])
        text = roster.report(request, schedule, found)
        self.assertIn('приход в 9, а накануне в 11', text)
        self.assertIn('в обязательный выходной', text)


class ParseTests(unittest.TestCase):
    TODAY = date(2026, 11, 2)

    def test_full_input(self):
        request = roster.parse_request('16.11 30.11\nАня: 18 19\nМиша: 20-22, 25\nОля\nпраздник 21.11 7\nнужно 17.11 6', self.TODAY)
        self.assertEqual((request.start, request.end), (date(2026, 11, 16), date(2026, 11, 30)))
        self.assertEqual(request.people, ['Аня', 'Миша', 'Оля'])
        self.assertEqual(request.days_off['Миша'], {date(2026, 11, d) for d in (20, 21, 22, 25)})
        self.assertEqual(request.days_off['Оля'], set())
        self.assertEqual(request.counts, {date(2026, 11, 21): 7, date(2026, 11, 17): 6})
        self.assertEqual(request.holidays, {date(2026, 11, 21)})

    def test_period_across_new_year(self):
        request = roster.parse_request('28.12 5.01\nАня: 2', date(2026, 12, 20))
        self.assertEqual(request.end, date(2027, 1, 5))
        self.assertEqual(request.days_off['Аня'], {date(2027, 1, 2)})

    def test_bad_input_is_explained(self):
        for text, part in ((None, 'Сборка графика'), ('16.11', 'начало и конец'), ('16.11 30.11', 'ни одного'),
                           ('32.11 30.11\nА', 'Такой даты нет'), ('16.11 30.11\nА: 5', 'нет в выбранном'),
                           ('16.11 30.11\nА\nа', 'дважды'), ('16.11 30.11\nА: 22-20', 'наоборот'),
                           ('1.01 1.06\nА', 'слишком длинный'), ('16.11 30.11\nА\nнужно 20.11', 'нужно 21.11 7'),
                           ('1.11 15.12\nА: 5', 'дважды')):
            with self.assertRaises(roster.RosterInputError) as caught:
                roster.parse_request(text, self.TODAY)
            self.assertIn(part, str(caught.exception), text)


class OutputTests(unittest.TestCase):
    def test_html_escapes_names_and_marks_days_off(self):
        request = roster.Request(START, START, ['<b>Я</b>'], {'<b>Я</b>': {START}})
        page = roster.render_html(request, solved(request))
        self.assertNotIn('<b>Я</b>', page)
        self.assertIn('&lt;b&gt;Я&lt;/b&gt;', page)
        self.assertIn('class="off"', page)


class ChatTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid', 'SUPABASE_KEY': 'test'}), patch('supabase.create_client'):
            import roster_chat
            import admin
        cls.chat, cls.admin = roster_chat, admin

    def message(self):
        return NS(answer=AsyncMock(), answer_document=AsyncMock())

    async def test_owner_gets_report_and_file_and_names_are_escaped(self):
        message = self.message()
        args = '16.11 17.11\n<A>: 16\nБ\nВ\nГ\nД\nЕ'
        await self.chat.roster_command(message, NS(args=args))
        text = message.answer.await_args_list[0].args[0]
        self.assertIn('График 16 ноября', text)
        self.assertNotIn('<A>', text)
        document = message.answer_document.await_args.args[0]
        self.assertTrue(document.filename.endswith('.html'))
        self.assertIn('&lt;A&gt;'.encode(), document.data)

    async def test_input_mistake_gets_an_explanation_and_no_file(self):
        message = self.message()
        await self.chat.roster_command(message, NS(args=None))
        self.assertIn('Сборка графика', message.answer.await_args.args[0])
        message.answer_document.assert_not_awaited()

    async def test_failed_build_is_not_silent(self):
        message = self.message()
        with patch.object(self.chat.roster, 'build', side_effect=RuntimeError('x')):
            await self.chat.roster_command(message, NS(args='16.11 17.11\nА'))
        self.assertIn('Не получилось собрать', message.answer.await_args.args[0])

    async def test_router_answers_only_the_owner(self):
        check = self.chat.router.message._handler.filters
        self.assertTrue(check)
        message = lambda uid: NS(chat=NS(type='private'), from_user=NS(id=uid))
        with patch.object(self.admin, 'ADMIN_ID', 99):
            results = []
            for uid in (99, 100):
                ok = True
                for item in check:
                    ok = ok and bool(await item.call(message(uid)))
                results.append(ok)
        self.assertEqual(results, [True, False])

    def test_long_report_is_split_by_lines(self):
        parts = list(self.chat.chunks('\n'.join(['строка'] * 2000)))
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= self.chat.MESSAGE_LIMIT for p in parts))


if __name__ == '__main__':
    unittest.main()
