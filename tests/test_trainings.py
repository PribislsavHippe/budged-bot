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
                         'Тренинг в твой выходной:\nср, 18 ноября, 12:00–14:30 — Сторителлинг, ведёт Иванова')
        buttons = trainings.invitation_markup([story, welcome], 'doc').inline_keyboard
        self.assertEqual([b.text for row in buttons for b in row],
                         ['Записаться: 18.11 Сторителлинг', 'Записаться: 07.12 Welcome тренинг', 'Таблица'])
        self.assertLessEqual(max(len(b.callback_data.encode()) for row in buttons[:2] for b in row), 64)
        self.assertEqual(trainings.report(story, ['Оля'], ['Аня']),
                         'Тренинг завтра: ср, 18 ноября, 12:00–14:30 — Сторителлинг.\nЗаписались: Оля.\n'
                         'Был выходной, но не записались: Аня.')
        self.assertIn('Записались: никто.', trainings.report(story, [], ['Аня']))
        saved = trainings.stored(story)
        self.assertNotIn('names', saved)
        self.assertEqual(trainings.restored(json.loads(json.dumps(saved)))['date'], story['date'])


class FakeGoogle:
    """httpx.AsyncClient на месте Google: токен, чтение листа, запись диапазона."""
    def __init__(self, rows):
        self.rows, self.writes, self.posts = rows, [], []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def _response(self, body):
        return NS(json=lambda: body, raise_for_status=lambda: None)

    async def post(self, url, data):
        self.posts.append(data)
        return self._response({'access_token': 'token', 'expires_in': 3600})

    async def get(self, url, headers=None, params=None):
        return self._response({'values': self.rows})

    async def put(self, url, headers=None, params=None, json=None):
        self.writes.append((url.rsplit('/', 1)[1], json['values']))
        return self._response({})


def account():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return json.dumps({'client_email': 'bot@example.iam.gserviceaccount.com', 'private_key': pem.decode(),
                       'token_uri': 'https://oauth2.example/token'})


class Writing(unittest.IsolatedAsyncioTestCase):
    async def test_writes_into_the_first_free_numbered_row_after_rereading(self):
        story = next(s for s in sheet.sessions('Ноябрь', ROWS, TODAY) if s['activity'] == 'СТОРИТЕЛЛИНГ')
        google = FakeGoogle(ROWS)
        sheet._token.update(value=None, until=0)
        with patch.dict(os.environ, {'GOOGLE_SERVICE_ACCOUNT': account()}), patch('httpx.AsyncClient', google):
            self.assertEqual(await sheet.write('doc', story, ['Аня Котова', 'Сад', 'официант'], TODAY), 'ok')
            self.assertEqual(await sheet.write('doc', story, ['Анна Сергеева', 'Река', 'официант'], TODAY), 'already')
            gone = {**story, 'key': 'other'}
            self.assertEqual(await sheet.write('doc', gone, ['Аня Котова', 'Сад', 'официант'], TODAY), 'gone')
        self.assertEqual(google.writes, [("'Ноябрь'!C9:E9", [['Аня Котова', 'Сад', 'официант']])])
        self.assertEqual(len(google.posts), 1)                            # токен один на все запросы
        self.assertEqual(google.posts[0]['assertion'].count('.'), 2)

    async def test_a_full_session_is_not_overwritten(self):
        full = [list(r) for r in ROWS]
        full[8][2], full[9][2] = 'Кто-то Один', 'Кто-то Два'
        story = next(s for s in sheet.sessions('Ноябрь', ROWS, TODAY) if s['activity'] == 'СТОРИТЕЛЛИНГ')
        google = FakeGoogle(full)
        sheet._token.update(value='token', until=10 ** 12)
        self.assertEqual(await self._write(google, story), 'full')
        self.assertEqual(google.writes, [])

    async def _write(self, google, story):
        with patch('httpx.AsyncClient', google):
            return await sheet.write('doc', story, ['Аня Котова', 'Сад', 'официант'], TODAY)


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
    BOUND = {'restaurant_id': 'r', 'chat_id': -5, 'sheet_id': 'doc', 'label': 'Сад', 'position': 'официант', 'active': True}

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
    async def run_sync(self, hour=12, rows=ROWS, day=TODAY):
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
        self.assertTrue(all(text.startswith('Тренинг в твой выходной') for to, text in sent if to != -5))
        self.assertNotIn('names', self.store['training_sessions'][0]['session'])
        done, sent, _ = await self.run_sync()
        self.assertEqual((done, sent), ({'announced': 0, 'invited': 0, 'reported': 0}, []))   # повтор ничего не шлёт

    async def test_no_personal_messages_at_night(self):
        done, sent, _ = await self.run_sync(hour=22)
        self.assertEqual(done['invited'], 0)
        self.assertEqual([to for to, _ in sent], [-5])

    async def test_managers_get_a_report_the_evening_before(self):
        self.store['training_signups'] = []
        await self.run_sync()
        key = next(r['session_key'] for r in self.store['training_sessions'] if r['session']['date'] == '2026-11-18')
        self.store['training_signups'].append({'restaurant_id': 'r', 'session_key': key, 'link_id': 'o'})
        done, sent, _ = await self.run_sync(hour=17, day=date(2026, 11, 17))
        self.assertEqual(done['reported'], 0)
        done, sent, _ = await self.run_sync(hour=18, day=date(2026, 11, 17))
        self.assertEqual(done['reported'], 1)
        reports = {to: text for to, text in sent}
        self.assertEqual(sorted(reports), [7, 8])                         # менеджер и владелец
        self.assertIn('Записались: Оля.', reports[7])
        self.assertIn('Был выходной, но не записались: Аня.', reports[7])
        done, _, _ = await self.run_sync(hour=19, day=date(2026, 11, 17))
        self.assertEqual(done['reported'], 0)

    async def test_a_cancelled_session_is_not_announced(self):
        cancelled = [list(r) for r in ROWS]
        cancelled[4][2] += ' ОТМЕНЕНО'
        _, sent, _ = await self.run_sync(rows=cancelled)
        self.assertNotIn('Сторителлинг', next(text for to, text in sent if to == -5))
        self.assertEqual([to for to, _ in sent], [-5])


class Signing(Base):
    async def press(self, uid, write='ok', google=True):
        with self.patches():
            bot = NS(send_message=AsyncMock(return_value=NS(message_id=1)))
            await trainings.sync(bot, self.BOUND, datetime(2026, 11, 16, 12, tzinfo=MSK))
            key = next(r for r in self.store['training_sessions'] if r['session']['date'] == '2026-11-18')['short_id']
            written = AsyncMock(return_value=write)
            with patch.object(sheet, 'write', new=written), patch.object(sheet, 'configured', return_value=google):
                answer, session = await trainings.sign(uid, key)
        return answer, session, written

    async def test_a_person_off_that_day_is_written_once(self):
        answer, session, written = await self.press(1)
        self.assertEqual(answer, 'Записал тебя: Сторителлинг, 18 ноября в 12:00.')
        self.assertEqual(session['date'], date(2026, 11, 18))
        self.assertEqual(written.await_args.args[2], ['Котова Аня', 'Сад', 'официант'])
        self.assertEqual(len(self.store['training_signups']), 1)
        answer, _, written = await self.press(1)
        self.assertEqual(answer, trainings.ANSWERS['already'])
        written.assert_not_awaited()

    async def test_refusals(self):
        self.assertEqual((await self.press(2))[0], trainings.NOT_OFF)            # у Влада смена
        self.assertEqual((await self.press(9))[0], trainings.ANSWERS['gone'])    # другой ресторан: такого занятия нет
        self.assertEqual((await self.press(4))[0], trainings.NOT_STAFF)          # не подключён к боту
        self.assertEqual((await self.press(1, google=False))[:2], (trainings.NO_GOOGLE, None))
        answer, session, _ = await self.press(1, write='full')
        self.assertEqual((answer, session), (trainings.ANSWERS['full'], None))
        self.assertFalse(self.store.get('training_signups'))


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
        await self.call(chat.membership(event), store, [{'id': 'r', 'name': 'Сад', 'role': 'manager'}])
        self.assertEqual(store['training_chats'][0]['chat_id'], -5)
        self.assertEqual(event.bot.send_message.await_args.args[1], chat.BOUND)
        event = self.event()
        await self.call(chat.membership(event), {}, [])
        self.assertEqual(event.bot.send_message.await_args.args[1], chat.NOT_MANAGER)
        event = self.event(old='member', new='kicked')
        await self.call(chat.membership(event), store, [])
        self.assertFalse(store['training_chats'][0]['active'])

    async def test_the_sheet_link_is_taken_only_as_a_reply_to_the_bot_from_a_manager(self):
        import training_chat as chat
        store = {'training_chats': [{'restaurant_id': 'r', 'chat_id': -5, 'active': True, 'sheet_id': None}]}
        link = 'https://docs.google.com/spreadsheets/d/10miCtSWFJadfLBS0qERYwKc-kt8IsMH3a7AjKZDq8Lo/edit'
        def message(author=99):
            return NS(text=link, chat=NS(id=-5), from_user=NS(id=1), bot=NS(id=99), reply=AsyncMock(),
                      reply_to_message=NS(from_user=NS(id=author)))
        other = message(author=42)
        with patch.object(sheet, 'read', new=AsyncMock(return_value=sheet.sessions('Ноябрь', ROWS, TODAY))), \
                patch.object(trainings, 'sync', new=AsyncMock()), patch.object(sheet, 'configured', return_value=False):
            await self.call(chat.sheet_link(other), store, [{'id': 'r'}])
            other.reply.assert_not_awaited()
            await self.call(chat.sheet_link(message()), store, [])            # не менеджер
            self.assertIsNone(store['training_chats'][0]['sheet_id'])
            good = message()
            await self.call(chat.sheet_link(good), store, [{'id': 'r'}])
        self.assertEqual(store['training_chats'][0]['sheet_id'], '10miCtSWFJadfLBS0qERYwKc-kt8IsMH3a7AjKZDq8Lo')
        self.assertTrue(good.reply.await_args.args[0].startswith('Таблица подключена.'))

    async def test_pressing_in_a_private_chat_removes_the_button_and_adds_the_answer(self):
        import training_chat as chat
        story = next(s for s in sheet.sessions('Ноябрь', ROWS, TODAY) if s['activity'] == 'СТОРИТЕЛЛИНГ')
        markup = trainings.invitation_markup([story], 'doc')
        message = NS(text='Тренинг в твой выходной:', reply_markup=markup, edit_text=AsyncMock())
        query = NS(from_user=NS(id=1), data=markup.inline_keyboard[0][0].callback_data, message=message, answer=AsyncMock())
        with patch.object(trainings, 'sign', new=AsyncMock(return_value=('Записал тебя: Сторителлинг.', story))):
            await chat.sign_up(query)
        self.assertFalse(query.answer.await_args.kwargs['show_alert'])
        text, kwargs = message.edit_text.await_args.args[0], message.edit_text.await_args.kwargs
        self.assertEqual(text, 'Тренинг в твой выходной:\nЗаписал тебя: Сторителлинг.')
        self.assertEqual([b.text for row in kwargs['reply_markup'].inline_keyboard for b in row], ['Таблица'])

if __name__ == '__main__':
    unittest.main()
