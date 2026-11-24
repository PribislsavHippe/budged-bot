"""Тренинги: объявление в общем чате, личные приглашения тем, у кого выходной, запись и отчёт менеджеру.

Бот читает таблицу записи на тренинги (`training_sheet`; один файл, листы по месяцам — читаются все).
- В общий чат — коротко: открыта запись, список занятий без имён.
- Лично (чат с ботом) — тем, у кого в день занятия выходной по опубликованному графику, с кнопкой «Записаться».
  На тренинг идут только в выходной. Кнопка открывает таблицу на свободной строке занятия: люди вписывают себя сами.
- В 17:00 накануне — личное напоминание тем, кто ещё не записался и попадёт в список менеджера.
- В 18:00 накануне — отчёт менеджерам: кто записался и кто не записался, хотя у него выходной.
Новые тексты согласуются с владельцем.
"""
import asyncio
import hashlib
import logging
import os
import re
from datetime import date, datetime, timedelta

from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from postgrest.exceptions import APIError

import db
from chat_tone import friend_text
import roster
import schedule
import training_sheet as sheet
from workday import MSK, op_today

MISSING = ('42P01', 'PGRST205', '42703', 'PGRST204')
WEEK = ('пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс')
INVITE_HOURS = (10, 21)        # личные сообщения — только днём
REPORT_HOUR = 18               # отчёт менеджеру — накануне занятия с этого часа
REMINDER_HOUR = REPORT_HOUR - 1
_locks = {}


def document(bound):
    """Таблица тренингов: одна на всех, ссылка в настройках бота (`TRAINING_SHEET`). В репозитории её нет:
    таблица открыта по ссылке, и в ней имена сотрудников."""
    raw = (bound or {}).get('sheet_id') or os.getenv('TRAINING_SHEET', '').strip()
    return sheet.sheet_id(raw) or (raw if re.fullmatch(r'[A-Za-z0-9_-]{20,100}', raw) else None)


def _lock(key):
    return _locks.setdefault(key, asyncio.Lock())


def _plain(text):
    text = ' '.join(str(text).split())
    return text[0] + text[1:].lower() if text.isupper() else text


def _clock(minutes):
    return f'{minutes // 60}:{minutes % 60:02d}'


def short(key):
    return hashlib.sha256(key.encode()).hexdigest()[:10]


def upcoming(found, today):
    """Будущие занятия без повторов: один и тот же блок часто скопирован на лист следующего месяца."""
    out = {}
    for s in sorted(found, key=lambda s: (s['date'], s['start'])):
        if s['date'] >= today and s['key'] not in out:
            out[s['key']] = s
    return list(out.values())


def off_people(snapshot, day):
    """Люди опубликованного графика, у которых в этот день выходной. Непонятная клетка — не выходной."""
    out = []
    for person in (snapshot or {}).get('people') or []:
        name = person.get('name')
        value = str(((snapshot.get('cells') or {}).get(name) or {}).get(day.isoformat(), '')).replace('*', '').strip()
        try:
            if name and schedule.cell(value) is None:
                out.append({'name': name, 'link': person.get('link')})
        except ValueError:
            pass
    return out


def stored(session):
    """Занятие для базы: без имён из таблицы."""
    return {'key': session['key'], 'tab': session['tab'], 'gid': session.get('gid'), 'activity': session['activity'][:80],
            'host': session.get('host', '')[:80], 'date': session['date'].isoformat(), 'start': session['start'],
            'end': session['end'], 'cancelled': session['cancelled'], 'col': session['col']}


def restored(row):
    return {**row, 'date': date.fromisoformat(row['date'])}


def line(session):
    day = session['date']
    return (f"{WEEK[day.weekday()]}, {day.day} {roster.MONTHS[day.month - 1]}, "
            f"{_clock(session['start'])}–{_clock(session['end'])} — {_plain(session['activity'])}")


def _day(session):
    return f"{session['date'].day:02d}.{session['date'].month:02d}"


# --- Тексты ---------------------------------------------------------------------------------------------------

def announcement(sessions):
    return ('Открыта запись на тренинги:\n' + '\n'.join(line(s) for s in sessions) +
            '\nТем, у кого в эти дни выходной, напишу лично.')


def invitation(sessions, uid=None):
    """По просьбе менеджера: «ты обязан пойти», но с иронией. ЧЕРНОВИК."""
    body = ('в твой выходной тренинг. Ты должен идти ☠️' if len(sessions) == 1 else
            'в твои выходные тренинги. Ты должен идти на все ☠️')
    head = friend_text(uid, 'Новости: ' + body, 'Приятель, ' + body)
    return (head + '\n' + '\n'.join(line(s) + (f", ведёт {_plain(s['host'])}" if s.get('host') else '') for s in sessions))


def invitation_markup(sessions, document):
    return Markup(inline_keyboard=[[Button(text=f"Записаться: {_day(s)} {_plain(s['activity'])}"[:60],
                                           url=sheet.link(document, s))] for s in sessions])


def report(session, signed, missed):
    lines = [f'Тренинг завтра: {line(session)}.',
             'Записались: ' + (', '.join(signed) if signed else 'никто') + '.']
    if missed:
        lines.append('Был выходной, но не записались: ' + ', '.join(missed) + '.')
    return '\n'.join(lines)


def reminder_text(session, uid):
    first = f'Завтра тренинг „{_plain(session["activity"])}“ в {_clock(session["start"])}. Ты пока не записался. '
    last = 'В 18:00 менеджер получит список тех, кто не записался'
    return friend_text(uid, first + last + ' 👀', first + last + ', приятель 👀')


# --- Данные ---------------------------------------------------------------------------------------------------

async def binding(chat_id=None, restaurant_id=None):
    query = db.supabase.table('training_chats').select('*').eq('active', True)
    query = query.eq('chat_id', chat_id) if chat_id is not None else query.eq('restaurant_id', restaurant_id)
    try:
        rows = (await db._execute(query.limit(1))).data
    except APIError as error:
        if error.code in MISSING:
            return None
        raise
    return rows[0] if rows else None


async def published(restaurant_id, day):
    rows = (await db._execute(db.supabase.table('rosters').select('published')
                              .eq('restaurant_id', restaurant_id).lte('start_date', day.isoformat())
                              .gte('end_date', day.isoformat()).not_.is_('published', 'null').limit(1))).data
    return rows[0]['published'] if rows else None


async def _rows(table, restaurant_id, **filters):
    query = db.supabase.table(table).select('*').eq('restaurant_id', restaurant_id)
    for key, value in filters.items():
        query = query.eq(key, value)
    return (await db._execute(query)).data


def signed_names(snapshot, sheet_names, reports):
    """Prefer an exact full name. Short forms only count when they identify one person."""
    people = (snapshot or {}).get('people') or []
    names = [(sheet.name_words(person['name']), sheet.name_words(reports.get(person.get('link'))))
             for person in people]
    signed = set()
    for name in sheet_names:
        words = sheet.name_words(name)
        if not words:
            continue
        matches = [i for i, forms in enumerate(names) if len(words) > 1 and words in forms]
        if not matches:
            matches = [i for i, forms in enumerate(names) if any(sheet.short_name_matches(words, form) for form in forms)]
        if len(matches) == 1:
            signed.add(matches[0])
    return [person['name'] for i, person in enumerate(people) if i in signed]


async def _employee_links(rid):
    links = (await db._execute(db.supabase.table('employee_links').select('id,report_name,user_id,role')
                               .eq('restaurant_id', rid).eq('status', 'approved'))).data
    return {link['id']: link for link in links}


async def managers(restaurant_id):
    """Кому отчёт: владелец ресторана, управляющий и менеджеры."""
    owner = (await db._execute(db.supabase.table('restaurants').select('owner_id').eq('id', restaurant_id))).data
    links = (await db._execute(db.supabase.table('employee_links').select('user_id,role')
                               .eq('restaurant_id', restaurant_id).eq('status', 'approved'))).data
    ids = [r['user_id'] for r in links if r.get('role') in ('manager', 'director')]
    if owner and owner[0].get('owner_id'):
        ids.insert(0, owner[0]['owner_id'])
    return list(dict.fromkeys(ids))


# --- Синхронизация --------------------------------------------------------------------------------------------

async def sync(bot, bound, now=None):
    """Прочитать таблицу: запомнить занятия, объявить новые в общем чате, пригласить тех, у кого выходной,
    накануне напомнить о записи и отчитаться менеджерам. Повторный вызов ничего не дублирует."""
    now = now or datetime.now(MSK)
    today = op_today()
    doc = document(bound)
    if not doc:
        return {}
    async with _lock(doc):
        problems = []
        found = upcoming(await sheet.read(doc, today, problems), today)
        await _warn(bot, problems)
        rid = bound['restaurant_id']
        known = {r['session_key']: r for r in await _rows('training_sessions', rid)}
        for s in found:
            row = known.get(s['key'])
            if row is None:
                row = {'restaurant_id': rid, 'session_key': s['key'], 'short_id': short(s['key']), 'session': stored(s)}
                await db._execute(db.supabase.table('training_sessions').insert(row))
                known[s['key']] = row
            elif row['session'] != stored(s):
                await db._execute(db.supabase.table('training_sessions').update({'session': stored(s)})
                                  .eq('restaurant_id', rid).eq('session_key', s['key']))
                row['session'] = stored(s)
        open_ = [s for s in found if not s['cancelled'] and s['date'] > today]
        done = {'announced': 0, 'invited': 0, 'reported': 0}
        if not open_:
            return done
        restaurant = bound.get('label')
        if not restaurant:
            places = (await db._execute(db.supabase.table('restaurants').select('name').eq('id', rid))).data
            restaurant = places[0]['name'] if places else ''
        for session in open_:
            session['local_signups'] = sheet.signup_names(session, restaurant)
        fresh = [s for s in open_ if not known[s['key']].get('announced_at')]
        if fresh:
            try:
                await bot.send_message(bound['chat_id'], announcement(fresh))
                for s in fresh:
                    await db._execute(db.supabase.table('training_sessions').update({'announced_at': now.isoformat()})
                                      .eq('restaurant_id', rid).eq('session_key', s['key']))
                done['announced'] = len(fresh)
            except Exception as error:
                logging.warning('training announcement failed: %s', type(error).__name__)
        if INVITE_HOURS[0] <= now.hour < INVITE_HOURS[1]:
            done['invited'] = await _invite(bot, bound, open_, doc)
        tomorrow = today + timedelta(days=1)
        pending = [s for s in open_ if s['date'] == tomorrow and not known[s['key']].get('reported_at')]
        if pending and REMINDER_HOUR <= now.hour < REPORT_HOUR and await managers(rid):
            for s in pending:
                await _remind_off_staff(bot, rid, s, doc)
        if now.hour >= REPORT_HOUR:
            for s in pending:
                done['reported'] += await _report(bot, rid, s, now)
        return done


_warned = set()


def unreadable(tab, text):
    """ЧЕРНОВИК: владельцу бота, когда в таблице дата, которую бот не понял (формат поменяли)."""
    return (f'Тренинги: не понял дату на листе «{tab.strip()}»: «{text}». '
            'Это занятие бот пропустит, пока дату не запишут как раньше, например «18.11 с 12:00 до 14:30».')


async def _warn(bot, problems):
    """Таблицу ведут руками: если формат поменяется, бот скажет об этом, а не замолчит. Каждую строку — один раз."""
    from admin import ADMIN_ID
    for tab, text in problems:
        if not ADMIN_ID or (tab, text) in _warned:
            continue
        _warned.add((tab, text))
        try:
            await bot.send_message(ADMIN_ID, unreadable(tab, text))
        except Exception as error:
            logging.warning('training warning failed: %s', type(error).__name__)


async def _invite(bot, bound, sessions, doc):
    rid = bound['restaurant_id']
    invited = {(r['session_key'], r['link_id']) for r in await _rows('training_invites', rid)}
    links = await _employee_links(rid)
    reports = {link_id: link['report_name'] for link_id, link in links.items()}
    per_link = {}
    for s in sessions:
        snapshot = await published(rid, s['date'])
        signed = set(signed_names(snapshot, s['local_signups'], reports))
        for person in off_people(snapshot, s['date']):
            pair = (s['key'], person['link'])
            if person['link'] and pair not in invited and person['name'] not in signed:
                per_link.setdefault(person['link'], []).append(s)
    if not per_link:
        return 0
    count = 0
    for link, mine in per_link.items():
        uid = links.get(link, {}).get('user_id')
        if not uid:
            continue
        try:
            await bot.send_message(uid, invitation(mine, uid), reply_markup=invitation_markup(mine, doc))
        except Exception as error:
            logging.warning('training invitation failed: %s', type(error).__name__)
            continue
        for s in mine:
            await db._execute(db.supabase.table('training_invites').upsert(
                {'restaurant_id': rid, 'session_key': s['key'], 'link_id': link},
                on_conflict='restaurant_id,session_key,link_id'))
        count += 1
    return count


async def _attendance(rid, session):
    """Данные для напоминания и отчёта из одной и той же сверки графика с таблицей."""
    snapshot = await published(rid, session['date'])
    if snapshot is None:
        return None
    links = await _employee_links(rid)
    signed = signed_names(snapshot, session['local_signups'], {link_id: link['report_name'] for link_id, link in links.items()})
    missed = [p for p in off_people(snapshot, session['date']) if p['name'] not in signed]
    return missed, links


async def _remind_off_staff(bot, rid, session, doc):
    attendance = await _attendance(rid, session)
    if attendance is None:
        return
    missed, links = attendance
    markup = Markup(inline_keyboard=[[Button(text='Записаться', url=sheet.link(doc, session))]])
    for person in missed:
        link = links.get(person['link'])
        if not link or link.get('role') != 'staff' or not link.get('user_id'):
            continue
        try:
            # Reserve once before contacting Telegram, including across restarts and parallel syncs.
            claimed = (await db._execute(db.supabase.table('training_reminders').upsert(
                {'restaurant_id': rid, 'session_key': session['key'], 'link_id': link['id']},
                on_conflict='restaurant_id,session_key,link_id', ignore_duplicates=True))).data
        except APIError as error:
            if error.code in MISSING:
                return
            raise
        if not claimed:
            continue
        uid = link['user_id']
        try:
            await asyncio.wait_for(bot.send_message(uid, reminder_text(session, uid), reply_markup=markup,
                                                    parse_mode=None), timeout=5)
        except Exception as error:
            logging.warning('training reminder failed: %s', type(error).__name__)


async def _report(bot, rid, session, now):
    attendance = await _attendance(rid, session)
    if attendance is None:
        return 0
    missed, _ = attendance
    body = report(session, session['local_signups'], [person['name'] for person in missed])
    sent = 0
    for uid in await managers(rid):
        try:
            await bot.send_message(uid, body, parse_mode=None)
            sent += 1
        except Exception as error:
            logging.warning('training report failed: %s', type(error).__name__)
    await db._execute(db.supabase.table('training_sessions').update({'reported_at': now.isoformat()})
                      .eq('restaurant_id', rid).eq('session_key', session['key']))
    return 1 if sent else 0


async def sync_all(bot):
    """Фоновая задача: днём раз в полчаса."""
    if not 9 <= datetime.now(MSK).hour < 22:
        return 0
    try:
        rows = (await db._execute(db.supabase.table('training_chats').select('*').eq('active', True))).data
    except APIError as error:
        if error.code in MISSING:
            return 0
        raise
    for bound in rows:
        try:
            await sync(bot, bound)
        except Exception as error:
            from diagnostics import failure
            failure(error, area='trainings', stage='sync')
    return len(rows)
