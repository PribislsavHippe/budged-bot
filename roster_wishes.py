"""Shared period wishes for the bot, roster editor and personal calendar."""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from postgrest.exceptions import APIError

import days_off
import db
import roster
from workday import MSK, op_today


NO_PERIOD = 'На эту дату ещё нет периода графика. Пока можно сохранить пожелание по дням недели или режиму работы.'
NO_ACCESS = 'Пожелания к графику доступны после подтверждения в ресторане. Попроси менеджера проверить твою заявку.'
SAVE_UNKNOWN = 'Не получил подтверждение сохранения. Проверь пожелание перед повтором.'
LOAD_FAILED = 'Не получилось загрузить пожелания. Попробуй ещё раз.'
CONFLICT = 'Пожелание уже изменилось. Проверь актуальный выбор и сохрани ещё раз.'


def migrated(row):
    return 'collect_wishes_at' in row


def in_roster(row, link, snapshot='draft'):
    return any(person.get('link') == link for person in (row.get(snapshot) or {}).get('people', []))


def outside(row):
    start, end = map(date.fromisoformat, (row['start_date'], row['end_date']))
    return f'Эта дата не входит в график на {days_off.period_text(start, end)}. Выбери день этого периода.'


async def supported():
    try:
        await db._execute(db.supabase.table('day_off_requests').select('desired_days').limit(1))
        return True
    except APIError as error:
        if error.code in days_off.MISSING:
            return False
        raise


async def employee(uid):
    try:
        rows = (await db._execute(db.supabase.table('employee_links').select('id,restaurant_id,report_name')
                                  .eq('user_id', uid).eq('status', 'approved').eq('role', 'staff')
                                  .limit(1))).data
    except APIError as error:
        if error.code in days_off.MISSING:
            return None
        raise
    return rows[0] if rows else None


async def period_rows(link, first, last=None):
    query = db.supabase.table('rosters').select('*').eq('restaurant_id', link['restaurant_id'])
    query = query.gte('end_date', first.isoformat())
    if last is not None:
        query = query.lte('start_date', last.isoformat())
    return (await db._execute(query.order('start_date'))).data


async def requests(period_id):
    rows = await db._pages(lambda: db.supabase.table('day_off_requests').select('*')
                           .eq('roster_id', period_id).eq('answered', True).order('link_id'))
    return {row['link_id']: row for row in rows}


async def save(uid, link, row, operation, changes, *, revision=None, weekdays=None, pattern=None, clear=False):
    """Write all parts of one reply in one transaction, including permanent wishes."""
    return (await db._execute(db.supabase.rpc('write_roster_wishes', {
        'p_actor': uid, 'p_link': link['id'], 'p_roster': row['id'] if row else None,
        'p_operation': str(operation), 'p_revision': revision, 'p_changes': changes,
        'p_weekdays': weekdays, 'p_pattern': pattern, 'p_clear': clear,
        'p_today': op_today().isoformat(),
    }), retries=1)).data


def limit_for(row, link):
    start, end = map(date.fromisoformat, (row['start_date'], row['end_date']))
    person = next((p for p in row['draft']['people'] if p.get('link') == link), {})
    return roster.off_limit((end - start).days + 1, bool(person.get('student')))


def over_limit(row, link, saved):
    limit = limit_for(row, link)
    if len(saved['days']) <= limit:
        return ''
    return f'На этот период обязательных выходных — {limit}. Остальные даты тоже записаны, их нужно согласовать с менеджером.'


def saved_dates(kind, days):
    dates = days_off.days_text(date.fromisoformat(day) for day in days)
    if kind == 'required':
        return f'Записал просьбу об обязательном выходном: {dates}. Менеджер увидит её при составлении графика.'
    return f'Записал желательные выходные: {dates}. Постараемся учесть их при составлении графика.'


def problem(result, row=None):
    return {
        'period': outside(row) if row else NO_PERIOD,
        'access': NO_ACCESS,
        'conflict': CONFLICT,
        'limit': 'Больше 12 выходных за один график записать нельзя. Напиши, какие важнее, или договорись с менеджером.',
    }.get(result['status'], SAVE_UNKNOWN)


async def calendar(uid, first, last):
    """Own request and published outcome, independent of the private money mode."""
    from identity_chat import enabled
    if not enabled():
        return {}
    try:
        link = await employee(uid)
        if not link:
            return {}
        periods = [row for row in await period_rows(link, first, last) if migrated(row)
                   and (in_roster(row, link['id']) or in_roster(row, link['id'], 'published'))]
        if not periods:
            return {}
        rows = (await db._execute(db.supabase.table('day_off_requests')
                                  .select('roster_id,days,desired_days,revision').eq('link_id', link['id'])
                                  .in_('roster_id', [r['id'] for r in periods]))).data
    except Exception as error:
        from diagnostics import failure
        failure(error, area='roster_wishes', stage='calendar')
        return {'error': LOAD_FAILED}

    answers = {r['roster_id']: r for r in rows}
    result = {}
    today = op_today()
    for period in periods:
        published = period.get('published')
        person = next((p for p in (published or {}).get('people', []) if p.get('link') == link['id']), None)
        cells = (published or {}).get('cells', {}).get(person['name'], {}) if person else {}
        answer = answers.get(period['id'], {})
        required = set(answer.get('days', []))
        preferred = set(answer.get('desired_days', []))
        day = max(first, date.fromisoformat(period['start_date']))
        end = min(last, date.fromisoformat(period['end_date']))
        while day <= end:
            key = day.isoformat()
            kind = 'required' if key in required else 'preferred' if key in preferred else None
            if key in result:
                return {'error': LOAD_FAILED}
            considered = False
            if person and kind:
                field = 'off_requested' if kind == 'required' else 'wish_dates'
                considered = key in (published or {}).get(field, {}).get(person['name'], [])
            result[key] = {
                'roster_id': period['id'], 'revision': answer.get('revision', 0),
                'kind': kind, 'published': published is not None,
                'changed': bool(kind and published is not None and not considered),
                'working': str(cells.get(key, '')).strip()[:1].isdigit() if person else None,
                'editable': day >= today and in_roster(period, link['id']),
            }
            day += timedelta(days=1)
    return {'days': result}


async def edit(uid, body):
    from identity_chat import enabled
    if not enabled():
        return {'error': NO_ACCESS}, 403
    try:
        day = date.fromisoformat(body['date'])
        roster_id = str(UUID(str(body['roster_id'])))
        operation = UUID(str(body['operation_id']))
        kind = body['kind']
        revision = body['revision']
        if kind not in ('required', 'preferred', 'none') or type(revision) is not int or revision < 0:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return {'error': 'Проверь дату и действие.'}, 400
    try:
        link = await employee(uid)
        if not link:
            return {'error': NO_ACCESS}, 403
        rows = (await db._execute(db.supabase.table('rosters').select('*')
                                  .eq('id', roster_id).eq('restaurant_id', link['restaurant_id']))).data
        row = next((r for r in rows if migrated(r)), None)
        if row is None:
            return {'error': NO_PERIOD}, 404
        result = await save(uid, link, row, operation, {day.isoformat(): kind}, revision=revision)
    except Exception as error:
        from diagnostics import failure
        failure(error, area='roster_wishes', stage='edit')
        return {'error': SAVE_UNKNOWN}, 503
    if result['status'] != 'ok':
        status = {'access': 403, 'conflict': 409}.get(result['status'], 400)
        return {'error': problem(result, row)}, status
    if kind == 'none':
        message = f'Просьбу о выходном на {days_off.days_text([day])} убрал.'
    elif row.get('published_at'):
        message = 'Просьба записана. Менеджер увидит изменение. Пока действует опубликованный график.'
    else:
        message = saved_dates(kind, [day.isoformat()])
    if kind == 'required' and (note := over_limit(row, link['id'], result)):
        message += ' ' + note
    return {'saved': True, 'message': message, 'revision': result['revision']}, 200


def reminder_text(row):
    start, end = map(date.fromisoformat, (row['start_date'], row['end_date']))
    span = days_off.period_text(start, end)
    if row['stage'] == 2:
        return (f'Если есть пожелания к графику на {span}, сейчас можно их записать в календаре под чаевыми '
                'или сообщением сюда. Если пожеланий нет, напиши «пожеланий нет».')
    example = f' Можно написать и здесь: «не могу работать {days_off.days_text([start])}'
    if end > start:
        example += f', желательно выходной {days_off.days_text([start + timedelta(days=1)])}'
    return (f'Собираем пожелания к графику на {span}. Если есть дни, когда нужен или желателен выходной, '
            f'запиши их в календаре под чаевыми.{example}». Если пожеланий нет, так и напиши.')


async def notice_current(claim):
    """A response or publication may have arrived while the send was reserved."""
    rows = (await db._execute(db.supabase.table('rosters').select('*').eq('id', claim['roster_id']))).data
    if not rows:
        return None
    row = rows[0]
    if row['published_at'] or row['start_date'] <= datetime.now(MSK).date().isoformat():
        return None
    if not in_roster(row, claim['link_id']):
        return None
    member = (await db._execute(db.supabase.table('employee_links').select('id')
                                .eq('id', claim['link_id']).eq('user_id', claim['user_id'])
                                .eq('status', 'approved').eq('role', 'staff')
                                .eq('restaurant_id', row['restaurant_id']))).data
    answered = (await db._execute(db.supabase.table('day_off_requests').select('answered')
                                  .eq('roster_id', row['id']).eq('link_id', claim['link_id'])
                                  .eq('answered', True))).data
    return {**claim, 'start_date': row['start_date'], 'end_date': row['end_date']} if member and not answered else None


async def remind(bot, roster_id=None):
    from identity_chat import enabled
    from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
    import asyncio
    if not enabled():
        return 0
    now = datetime.now(MSK)
    if not 10 <= now.hour < 20:
        return 0
    count = 0
    for _ in range(roster.MAX_PEOPLE):
        try:
            claim = (await db._execute(db.supabase.rpc('claim_roster_wish_notice', {
                'p_now': datetime.now(timezone.utc).isoformat(), 'p_roster': roster_id,
            }))).data
        except APIError as error:
            if error.code in (*days_off.MISSING, 'PGRST202', '42883'):
                return count
            from diagnostics import failure
            failure(error, area='roster_wishes', stage='claim')
            return count
        if not claim:
            return count
        try:
            current = await notice_current(claim)
            if current is None:
                continue
            markup = Markup(inline_keyboard=[[Button(text='Без изменений',
                                                      callback_data=f'off:keep:{claim["roster_id"]}')]])
            await asyncio.wait_for(bot.send_message(claim['user_id'], reminder_text(current),
                                                   reply_markup=markup, disable_notification=True), timeout=5)
            count += 1
            await db._execute(db.supabase.table('roster_wish_notices')
                              .update({'sent_at': datetime.now(timezone.utc).isoformat()})
                              .eq('roster_id', claim['roster_id']).eq('link_id', claim['link_id'])
                              .eq('stage', claim['stage']), retries=1)
        except Exception as error:
            from diagnostics import failure
            failure(error, area='roster_wishes', stage='notice')
        await asyncio.sleep(.05)
    return count
