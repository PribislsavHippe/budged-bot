"""Обязательные выходные, которые сотрудники пишут боту в Telegram обычным текстом.

Менеджер просит указать выходные на период: каждому приходит сообщение «напиши числа». Бот помнит,
от кого ждёт ответ (`day_off_requests.waiting`), и принимает только числа этого периода: если график
с 16 по 30, то «1» или «5» он не примет. Записанное сразу сохраняется; при сборке графика эти дни
ставятся как выходные, остальные до нормы расставляет программа. Менеджер видит только, кто ответил.
"""
import asyncio
import re
from datetime import date, timedelta

from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from postgrest.exceptions import APIError

import db
import roster

MISSING = ('42P01', 'PGRST205', '42703', 'PGRST204')
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


def edit_markup(start, end):
    return Markup(inline_keyboard=[[Button(text='Изменить', callback_data=f'{PREFIX}:e:{start}:{end}')]])


def prompt_text(start, end, limit, today=None, again=False):
    """Просьба написать числа; пример берётся из самого периода."""
    first = max(start, today) if today else start
    days = [first + timedelta(days=i) for i in range((end - first).days + 1)]
    example = ', '.join(str(d.day) for d in (days[1], days[8])) if len(days) > 8 else ''
    head = 'Напиши числа заново' if again else f'График на {period_text(start, end)}. Напиши числа обязательных выходных'
    tail = f' Например: {example}.' if example else ''
    return f'{head} — до {limit}.{tail}\nЕсли выходные не нужны, напиши «нет».'


def summary_text(start, end, chosen):
    if not chosen:
        return f'График на {period_text(start, end)}. Выходные не выбраны, их расставит программа.'
    return f'График на {period_text(start, end)}. Записал выходные: {days_text(chosen)}.'


NONE = re.compile(r'(нет|без выходных|не нужно|не надо|ничего|пропустить)[.!]?')
FILLER = re.compile(r'\b(и|числа|число|выходные|выходной|мои)\b')
MONTH = re.compile(r'\b(' + '|'.join(roster.MONTHS) + r')\b')


def parse(text):
    """Разбирает сообщение с числами. None — это не похоже на выходные (пусть обработает бот как обычно).

    Иначе {'none': True} для «нет» или {'numbers': [..], 'months': {..}}. Число больше 31 — не дата
    (скорее сумма), поэтому такое сообщение сюда не попадает."""
    t = (text or '').lower().replace('ё', 'е').strip()
    if not t or len(t) > 60:
        return None
    if NONE.fullmatch(t):
        return {'none': True}
    months = {roster.MONTHS.index(m) + 1 for m in MONTH.findall(t)}
    rest = FILLER.sub(' ', MONTH.sub(' ', t)).strip()
    if not rest or not re.fullmatch(r'[\d\s,;\-–—]+', rest) or not re.search(r'\d', rest):
        return None
    numbers = []
    for first, second in re.findall(r'(\d+)(?:\s*[-–—]\s*(\d+))?', rest):
        lo, hi = int(first), int(second or first)
        if not 1 <= lo <= hi <= 31 or hi - lo > 14:
            return None
        numbers += range(lo, hi + 1)
    return {'numbers': sorted(set(numbers)), 'months': months}


def resolve(parsed, start, end, today, limit):
    """Превращает числа в даты периода. Возвращает (даты, None) или (None, что ответить сотруднику)."""
    if parsed.get('none'):
        return set(), None
    span = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    if parsed['months'] and not parsed['months'] <= {d.month for d in span}:
        return None, f'График на {period_text(start, end)}. Пришли числа этого периода.'
    chosen, outside, past = set(), [], []
    for number in parsed['numbers']:
        day = next((d for d in span if d.day == number and d >= today), None)
        if day:
            chosen.add(day)
        elif any(d.day == number for d in span):
            past.append(number)
        else:
            outside.append(number)
    if outside:
        return None, f'График на {period_text(start, end)}. Числа {", ".join(map(str, outside))} в него не входят. Пришли числа ещё раз.'
    if past:
        return None, f'Эти дни уже прошли: {", ".join(map(str, past))}. Пришли числа ещё раз.'
    if len(chosen) > limit:
        return None, f'Можно выбрать до {limit}, а в сообщении {len(chosen)}. Пришли числа ещё раз.'
    return chosen, None


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


async def waiting(uid):
    """Кого и по какому периоду бот ждёт: {'link': ..., 'start', 'end'} или None."""
    link = await member(uid)
    if not link:
        return None
    rows = (await db._execute(db.supabase.table('day_off_requests').select('start_date,end_date')
                              .eq('link_id', link['id']).eq('waiting', True)
                              .gte('end_date', roster_today().isoformat()).order('start_date').limit(1))).data
    if not rows:
        return None
    return {'link': link, 'start': date.fromisoformat(rows[0]['start_date']), 'end': date.fromisoformat(rows[0]['end_date'])}


async def reopen(link, start):
    """«Изменить»: бот снова ждёт числа, прежний ответ остаётся в силе, пока не пришёл новый."""
    await db._execute(db.supabase.table('day_off_requests').update({'waiting': True})
                      .eq('link_id', link['id']).eq('start_date', start.isoformat()), retries=1)


async def get(link_id, start):
    rows = (await db._execute(db.supabase.table('day_off_requests').select('days').eq('link_id', link_id)
                              .eq('start_date', start.isoformat()))).data
    return {date.fromisoformat(d) for d in rows[0]['days']} if rows else None


async def save(link, start, end, days):
    from datetime import datetime, timezone
    row = {'link_id': link['id'], 'restaurant_id': link['restaurant_id'], 'start_date': start.isoformat(),
           'end_date': end.isoformat(), 'days': sorted(d.isoformat() for d in days),
           'updated_at': datetime.now(timezone.utc).isoformat(), 'answered': True, 'waiting': False}
    await db._execute(db.supabase.table('day_off_requests').upsert(row, on_conflict='link_id,start_date'), retries=1)


async def for_period(restaurant_id, start):
    """Ответы сотрудников на период: {id сотрудника: [даты]}. Без migration_v32 ответов нет."""
    try:
        rows = await db._pages(lambda: db.supabase.table('day_off_requests').select('link_id,days')
                               .eq('restaurant_id', restaurant_id).eq('start_date', start.isoformat())
                               .eq('answered', True).order('link_id'))
    except APIError as error:
        if error.code in MISSING:
            return {}
        raise
    return {r['link_id']: list(r['days']) for r in rows}


async def ask(bot, restaurant_id, start, end, links, answered):
    """Просит написать выходные тех, кто ещё не ответил, и запоминает, что ждёт от них числа."""
    today = roster_today()
    sent = 0
    users = (await db._execute(db.supabase.table('employee_links').select('id,user_id,status')
                               .in_('id', list(links)))).data if links else []
    for row in users:
        if row['status'] != 'approved' or row['id'] in answered:
            continue
        limit = roster.off_limit((end - start).days + 1, await is_student(restaurant_id, row['id']))
        try:
            await asyncio.wait_for(bot.send_message(row['user_id'], prompt_text(start, end, limit, today)), timeout=5)
            wait = {'link_id': row['id'], 'restaurant_id': restaurant_id, 'start_date': start.isoformat(),
                    'end_date': end.isoformat(), 'days': [], 'answered': False, 'waiting': True}
            await db._execute(db.supabase.table('day_off_requests')
                              .upsert(wait, on_conflict='link_id,start_date', ignore_duplicates=True), retries=1)
            sent += 1
        except Exception as error:
            from diagnostics import failure
            failure(error, area='days_off', stage='ask')
        await asyncio.sleep(0.05)
    return sent


def roster_today():
    from workday import op_today
    return op_today()
