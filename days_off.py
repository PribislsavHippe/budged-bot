"""Обязательные выходные, которые сотрудники записывают в Telegram.

Менеджер просит выбрать выходные на период: каждому приходит сообщение с датами и кнопками.
Выбранное сразу сохраняется (`day_off_requests`); при сборке графика эти дни ставятся как выходные,
остальные выходные до нормы расставляет программа. Менеджер видит только, кто ответил.
"""
import asyncio
from datetime import date, timedelta

from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from postgrest.exceptions import APIError

import db
import roster

MISSING = ('42P01', 'PGRST205')
WEEK = ('пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс')
PREFIX = 'off'


def period_text(start, end):
    if start.month == end.month:
        return f'{start.day}–{end.day} {roster.MONTHS[end.month - 1]}'
    return f'{start.day} {roster.MONTHS[start.month - 1]} – {end.day} {roster.MONTHS[end.month - 1]}'


def days_text(days):
    days = sorted(days)
    if not days:
        return ''
    same = len({d.month for d in days}) == 1
    parts = ', '.join(str(d.day) for d in days) if same else ', '.join(f'{d.day} {roster.MONTHS[d.month - 1]}' for d in days)
    return parts + (f' {roster.MONTHS[days[0].month - 1]}' if same else '')


def keyboard(start, end, chosen, today, finished=False):
    """Кнопки-даты по пять в ряд; выбранная отмечена галочкой. Прошедшие дни не показываются."""
    if finished:
        return Markup(inline_keyboard=[[Button(text='Изменить', callback_data=f'{PREFIX}:e:{start}:{end}')]])
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    days = [d for d in days if d >= today]
    rows = []
    for i in range(0, len(days), 5):
        rows.append([Button(text=('✓ ' if d in chosen else '') + f'{WEEK[d.weekday()]} {d.day}',
                            callback_data=f'{PREFIX}:t:{start}:{end}:{d}') for d in days[i:i + 5]])
    rows.append([Button(text='Готово', callback_data=f'{PREFIX}:d:{start}:{end}')])
    return Markup(inline_keyboard=rows)


def prompt_text(start, end, chosen, limit):
    return f'График на {period_text(start, end)}. Отметь обязательные выходные: до {limit}.\nВыбрано: {len(chosen)}.'


def summary_text(start, end, chosen):
    if not chosen:
        return f'График на {period_text(start, end)}. Выходные не выбраны, их расставит программа.'
    return f'График на {period_text(start, end)}. Записал выходные: {days_text(chosen)}.'


async def member(uid):
    """Подтверждённый сотрудник ресторана, если он такой один."""
    rows = (await db._execute(db.supabase.table('employee_links').select('id,restaurant_id,report_name')
                              .eq('user_id', uid).eq('status', 'approved'))).data
    return rows[0] if rows else None


async def period_exists(restaurant_id, start, end):
    rows = (await db._execute(db.supabase.table('rosters').select('id').eq('restaurant_id', restaurant_id)
                              .eq('start_date', start.isoformat()).eq('end_date', end.isoformat()).limit(1))).data
    return bool(rows)


async def is_student(restaurant_id, link_id):
    """Студентам положено больше выходных; отметка живёт в последнем графике ресторана."""
    rows = (await db._execute(db.supabase.table('rosters').select('draft').eq('restaurant_id', restaurant_id)
                              .order('start_date', desc=True).limit(1))).data
    people = (rows[0]['draft'] or {}).get('people', []) if rows else []
    return any(p.get('link') == link_id and p.get('student') for p in people)


async def get(link_id, start):
    rows = (await db._execute(db.supabase.table('day_off_requests').select('days').eq('link_id', link_id)
                              .eq('start_date', start.isoformat()))).data
    return {date.fromisoformat(d) for d in rows[0]['days']} if rows else None


async def save(link, start, end, days):
    from datetime import datetime, timezone
    row = {'link_id': link['id'], 'restaurant_id': link['restaurant_id'], 'start_date': start.isoformat(),
           'end_date': end.isoformat(), 'days': sorted(d.isoformat() for d in days),
           'updated_at': datetime.now(timezone.utc).isoformat()}
    await db._execute(db.supabase.table('day_off_requests').upsert(row, on_conflict='link_id,start_date'), retries=1)


async def for_period(restaurant_id, start):
    """Ответы сотрудников на период: {id сотрудника: [даты]}. Без migration_v32 ответов нет."""
    try:
        rows = await db._pages(lambda: db.supabase.table('day_off_requests').select('link_id,days')
                               .eq('restaurant_id', restaurant_id).eq('start_date', start.isoformat()).order('link_id'))
    except APIError as error:
        if error.code in MISSING:
            return {}
        raise
    return {r['link_id']: list(r['days']) for r in rows}


async def ask(bot, restaurant_id, start, end, links, answered):
    """Присылает просьбу выбрать выходные тем, кто ещё не ответил. Возвращает, скольким отправлено."""
    today = roster_today()
    sent = 0
    users = (await db._execute(db.supabase.table('employee_links').select('id,user_id,status')
                               .in_('id', list(links)))).data if links else []
    for row in users:
        if row['status'] != 'approved' or row['id'] in answered:
            continue
        link = {'id': row['id'], 'restaurant_id': restaurant_id}
        limit = roster.off_limit((end - start).days + 1, await is_student(restaurant_id, row['id']))
        try:
            await asyncio.wait_for(bot.send_message(row['user_id'], prompt_text(start, end, set(), limit),
                                                    reply_markup=keyboard(start, end, set(), today)), timeout=5)
            sent += 1
        except Exception as error:
            from diagnostics import failure
            failure(error, area='days_off', stage='ask')
        await asyncio.sleep(0.05)
    return sent


def roster_today():
    from workday import op_today
    return op_today()
