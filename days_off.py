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


# ─── пожелания по дням недели ───────────────────────────────────────────────
# Человек пишет боту «выходной по вторникам»: дни недели запоминаются надолго, программа старается
# ставить обязательные выходные на эти дни и не ставить в них смены. Людей в смене это не перебивает.

WEEKDAY_WORDS = ((0, r'понедельник\w*|пн'), (1, r'вторник\w*|вт'), (2, r'сред[аеуы]\w*|ср'),
                 (3, r'четверг\w*|чт'), (4, r'пятниц\w*|пт'), (5, r'суббот\w*|сб'), (6, r'воскресен\w*|вс'))
OFF_INTENT = re.compile(r'выходн|отдых|не\s+могу|не\s+работ|не\s+став|свобод|желательн|хочу|удобн|не\s+выйд|не\s+выход')
DATIVE = ('понедельникам', 'вторникам', 'средам', 'четвергам', 'пятницам', 'субботам', 'воскресеньям')


# Число дня в обычной фразе: «17 и 19», «17-19». «2/2», «9:00», «17.11» и суммы от 100 не считаются.
DAY_NUMBER = re.compile(r'(?<![\d/:.,])(\d{1,2})(?:\s*[-–—]\s*(\d{1,2}))?(?![\d/:]|[.,]\d)')


def parse_request(text):
    """Просьба о выходных обычной фразой: дни недели (пожелание надолго) и числа (обязательные выходные
    на график). {'weekdays': [...], 'dates': {'numbers': [...], 'months': {...}} или None} или None."""
    t = (text or '').lower().replace('ё', 'е')
    if not t or len(t) > 300 or not OFF_INTENT.search(t):
        return None
    # «хочу работать по вторникам» — наоборот, это не просьба о выходном.
    if 'работ' in t and not re.search(r'не\s+работ', t):
        return None
    weekdays = sorted({n for n, pattern in WEEKDAY_WORDS if re.search(rf'\b(?:{pattern})\b', t)})
    numbers = []
    for first, second in DAY_NUMBER.findall(t):
        lo, hi = int(first), int(second or first)
        if 1 <= lo <= hi <= 31 and hi - lo <= 14:
            numbers += range(lo, hi + 1)
    if not weekdays and not numbers:
        return None
    months = {roster.MONTHS.index(m) + 1 for m in MONTH.findall(t)}
    return {'weekdays': weekdays, 'dates': {'numbers': sorted(set(numbers)), 'months': months} if numbers else None}


def parse_weekdays(text):
    """Дни недели, в которые человек просит выходной, или None."""
    found = parse_request(text)
    return found['weekdays'] or None if found else None


async def place_dates(link, uid, dates):
    """Куда записать числа: в график, на который бот ждёт ответ, иначе в ближайший график, куда они
    попадают. Возвращает (начало, конец, даты, None) или (None, None, None, что ответить)."""
    today = roster_today()
    rows = (await db._execute(db.supabase.table('rosters').select('start_date,end_date')
                              .eq('restaurant_id', link['restaurant_id']).gte('end_date', today.isoformat())
                              .order('start_date'))).data
    periods = [(date.fromisoformat(r['start_date']), date.fromisoformat(r['end_date'])) for r in rows]
    wait = await waiting(uid)
    if wait:
        periods.sort(key=lambda p: p[0] != wait['start'])
    first_problem = None
    for start, end in periods:
        limit = roster.off_limit((end - start).days + 1, await is_student(link['restaurant_id'], link['id']))
        chosen, problem = resolve(dates, start, end, today, limit)
        if chosen:
            return start, end, chosen, None
        first_problem = first_problem or problem
    return None, None, None, first_problem or ('Графика на эти числа пока нет. Когда менеджер попросит выходные, '
                                               'бот напишет — ответь ему числами.')


def weekdays_text(days):
    names = [DATIVE[d] for d in sorted(days)]
    return 'по ' + (names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' и ' + names[-1])


async def save_weekdays(link, days):
    from datetime import datetime, timezone
    table = db.supabase.table('weekday_wishes')
    if not days:
        await db._execute(table.delete().eq('link_id', link['id']), retries=1)
        return
    row = {'link_id': link['id'], 'restaurant_id': link['restaurant_id'], 'days': sorted(set(days)),
           'updated_at': datetime.now(timezone.utc).isoformat()}
    await db._execute(table.upsert(row, on_conflict='link_id'), retries=1)


async def weekdays_for(restaurant_id):
    """Пожелания ресторана: {id сотрудника: [дни недели]}. Без migration_v35 пожеланий нет."""
    try:
        rows = await db._pages(lambda: db.supabase.table('weekday_wishes').select('link_id,days')
                               .eq('restaurant_id', restaurant_id).order('link_id'))
    except APIError as error:
        if error.code in MISSING:
            return {}
        raise
    return {r['link_id']: [int(d) for d in r['days'] if 0 <= int(d) <= 6] for r in rows}
