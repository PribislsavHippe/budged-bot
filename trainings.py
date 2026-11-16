"""Тренинги: объявление в общем чате, личные приглашения тем, у кого выходной, запись и отчёт менеджеру.

Бот читает таблицу записи на тренинги (`training_sheet`; один файл, листы по месяцам — читаются все).
- В общий чат — коротко: открыта запись, список занятий без имён.
- Лично (чат с ботом) — тем, у кого в день занятия выходной по опубликованному графику, с кнопкой «Записаться».
  На тренинг идут только в выходной. Кнопка вписывает человека в таблицу (сервисный аккаунт Google,
  `GOOGLE_SERVICE_ACCOUNT`); без него бот предлагает записаться в самой таблице.
- Менеджерам накануне занятия — отчёт: кто записался и у кого был выходной, но он не записался.
Тексты — ЧЕРНОВИКИ до согласования владельца.
"""
import asyncio
import hashlib
import logging
from datetime import date, datetime, timedelta

from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from postgrest.exceptions import APIError

import db
import roster
import schedule
import training_sheet as sheet
from workday import MSK, op_today

MISSING = ('42P01', 'PGRST205', '42703', 'PGRST204')
WEEK = ('пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс')
PREFIX = 'tr:'
INVITE_HOURS = (10, 21)        # личные сообщения — только днём
REPORT_HOUR = 18               # отчёт менеджеру — накануне занятия с этого часа
_locks = {}


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
    return {'key': session['key'], 'tab': session['tab'], 'activity': session['activity'][:80],
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


# --- Тексты (ЧЕРНОВИКИ) ----------------------------------------------------------------------------------------

def announcement(sessions):
    return ('Открыта запись на тренинги:\n' + '\n'.join(line(s) for s in sessions) +
            '\nТем, у кого в эти дни выходной, напишу лично.')


def invitation(sessions):
    head = 'Тренинг в твой выходной:' if len(sessions) == 1 else 'Тренинги в твои выходные:'
    return head + '\n' + '\n'.join(line(s) + (f", ведёт {_plain(s['host'])}" if s.get('host') else '') for s in sessions)


def invitation_markup(sessions, document, signed=()):
    rows = [[Button(text=f"Записаться: {_day(s)} {_plain(s['activity'])}"[:60], callback_data=PREFIX + short(s['key']))]
            for s in sessions if s['key'] not in signed]
    rows.append([Button(text='Таблица', url=f'https://docs.google.com/spreadsheets/d/{document}/edit')])
    return Markup(inline_keyboard=rows)


def report(session, signed, missed):
    lines = [f'Тренинг завтра: {line(session)}.',
             'Записались: ' + (', '.join(signed) if signed else 'никто') + '.']
    if missed:
        lines.append('Был выходной, но не записались: ' + ', '.join(missed) + '.')
    return '\n'.join(lines)


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


async def signed_names(restaurant_id, key, snapshot, sheet_names):
    """Кто из графика уже записан: через бота (по сотруднику) и руками в таблице (по имени)."""
    links = {r['link_id'] for r in await _rows('training_signups', restaurant_id, session_key=key)}
    return [p['name'] for p in (snapshot or {}).get('people') or []
            if p.get('link') in links or any(sheet.same_person(n, p.get('name', '')) for n in sheet_names)]


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
    накануне занятия отчитаться менеджерам. Повторный вызов ничего не дублирует."""
    now = now or datetime.now(MSK)
    today = op_today()
    if not bound.get('sheet_id'):
        return {}
    async with _lock(bound['sheet_id']):
        found = upcoming(await sheet.read(bound['sheet_id'], today), today)
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
            done['invited'] = await _invite(bot, bound, open_)
        if now.hour >= REPORT_HOUR:
            for s in open_:
                if s['date'] == today + timedelta(days=1) and not known[s['key']].get('reported_at'):
                    done['reported'] += await _report(bot, rid, s, now)
        return done


async def _invite(bot, bound, sessions):
    rid = bound['restaurant_id']
    invited = {(r['session_key'], r['link_id']) for r in await _rows('training_invites', rid)}
    signed = {(r['session_key'], r['link_id']) for r in await _rows('training_signups', rid)}
    per_link = {}
    for s in sessions:
        snapshot = await published(rid, s['date'])
        for person in off_people(snapshot, s['date']):
            pair = (s['key'], person['link'])
            if person['link'] and pair not in invited and pair not in signed:
                per_link.setdefault(person['link'], []).append(s)
    if not per_link:
        return 0
    links = (await db._execute(db.supabase.table('employee_links').select('id,user_id')
                               .eq('restaurant_id', rid).eq('status', 'approved'))).data
    users = {r['id']: r['user_id'] for r in links}
    count = 0
    for link, mine in per_link.items():
        uid = users.get(link)
        if not uid:
            continue
        try:
            await bot.send_message(uid, invitation(mine), reply_markup=invitation_markup(mine, bound['sheet_id']))
        except Exception as error:
            logging.warning('training invitation failed: %s', type(error).__name__)
            continue
        for s in mine:
            await db._execute(db.supabase.table('training_invites').upsert(
                {'restaurant_id': rid, 'session_key': s['key'], 'link_id': link},
                on_conflict='restaurant_id,session_key,link_id'))
        count += 1
    return count


async def _report(bot, rid, session, now):
    snapshot = await published(rid, session['date'])
    if snapshot is None:
        return 0
    signed = await signed_names(rid, session['key'], snapshot, session.get('names', []))
    missed = [p['name'] for p in off_people(snapshot, session['date']) if p['name'] not in signed]
    body = report(session, signed, missed)
    sent = 0
    for uid in await managers(rid):
        try:
            await bot.send_message(uid, body)
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
        rows = (await db._execute(db.supabase.table('training_chats').select('*').eq('active', True)
                                  .not_.is_('sheet_id', 'null'))).data
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


# --- Запись ---------------------------------------------------------------------------------------------------

NOT_STAFF = 'Записаться может сотрудник ресторана из графика.'
NOT_OFF = 'В этот день у тебя смена. На тренинг можно только в выходной.'
NOT_IN_ROSTER = 'Тебя нет в графике на этот день.'
NO_GOOGLE = 'Бот пока не может писать в таблицу. Запишись в ней сам — кнопка «Таблица».'
ANSWERS = {
    'ok': 'Записал тебя: {when}.',
    'already': 'Ты уже записан.',
    'full': 'Свободных строк нет. Напиши организатору или посмотри таблицу.',
    'gone': 'Этого занятия в таблице больше нет или его отменили.',
}


def _when(session):
    day = session['date']
    return f"{_plain(session['activity'])}, {day.day} {roster.MONTHS[day.month - 1]} в {_clock(session['start'])}"


async def sign(uid, short_id):
    """Нажатие «Записаться» в личном чате. Возвращает (ответ, занятие или None, если не записан)."""
    links = (await db._execute(db.supabase.table('employee_links').select('id,report_name,restaurant_id')
                               .eq('user_id', uid).eq('status', 'approved').limit(1))).data
    if not links:
        return NOT_STAFF, None
    link = links[0]
    rid = link['restaurant_id']
    bound = await binding(restaurant_id=rid)
    rows = [r for r in await _rows('training_sessions', rid) if r['short_id'] == short_id]
    if not bound or not bound.get('sheet_id') or not rows:
        return ANSWERS['gone'], None
    row = rows[0]
    session = restored(row['session'])
    if session['cancelled'] or session['date'] <= op_today():
        return ANSWERS['gone'], None
    snapshot = await published(rid, session['date'])
    person = next((p for p in (snapshot or {}).get('people') or [] if p.get('link') == link['id']), None)
    if person is None:
        return NOT_IN_ROSTER, None
    if person['name'] not in {p['name'] for p in off_people(snapshot, session['date'])}:
        return NOT_OFF, None
    if await _rows('training_signups', rid, session_key=row['session_key'], link_id=link['id']):
        return ANSWERS['already'], session
    if not sheet.configured():
        return NO_GOOGLE, None
    label = bound.get('label') or (await db._execute(db.supabase.table('restaurants').select('name')
                                                     .eq('id', rid))).data[0]['name']
    async with _lock(bound['sheet_id']):
        result = await sheet.write(bound['sheet_id'], session, [link['report_name'], label, bound['position']],
                                   op_today())
        if result in ('ok', 'already'):
            await db._execute(db.supabase.table('training_signups').upsert(
                {'restaurant_id': rid, 'session_key': row['session_key'], 'link_id': link['id']},
                on_conflict='restaurant_id,session_key,link_id'))
    return ANSWERS[result].format(when=_when(session)), session if result in ('ok', 'already') else None
