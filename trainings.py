"""Тренинги: объявление в общем чате, личные приглашения тем, у кого выходной, запись и отчёт менеджеру.

Бот читает таблицу записи на тренинги (`training_sheet`; один файл, листы по месяцам — читаются все).
- В общий чат — коротко: открыта запись, список занятий без имён.
- Лично (чат с ботом) — тем, у кого в день занятия выходной по опубликованному графику, с кнопкой «Записаться».
  На тренинг идут только в выходной. Кнопка открывает таблицу на свободной строке занятия: люди вписывают себя сами.
- Менеджерам накануне занятия — отчёт: кто записался и у кого был выходной, но он не записался.
Тексты — ЧЕРНОВИКИ до согласования владельца.
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
import roster
import schedule
import training_sheet as sheet
from workday import MSK, op_today

MISSING = ('42P01', 'PGRST205', '42703', 'PGRST204')
WEEK = ('пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс')
INVITE_HOURS = (10, 21)        # личные сообщения — только днём
REPORT_HOUR = 18               # отчёт менеджеру — накануне занятия с этого часа
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


# --- Тексты (ЧЕРНОВИКИ) ----------------------------------------------------------------------------------------

def announcement(sessions):
    return ('Открыта запись на тренинги:\n' + '\n'.join(line(s) for s in sessions) +
            '\nТем, у кого в эти дни выходной, напишу лично.')


def invitation(sessions):
    head = 'Тренинг в твой выходной:' if len(sessions) == 1 else 'Тренинги в твои выходные:'
    return (head + '\n' + '\n'.join(line(s) + (f", ведёт {_plain(s['host'])}" if s.get('host') else '') for s in sessions)
)


def invitation_markup(sessions, document):
    return Markup(inline_keyboard=[[Button(text=f"Записаться: {_day(s)} {_plain(s['activity'])}"[:60],
                                           url=sheet.link(document, s))] for s in sessions])


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


def signed_names(snapshot, sheet_names, reports=None):
    """Кто из графика записан в таблице: по имени в графике или по имени, под которым человек подключён к боту."""
    reports = reports or {}
    return [p['name'] for p in (snapshot or {}).get('people') or []
            if any(sheet.same_person(n, p.get('name', '')) or sheet.same_person(n, reports.get(p.get('link'), ''))
                   for n in sheet_names)]


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
    doc = document(bound)
    if not doc:
        return {}
    async with _lock(doc):
        found = upcoming(await sheet.read(doc, today), today)
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
            done['invited'] = await _invite(bot, bound, open_, doc)
        if now.hour >= REPORT_HOUR:
            for s in open_:
                if s['date'] == today + timedelta(days=1) and not known[s['key']].get('reported_at'):
                    done['reported'] += await _report(bot, rid, s, now)
        return done


async def _invite(bot, bound, sessions, doc):
    rid = bound['restaurant_id']
    invited = {(r['session_key'], r['link_id']) for r in await _rows('training_invites', rid)}
    per_link = {}
    for s in sessions:
        snapshot = await published(rid, s['date'])
        signed = set(signed_names(snapshot, s.get('names', [])))
        for person in off_people(snapshot, s['date']):
            pair = (s['key'], person['link'])
            if person['link'] and pair not in invited and person['name'] not in signed:
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
            await bot.send_message(uid, invitation(mine), reply_markup=invitation_markup(mine, doc))
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
    links = (await db._execute(db.supabase.table('employee_links').select('id,report_name')
                               .eq('restaurant_id', rid).eq('status', 'approved'))).data
    signed = signed_names(snapshot, session.get('names', []), {r['id']: r['report_name'] for r in links})
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
