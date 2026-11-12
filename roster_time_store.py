"""Server-side storage and expansion of date and weekday time rules."""

from collections import defaultdict
from datetime import date, timedelta

from postgrest.exceptions import APIError

import db
import days_off
from roster_time import Rule, clock
from workday import op_today

WEEK = ('По понедельникам', 'По вторникам', 'По средам', 'По четвергам',
        'По пятницам', 'По субботам', 'По воскресеньям')


async def supported():
    try:
        await db._execute(db.supabase.table('shift_time_rules').select('target').limit(1))
    except APIError as error:
        if error.code in days_off.MISSING:
            return False
        raise
    return True


async def write(uid, rule, remove=None):
    return (await db._execute(db.supabase.rpc('write_shift_time_rules', {
        'p_actor': uid, 'p_operation': rule['op'], 'p_remove': remove,
        'p_roster': rule.get('period'), 'p_targets': rule.get('keys'),
        'p_kind': rule.get('kind'), 'p_start': rule.get('start'), 'p_end': rule.get('end'),
        'p_today': op_today().isoformat(),
    }), retries=1)).data


def rule_lines(rule, manager=False):
    keys, start, end = rule['keys'], clock(rule['start'] / 60), rule['end']
    days = [date.fromisoformat(k) for k in keys if len(k) > 1]
    targets = ([days_off.days_text(days)] if days else []) + [WEEK[int(k)] for k in keys if len(k) == 1]
    if rule['kind'] == 'limit':
        text = f'только с {start} до {clock(end / 60)}' if end is not None else f'только с {start}'
        if not manager:
            text = f'можешь работать {text}' if end is not None else f'можешь начать смену только в {start} или позже'
    else:
        text = f'удобнее работать с {start} до {clock(end / 60)}' if end is not None else f'удобнее начать смену в {start}'
    return '\n'.join(f'{target} — {text}.' for target in targets)


def next_weekdays(period, today, weekdays):
    first = max(today, date.fromisoformat(period['start_date']))
    last = date.fromisoformat(period['end_date'])
    days = [first + timedelta(days=(day - first.weekday()) % 7) for day in weekdays]
    return sorted(day.isoformat() for day in days if day <= last)


async def rules_for(restaurant, start, end, names):
    if not names:
        return {}, {}
    span = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    targets = [str(i) for i in range(7)] + [day.isoformat() for day in span]
    try:
        rows = await db._pages(lambda: db.supabase.table('shift_time_rules').select('*')
                               .eq('restaurant_id', restaurant).in_('link_id', list(names))
                               .in_('target', targets).order('link_id').order('target').order('kind'))
    except APIError as error:
        if error.code in days_off.MISSING:
            return {}, {}
        raise
    by_person = defaultdict(dict)
    for row in rows:
        by_person[names[row['link_id']]][row['target'], row['kind']] = row['start_min'], row['end_min']
    effective, summaries = {}, {}
    for name, rules in by_person.items():
        selected = {}
        for day in span:
            key = day.isoformat()
            weekly = str(day.weekday())
            limits = [rules[k, 'limit'] for k in (weekly, key) if (k, 'limit') in rules]
            wish = rules.get((key, 'wish'), rules.get((weekly, 'wish')))
            if limits or wish:
                selected[key] = [max((r[0] for r in limits), default=360),
                                 min((r[1] for r in limits if r[1] is not None), default=1380),
                                 *(wish or (None, None))]
        if selected:
            effective[name] = selected
        grouped = defaultdict(list)
        for (target, kind), (first, last) in rules.items():
            grouped[(len(target) > 1, kind, first, last)].append(target)
        summaries[name] = [rule_lines({'keys': sorted(keys), 'kind': kind, 'start': first, 'end': last}, manager=True)
                           for (_, kind, first, last), keys in sorted(grouped.items(), key=lambda item: min(item[1]))]
    return effective, summaries


def as_rules(raw):
    return {name: {date.fromisoformat(day): Rule(*(None if v is None else v / 60 for v in values))
                   for day, values in days.items()} for name, days in raw.items()}
