"""Turn one explicit wish message into one atomic change."""

from datetime import date, timedelta
import re
from uuid import NAMESPACE_URL, UUID, uuid5

from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup

import days_off
import roster
import roster_wishes as wishes

_PREFERRED = r'желательн\w*|по возможности|не\s*обязательн\w*'
_REQUIRED = r'обязательн\w*|не (?:смогу|могу|буду работать)|нуж(?:ен|ны|но)\s+выходн\w*'
_MARKER = re.compile(rf'(?<!\w)(?:{_PREFERRED}|{_REQUIRED})')
_SPLIT = re.compile(rf'[;\n]+|(?={_MARKER.pattern})')
_INTENT = re.compile(r'выходн|отдых|не\s+став|не\s+выйд|не\s+выход')
_CYCLE = re.compile(
    rf'\b(?:режим\w*|график\w*|хочу работать|удобно(?: работать)?|нравится)\s+{days_off.PATTERN.pattern}'
)
_CYCLE_ALONE = re.compile(rf'(?:(?:и|работать)\s+)?{days_off.PATTERN.pattern}[.!]?')
_CYCLE_SUFFIX = re.compile(rf'{days_off.PATTERN.pattern}\s+(?:удобно|нравится)\b')
_ISO = re.compile(r'\b(\d{4})-(\d{2})-(\d{2})\b')
_MONTH = '|'.join(roster.MONTHS)
_DATE = re.compile(
    rf'(?<![\d./:])(?P<day>\d{{1,2}})(?:\s*[-–—]\s*(?P<last>\d{{1,2}}))?'
    rf'(?:(?:[./](?P<nmonth>\d{{1,2}})(?:[./](?P<year>\d{{4}}))?)|'
    rf'(?:(?:-?(?:го|е))?\s+(?P<month>{_MONTH})\b)|-?(?:го|е)\b)?(?![\d/:]|\.\d|\w)'
)


def parse_wish(text, *, bare=False):
    text = (text or '').lower().replace('ё', 'е').strip()
    if not text or len(text) > 300:
        return None
    if text.rstrip('.!') in ('пожеланий нет', 'без изменений') or (bare and days_off.NONE.fullmatch(text)):
        return {'dates': [], 'weekdays': [], 'pattern': None, 'empty': text.rstrip('.!') != 'без изменений'}
    text = _ISO.sub(r'\3.\2.\1', text)
    has_weekday = any(re.search(rf'\b(?:{rule})\b', text) for _, rule in days_off.WEEKDAY_WORDS)
    pattern_match = _CYCLE_ALONE.fullmatch(text) or _CYCLE.search(text) or _CYCLE_SUFFIX.search(text)
    if not pattern_match and has_weekday:
        pattern_match = re.search(rf'\bи\s+{days_off.PATTERN.pattern}\s*$', text)
    explicit = (_INTENT.search(text) or days_off.NOT_WORK.search(text) or _MARKER.search(text)
                or has_weekday and re.search(r'\b(?:хочу|нужно|нужен|нужны)\b', text))
    if not explicit and not bare and not pattern_match:
        return None
    pattern = f'{pattern_match[1]}/{pattern_match[2]}' if pattern_match else None
    plain = text[:pattern_match.start()] + ' ' + text[pattern_match.end():] if pattern_match else text
    dates, weekdays = [], set()
    kind = None
    for part in _SPLIT.split(plain):
        part = part.strip()
        if re.search(r'рабо[тч]', part) and not (re.match(_PREFERRED, part) or days_off.NOT_WORK.search(part)):
            continue
        if re.match(_PREFERRED, part):
            kind = 'preferred'
        elif re.match(_REQUIRED, part):
            kind = 'required'
        for number, rule in days_off.WEEKDAY_WORDS:
            if re.search(rf'\b(?:{rule})\b', part):
                weekdays.add(number)
        dates.extend(date_references(part, kind))
    if not (dates or weekdays or pattern):
        return None
    return {'dates': dates, 'weekdays': sorted(weekdays), 'pattern': pattern, 'empty': None}


def date_references(text, kind=None):
    matches = []
    for match in _DATE.finditer(text):
        lo, hi = int(match['day']), int(match['last'] or match['day'])
        month = int(match['nmonth']) if match['nmonth'] else (
            roster.MONTHS.index(match['month']) + 1 if match['month'] else None)
        year = int(match['year']) if match['year'] else None
        matches.append((lo, hi, month, year))
    dates = []
    last_month = (None, None)
    for i, (lo, hi, month, year) in enumerate(matches):
        if month:
            last_month = month, year
        else:
            month, year = next(((m, y) for _, _, m, y in matches[i + 1:] if m), last_month)
        dates.append([lo, hi, month, year, kind])
    return dates


def resolve_dates(refs, row, today):
    """Each reference has to resolve to exactly one date inside the period."""
    start, end = map(date.fromisoformat, (row['start_date'], row['end_date']))
    span = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    changes = {}
    for lo, hi, month, year, kind in refs:
        if not 1 <= lo <= hi <= 31 or hi - lo > 14 or (month and not 1 <= month <= 12):
            return None
        for number in range(lo, hi + 1):
            found = [day for day in span if day >= today and day.day == number
                     and (not month or day.month == month) and (not year or day.year == year)]
            if len(found) != 1:
                return None
            key = found[0].isoformat()
            if key in changes and changes[key] != kind:
                return None
            changes[key] = kind
    return changes


def operation(chat_id, message_id):
    return uuid5(NAMESPACE_URL, f'roster-wish:{chat_id}:{message_id}')


def reply_period(message):
    reply = getattr(message, 'reply_to_message', None)
    if not reply or not reply.from_user or reply.from_user.id != message.bot.id:
        return None
    for row in getattr(getattr(reply, 'reply_markup', None), 'inline_keyboard', []):
        for button in row:
            data = button.callback_data or ''
            if data.startswith('off:keep:'):
                return data.removeprefix('off:keep:')
    return None


async def active_period(link, periods):
    if not periods:
        return None
    import db
    ids = [r['id'] for r in periods]
    notices = (await db._execute(db.supabase.table('roster_wish_notices').select('roster_id')
                                 .eq('link_id', link['id']).eq('stage', 1)
                                 .in_('roster_id', ids).not_.is_('sent_at', 'null'))).data
    sent = {n['roster_id'] for n in notices}
    for row in periods:
        if row['id'] in sent and link['id'] not in await wishes.requests(row['id']):
            return row
    return None


async def message_filter(message):
    from identity_chat import enabled
    if getattr(message, 'forward_origin', None):
        return False
    text = message.text
    parsed = parse_wish(text)
    bare = days_off.parse(text)
    if not parsed and not bare:
        return False
    if not enabled():
        return unavailable_preference(parsed)
    try:
        return await match_message(message, parsed, bare)
    except Exception as error:
        from diagnostics import failure
        failure(error, area='roster_wishes', stage='match_message')
        return {'parsed_wish': parsed, 'unavailable': True} if parsed else False


async def match_message(message, parsed, bare):
    if not await wishes.supported():
        return unavailable_preference(parsed)
    link = await wishes.employee(message.from_user.id)
    if not link:
        return {'parsed_wish': parsed, 'wish_link': None} if parsed else False
    rows = [r for r in await wishes.period_rows(link, days_off.roster_today())
            if wishes.migrated(r) and wishes.in_roster(r, link['id'])]
    target = reply_period(message)
    period = next((r for r in rows if r['id'] == target), None)
    if target and period is None:
        return {'parsed_wish': parsed or parse_wish(message.text, bare=True), 'wish_link': link, 'wish_rows': []}
    wait = await days_off.waiting(message.from_user.id) if period is None and bare and not parsed else None
    # A standalone number can be a tip. Require a direct reply or explicit
    # wording, except when a legacy edit button is waiting for numbers.
    if not parsed and not target and not wait and not bare.get('none') and len(bare['numbers']) == 1:
        return False
    if period is None and wait:
        period = next((r for r in rows if r['start_date'] == wait['start'].isoformat()), None)
    if period is None:
        period = await active_period(link, rows)
    if parsed and parsed['empty'] is False and not period:
        return False
    if not parsed:
        if not period:
            return False
        parsed = parse_wish(message.text, bare=True)
        if not parsed:
            return False
    if period:
        rows = [period] if target else [period, *(r for r in rows if r is not period)]
    return {'parsed_wish': parsed, 'wish_link': link, 'wish_rows': rows}


def unavailable_preference(parsed):
    # The legacy flow treats any date as required.
    if parsed and any(ref[-1] == 'preferred' for ref in parsed['dates']):
        return {'parsed_wish': parsed, 'unavailable': True}
    return False


def summary(row, parsed, changes, result, link_id):
    required = sorted(day for day, kind in changes.items() if kind == 'required')
    preferred = sorted(day for day, kind in changes.items() if kind == 'preferred')
    span = days_off.period_text(date.fromisoformat(row['start_date']), date.fromisoformat(row['end_date'])) if row else ''
    if parsed['empty'] is True:
        return f'Записал: пожеланий на {span} нет. Повторно напоминать об этом графике не буду.'
    if parsed['empty'] is False:
        return f'Пожелания на {span} оставил без изменений.'
    pattern = parsed['pattern']
    if row and (bool(required) + bool(preferred) + bool(pattern) > 1):
        date_text = lambda days: days_off.days_text(map(date.fromisoformat, days)) or 'не указаны'
        answer = (f'Записал пожелания на {span}.\nОбязательные выходные: {date_text(required)}.\n'
                  f'Желательные выходные: {date_text(preferred)}.')
        if pattern:
            answer += f'\nРежим: {pattern}.'
    elif required or preferred:
        answer = wishes.saved_dates('required' if required else 'preferred', required or preferred)
    elif pattern:
        answer = (f'Записал пожелание работать {pattern}. При составлении графика постараемся его учесть, '
                  'но отдельные смены и выходные могут отличаться.')
    else:
        answer = ''
    if parsed['weekdays']:
        answer += ('\n' if answer else '') + f'Записал пожелание: выходные {days_off.weekdays_text(result["weekdays"])}.'
    if row and required and (note := wishes.over_limit(row, link_id, result)):
        answer += ' ' + note
    return answer


async def write_reply(message, state, parsed_wish, wish_link=None, wish_rows=(), unavailable=False):
    if unavailable:
        await message.answer(wishes.LOAD_FAILED)
        return
    if not wish_link:
        await message.answer(wishes.NO_ACCESS)
        return
    if not parsed_wish:
        await message.answer(wishes.NO_PERIOD)
        return
    parsed, row, changes = parsed_wish, None, {}
    if parsed['dates']:
        for candidate in wish_rows:
            found = resolve_dates(parsed['dates'], candidate, days_off.roster_today())
            if found:
                row, changes = candidate, found
                break
        if not row:
            await message.answer(wishes.outside(wish_rows[0]) if wish_rows else wishes.NO_PERIOD)
            return
    elif wish_rows:
        row = wish_rows[0]
    elif parsed['empty'] is not None:
        await message.answer(wishes.NO_PERIOD)
        return

    oid = operation(message.chat.id, message.message_id)
    if any(kind is None for kind in changes.values()):
        await state.update_data(roster_wish_pending={
            'id': str(oid), 'roster_id': row['id'], 'parsed': parsed, 'changes': changes,
            'start_date': row['start_date'], 'end_date': row['end_date'],
        })
        days = days_off.days_text(date.fromisoformat(d) for d, kind in changes.items() if kind is None)
        buttons = [Button(text=label, callback_data=f'off:kind:{oid.hex}:{key}') for label, key in
                   [('Обязательные', 'r'), ('Желательные', 'p'), ('Отмена', 'x')]]
        await message.answer(f'Записать {days} как обязательные или желательные выходные?',
                             reply_markup=Markup(inline_keyboard=[buttons]))
        return
    await commit_reply(message, message.from_user.id, wish_link, row, oid, parsed, changes)


async def commit_reply(message, uid, link, row, oid, parsed, changes):
    try:
        result = await wishes.save(uid, link, row, oid, changes, weekdays=parsed['weekdays'],
                                   pattern=parsed['pattern'], clear=parsed['empty'] is True)
    except Exception as error:
        from diagnostics import failure
        failure(error, area='roster_wishes', stage='reply')
        await message.answer(wishes.SAVE_UNKNOWN)
        return False
    if result['status'] != 'ok':
        await message.answer(wishes.problem(result, row))
        return False
    if row:
        import research
        research.track(uid, 'days_off_answered', source='bot', screen='schedule')
    markup = (Markup(inline_keyboard=[[Button(text='Удалить пожелание', callback_data='off:w')]])
              if parsed['weekdays'] or parsed['pattern'] else None)
    await message.answer(summary(row, parsed, changes, result, link['id']), reply_markup=markup)
    return True


async def callback_kind(callback, state):
    _, _, token, kind = callback.data.split(':')
    data = await state.get_data()
    pending = data.get('roster_wish_pending')
    if not pending or pending['id'].replace('-', '') != token or kind not in ('r', 'p', 'x'):
        await callback.answer('Эта кнопка устарела. Напиши числа выходных сообщением.', show_alert=True)
        return
    await callback.answer()
    if kind == 'x':
        data.pop('roster_wish_pending', None)
        await state.set_data(data)
        await callback.message.edit_text('Отмена.')
        return
    uid = callback.from_user.id
    link = await wishes.employee(uid)
    row = next((r for r in await wishes.period_rows(link, days_off.roster_today())
                if r['id'] == pending['roster_id'] and r['start_date'] == pending['start_date']
                and r['end_date'] == pending['end_date']), None) if link else None
    if not row:
        await callback.message.answer(wishes.NO_PERIOD)
        return
    changes = {day: value or ('required' if kind == 'r' else 'preferred')
               for day, value in pending['changes'].items()}
    if await commit_reply(callback.message, uid, link, row, UUID(pending['id']), pending['parsed'], changes):
        data.pop('roster_wish_pending', None)
        await state.set_data(data)
        await callback.message.edit_reply_markup(reply_markup=None)


async def callback_keep(callback):
    uid = callback.from_user.id
    link = await wishes.employee(uid)
    key = callback.data.removeprefix('off:keep:')
    row = next((r for r in await wishes.period_rows(link, days_off.roster_today()) if r['id'] == key), None) if link else None
    await callback.answer()
    if not row:
        await callback.message.answer(wishes.NO_PERIOD)
        return
    parsed = {'dates': [], 'weekdays': [], 'pattern': None, 'empty': False}
    oid = uuid5(NAMESPACE_URL, f'roster-wish-keep:{uid}:{key}')
    if await commit_reply(callback.message, uid, link, row, oid, parsed, {}):
        await callback.message.edit_reply_markup(reply_markup=None)
