"""Прототип: сборка графика официантов и разбор его слабых мест. Только правила, без нейросети.

Правила владельца (2 ноября 2026): смена 12 часов, ресторан закрывается в 23;
в будни 5 человек, в выходные 6, число можно менять на дату, и его нужно набрать всегда;
в закрытии всегда 3 (приход с 11 и позже); в пт–вс открывают двое, в будни один;
обязательных выходных 3 на 15 дней, у студентов больше; из обязательного выходного
выходят, только если иначе не набрать людей; в смены подряд приход не раньше, чем накануне;
денежные дни поровну.

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
MAX_MANDATORY = 3
MANDATORY_PERIOD_DAYS = 15
MORNING_STARTS = {8, 9, 10}
EVENING_STARTS = {11, 12, 14}
MONEY_WEEKDAYS = {4, 5, 6}
WEEKDAYS = ('пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс')
MONTHS = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа',
          'сентября', 'октября', 'ноября', 'декабря')
MAX_DAYS = 62
MAX_PEOPLE = 60

# Веса подобраны так, чтобы нарушение правила владельца всегда стоило дороже любой неравномерности.
RECALL_COST = 5000.0
RECALL_PROTECTED_COST = 20000.0
RECALL_SPREAD_COST = 400.0
RISE_COST = 1000.0
STREAK_COST = 80.0
LONELY_COST = 3.0
SHIFT_COST = 20.0
MONEY_COST = 30.0
HOUR_COST = 0.4
WISH_OFF_COST = 45.0
WISH_START_COST = 30.0
REFUSAL_COST = 8.0


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
    # Пожелания: выполняются по возможности, правила и обязательные выходные важнее.
    wish_off: dict[str, set[date]] = field(default_factory=dict)
    wish_starts: dict[str, set[int]] = field(default_factory=dict)
    # У студентов обязательных выходных может быть больше нормы из-за учёбы.
    students: set[str] = field(default_factory=set)

    @property
    def days(self):
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]

    @property
    def mandatory_limit(self):
        return MAX_MANDATORY * max(1, round(len(self.days) / MANDATORY_PERIOD_DAYS))

    def is_money(self, day):
        return day in self.holidays or day.weekday() in MONEY_WEEKDAYS

    def is_off(self, person, day):
        return day in self.days_off.get(person, ())

    def is_present(self, person):
        """Кто выходной весь период, тот в отпуске: его не вызывают."""
        return any(not self.is_off(person, day) for day in self.days)

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
    """Приходы на день, самые важные первыми. Если людей не хватит, пустует середина смены,
    затем второй на открытие: закрывают всегда трое, но кто-то должен и открыть."""
    first = open_hour(day, holiday)
    closers = [11, 12, 12] if day.weekday() == 4 and not holiday else [11, 12, 14]
    important = [first] + closers + [first] * (openers_needed(day, holiday) - 1)
    if count <= len(important):
        return important[:count]
    between = list(range(first + 1, LAST_CLOSER_START))
    return important + [between[i % len(between)] for i in range(count - len(important))]


# ─── сборка ──────────────────────────────────────────────────────────────────

def _recall_price(request, name):
    """Студентов и тех, у кого выходных больше нормы, вызывают в последнюю очередь."""
    protected = name in request.students or len(request.days_off.get(name, ())) > request.mandatory_limit
    return RECALL_PROTECTED_COST if protected else RECALL_COST


def _person_cost(row, money, target, wish_off, wish_starts, off, recall_price):
    """Штраф одного человека. row — приход по дням или None."""
    cost = streak = shifts = paid = hours = refused = recalled = 0
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
        if off[index]:
            recalled += 1
        if wish_off[index]:
            cost += WISH_OFF_COST
            refused += 1
        if wish_starts and start not in wish_starts:
            cost += WISH_START_COST
            refused += 1
    if streak > MAX_STREAK:
        cost += STREAK_COST * (streak - MAX_STREAK) ** 2
    elif streak == 1:
        cost += LONELY_COST
    cost += recall_price * recalled + RECALL_SPREAD_COST * recalled * recalled
    # Каждый следующий отказ одному человеку дороже: отказывать лучше понемногу всем.
    return (cost + REFUSAL_COST * refused * refused + SHIFT_COST * (shifts - target[0]) ** 2 + MONEY_COST * (paid - target[1]) ** 2
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


def build(request, iterations=250000, seed=7, restarts=3):
    """Подбирает график отжигом с нескольких начал. Тот же ввод даёт тот же график."""
    return min((_search(request, iterations, seed + i) for i in range(restarts)), key=lambda found: found[0])[1]


def _search(request, iterations, seed):
    rng = random.Random(seed)
    days = request.days
    people = request.people
    money = [request.is_money(day) for day in days]
    available = [[p for p, name in enumerate(people) if not request.is_off(name, day)] for day in days]
    present = [p for p, name in enumerate(people) if request.is_present(name)]
    pool = []
    slots = []
    for d, day in enumerate(days):
        # Число людей обязательно: если свободных мало, выходят из обязательного выходного.
        pool.append(available[d] if len(available[d]) >= request.count(day) else present)
        full = day_slots(day, request.count(day), day in request.holidays)
        slots.append(full[:len(pool[d])])

    total_shifts = sum(len(s) for s in slots)
    total_money = sum(len(s) for d, s in enumerate(slots) if money[d])
    total_hours = sum(shift_hours(h) for s in slots for h in s)
    free_days = [sum(p in available[d] for d in range(len(days))) for p in range(len(people))]
    free_money = [sum(p in available[d] for d in range(len(days)) if money[d]) for p in range(len(people))]
    shift_share = _fair_shares(total_shifts, free_days)
    money_share = _fair_shares(total_money, free_money)
    hours_per_shift = total_hours / total_shifts if total_shifts else 0
    targets = [(shift_share[p], money_share[p], shift_share[p] * hours_per_shift) for p in range(len(people))]

    wish_off = [[day in request.wish_off.get(name, ()) for day in days] for name in people]
    wish_starts = [request.wish_starts.get(name) or None for name in people]
    off = [[request.is_off(name, day) for day in days] for name in people]
    prices = [_recall_price(request, name) for name in people]

    def person_cost(p):
        return _person_cost(table[p], money, targets[p], wish_off[p], wish_starts[p], off[p], prices[p])

    table = [[None] * len(days) for _ in people]
    for d in range(len(days)):
        chosen = rng.sample(pool[d], len(slots[d]))
        for person, start in zip(chosen, slots[d]):
            table[person][d] = start

    costs = [person_cost(p) for p in range(len(people))]
    total = sum(costs)
    best = [row[:] for row in table]
    best_total = total
    movable = [d for d in range(len(days)) if slots[d]]
    if not movable:
        return best_total, best

    hot, cold = 60.0, 0.2
    for step in range(iterations):
        heat = hot * (cold / hot) ** (step / iterations)
        d = rng.choice(movable)
        a = rng.choice(pool[d])
        b = rng.choice(pool[d])
        if a == b or table[a][d] == table[b][d]:
            continue
        table[a][d], table[b][d] = table[b][d], table[a][d]
        cost_a = person_cost(a)
        cost_b = person_cost(b)
        change = cost_a + cost_b - costs[a] - costs[b]
        if change <= 0 or rng.random() < math.exp(-change / heat):
            costs[a], costs[b] = cost_a, cost_b
            total += change
            if total < best_total - 1e-9:
                best_total = total
                best = [row[:] for row in table]
        else:
            table[a][d], table[b][d] = table[b][d], table[a][d]
    return best_total, best


def as_schedule(request, table):
    return {name: {day: table[p][d] for d, day in enumerate(request.days) if table[p][d] is not None}
            for p, name in enumerate(request.people)}


# ─── разбор ──────────────────────────────────────────────────────────────────

@dataclass
class Audit:
    shortages: list = field(default_factory=list)   # (день, работают, нужно, кто остался в обязательном выходном)
    closers: list = field(default_factory=list)     # (день, сколько закрывают)
    openers: list = field(default_factory=list)     # (день, сколько открывают, нужно)
    rises: list = field(default_factory=list)       # (имя, день, приход накануне, приход)
    forced: list = field(default_factory=list)      # (имя, день): смена в обязательный выходной
    no_reserve: list = field(default_factory=list)  # дни, когда заменить некого
    streaks: list = field(default_factory=list)     # (имя, первый день, длина)
    people: dict = field(default_factory=dict)      # имя → {'shifts', 'money', 'hours'}
    too_many_off: list = field(default_factory=list)  # (имя, сколько обязательных выходных)
    wish_total: int = 0                             # сколько пожеланий было
    missed_off: list = field(default_factory=list)  # (имя, день): просил выходной, стоит смена
    missed_starts: list = field(default_factory=list)  # (имя, день, приход): приход не тот, что просил


def audit(request, schedule):
    """Проверяет любой график, собранный программой или вручную."""
    result = Audit()
    days = request.days
    for day in days:
        working = [name for name in request.people if day in schedule.get(name, {})]
        need = request.count(day)
        off = [name for name in request.people if request.is_off(name, day)]
        if len(working) < need:
            result.shortages.append((day, len(working), need, [name for name in off if name not in working]))
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
        wanted_off = request.wish_off.get(name, set()) - request.days_off.get(name, set())
        wanted = request.wish_starts.get(name)
        off_count = len(request.days_off.get(name, ()))
        if off_count > request.mandatory_limit and name not in request.students:
            result.too_many_off.append((name, off_count))
        result.wish_total += len(wanted_off) + (len(own) if wanted else 0)
        result.missed_off += [(name, day) for day in days if day in wanted_off and day in own]
        if wanted:
            result.missed_starts += [(name, day, own[day]) for day in days if day in own and own[day] not in wanted]
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
        free = sum(not request.is_off(n, day) for n in request.people)
        if free < request.count(day):
            study = ' (учёба)' if name in request.students else ''
            problems.append(f'{name}{study}: выход {_day(day)} вместо обязательного выходного: '
                            f'свободных только {free}, нужно {request.count(day)}.')
        else:
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
    if found.too_many_off:
        lines += ['', f'Обязательных выходных больше {request.mandatory_limit} за период: '
                  + ', '.join(f'{name} ({count})' for name, count in found.too_many_off)
                  + '. Если это учёба, добавь в строку человека «; учёба».']
    if found.wish_total:
        missed = len(found.missed_off) + len(found.missed_starts)
        lines += ['', f'Пожелания: выполнено {found.wish_total - missed} из {found.wish_total}.']
        by_off = {}
        for name, day in found.missed_off:
            by_off.setdefault(name, []).append(day)
        for name, wished in by_off.items():
            lines.append(f'• {name}: выходной не получился {", ".join(_day(d) for d in wished)}.')
        by_start = {}
        for name, day, start in found.missed_starts:
            by_start.setdefault(name, []).append((day, start))
        for day in request.days:
            asking = [n for n in request.people if day in request.wish_off.get(n, ()) and not request.is_off(n, day)]
            free = sum(not request.is_off(n, day) for n in request.people)
            spare = max(0, free - request.count(day))
            if len(asking) > spare:
                lines.append(f'• {_day(day)}: выходной просят {len(asking)}, отпустить можно только {spare}.')
        for name, shifts in by_start.items():
            lines.append(f'• {name}: не тот приход в {len(shifts)} из {found.people[name]["shifts"]} смен: '
                         + ', '.join(f'{day.day}.{day.month:02d} в {start}' for day, start in shifts) + '.')
    if found.no_reserve:
        lines += ['', 'Некем заменить, если кто-то заболеет: ' + ', '.join(_day(d) for d in found.no_reserve) + '.']
    if found.people:
        present = {name: stats for name, stats in found.people.items()
                   if len(request.days_off.get(name, ())) < len(request.days)}
        high, low, most, least = _spread(present or found.people, 'money')
        lines += ['', f'Денежные дни (пт–вс и праздники): от {low} до {high}.']
        if high - low > 1:
            lines.append(f'Больше всего: {", ".join(most)}. Меньше всего: {", ".join(least)}.')
        high, low, most, least = _spread(present or found.people, 'hours')
        lines += [f'Часы: от {low} до {high}. Больше всего: {", ".join(most)}. Меньше всего: {", ".join(least)}.']
        high, low, _, _ = _spread(present or found.people, 'shifts')
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
            wanted = request.wish_starts.get(name)
            kind = ('off' if request.is_off(name, day)
                    else 'miss' if start is not None and (day in request.wish_off.get(name, ()) or (wanted and start not in wanted))
                    else 'wish' if day in request.wish_off.get(name, ())
                    else '')
            cells.append(f'<td class="{kind}">{"" if start is None else start}</td>')
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
            '.off{background:#e01f1f;color:#fff}.wish{background:#fdeeb5}.miss{background:#f6c6c6;font-weight:600}.s{background:#eef3ec;font-weight:500}.t{background:#e2efda}'
            'p{max-width:44rem}</style><div class="w"><table><thead><tr><th></th>' + head +
            '<th>смен</th><th>пт–вс</th><th>часов</th></tr></thead><tbody>' + ''.join(rows) +
            '<tr><th class="n">В смене</th>' + totals + '<td></td><td></td><td></td></tr></tbody></table></div>'
            '<p>Красное — обязательный выходной, число на красном — выход вместо него, потому что иначе не набрать людей. Жёлтое — просил выходной, и он получился. Розовое — пожелание не выполнено.</p><p>' + text + '</p></html>')


# ─── ввод ────────────────────────────────────────────────────────────────────

HELP = ('Сборка графика. Первая строка: /roster, начало и конец. Дальше по строке на человека '
        'с его обязательными выходными:\n\n'
        '/roster 16.11 30.11\n'
        'Имя1: 18 19 20\n'
        'Имя2: 22, 25; хочу 21 23; утро\n'
        'Имя3: ; приход 11 12\n'
        'Имя4: 17 18 19 20 21 26; учёба\n'
        'Имя5\n'
        'праздник 4.12 7\n'
        'нужно 21.11 7\n\n'
        'Числа сразу после имени — обязательные выходные. После точки с запятой идут пожелания, они '
        'выполняются по возможности: «хочу» — желательный выходной, «утро» — приход до 11, «вечер» — '
        'с 11, «приход 11 12» — конкретные часы.\n'
        'Обязательных выходных 3 на 15 дней. «учёба» разрешает больше. Людей на смену набирается '
        'всегда: если свободных мало, кто-то выйдет из обязательного выходного, студенты в последнюю очередь.\n'
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
        if word in ('нужно', 'людей', 'праздник') and ':' not in line:
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
        sections = rest.split(';')
        request.days_off[name] = _parse_days(sections[0], start, end, today)
        for section in sections[1:]:
            words = section.split()
            if not words:
                continue
            key = words[0].lower()
            tail = ' '.join(words[1:])
            starts = request.wish_starts.setdefault(name, set())
            if key in ('хочу', 'желательно'):
                request.wish_off.setdefault(name, set()).update(_parse_days(tail, start, end, today))
            elif key in ('учёба', 'учеба', 'студент', 'студентка'):
                request.students.add(name)
            elif key == 'утро':
                starts.update(MORNING_STARTS)
            elif key in ('вечер', 'закрытие'):
                starts.update(EVENING_STARTS)
            elif key == 'приход':
                if not words[1:] or not all(w.strip(',').isdigit() and 6 <= int(w.strip(',')) <= 22 for w in words[1:]):
                    raise RosterInputError(f'Не понял часы в строке «{line}». Нужно так: приход 11 12')
                starts.update(int(w.strip(',')) for w in words[1:])
            else:
                raise RosterInputError(f'Не понял пожелание «{section.strip()}». Можно: хочу 5 6; утро; вечер; приход 11 12; учёба')
            if not starts:
                request.wish_starts.pop(name)
    if not request.people:
        raise RosterInputError('Не вижу ни одного официанта. ' + HELP)
    if len(request.people) > MAX_PEOPLE:
        raise RosterInputError(f'Слишком много людей: можно до {MAX_PEOPLE}.')
    return request
