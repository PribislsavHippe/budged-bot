"""Interpret and confirm an employee's own shift-time rules in Telegram."""

import re
from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup

import days_off
import roster_time_store as store
import roster_wish_chat as off
import roster_wishes as wishes


WORDS = {
    'одного': 1, 'часа': 1, 'двух': 2, 'трех': 3, 'четырех': 4, 'пяти': 5, 'шести': 6,
    'семи': 7, 'восьми': 8, 'девяти': 9, 'десяти': 10, 'одиннадцати': 11, 'двенадцати': 12,
}
TIME = rf'(?:\d{{1,2}}(?::\d{{2}})?|{"|".join(WORDS)})(?:\s+час(?:а|ов)?)?(?:\s+(?:утра|дня|вечера))?'
RANGE = re.compile(rf'\b(?:с|от)\s+(?P<start>{TIME})\s*(?:до|[-–—])\s*(?P<end>{TIME})(?!\d|:\d)')
DASH_RANGE = re.compile(rf'(?<![\d./-])(?P<start>\d{{1,2}}(?::\d{{2}})?)\s*[-–—]\s*'
                        rf'(?P<end>\d{{1,2}}(?::\d{{2}})?)(?![\d./]|:\d)(?!\s+(?:числа|{off._MONTH})\b)')
START = re.compile(rf'\b(?:с|к|в|(?:не\s+)?раньше|после)\s+(?P<start>{TIME})'
                   rf'(?![\d:./]|-?(?:го|е)\b)(?!\s+(?:числа|{off._MONTH})\b|\s+по\s+\d)')
HARD = re.compile(r'\b(?:только|не\s+раньше|не\s+позже|доступ\w*|ограничен\w*)\b')
SOFT = re.compile(r'\b(?:хочу|хотел\w*|желательн\w*|удобн\w*|лучше|по возможности|предпоч\w*|подойдет|ближе)\b')
WORK = re.compile(r'\b(?:могу|смогу|буду|выйти|выйду|выхожу|работ\w*|смен\w*|начина\w*|начал\w*|постав\w*|приход\w*)\b')
NEGATIVE = re.compile(r'\bне\s+(?:могу|смогу|буду)\b')
MONEY = re.compile(r'₽|\b(?:рубл\w*|такси|чай|чаев\w*|расход\w*|продаж\w*)\b')
RELATIVE = re.compile(r'\b(сегодня|завтра|послезавтра)\b')
RELATIVE_DAYS = {'сегодня': 0, 'завтра': 1, 'послезавтра': 2}
ASK_DATE = 'На какие даты или дни недели это правило? Напиши месяц, если речь о конкретных числах.'
ASK_TIME = 'Уточни время и скажи, это ограничение или пожелание. Например: «26 ноября могу только с 9 до 17».'
BAD_CLOCK = 'Проверь часы. Начало должно быть раньше окончания, ресторан закрывается в 23:00.'
STALE = 'Это правило уже изменилось. Напиши новое пожелание или используй кнопку под последним сохранением.'


def minutes(value):
    first, *rest = value.split()
    if first in WORDS:
        hour, minute = WORDS[first], 0
    else:
        h, _, m = first.partition(':')
        hour, minute = int(h), int(m or '0')
    if rest and rest[-1] in ('дня', 'вечера') and hour < 12:
        hour += 12
    if minute >= 60:
        raise ValueError
    return hour * 60 + minute


def _weekday(text):
    return [day for day, rule in days_off.WEEKDAY_WORDS
            if re.search(rf'\b(?:{rule})\b', text) or day == 6 and re.search(r'\bвск\b', text)]


def parse(text):
    """Return extracted fields or a specific question for an incomplete wish."""
    text = ' '.join((text or '').lower().replace('ё', 'е').split())
    if not text or text.startswith('/') or len(text) > 400 or MONEY.search(text):
        return None
    text = off._ISO.sub(r'\3.\2.\1', text)
    explicit_time = RANGE.search(text) or START.search(text)
    time = explicit_time or DASH_RANGE.search(text)
    if not time:
        if (WORK.search(text) or SOFT.search(text) or HARD.search(text)) and re.search(r'\bдо\s+\d{1,2}', text):
            return {'question': ASK_TIME}
        return None
    other = text[:time.start()] + ' ' + text[time.end():]
    weekdays = _weekday(other)
    dates = off.date_references(other)
    relative = RELATIVE.findall(other)
    if not explicit_time and ':' not in time.group() and not (dates or weekdays or relative):
        return None
    remainder = off._DATE.sub(' ', other)
    implicit = dates and not re.search(r'\w', re.sub(r'\b(?:и|на|число|числа)\b', '', remainder))
    if not (HARD.search(text) or WORK.search(text) or SOFT.search(text) or weekdays or relative or implicit):
        return None
    if NEGATIVE.search(text) or RANGE.search(other) or START.search(other) or re.search(r'\bвыходн(?:ой|ые|ых|ого)\b', other):
        return {'question': ASK_TIME}
    if not weekdays and not dates and not relative:
        return {'question': ASK_DATE}
    if sum(map(bool, (weekdays, dates, relative))) > 1:
        return {'question': ASK_DATE}
    try:
        start = minutes(time['start'])
        end = minutes(time.groupdict()['end']) if time.groupdict().get('end') else None
    except ValueError:
        return {'question': BAD_CLOCK}
    if not 360 <= start < 1380 or end is not None and not start < end <= 1380:
        return {'question': BAD_CLOCK}
    limited = HARD.search(text) or re.search(r'\b(?:могу|смогу)\b', text) and not SOFT.search(text)
    recurring = bool(re.search(r'\b(?:кажд\w*|еженедельно|по)\b', other.replace('по возможности', '')))
    return {'dates': dates, 'relative': relative, 'weekdays': weekdays, 'recurring': recurring,
            'kind': 'limit' if limited else 'wish',
            'start': start, 'end': end}


async def time_filter(message):
    if getattr(message, 'forward_origin', None):
        return False
    result = parse(message.text)
    return {'parsed_time': result} if result else False


async def begin(message, state, parsed_time):
    from identity_chat import enabled
    if parsed_time.get('question'):
        await message.answer(parsed_time['question'])
        return
    uid = message.from_user.id
    try:
        link = await wishes.employee(uid) if enabled() else None
        if not link:
            await message.answer(wishes.NO_ACCESS)
            return
        if not await store.supported():
            await message.answer(wishes.LOAD_FAILED)
            return
        today = days_off.roster_today()
        periods = [r for r in await wishes.period_rows(link, today) if wishes.in_roster(r, link['id'])]
        target = off.reply_period(message)
        chosen = next((r for r in periods if r['id'] == target), None)
        available = [chosen] if chosen else periods if not target else []
        refs = parsed_time['dates']
        if parsed_time['relative']:
            actual = [today + timedelta(days=RELATIVE_DAYS[word]) for word in parsed_time['relative']]
            refs = [[day.day, day.day, day.month, day.year, None] for day in actual]
        if refs:
            possible = [(row, off.resolve_dates(refs, row, today)) for row in available]
            matches = [(row, found) for row, found in possible if found]
            if len(matches) != 1:
                await message.answer(ASK_DATE if matches else wishes.outside(possible[0][0])
                                     if len(possible) == 1 else wishes.NO_PERIOD)
                return
            chosen, found = matches[0]
            keys = sorted(found)
        elif not parsed_time['recurring']:
            upcoming = [(row, days[0]) for row in available
                        if (days := store.next_weekdays(row, today, parsed_time['weekdays']))]
            if not upcoming:
                await message.answer(wishes.NO_PERIOD)
                return
            chosen, _ = min(upcoming, key=lambda pair: pair[1])
            keys = store.next_weekdays(chosen, today, parsed_time['weekdays'])
        else:
            keys = [str(d) for d in parsed_time['weekdays']]
            if chosen is None and not target:
                chosen = await off.active_period(link, periods)
        operation = uuid5(NAMESPACE_URL, f'shift-time:{uid}:{message.message_id}')
        pending = {'uid': uid, 'op': str(operation), 'keys': keys, 'kind': parsed_time['kind'],
                   'start': parsed_time['start'], 'end': parsed_time['end'], 'period': chosen['id'] if chosen else None}
        await state.update_data(shift_time=pending)
        buttons = Markup(inline_keyboard=[[Button(text='Сохранить', callback_data=f'off:time:s:{operation.hex}'),
                                           Button(text='Отмена', callback_data=f'off:time:x:{operation.hex}')]])
        await message.answer(f'Проверь, как я понял твоё сообщение.\n{store.rule_lines(pending)}\nСохранить?',
                             reply_markup=buttons)
    except Exception as error:
        from diagnostics import failure
        failure(error, area='roster_time', stage='confirm')
        await message.answer(wishes.LOAD_FAILED)


async def action(callback, state):
    _, _, command, hex_id = callback.data.split(':')
    uid = callback.from_user.id
    data = await state.get_data()
    pending = data.get('shift_time') or {}
    if command != 'r' and (pending.get('uid') != uid or pending['op'].replace('-', '') != hex_id):
        await callback.answer(STALE, show_alert=True)
        return
    await callback.answer()
    if command == 'x':
        await state.update_data(shift_time=None)
        await callback.message.edit_text('Отмена.')
        return
    try:
        if command == 'r':
            original = UUID(hex_id)
            op = uuid5(NAMESPACE_URL, f'shift-time-remove:{uid}:{original}')
            result = await store.write(uid, {'op': str(op)}, remove=str(original))
        else:
            result = await store.write(uid, pending)
    except Exception as error:
        from diagnostics import failure
        failure(error, area='roster_time', stage='write')
        await callback.message.answer(wishes.SAVE_UNKNOWN)
        return
    if result != 'ok':
        await callback.message.answer({'stale': STALE, 'access': wishes.NO_ACCESS, 'period': wishes.NO_PERIOD}
                                      .get(result, wishes.SAVE_UNKNOWN))
        return
    if command == 'r':
        await callback.message.edit_text('Убрал правило времени для графика.')
    else:
        await state.update_data(shift_time=None)
        buttons = Markup(inline_keyboard=[[Button(text='Убрать правило', callback_data=f'off:time:r:{hex_id}')]])
        await callback.message.edit_text(f'Записал время для графика.\n{store.rule_lines(pending)}', reply_markup=buttons)
