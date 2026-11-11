"""Опубликованный график становится личными сменами официантов.

Личные смены (`shifts`) питают календарь в приложении, подписку на календарь iPhone и вечерние напоминания.
Пока график публиковался только как снимок, ничего из этого не работало. Теперь публикация записывает каждому
подтверждённому сотруднику его смены на период графика; опубликованный график — единственный источник смен
на этот период: смены, которых в нём нет, из будущих дней убираются. Прошедшие дни не удаляются.
"""
import asyncio
import logging
from datetime import date

import db
import schedule
from workday import op_today


# Версия опубликованного графика, которая уже записывается или записана: {id графика: версия}.
# Запись идёт в фоне, поэтому старая публикация не должна дописать свои смены после новой.
_versions = {}
_locks = {}


def desired(snapshot, name):
    """Смены человека из снимка: ([{'date', 'start', 'end'}], даты, которые трогать нельзя).
    Отметка «*» и выходные дни без смены не в счёт; непонятная клетка (например, старый интервал до 24)
    не должна стирать уже стоящую личную смену, поэтому её дата защищена."""
    cells, protected = [], set()
    for day, value in sorted(((snapshot or {}).get('cells') or {}).get(name, {}).items()):
        try:
            times = schedule.cell(str(value).replace('*', '').strip())
        except ValueError:
            protected.add(day)
            continue
        if times:
            cells.append({'date': day, 'start': times[0], 'end': times[1]})
    return cells, protected


async def sync_person(uid, cells, start, end, protected=(), alive=lambda: True):
    """Записывает смены человека и убирает будущие смены периода, которых в графике нет.
    alive() — пока публикация актуальна: после новой публикации старая запись прекращается."""
    await db.get_or_create_user(uid)
    for i in range(0, len(cells), 31):
        if not alive():
            return 0, 0
        await schedule.save(uid, cells[i:i + 31])
    if not alive():
        return 0, 0
    keep = {c['date'] for c in cells} | set(protected)
    today = op_today().isoformat()
    rows = (await db._execute(db.supabase.table('shifts').select('shift_date').eq('user_id', uid)
                              .gte('shift_date', start.isoformat()).lte('shift_date', end.isoformat()))).data
    stale = [r['shift_date'] for r in rows if r['shift_date'] not in keep and r['shift_date'] >= today]
    for day in stale:
        if not alive():
            break
        await db._execute(db.supabase.table('shifts').delete().eq('user_id', uid).eq('shift_date', day))
    return len(cells), len(stale)


async def sync_published(restaurant_id, start, end, snapshot, previous=None, version=0, roster_id=None):
    """Записывает смены под версией публикации: более новая версия вытесняет старую, а записи одной
    публикации идут по очереди. Возвращает, скольким людям записано."""
    key = roster_id or (restaurant_id, start.isoformat())
    _versions[key] = max(_versions.get(key, 0), version)
    async with _locks.setdefault(key, asyncio.Lock()):
        if version < _versions[key]:
            return 0
        return await _sync(restaurant_id, start, end, snapshot, previous, lambda: version >= _versions[key])


async def _sync(restaurant_id, start, end, snapshot, previous, alive):
    """Смены всех из опубликованного графика (и ушедших из него с прошлой публикации).
    Сбой одного человека не мешает остальным."""
    people = {p['link']: p['name'] for p in (snapshot or {}).get('people', []) if p.get('link')}
    gone = {p['link']: p['name'] for p in (previous or {}).get('people', [])
            if p.get('link') and p['link'] not in people}
    links = list(people) + list(gone)
    if not links:
        return 0
    rows = (await db._execute(db.supabase.table('employee_links').select('id,user_id,status')
                              .in_('id', links).eq('restaurant_id', restaurant_id))).data
    users = {r['id']: r['user_id'] for r in rows if r['status'] == 'approved' and r.get('user_id')}
    done = 0
    for link in links:
        uid = users.get(link)
        if not uid:
            continue
        try:
            cells, protected = desired(snapshot, people[link]) if link in people else ([], set())
            await sync_person(uid, cells, start, end, protected, alive)
            done += 1
        except Exception as error:
            from diagnostics import failure
            failure(error, area='roster', stage='sync_shifts')
            logging.warning('roster sync: one person failed')
    return done


async def resync_all():
    """При запуске: догоняет графики, опубликованные до появления синхронизации (повтор безопасен)."""
    try:
        rows = await db._pages(lambda: db.supabase.table('rosters')
                               .select('id,restaurant_id,start_date,end_date,published,revision')
                               .not_.is_('published', 'null').gte('end_date', op_today().isoformat())
                               .order('start_date'))
    except Exception as error:
        from diagnostics import failure
        failure(error, area='roster', stage='resync_fetch')
        return 0
    total = 0
    for r in rows:
        total += await sync_published(r['restaurant_id'], date.fromisoformat(r['start_date']),
                                      date.fromisoformat(r['end_date']), r['published'], version=r.get('revision') or 0,
                                      roster_id=r['id'])
    return total
