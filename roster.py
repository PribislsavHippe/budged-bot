"""Прототип: сборка графика официантов и разбор его слабых мест. Только правила, без нейросети.

Правила владельца (2 ноября 2026): смена 12 часов, ресторан закрывается в 23;
в будни 5 человек, в выходные 6, число можно менять на дату; в закрытии всегда 3
(приход с 11 и позже); в пт–вс открывают двое, в будни один; обязательные выходные
неприкосновенны; в смены подряд приход не раньше, чем накануне; денежные дни поровну.

Из таблицы выведено и ещё не подтверждено: часы открытия (8 в сб–вс, 9 в пн–пт),
раскладка приходов по дням и мягкий предел в 3 смены подряд.

Имена живут только в сообщении владельца: модуль ничего не сохраняет и не пишет в логи.
"""
import html
import math
import random
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

CLOSE_HOUR = 23
SHIFT_HOURS = 12
CLOSERS = 3
LAST_CLOSER_START = 11
WEEKDAY_COUNT = 5
WEEKEND_COUNT = 6
MAX_STREAK = 3
MONEY_WEEKDAYS = {4, 5, 6}
WEEKDAYS = ('пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс')
MONTHS = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа',
          'сентября', 'октября', 'ноября', 'декабря')
MAX_DAYS = 62
MAX_PEOPLE = 60

# Веса подобраны так, чтобы нарушение правила владельца всегда стоило дороже любой неравномерности.
RISE_COST = 1000.0
STREAK_COST = 80.0
LONELY_COST = 3.0
SHIFT_COST = 20.0
MONEY_COST = 30.0
HOUR_COST = 0.4


class RosterInputError(ValueError):
    """Сообщение для владельца: что исправить во вводе."""


@dataclass
class Request:
    start: date
    end: date
    people: list[str]
    days_off: dict[str, set[date]] = field(default_factory=dict)
    counts: dict[date, int] = field(default_factory=dict)
    holidays: set[date] = field(default_factory=set)

    @property
    def days(self):
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]

    def is_money(self, day):
        return day in self.holidays or day.weekday() in MONEY_WEEKDAYS

    def is_off(self, person, day):
        return day in self.days_off.get(person, ())

    def count(self, day):
        if day in self.counts:
            return self.counts[day]
        return WEEKEND_COUNT if self.is_money(day) else WEEKDAY_COUNT


def shift_hours(start):
    return min(SHIFT_HOURS, CLOSE_HOUR - start)


def open_hour(day, holiday=False):
    return 8 if holiday or day.weekday() >= 5 else 9


def openers_needed(day, holiday=False):
    return 2 if holiday or day.weekday() in MONEY_WEEKDAYS else 1


def day_slots(day, count, holiday=False):
    """Приходы на день, самые важные первыми: если людей не хватит, пустует середина смены."""
    first = open_hour(day, holiday)
    closers = [11, 12, 12] if day.weekday() == 4 and not holiday else [11, 12, 14]
    important = [first] * openers_needed(day, holiday) + closers
    if count <= len(important):
        return important[:count]
    between = list(range(first + 1, LAST_CLOSER_START))
    return important + [between[i % len(between)] for i in range(count - len(important))]


# ─── сборка ──────────────────────────────────────────────────────────────────

def _person_cost(row, money, target):
    """Штраф одного человека. row — приход по дням или None."""
    cost = streak = shifts = paid = hours = 0
    previous = None
    for index, start in enumerate(row):
        if start is None:
            if streak > MAX_STREAK:
                cost += STREAK_COST * (streak - MAX_STREAK) ** 2
            elif streak == 1:
                cost += LONELY_COST
            streak = 0
            previous = None
            continue
        if previous is not None and start < previous:
            cost += RISE_COST
        previous = start
        streak += 1
        shifts += 1
        hours += min(SHIFT_HOURS, CLOSE_HOUR - start)
        if money[index]:
            paid += 1
    if streak > MAX_STREAK:
        cost += STREAK_COST * (streak - MAX_STREAK) ** 2
    elif streak == 1:
        cost += LONELY_COST
    return (cost + SHIFT_COST * (shifts - target[0]) ** 2 + MONEY_COST * (paid - target[1]) ** 2
            + HOUR_COST * (hours - target[2]) ** 2)


def _fair_shares(total, limits):
    """Поровну на всех, но не больше, чем человек может: остаток делят остальные."""
    shares = [0.0] * len(limits)
    open_people = list(range(len(limits)))
    left = float(total)
    while open_people and left > 1e-9:
        each = left / len(open_people)
        capped = [p for p in open_people if limits[p] - shares[p] <= each]
        if not capped:
            for p in open_people:
                shares[p] += each
            break
        for p in capped:
            left -= limits[p] - shares[p]
            shares[p] = float(limits[p])
        open_people = [p for p in open_people if p not in capped]
    return shares


def build(request, iterations=200000, seed=7):
    """Подбирает график отжигом. Результат повторяем: тот же ввод даёт тот же график."""
    rng = random.Random(seed)
    days = request.days
    people = request.people
    money = [request.is_money(day) for day in days]
    available = [[p for p, name in enumerate(people) if not request.is_off(name, day)] for day in days]
    slots = []
    for d, day in enumerate(days):
        full = day_slots(day, request.count(day), day in request.holidays)
        slots.append(full[:len(available[d])])

    total_shifts = sum(len(s) for s in slots)
    total_money = sum(len(s) for d, s in enumerate(slots) if money[d])
    total_hours = sum(shift_hours(h) for s in slots for h in s)
    free_days = [sum(p in available[d] for d in range(len(days))) for p in range(len(people))]
    free_money = [sum(p in available[d] for d in range(len(days)) if money[d]) for p in range(len(people))]
    shift_share = _fair_shares(total_shifts, free_days)
    money_share = _fair_shares(total_money, free_money)
    hours_per_shift = total_hours / total_shifts if total_shifts else 0
    targets = [(shift_share[p], money_share[p], shift_share[p] * hours_per_shift) for p in range(len(people))]

    table = [[None] * len(days) for _ in people]
    for d in range(len(days)):
        chosen = rng.sample(available[d], len(slots[d]))
        for person, start in zip(chosen, slots[d]):
            table[person][d] = start

    costs = [_person_cost(table[p], money, targets[p]) for p in range(len(people))]
    total = sum(costs)
    best = [row[:] for row in table]
    best_total = total
    movable = [d for d in range(len(days)) if slots[d]]
    if not movable:
        return best

    hot, cold = 60.0, 0.2
    for step in range(iterations):
        heat = hot * (cold / hot) ** (step / iterations)
        d = rng.choice(movable)
        a = rng.choice(available[d])
        b = rng.choice(available[d])
        if a == b or table[a][d] == table[b][d]:
            continue
        table[a][d], table[b][d] = table[b][d], table[a][d]
        cost_a = _person_cost(table[a], money, targets[a])
        cost_b = _person_cost(table[b], money, targets[b])
        change = cost_a + cost_b - costs[a] - costs[b]
        if change <= 0 or rng.random() < math.exp(-change / heat):
            costs[a], costs[b] = cost_a, cost_b
            total += change
            if total < best_total - 1e-9:
                best_total = total
                best = [row[:] for row in table]
        else:
            table[a][d], table[b][d] = table[b][d], table[a][d]
    return best


def as_schedule(request, table):
    return {name: {day: table[p][d] for d, day in enumerate(request.days) if table[p][d] is not None}
            for p, name in enumerate(request.people)}


# ─── разбор ──────────────────────────────────────────────────────────────────

@dataclass
class Audit:
    shortages: list = field(default_factory=list)   # (день, работают, нужно, кто в обязательном выходном)
    closers: list = field(default_factory=list)     # (день, сколько закрывают)
    openers: list = field(default_factory=list)     # (день, сколько открывают, нужно)
    rises: list = field(default_factory=list)       # (имя, день, приход накануне, приход)
    forced: list = field(default_factory=list)      # (имя, день): смена в обязательный выходной
    no_reserve: list = field(default_factory=list)  # дни, когда заменить некого
    streaks: list = field(default_factory=list)     # (имя, первый день, длина)
    people: dict = field(default_factory=dict)      # имя → {'shifts', 'money', 'hours'}


def audit(request, schedule):
    """Проверяет любой график, собранный программой или вручную."""
    result = Audit()
    days = request.days
    for day in days:
        working = [name for name in request.people if day in schedule.get(name, {})]
        need = request.count(day)
        off = [name for name in request.people if request.is_off(name, day)]
        if len(working) < need:
            result.shortages.append((day, len(working), need, off))
        closing = sum(schedule[name][day] >= LAST_CLOSER_START for name in working)
        if closing != CLOSERS:
            result.closers.append((day, closing))
        holiday = day in request.holidays
        opening = sum(schedule[name][day] == open_hour(day, holiday) for name in working)
        if opening != openers_needed(day, holiday):
            result.openers.append((day, opening, openers_needed(day, holiday)))
        if len(request.people) - len(off) - len(working) <= 0:
            result.no_reserve.append(day)
    for name in request.people:
        own = schedule.get(name, {})
        streak_start = None
        length = 0
        for index, day in enumerate(days + [None]):
            if day is not None and day in own:
                if length == 0:
                    streak_start = day
                length += 1
                if request.is_off(name, day):
                    result.forced.append((name, day))
                previous = days[index - 1] if index else None
                if previous in own and own[day] < own[previous]:
                    result.rises.append((name, day, own[previous], own[day]))
            else:
                if length > MAX_STREAK:
                    result.streaks.append((name, streak_start, length))
                length = 0
        result.people[name] = {
            'shifts': len(own),
            'money': sum(request.is_money(day) for day in own),
            'hours': sum(shift_hours(start) for start in own.values()),
        }
    return result


def _day(day):
    return f'{day.day} {MONTHS[day.month - 1]}'


def _spread(values, key):
    numbers = {name: stats[key] for name, stats in values.items()}
    high = max(numbers.values())
    low = min(numbers.values())
    return high, low, [n for n, v in numbers.items() if v == high], [n for n, v in numbers.items() if v == low]


def report(request, schedule, findings=None):
    """Текст для владельца: сначала что мешает, потом как распределилась нагрузка."""
    found = findings or audit(request, schedule)
    lines = [f'График {_day(request.start)} – {_day(request.end)}: {len(request.people)} человек, '
             f'смен всего: {sum(s["shifts"] for s in found.people.values())}.']
    problems = []
    for day, have, need, off in found.shortages:
        hint = f' В обязательном выходном: {", ".join(off)}.' if off else ''
        problems.append(f'{_day(day)}: не хватает людей, {have} из {need}.{hint}')
    for name, day in found.forced:
        problems.append(f'{name}: смена {_day(day)} в обязательный выходной.')
    for name, day, before, after in found.rises:
        problems.append(f'{name}: {_day(day)} приход в {after}, а накануне в {before}. Нельзя приходить раньше, чем накануне.')
    for day, closing in found.closers:
        problems.append(f'{_day(day)}: в закрытии {closing}, нужно {CLOSERS}.')
    for day, opening, need in found.openers:
        problems.append(f'{_day(day)}: открывают {opening}, нужно {need}.')
    for name, first, length in found.streaks:
        problems.append(f'{name}: смен подряд с {_day(first)}: {length}.')
    if problems:
        lines += ['', 'Что не получилось:'] + [f'• {p}' for p in problems]
    else:
        lines += ['', 'Все правила соблюдены.']
    if found.no_reserve:
        lines += ['', 'Некем заменить, если кто-то заболеет: ' + ', '.join(_day(d) for d in found.no_reserve) + '.']
    if found.people:
        high, low, most, least = _spread(found.people, 'money')
        lines += ['', f'Денежные дни (пт–вс и праздники): от {low} до {high}.']
        if high - low > 1:
            lines.append(f'Больше всего: {", ".join(most)}. Меньше всего: {", ".join(least)}.')
        high, low, most, least = _spread(found.people, 'hours')
        lines += [f'Часы: от {low} до {high}. Больше всего: {", ".join(most)}. Меньше всего: {", ".join(least)}.']
        high, low, _, _ = _spread(found.people, 'shifts')
        lines.append(f'Смены: от {low} до {high}.')
    return '\n'.join(lines)


def render_html(request, schedule, findings=None):
    """Таблица в привычном виде: красное — обязательный выходной, справа итоги по человеку."""
    found = findings or audit(request, schedule)
    days = request.days
    esc = html.escape
    head = ''.join(f'<th class="{"m" if request.is_money(d) else ""}">{WEEKDAYS[d.weekday()]}<br>{d.day}.{d.month:02d}</th>' for d in days)
    rows = []
    for name in request.people:
        cells = []
        for day in days:
            start = schedule.get(name, {}).get(day)
            if request.is_off(name, day):
                cells.append(f'<td class="off">{"" if start is None else start}</td>')
            else:
                cells.append(f'<td>{"" if start is None else start}</td>')
        stats = found.people[name]
        rows.append(f'<tr><th class="n">{esc(name)}</th>{"".join(cells)}'
                    f'<td class="s">{stats["shifts"]}</td><td class="s">{stats["money"]}</td><td class="s">{stats["hours"]}</td></tr>')
    totals = ''.join(f'<td class="t">{sum(day in schedule.get(n, {}) for n in request.people)}</td>' for day in days)
    text = esc(report(request, schedule, found)).replace('\n', '<br>')
    return ('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>График</title><style>'
            'body{font:15px/1.45 -apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:16px;color:#1d2326;background:#fff}'
            '.w{overflow-x:auto}table{border-collapse:collapse}th,td{border:1px solid #bbb;padding:4px 7px;text-align:center;font-variant-numeric:tabular-nums}'
            'thead th{background:#f4b183;font-weight:500;font-size:13px}thead th.m{background:#e8894a}'
            '.n{text-align:left;font-weight:500;white-space:nowrap;background:#f3f3f3;position:sticky;left:0}'
            '.off{background:#e01f1f;color:#fff}.s{background:#eef3ec;font-weight:500}.t{background:#e2efda}'
            'p{max-width:44rem}</style><div class="w"><table><thead><tr><th></th>' + head +
            '<th>смен</th><th>пт–вс</th><th>часов</th></tr></thead><tbody>' + ''.join(rows) +
            '<tr><th class="n">В смене</th>' + totals + '<td></td><td></td><td></td></tr></tbody></table></div><p>' + text + '</p></html>')


# ─── ввод ────────────────────────────────────────────────────────────────────

HELP = ('Сборка графика. Первая строка: /roster, начало и конец. Дальше по строке на человека '
        'с его обязательными выходными:\n\n'
        '/roster 16.11 30.11\n'
        'Имя1: 18 19\n'
        'Имя2: 20-22, 25\n'
        'Имя3\n'
        'праздник 4.12 7\n'
        'нужно 21.11 7\n\n'
        '«нужно» меняет число людей на дату, например на стажёра. «праздник» считает дату денежной, '
        'открывают её как выходной. Число без месяца относится к выбранному периоду.')


def _parse_date(text, today):
    match = re.fullmatch(r'(\d{1,2})\.(\d{1,2})(?:\.(\d{2}|\d{4}))?', text)
    if not match:
        raise RosterInputError(f'Не понял дату «{text}». Нужно так: 16.11')
    year = int(match[3]) if match[3] else today.year
    if year < 100:
        year += 2000
    try:
        value = date(year, int(match[2]), int(match[1]))
    except ValueError:
        raise RosterInputError(f'Такой даты нет: {text}') from None
    if not match[3] and (today - value).days > 180:
        value = value.replace(year=year + 1)
    return value


def _in_period(text, start, end, today):
    """«18» или «18.11» — дата внутри периода."""
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    if re.fullmatch(r'\d{1,2}', text):
        matches = [day for day in days if day.day == int(text)]
        if len(matches) > 1:
            raise RosterInputError(f'Число {text} встречается в периоде дважды, укажи месяц: {text}.{start.month:02d}')
        if not matches:
            raise RosterInputError(f'Числа {text} нет в выбранном периоде.')
        return matches[0]
    value = _parse_date(text, today)
    if value < start or value > end:
        raise RosterInputError(f'Дата {text} вне выбранного периода.')
    return value


def _parse_days(text, start, end, today):
    found = set()
    for token in re.split(r'[,\s]+', text.strip()):
        if not token:
            continue
        if '-' in token:
            left, right = token.split('-', 1)
            first = _in_period(left, start, end, today)
            last = _in_period(right, start, end, today)
            if last < first:
                raise RosterInputError(f'Диапазон наоборот: {token}')
            found |= {first + timedelta(days=i) for i in range((last - first).days + 1)}
        else:
            found.add(_in_period(token, start, end, today))
    return found


def parse_request(arguments, today):
    lines = [line.strip() for line in (arguments or '').splitlines() if line.strip()]
    if not lines:
        raise RosterInputError(HELP)
    period = lines[0].split()
    if len(period) != 2:
        raise RosterInputError('В первой строке нужны начало и конец периода: /roster 16.11 30.11')
    start = _parse_date(period[0], today)
    end = _parse_date(period[1], today)
    if end < start:
        end = end.replace(year=end.year + 1)
    if (end - start).days + 1 > MAX_DAYS:
        raise RosterInputError(f'Период слишком длинный: можно до {MAX_DAYS} дней.')
    request = Request(start, end, [])
    seen = set()
    for line in lines[1:]:
        word = line.split()[0].lower()
        if word in ('нужно', 'людей', 'праздник'):
            parts = line.split()
            if len(parts) not in (2, 3) or (word != 'праздник' and len(parts) != 3):
                raise RosterInputError(f'Не понял строку «{line}». Нужно так: нужно 21.11 7')
            day = _in_period(parts[1], start, end, today)
            if word == 'праздник':
                request.holidays.add(day)
            if len(parts) == 3:
                if not parts[2].isdigit() or not 1 <= int(parts[2]) <= 30:
                    raise RosterInputError(f'Не понял число людей в строке «{line}».')
                request.counts[day] = int(parts[2])
            continue
        name, _, rest = line.partition(':')
        name = name.strip()
        if not name or len(name) > 40:
            raise RosterInputError(f'Не понял имя в строке «{line}».')
        if name.casefold() in seen:
            raise RosterInputError(f'Имя встречается дважды: {name}')
        seen.add(name.casefold())
        request.people.append(name)
        request.days_off[name] = _parse_days(rest, start, end, today)
    if not request.people:
        raise RosterInputError('Не вижу ни одного официанта. ' + HELP)
    if len(request.people) > MAX_PEOPLE:
        raise RosterInputError(f'Слишком много людей: можно до {MAX_PEOPLE}.')
    return request
