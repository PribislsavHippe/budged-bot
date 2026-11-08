"""График смен ресторана: составление на сайте и просмотр командой в мини-приложении.

Составляет владелец ресторана или сотрудник, которому владелец дал право «составляет график».
Это право открывает только график: продажи и личные записи остаются закрыты.
Команда видит только опубликованный график; пожелания и пометка «учёба» в него не попадают.
"""
import asyncio
import re
import re
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

from aiohttp import web
from postgrest.exceptions import APIError

import days_off
import db
import holidays_ru
import identity
import roster
from workday import op_today

# Ошибки «таблицы или колонки нет»: база ещё не обновлена до migration_v30.
MISSING = ('42P01', 'PGRST205', '42703', 'PGRST204')
NOT_READY = 'График пока недоступен: владельцу нужно обновить базу (migration_v30.sql).'
MAX_EVENT = 40
CELL = re.compile(r'^(?:(\d{1,2})(?:-(\d{1,2}))?)?(\*)?$')
# Сборка занимает процессор на десятки секунд: одна за раз на весь сервер.
_building = asyncio.Semaphore(1)


class Stale(Exception):
    """Черновик успели изменить в другом окне."""


def _day(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError('Не получилось прочитать дату. Обнови страницу.') from None


def _int(value, low, high, what):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{what}: число от {low} до {high}.')
    return value


def parse_cell(value):
    """'9', '9-17', '12*' или '*' → (приход, уход, необычное). Пустая строка — нет смены."""
    match = CELL.match(str(value).strip())
    if not match:
        raise ValueError(f'Не понял смену «{value}». Пиши час прихода (9) или интервал (9-17).')
    start, end, note = match.groups()
    if start is None:
        return None, None, bool(note)
    start = int(start)
    end = int(end) if end else None
    if not 6 <= start <= 22 or (end is not None and not start < end <= 24):
        raise ValueError(f'Смена «{value}» вне часов работы ресторана.')
    return start, end, bool(note)


def normalize(draft, start, end):
    """Проверяет черновик с сайта и оставляет только известные поля."""
    if not isinstance(draft, dict):
        raise ValueError('Черновик повреждён. Обнови страницу.')
    days = {date.fromordinal(start.toordinal() + i).isoformat() for i in range((end - start).days + 1)}
    people, seen = [], set()
    for person in draft.get('people') or []:
        if not isinstance(person, dict):
            continue
        name = identity.name(str(person.get('name', '')))
        if name.casefold() in seen:
            raise ValueError(f'Имя «{name}» встречается дважды.')
        seen.add(name.casefold())
        link = person.get('link')
        people.append({'name': name, 'student': bool(person.get('student')),
                       'link': str(UUID(str(link))) if link else None})
    if len(people) > roster.MAX_PEOPLE:
        raise ValueError(f'В графике не больше {roster.MAX_PEOPLE} человек.')
    names = {p['name'] for p in people}

    def per_person(key, check):
        source = draft.get(key) or {}
        if not isinstance(source, dict):
            raise ValueError('Черновик повреждён. Обнови страницу.')
        return {name: sorted({check(v) for v in values})
                for name, values in source.items() if name in names and isinstance(values, list) and values}

    def in_period(value):
        value = _day(value).isoformat()
        if value not in days:
            raise ValueError('Дата вне периода графика.')
        return value

    result = {'people': people,
              'off': per_person('off', in_period),
              'off_auto': per_person('off_auto', in_period),
              'off_requested': per_person('off_requested', in_period),
              'wish_off': per_person('wish_off', in_period),
              'wish_starts': per_person('wish_starts', lambda h: _int(h, 6, 22, 'Удобный приход')),
              'days': {}, 'events': {}, 'cells': {}}
    for key, setting in (draft.get('days') or {}).items():
        key = in_period(key)
        if not isinstance(setting, dict):
            continue
        clean = {}
        for field, high in (('count', roster.MAX_PEOPLE), ('closers', 10), ('openers', 10)):
            if setting.get(field) is not None:
                clean[field] = _int(setting[field], 0, high, {'count': 'Людей в смене', 'closers': 'Закрывают',
                                                               'openers': 'Открывают'}[field])
        # Праздник: True или False — явное решение менеджера; без него берётся календарь России.
        if isinstance(setting.get('holiday'), bool):
            clean['holiday'] = setting['holiday']
        if clean:
            result['days'][key] = clean
    for key, label in (draft.get('events') or {}).items():
        label = ' '.join(str(label).split())[:MAX_EVENT]
        if label:
            result['events'][in_period(key)] = label
    for name, row in (draft.get('cells') or {}).items():
        if name not in names or not isinstance(row, dict):
            continue
        clean = {}
        for key, value in row.items():
            if str(value).strip():
                parse_cell(value)
                clean[in_period(key)] = str(value).strip()
        if clean:
            result['cells'][name] = clean
    result['variants'] = clean_variants(draft.get('variants'), names, in_period)
    result['picked'] = bool(draft.get('picked'))
    chosen = str(draft.get('variant') or '')
    result['variant'] = chosen if any(v['id'] == chosen for v in result['variants']) else None
    return result


def trim(draft, start, end):
    """Оставляет в черновике только дни нового периода. Варианты считались на прежние даты, поэтому сбрасываются."""
    inside = {date.fromordinal(start.toordinal() + i).isoformat() for i in range((end - start).days + 1)}
    out = dict(draft)
    for key in ('off', 'off_auto', 'off_requested', 'wish_off'):
        kept = {n: [d for d in days if d in inside] for n, days in (draft.get(key) or {}).items()}
        out[key] = {n: days for n, days in kept.items() if days}
    out['days'] = {d: s for d, s in (draft.get('days') or {}).items() if d in inside}
    out['events'] = {d: s for d, s in (draft.get('events') or {}).items() if d in inside}
    cells = {n: {d: v for d, v in row.items() if d in inside} for n, row in (draft.get('cells') or {}).items()}
    out['cells'] = {n: row for n, row in cells.items() if row}
    out['variants'], out['variant'], out['picked'] = [], None, True
    return out


def clean_variants(raw, names, in_period):
    """Варианты графика, собранные программой: смены, автоматические выходные и описание."""
    out = []
    for item in (raw if isinstance(raw, list) else [])[:5]:
        if not isinstance(item, dict):
            continue
        vid = str(item.get('id', ''))
        title = ' '.join(str(item.get('title', '')).split())[:40]
        if not re.fullmatch(r'[a-z]{1,16}', vid) or not title:
            continue
        cells = {}
        for name, row in (item.get('cells') or {}).items():
            if name in names and isinstance(row, dict):
                clean = {}
                for key, value in row.items():
                    if str(value).strip():
                        parse_cell(value)
                        clean[in_period(key)] = str(value).strip()
                if clean:
                    cells[name] = clean
        off = {name: sorted({in_period(d) for d in days}) for name, days in (item.get('off') or {}).items()
               if name in names and isinstance(days, list) and days}
        text = lambda key: [' '.join(t.split())[:140] for t in (item.get(key) or [])[:4] if isinstance(t, str)]
        out.append({'id': vid, 'title': title, 'cells': cells, 'off': off, 'pros': text('pros'), 'cons': text('cons')})
    return out


def to_request(draft, start, end):
    """Черновик → условия сборки из roster.py."""
    dates = lambda values: {date.fromisoformat(v) for v in values}
    request = roster.Request(start=start, end=end, people=[p['name'] for p in draft['people']])
    request.days_off = {n: dates(v) for n, v in draft['off'].items()}
    request.wish_off = {n: dates(v) for n, v in draft['wish_off'].items()}
    request.wish_starts = {n: set(v) for n, v in draft['wish_starts'].items()}
    request.students = {p['name'] for p in draft['people'] if p['student']}
    for key, setting in draft['days'].items():
        day = date.fromisoformat(key)
        if 'count' in setting:
            request.counts[day] = setting['count']
        if 'closers' in setting:
            request.closers[day] = setting['closers']
        if 'openers' in setting:
            request.openers[day] = setting['openers']
    # Праздники России — дни, когда людей нужно больше, как в пятницу или субботу.
    # Явная отметка менеджера (есть или нет) важнее календаря.
    calendar = holidays_ru.between(start, end)
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        flag = draft['days'].get(day.isoformat(), {}).get('holiday')
        if flag if isinstance(flag, bool) else day in calendar:
            request.holidays.add(day)
    return request


def to_schedule(draft):
    schedule = {}
    for name, row in draft['cells'].items():
        for key, value in row.items():
            start, end, _ = parse_cell(value)
            if start is not None:
                schedule.setdefault(name, {})[date.fromisoformat(key)] = (start, end) if end else start
    return schedule


def check(draft, start, end):
    """Что нарушено в текущем черновике: тот же разбор, что после сборки."""
    if not draft['people']:
        return {'text': 'Добавь людей в график.', 'problems': 0, 'days': {}, 'items': []}
    request = to_request(draft, start, end)
    schedule = to_schedule(draft)
    found = roster.audit(request, schedule)
    flagged = {}
    for day, *_ in found.shortages + found.closers + found.openers:
        flagged[day.isoformat()] = True
    text = roster.report(request, schedule, found)
    # Нарушения тем же порядком, что в тексте разбора, но с днём и человеком:
    # на сайте строку можно нажать и перейти к нужной клетке.
    where = ([(day, None) for day, *_ in found.shortages] + [(day, name) for name, day in found.forced]
             + [(day, name) for name, day, *_ in found.rises] + [(day, None) for day, *_ in found.closers]
             + [(day, None) for day, *_ in found.openers] + [(day, name) for name, day, _ in found.streaks])
    lines = text.split('\n')
    bullets = []
    if 'Что не получилось:' in lines:
        for line in lines[lines.index('Что не получилось:') + 1:]:
            if not line.startswith('• '):
                break
            bullets.append(line[2:])
    items = [{'day': day.isoformat(), 'name': name, 'text': line} for (day, name), line in zip(where, bullets)]
    return {'text': text, 'problems': len(where), 'days': flagged, 'items': items}


def apply_variant(draft, variant, manual):
    """Ставит вариант в черновик: его смены и его автоматические выходные поверх ручных."""
    draft['cells'] = {name: dict(row) for name, row in variant['cells'].items()}
    draft['off_auto'] = {name: list(days) for name, days in variant['off'].items()}
    names = set(manual) | set(variant['off'])
    draft['off'] = {name: sorted(set(manual.get(name, [])) | set(variant['off'].get(name, []))) for name in names}
    draft['variant'] = variant['id']


def build_all(draft, start, end, requested=None):
    """Обязательные выходные расставляются сами, затем собираются несколько вариантов графика;
    лучший сразу встаёт в таблицу. Выходные, которые менеджер поставил сам (отпуск), остаются.
    requested — выходные, которые люди выбрали в Telegram ({имя: [даты]}): они ставятся первыми,
    не больше нормы на человека, остальное до нормы добавляет программа."""
    previous, asked_before = draft.get('off_auto', {}), draft.get('off_requested', {})
    manual = {name: [d for d in days if d not in previous.get(name, []) and d not in asked_before.get(name, [])]
              for name, days in draft['off'].items()}
    manual = {name: days for name, days in manual.items() if days}
    students = {p['name'] for p in draft['people'] if p['student']}
    period = (end - start).days + 1
    asked = {}
    for name, days in (requested or {}).items():
        if name not in {p['name'] for p in draft['people']}:
            continue
        keep = sorted({d for d in days if start.isoformat() <= d <= end.isoformat()})[:roster.off_limit(period, name in students)]
        if keep:
            asked[name] = keep
    fixed = {name: sorted(set(manual.get(name, [])) | set(asked.get(name, []))) for name in set(manual) | set(asked)}
    request = to_request({**draft, 'off': fixed}, start, end)
    found = roster.build_variants(request)
    draft['variants'] = [{'id': r['id'], 'title': r['title'],
                          'cells': {name: {day.isoformat(): str(hour) for day, hour in row.items()}
                                    for name, row in r['schedule'].items() if row},
                          'off': {name: sorted(d.isoformat() for d in days) for name, days in r['off'].items() if days},
                          'pros': r['pros'], 'cons': r['cons']} for r in found]
    apply_variant(draft, draft['variants'][0], fixed)
    draft['off_requested'] = asked
    # Лучший вариант уже стоит в таблице, но менеджер ещё не выбирал: сначала он видит все варианты.
    draft['picked'] = False


async def requested_offs(restaurant_id, start, draft):
    """Выходные, которые люди выбрали в Telegram, по именам из черновика."""
    got = await days_off.for_period(restaurant_id, start)
    by_link = {p['link']: p['name'] for p in draft['people'] if p.get('link')}
    return {by_link[link]: days for link, days in got.items() if link in by_link}


async def schedulable(uid):
    """Рестораны, где человек составляет график: владелец, управляющий и менеджеры."""
    import roles
    return await roles.places(uid)


async def carry_over(restaurant_id, start, members):
    """Новый график начинается с прошлого: тот же порядок людей, учёба и удобный приход.
    Ушедшие из ресторана выпадают, новые сотрудники добавляются в конец."""
    rows = (await db._execute(db.supabase.table('rosters').select('draft,start_date')
                              .eq('restaurant_id', restaurant_id).lt('start_date', start.isoformat())
                              .order('start_date', desc=True).limit(1))).data
    previous = rows[0]['draft'] if rows else {}
    active = {m['id']: m['report_name'] for m in members}
    by_name = {identity.name_key(n): link for link, n in active.items()}
    people, seen = [], set()
    for person in previous.get('people', []):
        # Вписанный вручную человек потом вступил в бота: теперь это он, а не тёзка.
        link = person.get('link') or by_name.get(identity.name_key(person['name']))
        if link and link not in active:
            continue
        if link in seen:
            continue
        name = active.get(link, person['name'])
        people.append({'name': name, 'student': bool(person.get('student')), 'link': link})
        seen.add(link or name)
    people += [{'name': name, 'student': False, 'link': link} for link, name in active.items() if link not in seen]
    starts = {p['name']: previous.get('wish_starts', {}).get(p['name']) for p in people}
    return {'people': people, 'wish_starts': {n: v for n, v in starts.items() if v}}


async def team(restaurant_id):
    """Подтверждённые официанты: менеджеры и управляющий в график смен не попадают."""
    return await db._pages(lambda: db.supabase.table('employee_links').select('id,report_name')
                           .eq('restaurant_id', restaurant_id).eq('status', 'approved').eq('role', 'staff')
                           .order('requested_at').order('id'))


def _period(row):
    return date.fromisoformat(row['start_date']), date.fromisoformat(row['end_date'])


def _summary(row):
    return {'id': row['id'], 'start': row['start_date'], 'end': row['end_date'],
            'published_at': row.get('published_at'), 'updated_at': row.get('updated_at'),
            'changed': bool(row.get('published_at')) and row.get('published') != row.get('draft')}


async def _load(roster_id, allowed):
    rows = (await db._execute(db.supabase.table('rosters').select('*').eq('id', str(UUID(str(roster_id)))))).data
    if not rows or rows[0]['restaurant_id'] not in allowed:
        return None
    return rows[0]


def _opened(row):
    start, end = _period(row)
    draft = row['draft']
    return {**_summary(row), 'revision': row['revision'], 'restaurant_id': row['restaurant_id'],
            'draft': draft, 'check': check(draft, start, end),
            'holidays': {d.isoformat(): name for d, name in holidays_ru.between(start, end).items()}}


async def _save(row, uid, revision, values):
    """Запись только поверх той версии, что видел менеджер: иначе Stale."""
    values = {**values, 'revision': revision + 1, 'updated_by': uid,
              'updated_at': datetime.now(timezone.utc).isoformat()}
    saved = (await db._execute(db.supabase.table('rosters').update(values)
                               .eq('id', row['id']).eq('revision', revision))).data
    if not saved:
        raise Stale()
    return saved[0]


def _track(uid, event, **extra):
    """Исследование: только факт и этап, без имён, дат, смен и чисел. Сбой телеметрии ничего не ломает."""
    try:
        import research
        research.track(uid, event, source='server', screen='schedule', **extra)
    except Exception:
        pass


async def handle(request):
    from webapp_api import _auth, NO_CACHE
    from identity_chat import enabled
    uid, body = await _auth(request)
    if uid is None:
        return body

    def response(data, status=200):
        return web.json_response(data, status=status, headers=NO_CACHE)
    if not enabled():
        return response({'error': 'График пока выключен.'}, 404)
    action = request.match_info['action']
    stage = 'access'
    try:
        places = await schedulable(uid)
        allowed = {r['id'] for r in places}
        if not places:
            return response({'error': 'График составляет администратор ресторана.'}, 403)
        if action == 'list':
            restaurant_id = str(UUID(str(body.get('restaurant_id') or places[0]['id'])))
            if restaurant_id not in allowed:
                return response({'error': 'Это не твой ресторан.'}, 403)
            stage = 'list'
            rows = await db._pages(lambda: db.supabase.table('rosters')
                                   .select('id,start_date,end_date,published_at,updated_at,draft,published')
                                   .eq('restaurant_id', restaurant_id).order('start_date', desc=True).order('id'))
            return response({'restaurants': places, 'restaurant_id': restaurant_id,
                             'periods': [_summary(r) for r in rows]})
        if action == 'create':
            restaurant_id = str(UUID(str(body.get('restaurant_id', ''))))
            if restaurant_id not in allowed:
                return response({'error': 'Это не твой ресторан.'}, 403)
            start, end = _day(body.get('start')), _day(body.get('end'))
            if end < start or (end - start).days >= roster.MAX_DAYS:
                raise ValueError(f'Период — от одного до {roster.MAX_DAYS} дней.')
            stage = 'create'
            # Периоды не пересекаются: иначе команде непонятно, какой график действует.
            clash = (await db._execute(db.supabase.table('rosters').select('start_date,end_date')
                                       .eq('restaurant_id', restaurant_id).lte('start_date', end.isoformat())
                                       .gte('end_date', start.isoformat()).limit(1))).data
            if clash:
                a, b = date.fromisoformat(clash[0]['start_date']), date.fromisoformat(clash[0]['end_date'])
                raise ValueError(f'Даты пересекаются с графиком {a.day}.{a.month:02d}–{b.day}.{b.month:02d}.')
            seed = await carry_over(restaurant_id, start, await team(restaurant_id))
            draft = normalize(seed, start, end)
            if draft['people']:
                # Сразу несколько вариантов: менеджеру остаётся выбрать и поправить.
                if _building.locked():
                    return response({'error': 'Сейчас собирается другой график. Попробуй через минуту.'}, 429)
                asked = await requested_offs(restaurant_id, start, draft)
                async with _building:
                    await asyncio.to_thread(build_all, draft, start, end, asked)
            row = {'id': str(uuid4()), 'restaurant_id': restaurant_id, 'start_date': start.isoformat(),
                   'end_date': end.isoformat(), 'draft': draft, 'updated_by': uid}
            try:
                created = (await db._execute(db.supabase.table('rosters').insert(row))).data[0]
            except APIError as error:
                if error.code == '23505':
                    raise ValueError('График с этой даты начала уже есть — открой его в списке.') from None
                raise
            _track(uid, 'roster_created')
            return response(_opened(created))
        row = await _load(body.get('id', ''), allowed)
        if not row:
            return response({'error': 'График не найден или доступ изменился.'}, 404)
        start, end = _period(row)
        if action == 'open':
            return response({**_opened(row), 'team': await team(row['restaurant_id'])})
        if action == 'delete':
            if row.get('published_at'):
                raise ValueError('Опубликованный график не удаляется.')
            stage = 'delete'
            await db._execute(db.supabase.table('rosters').delete().eq('id', row['id']).is_('published_at', 'null'))
            return response({'ok': True})
        if action in ('offs', 'ask_offs'):
            result = await offs_action(request, uid, row, start, end, action == 'ask_offs')
            if action == 'ask_offs' and result.get('sent'):
                _track(uid, 'days_off_asked')
            return response(result)
        revision = body.get('revision')
        if type(revision) is not int:
            raise ValueError('Обнови страницу: версия черновика неизвестна.')
        if action in ('save', 'build'):
            # В график попадают только подтверждённые сотрудники ресторана: человека по имени
            # добавить нельзя, уволенный (доступ отозван) убирается сам.
            raw = body.get('draft')
            if isinstance(raw, dict):
                approved = {m['id'] for m in await team(row['restaurant_id'])}
                raw = {**raw, 'people': [p for p in raw.get('people') or [] if isinstance(p, dict) and str(p.get('link')) in approved]}
            draft = normalize(raw, start, end)
            if action == 'build':
                if not draft['people']:
                    raise ValueError('В графике нет подтверждённых сотрудников.')
                stage = 'build'
                if _building.locked():
                    return response({'error': 'Сейчас собирается другой график. Попробуй через минуту.'}, 429)
                asked = await requested_offs(row['restaurant_id'], start, draft)
                async with _building:
                    await asyncio.to_thread(build_all, draft, start, end, asked)
            stage = 'save'
            saved = await _save(row, uid, revision, {'draft': draft})
            if action == 'build':
                _track(uid, 'roster_built')
            elif draft.get('picked') and not (row['draft'] or {}).get('picked'):
                _track(uid, 'roster_variant_picked')
            return response(_opened(saved))
        if action == 'dates':
            # Даты черновика меняются на месте: лишние дни отбрасываются, новые остаются пустыми.
            if row.get('published_at'):
                raise ValueError('У опубликованного графика даты не меняются.')
            new_start, new_end = _day(body.get('start')), _day(body.get('end'))
            if new_end < new_start or (new_end - new_start).days >= roster.MAX_DAYS:
                raise ValueError(f'Период — от одного до {roster.MAX_DAYS} дней.')
            stage = 'dates'
            others = (await db._execute(db.supabase.table('rosters').select('id,start_date,end_date')
                                        .eq('restaurant_id', row['restaurant_id']).lte('start_date', new_end.isoformat())
                                        .gte('end_date', new_start.isoformat()).limit(5))).data
            clash = [c for c in others if c['id'] != row['id']]
            if clash:
                a, b = date.fromisoformat(clash[0]['start_date']), date.fromisoformat(clash[0]['end_date'])
                raise ValueError(f'Даты пересекаются с графиком {a.day}.{a.month:02d}–{b.day}.{b.month:02d}.')
            draft = normalize(trim(row['draft'], new_start, new_end), new_start, new_end)
            saved = await _save(row, uid, revision, {
                'start_date': new_start.isoformat(), 'end_date': new_end.isoformat(), 'draft': draft})
            _track(uid, 'roster_dates_changed')
            return response(_opened(saved))
        if action == 'publish':
            if revision != row['revision']:
                raise Stale()
            stage = 'publish'
            now = datetime.now(timezone.utc).isoformat()
            saved = await _save(row, uid, revision, {'published': row['draft'], 'published_at': now})
            _track(uid, 'roster_republished' if row.get('published_at') else 'roster_published')
            stage = 'notify'
            told = await notify(request.app.get('bot'), uid, row['published'], row['draft'], start, end, row['restaurant_id'])
            return response({**_opened(saved), 'notified': told})
        return response({'error': 'Такого действия нет.'}, 404)
    except Stale:
        return response({'error': 'График изменили в другом окне. Обнови страницу, чтобы не потерять чужие правки.'}, 409)
    except ValueError as error:
        text = str(error)
        if 'UUID' in text or 'hexadecimal' in text:
            text = 'График не найден. Обнови страницу.'
        return response({'error': text}, 400)
    except APIError as error:
        if error.code in MISSING:
            return response({'error': NOT_READY}, 503)
        return _failure(response, uid, error, stage)
    except Exception as error:
        return _failure(response, uid, error, stage)


async def offs_action(request, uid, row, start, end, send):
    """Кто уже записал обязательные выходные в Telegram; при send — просит ответить остальных."""
    draft = row['draft']
    approved = {m['id'] for m in await team(row['restaurant_id'])}
    people = [p for p in draft.get('people', []) if p.get('link') in approved]
    links = {p['link']: p['name'] for p in people}
    try:
        got = await days_off.for_period(row['restaurant_id'], start)
        sent = 0
        if send:
            bot = request.app.get('bot')
            if not bot:
                raise ValueError('Бот сейчас недоступен. Попробуй позже.')
            sent = await days_off.ask(bot, row['restaurant_id'], start, end, links, set(got))
    except APIError as error:
        if error.code in MISSING:
            raise ValueError('Запись выходных пока недоступна: владельцу нужно обновить базу (migration_v33.sql).') from None
        raise
    answered = [link for link in links if link in got]
    asked_before = draft.get('off_requested', {})
    # Новое: ответ расходится с тем, что учтено при последней сборке.
    fresh = sum(1 for link in answered if sorted(got[link]) != sorted(asked_before.get(links[link], [])))
    return {'total': len(links), 'answered': len(answered), 'fresh': fresh, 'sent': sent}


# ─── сообщения команде ───────────────────────────────────────────────────────

WEEK = ('пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс')
_sending = set()


def own_shifts(snapshot, name):
    lines = []
    for key, value in sorted(((snapshot or {}).get('cells') or {}).get(name, {}).items()):
        start, end, _ = parse_cell(value)
        if start is not None:
            day = date.fromisoformat(key)
            lines.append(f'{WEEK[day.weekday()]} {day.day} — с {start}' + (f' до {end}' if end else ''))
    return lines


def notices(old, new, start, end, links=None):
    """Первая публикация — сообщение каждому из команды; дальше — только тем, чьи смены
    или обязательные выходные изменились. В сообщении человека — только его смены."""
    period = f'{start.day} {roster.MONTHS[start.month - 1]} – {end.day} {roster.MONTHS[end.month - 1]}'
    def mine(snapshot, name):
        snapshot = snapshot or {}
        return (snapshot.get('cells') or {}).get(name, {}), sorted((snapshot.get('off') or {}).get(name, []))
    result = []
    for person in new.get('people', []):
        name = person['name']
        link = person.get('link') or (links or {}).get(identity.name_key(name))
        if not link or (old is not None and mine(old, name) == mine(new, name)):
            continue
        shifts = own_shifts(new, name)
        head = f'Опубликован график на {period}.' if old is None else f'В графике на {period} изменились твои смены.'
        body = 'Твои смены:\n' + '\n'.join(shifts) if shifts else 'Смен у тебя в этом графике нет.'
        result.append((link, f'{head}\n\n{body}'))
    return result


async def notify(bot, publisher, old, new, start, end, restaurant_id):
    """Отправляет в фоне, чтобы публикация не ждала Telegram. Возвращает, скольким напишем."""
    if not bot:
        return 0
    names = {identity.name_key(m['report_name']): m['id'] for m in await team(restaurant_id)}
    planned = notices(old, new, start, end, names)
    if not bot or not planned:
        return 0
    links = (await db._execute(db.supabase.table('employee_links').select('id,user_id,status')
                               .in_('id', [link for link, _ in planned]))).data
    users = {r['id']: r['user_id'] for r in links if r['status'] == 'approved' and r['user_id'] != publisher}
    messages = [(users[link], text) for link, text in planned if link in users]

    async def send():
        for user_id, text in messages:
            try:
                await asyncio.wait_for(bot.send_message(user_id, text), timeout=5)
            except Exception as error:
                from diagnostics import failure
                failure(error, area='roster', stage='notify')
            await asyncio.sleep(0.05)
    task = asyncio.create_task(send())
    _sending.add(task)
    task.add_done_callback(_sending.discard)
    return len(messages)


def _failure(response, uid, error, stage):
    from diagnostics import failure
    code, reference = failure(error, area='roster', stage=stage)
    return response({'error': 'Не получилось открыть график. Попробуй ещё раз чуть позже.',
                     'code': code, 'reference': reference}, 503)


# ─── просмотр командой ───────────────────────────────────────────────────────

async def team_places(uid):
    """Где человек видит график: своя команда, свои рестораны и где он составляет график."""
    places = {r['id']: r for r in await identity.owner_restaurants(uid)}
    rows = (await db._execute(db.supabase.table('employee_links').select('id,restaurant_id,status,report_name')
                              .eq('user_id', uid))).data
    link = next((r for r in rows if r['status'] == 'approved'), None)
    if link and link['restaurant_id'] not in places:
        found = (await db._execute(db.supabase.table('restaurants').select('id,name').eq('id', link['restaurant_id']))).data
        if found:
            places[found[0]['id']] = found[0]
    return list(places.values()), link


def published_view(row, link):
    """Только опубликованное: смены, обязательные выходные и события дня."""
    snapshot = row['published']
    start, end = _period(row)
    days = [date.fromordinal(start.toordinal() + i).isoformat() for i in range((end - start).days + 1)]
    people = []
    for person in snapshot.get('people', []):
        name = person['name']
        me = bool(link) and (person.get('link') == link['id'] or (not person.get('link') and name == link['report_name']))
        people.append({'name': name, 'me': me, 'cells': snapshot.get('cells', {}).get(name, {}),
                       'off': snapshot.get('off', {}).get(name, [])})
    return {'start': row['start_date'], 'end': row['end_date'], 'days': days, 'people': people,
            'events': snapshot.get('events', {}), 'published_at': row['published_at'],
            'holidays': {d.isoformat(): name for d, name in holidays_ru.between(start, end).items()}}


async def view(request):
    from webapp_api import _auth, NO_CACHE, _calendar_base_url
    uid, body = await _auth(request)
    if uid is None:
        return body

    def response(data, status=200):
        return web.json_response(data, status=status, headers=NO_CACHE)
    try:
        places, link = await team_places(uid)
        if not places:
            return response({'available': False})
        ids = {r['id'] for r in places}
        restaurant_id = body.get('restaurant_id') or (link['restaurant_id'] if link else places[0]['id'])
        restaurant_id = str(UUID(str(restaurant_id)))
        if restaurant_id not in ids:
            return response({'error': 'Это не твой ресторан.'}, 403)
        rows = await db._pages(lambda: db.supabase.table('rosters')
                               .select('id,start_date,end_date,published,published_at')
                               .eq('restaurant_id', restaurant_id).not_.is_('published', 'null')
                               .order('start_date').order('id'))
        managing = {r['id'] for r in await schedulable(uid)}
        base = _calendar_base_url(request)
        result = {'available': True, 'restaurants': places, 'restaurant_id': restaurant_id,
                  'today': op_today().isoformat(), 'can_edit': restaurant_id in managing,
                  'site_url': base + '/restaurant#schedule' if base else None,
                  'periods': [{'start': r['start_date'], 'end': r['end_date']} for r in rows], 'period': None}
        if rows:
            wanted = body.get('start')
            today = op_today().isoformat()
            pick = (next((r for r in rows if r['start_date'] == wanted), None)
                    or next((r for r in rows if r['start_date'] <= today <= r['end_date']), None)
                    or next((r for r in rows if r['start_date'] > today), None) or rows[-1])
            result['period'] = published_view(pick, link if link and link['restaurant_id'] == restaurant_id else None)
            if restaurant_id not in managing:
                # Не чаще раза в день на человека: считаем, кто открывал график, а не сколько раз загрузилась страница.
                from uuid import NAMESPACE_URL, uuid5
                _track(uid, 'roster_viewed', operation=str(uuid5(NAMESPACE_URL, f'roster-viewed:{uid}:{op_today()}')))
        return response(result)
    except ValueError:
        return response({'error': 'Ресторан не найден. Открой приложение заново.'}, 400)
    except APIError as error:
        if error.code in MISSING:
            return response({'available': False})
        return _failure(response, uid, error, 'view')
    except Exception as error:
        return _failure(response, uid, error, 'view')


def register(app):
    app.router.add_post('/api/restaurant/roster/{action}', handle)
    app.router.add_post('/api/schedule/view', view)
