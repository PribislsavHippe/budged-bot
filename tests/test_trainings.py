"""Тренинги в общем чате: разбор таблицы, кто в выходной, текст, запись в таблицу и нажатие «Записаться»."""
import json
import os
import unittest
from datetime import date, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

with patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid', 'SUPABASE_KEY': 'test'}), patch('supabase.create_client'):
    import training_sheet as sheet
    import trainings
    from workday import MSK

TODAY = date(2026, 11, 16)


def grid(*rows, width=30):
    return [list(r) + [''] * (width - len(r)) for r in rows]


# Вымышленный лист в формате настоящей таблицы: обычный блок, блок без «Список сотрудников», занятия вправо.
ROWS = grid(
    [], [],
    ['СТОРИТЕЛЛИНГ', '', 'Название активности', 'Ведущий', '', 'Тренинг, который учит 01.01 с 10:00 до 11:00'],
    ['', '', 'СТОРИТЕЛЛИНГ', 'ИВАНОВА'],
    ['', '', 'Список сотрудников на 18.11 с 12:00 до 14:30'],
    ['', '', 'ФИО', 'Ресторан', 'Должность'],
    ['', '1', 'Петров Пётр', 'Сад', 'официант'],
    ['', '2', 'Сергеева Анна ', 'Река', 'Официант '],
    ['', '3'], ['', '4'],
    [],
    ['WELCOME', '', 'Название активности', 'Ведущий'],
    ['', '', 'Welcome тренинг', 'Смирнова'],
    ['', '', '07.12 с 11:00-13:00'],
    ['', '', 'ФИО', 'Ресторан', 'Должность'],
    ['', '1'],
    [],
    ['ВИНО', '', 'Название активности', 'Ведущий', '', '', 'Название активности', 'Ведущий', '', '', 'Название активности', 'Ведущий'],
    ['', '', 'Красные', 'Орлова', '', '', 'Красные', 'Орлова', '', '', 'Игристые', 'Орлова'],
    ['', '', 'Список сотрудников на 20.11 с 14:00 до 16:00', '', '', '', 'Список сотрудников на 14.10 с 14:00 до 16:00',
     '', '', '', 'Список сотрудников на 21.11 с 14:00 до 16:00'],
    ['', '', 'ФИО', 'Ресторан', 'Должность', '', 'ФИО', '', '', '', 'ФИО', 'Ресторан', 'Должность'],
    ['', '', 'ОТМЕНЕНО', '', '', '', '', '', '', '1', 'Анна Сергеева', 'Река', 'оф'],
    ['', '', '', '', '', '', '', '', '', '2'],
)


class Sheet(unittest.TestCase):
    def test_sessions_are_read_from_a_hand_made_sheet(self):
        found = {s['activity']: s for s in sheet.sessions('Ноябрь', ROWS, TODAY)}
        story = found['СТОРИТЕЛЛИНГ']
        self.assertEqual((story['date'], story['start'], story['end'], story['host']), (date(2026, 11, 18), 720, 870, 'ИВАНОВА'))
        self.assertEqual((story['col'], story['slots'], story['names']), (2, [8, 9], ['Петров Пётр', 'Сергеева Анна']))
        self.assertEqual((found['Welcome тренинг']['date'], found['Welcome тренинг']['start']), (date(2026, 12, 7), 660))
        reds = [s for s in sheet.sessions('Ноябрь', ROWS, TODAY) if s['activity'] == 'Красные']
        self.assertEqual([(s['date'], s['cancelled'], s['col']) for s in reds],
                         [(date(2026, 11, 20), True, 2), (date(2026, 10, 14), False, 6)])
        self.assertEqual((found['Игристые']['slots'], found['Игристые']['names']), ([22], ['Анна Сергеева']))

    def test_dates_without_a_year_take_the_nearest_one(self):
        self.assertEqual(sheet._when('05.01 с 12:00-13:00', date(2026, 12, 20))[0], date(2027, 1, 5))
        self.assertEqual(sheet._when('30.12 с 12:00-13:00', date(2027, 1, 3))[0], date(2026, 12, 30))
        self.assertIsNone(sheet._when('31.02 с 12:00-13:00', TODAY))
        self.assertIsNone(sheet._when('18.11', TODAY))

    def test_upcoming_drops_past_and_copied_sessions(self):
        copied = sheet.sessions('Ноябрь', ROWS, TODAY) + sheet.sessions('Декабрь', ROWS, TODAY)
        left = trainings.upcoming(copied, TODAY)
        self.assertEqual([s['date'].isoformat() for s in left], ['2026-11-18', '2026-11-20', '2026-11-21', '2026-12-07'])
        self.assertEqual({s['tab'] for s in left}, {'Ноябрь'})

    def test_people_and_columns(self):
        self.assertTrue(sheet.same_person('Сергеева Анна ', 'Анна Сергеева'))
        self.assertTrue(sheet.same_person('Сергеев Алексей', 'алексей сергеев'))
        self.assertFalse(sheet.same_person('Леся Б', 'Леся Богуцкая'))
        self.assertFalse(sheet.same_person('Анна', 'Анна'))
        self.assertEqual([sheet.column(i) for i in (0, 2, 25, 26, 27)], ['A', 'C', 'Z', 'AA', 'AB'])
        self.assertEqual(sheet.sheet_id('вот https://docs.google.com/spreadsheets/d/10miCtSWFJadfLBS0qERYwKc/edit#gid=1'),
                         '10miCtSWFJadfLBS0qERYwKc')
        self.assertIsNone(sheet.sheet_id('https://example.com/spreadsheets/d/10miCtSWFJadfLBS0qERYwKc'))


SNAPSHOT = {'people': [{'name': 'Аня', 'link': 'a'}, {'name': 'Влад', 'link': 'v'}, {'name': 'Оля', 'link': 'o'},
                       {'name': 'Петя', 'link': 'p'}],
            'cells': {'Аня': {'2026-11-18': ''}, 'Влад': {'2026-11-18': '15-23'}, 'Оля': {'2026-11-18': 'вых'},
                      'Петя': {'2026-11-18': '??'}}}


class Message(unittest.TestCase):
    def test_only_people_off_that_day_can_go(self):
        self.assertEqual([p['name'] for p in trainings.off_people(SNAPSHOT, date(2026, 11, 18))], ['Аня', 'Оля'])
        self.assertEqual(trainings.off_people(None, date(2026, 11, 18)), [])

    def test_texts(self):
        found = {s['activity']: s for s in sheet.sessions('Ноябрь', ROWS, TODAY)}
        story, welcome = found['СТОРИТЕЛЛИНГ'], found['Welcome тренинг']
        self.assertEqual(trainings.announcement([story, welcome]),
                         'Открыта запись на тренинги:\nср, 18 ноября, 12:00–14:30 — Сторителлинг\n'
                         'пн, 7 декабря, 11:00–13:00 — Welcome тренинг\nТем, у кого в эти дни выходной, напишу лично.')
        self.assertEqual(trainings.invitation([story]),
                         'Новости: в твой выходной тренинг. Ты должен идти ☠️\n'
                         'ср, 18 ноября, 12:00–14:30 — Сторителлинг, ведёт Иванова')
        self.assertTrue(trainings.invitation([story, welcome]).startswith('Новости: в твои выходные тренинги. Ты должен идти на все ☠️'))
        story, welcome = ({**s, 'gid': '941871425'} for s in (story, welcome))
        buttons = trainings.invitation_markup([story, welcome], 'doc').inline_keyboard
        self.assertEqual([(b.text, b.url) for row in buttons for b in row], [
            ('Записаться: 18.11 Сторителлинг', 'https://docs.google.com/spreadsheets/d/doc/edit#gid=941871425&range=C9'),
            ('Записаться: 07.12 Welcome тренинг', 'https://docs.google.com/spreadsheets/d/doc/edit#gid=941871425&range=C16')])
        self.assertEqual(sheet.link('doc', {**story, 'gid': None}), 'https://docs.google.com/spreadsheets/d/doc/edit')
        self.assertTrue(sheet.link('doc', {**story, 'slots': []}).endswith('range=C6'))     # строк нет — шапка
        self.assertEqual(trainings.report(story, ['Оля'], ['Аня']),
                         'Тренинг завтра: ср, 18 ноября, 12:00–14:30 — Сторителлинг.\nЗаписались: Оля.\n'
                         'Был выходной, но не записались: Аня.')
        self.assertIn('Записались: никто.', trainings.report(story, [], ['Аня']))
        saved = trainings.stored(story)
        self.assertNotIn('names', saved)
        self.assertEqual(trainings.restored(json.loads(json.dumps(saved)))['date'], story['date'])


class FakeTable:
    """Запросы к Supabase по цепочке: хватает select/eq/… и выполнения через db._execute."""
    def __init__(self, store, name):
        self.store, self.name, self.filters, self.action, self.payload = store, name, [], 'select', None

    def __getattr__(self, attr):
        if attr == 'not_':
            return self
        return lambda *args, **kwargs: self._chain(attr, args, kwargs)

    def _chain(self, attr, args, kwargs):
        if attr in ('insert', 'update', 'upsert'):
            self.action, self.payload = attr, args[0]
        elif attr == 'eq':
            self.filters.append(args)
        return self

    def run(self):
        rows = self.store.setdefault(self.name, [])
        match = [r for r in rows if all(str(r.get(k)) == str(v) for k, v in self.filters)]
        if self.action in ('insert', 'upsert'):
            rows.append(dict(self.payload))
        elif self.action == 'update':
            for r in match:
                r.update(self.payload)
        return NS(data=match)


def people():
    return [{'id': 'a', 'report_name': 'Котова Аня', 'user_id': 1, 'status': 'approved', 'restaurant_id': 'r', 'role': 'staff'},
            {'id': 'v', 'report_name': 'Влад', 'user_id': 2, 'status': 'approved', 'restaurant_id': 'r', 'role': 'staff'},
            {'id': 'o', 'report_name': 'Оля', 'user_id': 3, 'status': 'approved', 'restaurant_id': 'r', 'role': 'staff'},
            {'id': 'm', 'report_name': 'Менеджер', 'user_id': 7, 'status': 'approved', 'restaurant_id': 'r', 'role': 'manager'},
            {'id': 'x', 'report_name': 'Чужой', 'user_id': 9, 'status': 'approved', 'restaurant_id': 'q', 'role': 'staff'}]


class Base(unittest.IsolatedAsyncioTestCase):
    BOUND = {'restaurant_id': 'r', 'chat_id': -5, 'sheet_id': 'test-sheet-0000000000000', 'label': 'Сад', 'position': 'официант', 'active': True}

    def setUp(self):
        self.store = {'training_chats': [dict(self.BOUND)], 'employee_links': people(),
                      'restaurants': [{'id': 'r', 'name': 'Сад', 'owner_id': 8}]}

    def patches(self, rows=ROWS, snapshot_days=(date(2026, 11, 18),)):
        import db
        from contextlib import ExitStack
        stack = ExitStack()
        async def roster_for(restaurant, day):
            return SNAPSHOT if day in snapshot_days else None
        stack.enter_context(patch.object(db, 'supabase', NS(table=lambda name: FakeTable(self.store, name))))
        stack.enter_context(patch.object(db, '_execute', new=AsyncMock(side_effect=lambda q, **k: q.run())))
        stack.enter_context(patch.object(trainings, 'published', new=roster_for))
        stack.enter_context(patch.object(sheet, 'read', new=AsyncMock(side_effect=lambda d, t: sheet.sessions('Ноябрь', rows, t))))
        stack.enter_context(patch('trainings.op_today', return_value=TODAY))
        return stack


class Posting(Base):
    rows = ROWS

    async def run_sync(self, hour=12, rows=None, day=TODAY):
        rows = rows or self.rows
        bot = NS(send_message=AsyncMock(return_value=NS(message_id=100)))
        now = datetime(day.year, day.month, day.day, hour, tzinfo=MSK)
        with self.patches(rows), patch('trainings.op_today', return_value=day):
            done = await trainings.sync(bot, self.BOUND, now)
        return done, [(c.args[0], c.args[1]) for c in bot.send_message.await_args_list], bot

    async def test_the_team_chat_gets_one_short_announcement_and_people_off_get_invitations(self):
        done, sent, bot = await self.run_sync()
        self.assertEqual(done, {'announced': 3, 'invited': 2, 'reported': 0})
        chat = [text for to, text in sent if to == -5]
        self.assertEqual(len(chat), 1)
        self.assertNotIn('Аня', chat[0])                                  # в общем чате без имён
        self.assertEqual(sorted(to for to, _ in sent if to != -5), [1, 3])  # Аня и Оля, у Влада смена
        self.assertTrue(all(text.startswith('Новости: в твой выходной') for to, text in sent if to != -5))
        self.assertNotIn('names', self.store['training_sessions'][0]['session'])
        done, sent, _ = await self.run_sync()
        self.assertEqual((done, sent), ({'announced': 0, 'invited': 0, 'reported': 0}, []))   # повтор ничего не шлёт

    async def test_no_personal_messages_at_night(self):
        done, sent, _ = await self.run_sync(hour=22)
        self.assertEqual(done['invited'], 0)
        self.assertEqual([to for to, _ in sent], [-5])

    async def test_managers_get_a_report_the_evening_before(self):
        await self.run_sync()
        signed = [list(r) for r in ROWS]
        signed[8][2] = 'Аня Котова'                                       # вписалась сама; в боте она «Котова Аня»
        self.rows = signed
        done, sent, _ = await self.run_sync(hour=17, day=date(2026, 11, 17))
        self.assertEqual(done['reported'], 0)
        done, sent, _ = await self.run_sync(hour=18, day=date(2026, 11, 17))
        self.assertEqual(done['reported'], 1)
        reports = {to: text for to, text in sent}
        self.assertEqual(sorted(reports), [7, 8])                         # менеджер и владелец
        self.assertIn('Записались: Аня.', reports[7])
        self.assertIn('Был выходной, но не записались: Оля.', reports[7])
        self.rows = ROWS
        done, _, _ = await self.run_sync(hour=19, day=date(2026, 11, 17))
        self.assertEqual(done['reported'], 0)

    async def test_a_cancelled_session_is_not_announced(self):
        cancelled = [list(r) for r in ROWS]
        cancelled[4][2] += ' ОТМЕНЕНО'
        _, sent, _ = await self.run_sync(rows=cancelled)
        self.assertNotIn('Сторителлинг', next(text for to, text in sent if to == -5))
        self.assertEqual([to for to, _ in sent], [-5])


class Chat(unittest.IsolatedAsyncioTestCase):
    def event(self, old='left', new='member', uid=1):
        bot = NS(send_message=AsyncMock(), id=99)
        return NS(chat=NS(id=-5, type='group'), from_user=NS(id=uid), bot=bot,
                  old_chat_member=NS(status=old), new_chat_member=NS(status=new))

    async def call(self, call, store, places):
        import db
        import roles
        fake = NS(table=lambda name: FakeTable(store, name))
        with patch.object(db, 'supabase', fake), patch.object(db, '_execute', new=AsyncMock(side_effect=lambda q, **k: q.run())), \
                patch.object(roles, 'places', new=AsyncMock(return_value=places)), \
                patch.object(roles, 'place', new=AsyncMock(side_effect=lambda uid, rid=None: places[0] if places else None)):
            await call

    async def test_a_manager_adding_the_bot_binds_the_chat(self):
        import training_chat as chat
        store, event = {}, self.event()
        with patch.object(trainings, 'sync', new=AsyncMock()) as first:
            await self.call(chat.membership(event), store, [{'id': 'r', 'name': 'Сад', 'role': 'manager'}])
        self.assertEqual(store['training_chats'][0]['chat_id'], -5)
        self.assertEqual(event.bot.send_message.await_args.args[1], chat.BOUND)
        first.assert_awaited_once()                                       # не ждать получаса
        event = self.event()
        await self.call(chat.membership(event), {}, [])
        self.assertEqual(event.bot.send_message.await_args.args[1], chat.NOT_MANAGER)
        event = self.event(old='member', new='kicked')
        await self.call(chat.membership(event), store, [])
        self.assertFalse(store['training_chats'][0]['active'])

    def test_the_sheet_comes_from_settings(self):
        link = 'https://docs.google.com/spreadsheets/d/10miCtSWFJadfLBS0qERYwKc-kt8IsMH3a7AjKZDq8Lo/edit?gid=1#gid=1'
        for value, expected in ((link, '10miCtSWFJadfLBS0qERYwKc-kt8IsMH3a7AjKZDq8Lo'),
                                ('10miCtSWFJadfLBS0qERYwKc-kt8IsMH3a7AjKZDq8Lo', '10miCtSWFJadfLBS0qERYwKc-kt8IsMH3a7AjKZDq8Lo'),
                                ('', None), ('../etc', None)):
            with self.subTest(value=value), patch.dict(os.environ, {'TRAINING_SHEET': value}):
                self.assertEqual(trainings.document({'sheet_id': None}), expected)
        with patch.dict(os.environ, {'TRAINING_SHEET': link}):
            self.assertEqual(trainings.document({'sheet_id': 'own-sheet-of-this-restaurant-0'}), 'own-sheet-of-this-restaurant-0')

if __name__ == '__main__':
    unittest.main()
