"""Published shift lookup in the bot's private chat."""
import html
import re
from datetime import timedelta

from aiogram import F, Router

import db
import roles
import roster
import roster_api
from chat_dates import MONTHS
from roster_time import clock
from workday import now_msk


router = Router()
WEEKDAYS = ('понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье')
WEEKDAY_INPUT = ('понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу', 'воскресенье')
DAY_INPUT = r'сегодня|завтра|в(?:о)? (?:' + '|'.join(WEEKDAY_INPUT) + ')'
QUESTION = re.compile(rf'кто (?:из официантов )?работает(?: (?:{DAY_INPUT}))?')


def asked_date(text, today):
    phrase = ' '.join(text.casefold().split()).strip(' ?!.,—–')
    match = QUESTION.fullmatch(phrase)
    if not match:
        return None
    marker = phrase.rsplit(' ', 1)[-1]
    if marker == 'завтра':
        return today + timedelta(days=1)
    if marker in WEEKDAY_INPUT:
        return today + timedelta(days=(WEEKDAY_INPUT.index(marker) - today.weekday()) % 7)
    return today


def format_day(place, day, snapshot, contacts, requester):
    short_date = f'{day.day} {MONTHS[day.month - 1]}'
    heading = f'<b>{html.escape(place)} · {WEEKDAYS[day.weekday()]}, {short_date}</b>'
    if snapshot is None:
        return f'{heading}\nГрафик на {short_date} ещё не опубликован.'

    lines = []
    cells = snapshot.get('cells', {})
    for person in snapshot.get('people', []):
        name = person['name']
        value = cells.get(name, {}).get(day.isoformat())
        start, end, _ = roster_api.parse_cell(value or '')
        if start is None:
            continue
        rendered = html.escape(name)
        uid = contacts.get(person.get('link'))
        if uid and uid != requester:
            rendered = f'<a href="tg://user?id={int(uid)}">{rendered}</a>'
        end = end if end is not None else roster.end_of(start)
        lines.append((start, name.casefold(), f'{clock(start)}–{clock(end)} — {rendered}'))
    body = '\n'.join(line for _, _, line in sorted(lines))
    return f'{heading}\n' + (body or f'На {short_date} в графике нет смен официантов.')


async def place_day(place, day, uid):
    restaurant_id = place['id']
    target = day.isoformat()
    rows = (await db._execute(db.supabase.table('rosters').select('published')
        .eq('restaurant_id', restaurant_id).lte('start_date', target).gte('end_date', target)
        .not_.is_('published', 'null').order('published_at', desc=True).order('id').limit(1))).data
    snapshot = rows[0]['published'] if rows else None
    contacts = {}
    if snapshot:
        members = await db._pages(lambda: db.supabase.table('employee_links').select('id,user_id')
            .eq('restaurant_id', restaurant_id).eq('status', 'approved').order('id'))
        contacts = {member['id']: member['user_id'] for member in members}
    return format_day(place['name'], day, snapshot, contacts, uid)


def team_shift_request(message):
    day = asked_date(message.text, now_msk().date())
    return {'requested_shift_day': day} if day else False


@router.message(F.chat.type == 'private', F.forward_origin == None, F.text,
                team_shift_request)
async def who_works(message, requested_shift_day):
    day = requested_shift_day
    uid = message.from_user.id
    try:
        places, _ = await roster_api.team_places(uid)
        by_id = {place['id']: place for place in places}
        by_id.update((place['id'], place) for place in await roles.places(uid))
        blocks = [await place_day(place, day, uid) for place in by_id.values()]
    except Exception as error:
        from diagnostics import failure
        failure(error, area='roster', stage='team_shift_chat')
        await message.answer('Не получилось открыть график. Попробуй ещё раз чуть позже.')
        return
    if blocks:
        await message.answer('\n\n'.join(blocks), disable_web_page_preview=True)
