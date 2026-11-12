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


def solved(request, full=False):
    # Полный поиск нужен только проверкам качества; правилам хватает короткого, так CI укладывается во время.
    table = roster.build(request) if full else roster.build(request, iterations=60000, restarts=1)
    return roster.as_schedule(request, table)


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
        schedule = solved(request, full=True)
        found = roster.audit(request, schedule)
        for problems in (found.shortages, found.closers, found.openers, found.rises, found.forced):
            self.assertEqual(problems, [])
        money = [stats['money'] for stats in found.people.values()]
        self.assertLessEqual(max(money) - min(money), 1)
        hours = [stats['hours'] for stats in found.people.values()]
        self.assertLessEqual(max(hours) - min(hours), 12)
        shifts = [stats['shifts'] for stats in found.people.values()]
        self.assertLessEqual(max(shifts) - min(shifts), 1)
        self.assertEqual(found.streaks, [])

    def test_two_on_two_off_when_it_fits(self):
        days = [START + timedelta(days=i) for i in range(8)]
        request = roster.Request(START, days[-1], NAMES[:4], counts=dict.fromkeys(days, 2),
                                 openers=dict.fromkeys(days, 0), closers=dict.fromkeys(days, 2))
        schedule = solved(request)
        for own in schedule.values():
            worked = sum(1 << i for i, day in enumerate(days) if day in own)
            self.assertEqual(len(own), 4)
            self.assertEqual(roster.pattern_misses(worked, roster.pattern_masks('2/2', len(days))), 0)

    def test_employee_with_few_available_days_can_have_fewer_shifts(self):
        days = [START + timedelta(days=i) for i in range(8)]
        request = roster.Request(START, days[-1], NAMES[:5], days_off={NAMES[0]: set(days[1:])},
                                 counts=dict.fromkeys(days, 2), openers=dict.fromkeys(days, 0),
                                 closers=dict.fromkeys(days, 2))
        found = roster.audit(request, solved(request))
        self.assertEqual((found.shortages, found.forced, found.streaks), ([], [], []))
        self.assertEqual(found.people[NAMES[0]]['shifts'], 1)
        self.assertEqual(sorted(found.people[name]['shifts'] for name in NAMES[1:5]), [3, 4, 4, 4])

    def test_required_staffing_can_extend_a_streak_from_the_last_period(self):
        request = roster.Request(START, START, [NAMES[0]], pattern_history={NAMES[0]: '011'},
                                 counts={START: 1}, openers={START: 0}, closers={START: 1})
        schedule = solved(request)
        found = roster.audit(request, schedule)
        self.assertEqual(found.shortages, [])
        self.assertEqual(found.streaks, [(NAMES[0], START - timedelta(days=2), 3)])
        self.assertEqual(roster.audit(request, {}).streaks, [])

    def test_avoiding_long_streak_does_not_cause_extra_recalls(self):
        days = [START + timedelta(days=i) for i in range(13)]
        names = NAMES[:3]
        counts = {day: 2 if day == days[6] else 1 for day in days}
        request = roster.Request(START, days[-1], names, days_off={name: set(days[1:-1]) for name in names[1:]},
                                 counts=counts, openers=dict.fromkeys(days, 0), closers=counts)
        for variant in roster.build_variants(request, iterations=10000, restarts=1):
            found = roster.audit(request, variant['schedule'])
            self.assertEqual(found.shortages, [])
            self.assertEqual(len(found.forced), 1)

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

    def test_count_is_filled_from_days_off_with_the_fewest_people_named(self):
        request = fortnight({name: [0] for name in NAMES[4:]})
        schedule = solved(request)
        found = roster.audit(request, schedule)
        self.assertEqual(found.shortages, [])
        self.assertEqual(sorted(p[START] for p in schedule.values() if START in p), [9, 10, 11, 12, 14])
        self.assertEqual(len(found.forced), 1)
        self.assertIn(found.forced[0][0], NAMES[4:])
        self.assertIn('вместо обязательного выходного: свободных только 4, нужно 5', roster.report(request, schedule, found))
        self.assertIn(START, found.no_reserve)

    def test_day_off_is_not_touched_while_free_people_are_enough(self):
        request = fortnight({name: [0] for name in NAMES[5:]})
        found = roster.audit(request, solved(request))
        self.assertEqual((found.shortages, found.forced), ([], []))

    def test_students_are_called_after_everyone_else(self):
        request = fortnight({name: [0] for name in NAMES[4:]})
        request.students = set(NAMES[4:-1])
        found = roster.audit(request, solved(request))
        self.assertEqual([name for name, _ in found.forced], [NAMES[-1]])

    def test_students_are_called_when_nobody_else_is_left(self):
        request = fortnight({name: [0] for name in NAMES[4:]})
        request.students = set(NAMES[4:])
        schedule = solved(request)
        found = roster.audit(request, schedule)
        self.assertEqual((len(found.forced), found.shortages), (1, []))
        self.assertIn('(учёба): выход', roster.report(request, schedule, found))

    def test_recalls_are_spread_across_people(self):
        request = fortnight({name: [0, 7] for name in NAMES[4:]})
        found = roster.audit(request, solved(request))
        self.assertEqual(len(found.forced), 2)
        self.assertNotEqual(found.forced[0][0], found.forced[1][0])

    def test_person_on_vacation_is_never_called(self):
        request = fortnight({NAMES[0]: list(range(14)), **{name: [0] for name in NAMES[4:]}})
        found = roster.audit(request, solved(request))
        self.assertEqual(found.people[NAMES[0]]['shifts'], 0)
        self.assertEqual(found.shortages, [])

    def test_shortage_remains_only_when_there_are_too_few_people(self):
        names = NAMES[:4]
        request = roster.Request(START, START + timedelta(days=13), names, {names[0]: {START}})
        schedule = solved(request)
        working = sorted(p[START] for p in schedule.values() if START in p)
        self.assertEqual(working, [9, 11, 12, 14])
        found = roster.audit(request, schedule)
        self.assertEqual(found.shortages[0][:3], (START, 4, 5))
        self.assertEqual(found.shortages[0][3], [])
        self.assertIn('не хватает людей, 4 из 5', roster.report(request, schedule, found))

    def test_nobody_available_does_not_crash(self):
        request = roster.Request(START, START, ['А'], {'А': {START}})
        self.assertEqual(roster.as_schedule(request, roster.build(request)), {'А': {}})


class WishTests(unittest.TestCase):
    OFF = {NAMES[i]: [i, (i + 5) % 14, (i + 9) % 14] for i in range(11)}

    def test_wishes_are_met_when_possible_and_rules_still_hold(self):
        request = fortnight(self.OFF)
        request.wish_starts = {NAMES[0]: set(roster.MORNING_STARTS), NAMES[1]: {11, 12}}
        request.wish_off = {NAMES[2]: {START + timedelta(days=12)}}
        found = roster.audit(request, solved(request))
        self.assertEqual((found.rises, found.shortages, found.closers, found.openers), ([], [], [], []))
        self.assertEqual(found.missed_off, [])
        self.assertEqual(found.missed_starts, [])
        self.assertIn('Пожелания: выполнено', roster.report(request, solved(request)))

    def test_wishes_never_beat_rules_or_mandatory_days_off(self):
        request = fortnight(self.OFF)
        request.wish_starts = {name: set(roster.MORNING_STARTS) for name in NAMES}
        schedule = solved(request)
        found = roster.audit(request, schedule)
        self.assertEqual((found.closers, found.openers, found.forced, found.shortages), ([], [], [], []))
        refusals = {}
        for name, _, _ in found.missed_starts:
            refusals[name] = refusals.get(name, 0) + 1
        self.assertEqual(len(refusals), len(NAMES))
        self.assertLessEqual(max(refusals.values()) - min(refusals.values()), 1)

    def test_impossible_day_off_wish_is_explained(self):
        saturday = START + timedelta(days=5)
        request = fortnight(wish_off={name: {saturday} for name in NAMES})
        schedule = solved(request)
        found = roster.audit(request, schedule)
        self.assertEqual(len(found.missed_off), roster.WEEKEND_COUNT)
        self.assertIn('выходной просят 11, отпустить можно только 5', roster.report(request, schedule, found))

    def test_more_than_three_mandatory_days_off_is_flagged(self):
        request = fortnight({NAMES[0]: [0, 1, 2, 3]})
        found = roster.audit(request, solved(request))
        self.assertEqual(found.too_many_off, [(NAMES[0], 4)])

    def test_limit_is_three_per_fifteen_days_and_students_may_have_more(self):
        day = lambda n: START + timedelta(days=n)
        self.assertEqual(roster.Request(START, day(14), []).mandatory_limit, 3)
        self.assertEqual(roster.Request(START, day(29), []).mandatory_limit, 6)
        self.assertEqual(roster.Request(START, day(30), []).mandatory_limit, 6)
        request = fortnight({NAMES[0]: [0, 1, 2, 3, 4, 5], NAMES[1]: [0, 1, 2, 3]})
        request.students = {NAMES[0]}
        schedule = solved(request)
        found = roster.audit(request, schedule)
        self.assertEqual(found.too_many_off, [(NAMES[1], 4)])
        self.assertIn('«; учёба»', roster.report(request, schedule, found))

    def test_person_away_for_whole_period_does_not_distort_the_report(self):
        request = fortnight({NAMES[0]: list(range(14))})
        text = roster.report(request, solved(request))
        self.assertNotIn('Смены: от 0', text)

    def test_when_short_closing_is_kept_and_one_person_still_opens(self):
        friday = START + timedelta(days=4)
        self.assertEqual(sorted(roster.day_slots(friday, 6)[:4]), [9, 11, 12, 12])
        self.assertEqual(roster.day_slots(friday, 1), [9])

    def test_saturday_opener_after_friday_is_reported_as_unavoidable_rise(self):
        # Все, кто свободен, работают и в пятницу с 9+, и в субботу: двое должны открыть в 8.
        names = NAMES[:6]
        request = roster.Request(START + timedelta(days=4), START + timedelta(days=5), names)
        found = roster.audit(request, solved(request))
        self.assertEqual(len(found.rises), 2)


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
        self.assertIn('вместо обязательного выходного: свободных только 1, нужно 5', text)

    def test_shift_on_a_day_off_without_need_is_a_plain_violation(self):
        names = NAMES[:7]
        request = roster.Request(START, START, names, {names[0]: {START}})
        schedule = {name: {START: 9} for name in names}
        text = roster.report(request, schedule)
        self.assertIn(f'{names[0]}: смена 2 ноября в обязательный выходной.', text)


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

    def test_wishes_in_input(self):
        request = roster.parse_request('16.11 30.11\nАня: 18 19 20; хочу 25, 26; утро\nМиша: ; приход 11, 12\nОля: 21; вечер\nНужно: 22', self.TODAY)
        self.assertEqual(request.days_off['Аня'], {date(2026, 11, d) for d in (18, 19, 20)})
        self.assertEqual(request.wish_off['Аня'], {date(2026, 11, 25), date(2026, 11, 26)})
        self.assertEqual(request.wish_starts['Аня'], {8, 9, 10})
        self.assertEqual(request.days_off['Миша'], set())
        self.assertEqual(request.wish_starts['Миша'], {11, 12})
        self.assertEqual(request.wish_starts['Оля'], {11, 12, 14})
        self.assertIn('Нужно', request.people)
        self.assertNotIn('Оля', request.wish_off)

    def test_student_mark_in_input(self):
        request = roster.parse_request('16.11 30.11\nАня: 18-23; учёба\nМиша: 20; студент; утро\nОля: 21', self.TODAY)
        self.assertEqual(request.students, {'Аня', 'Миша'})
        self.assertEqual(len(request.days_off['Аня']), 6)
        self.assertEqual(request.wish_starts, {'Миша': {8, 9, 10}})

    def test_period_across_new_year(self):
        request = roster.parse_request('28.12 5.01\nАня: 2', date(2026, 12, 20))
        self.assertEqual(request.end, date(2027, 1, 5))
        self.assertEqual(request.days_off['Аня'], {date(2027, 1, 2)})

    def test_bad_input_is_explained(self):
        for text, part in ((None, 'Сборка графика'), ('16.11', 'начало и конец'), ('16.11 30.11', 'ни одного'),
                           ('32.11 30.11\nА', 'Такой даты нет'), ('16.11 30.11\nА: 5', 'нет в выбранном'),
                           ('16.11 30.11\nА\nа', 'дважды'), ('16.11 30.11\nА: 22-20', 'наоборот'),
                           ('1.01 1.06\nА', 'слишком длинный'), ('16.11 30.11\nА\nнужно 20.11', 'нужно 21.11 7'),
                           ('1.11 15.12\nА: 5', 'дважды'), ('16.11 30.11\nА: ; ночь', 'Не понял пожелание'),
                           ('16.11 30.11\nА: ; приход', 'Не понял часы'), ('16.11 30.11\nА: ; приход 3', 'Не понял часы')):
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

    def test_html_marks_met_and_missed_wishes(self):
        request = roster.Request(START, START + timedelta(days=1), ['А'], wish_off={'А': {START}})
        schedule = {'А': {START + timedelta(days=1): 9}}
        self.assertIn('class="wish"', roster.render_html(request, schedule))
        schedule = {'А': {START: 9}}
        self.assertIn('class="miss"', roster.render_html(request, schedule))


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
