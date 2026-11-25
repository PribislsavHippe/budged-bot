"""One combined invitation for the two New Year rosters at Italianskaya."""
import asyncio
from datetime import date, datetime, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from postgrest.exceptions import APIError

from workday import MSK

PERIODS = (('2026-12-16', '2026-12-31'), ('2027-01-01', '2027-01-15'))
INVITATION = ('Друзья, кому важно отдыхать в новогодний период — отмечайте нужный вам период. '
              'По кнопке откроется календарь с 16 декабря по 15 января. Когда соберем, сможем найти решение.')
_launch_lock = asyncio.Lock()


def is_holiday_restaurant(name):
    return name.strip().casefold() in {'итали', 'итальянская'}


def collection_only(row):
    return not row.get('published_at') and (row.get('draft') or {}).get('holiday_collection_only') is True


async def prepare_periods(restaurant_id, actor):
    """Reuse exact periods or reserve dates for collecting wishes, without calculating a draft."""
    import db
    from roster_api import carry_over, normalize, team

    async def covering_rows():
        return (await db._execute(db.supabase.table('rosters').select('*').eq('restaurant_id', restaurant_id)
                                  .gte('end_date', PERIODS[0][0]).lte('start_date', PERIODS[-1][1]))).data

    def by_period(rows):
        exact = {}
        for row in rows:
            bounds = (row['start_date'], row['end_date'])
            if bounds not in PERIODS:
                start, end = map(date.fromisoformat, bounds)
                raise ValueError(f'Новогодние даты пересекаются с графиком {start:%d.%m}–{end:%d.%m}. Проверь его даты.')
            exact[bounds] = row
        return exact

    found = by_period(await covering_rows())
    if all(period in found for period in PERIODS):
        return [found[period] for period in PERIODS]
    members = await team(restaurant_id)
    if not members:
        raise ValueError('В ресторане пока нет подключённых официантов.')
    new_rows = []
    for bounds in PERIODS:
        if bounds in found:
            continue
        start, end = map(date.fromisoformat, bounds)
        seed = await carry_over(restaurant_id, start, members)
        draft = normalize(seed, start, end)
        draft['holiday_collection_only'] = True
        new_rows.append({'id': str(uuid4()), 'restaurant_id': restaurant_id,
                         'start_date': bounds[0], 'end_date': bounds[1], 'draft': draft,
                         'updated_by': actor, 'collect_wishes_at': None})
    try:
        created = (await db._execute(db.supabase.table('rosters').insert(new_rows))).data
    except APIError as error:
        if error.code not in ('23505', '23P01') and 'roster_overlap' not in str(error):
            raise
        found = by_period(await covering_rows())
        if all(period in found for period in PERIODS):
            return [found[period] for period in PERIODS]
        raise ValueError('Графики изменились. Запусти сбор ещё раз.') from None
    found.update(by_period(created))
    return [found[period] for period in PERIODS]


async def launch(bot, actor):
    import os
    import db
    import research
    import roles
    import roster_wishes
    from admin import is_admin
    from diagnostics import failure
    from identity_chat import enabled
    from roster_api import off_recipients

    if not is_admin(actor):
        return 'Эту рассылку может запустить владелец бота.'
    now = datetime.now(MSK)
    if not enabled():
        return 'Сбор выходных для ресторана сейчас недоступен.'
    if not 10 <= now.hour < 20:
        return 'Рассылку можно запустить с 10:00 до 20:00 мск.'
    if now.date().isoformat() >= PERIODS[0][0]:
        return 'Первый период уже начался. Запроси выходные через нужный график в миниаппе.'
    host = (os.getenv('WEBHOOK_HOST') or '').rstrip('/')
    if not host:
        return 'Адрес миниаппа не настроен. Приглашения не отправлены.'
    if _launch_lock.locked():
        return 'Рассылка уже идёт.'
    async with _launch_lock:
        places = [place for place in await roles.places(actor) if is_holiday_restaurant(place['name'])]
        if len(places) != 1:
            return 'Не получилось однозначно найти Итальянскую. Приглашения не отправлены.'
        restaurant_id = places[0]['id']
        try:
            periods = await prepare_periods(restaurant_id, actor)
        except ValueError as error:
            return f'{error} Приглашения не отправлены.'
        if any(row.get('published_at') or not roster_wishes.migrated(row) for row in periods):
            return 'Сбор доступен для двух неопубликованных графиков с календарём пожеланий. Приглашения не отправлены.'
        members = {}
        for row in periods:
            links = {person['link'] for person in row['draft'].get('people', []) if person.get('link')}
            recipients = await off_recipients(row, links)
            if not recipients:
                return 'В каждом графике должен быть хотя бы один подключённый официант. Приглашения не отправлены.'
            for uid, link in recipients.items():
                members.setdefault(uid, (link, []))[1].append(row['id'])
        roster_ids = [row['id'] for row in periods]
        path = '/app/days-off?' + '&'.join(f'roster={roster_id}' for roster_id in roster_ids)
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='Отметить выходные', web_app=WebAppInfo(url=host + path)),
        ]])
        sent = previous = failed = 0
        for uid, (link, member_rosters) in members.items():
            # A Telegram timeout can arrive after delivery. Claim once before attempting the send.
            operation = uuid5(NAMESPACE_URL, f'holiday-off-invitation:{restaurant_id}:{PERIODS[0][0]}:{link}')
            claimed = (await db._execute(db.supabase.table('roster_wish_operations').upsert(
                {'id': str(operation), 'link_id': link, 'roster_id': member_rosters[0], 'result': {}},
                on_conflict='id', ignore_duplicates=True))).data
            if not claimed:
                previous += 1
                continue
            try:
                await asyncio.wait_for(bot.send_message(uid, INVITATION, reply_markup=markup,
                                                        disable_notification=True, parse_mode=None), timeout=5)
            except Exception as error:
                failed += 1
                failure(error, area='roster_wishes', stage='holiday_invitation_send')
                continue
            sent += 1
            research.track(uid, 'reminder_sent', source='server', screen='schedule', step='days_off')
            stamp = datetime.now(timezone.utc).isoformat()
            try:
                await db._execute(db.supabase.table('roster_wish_notices').upsert(
                    [{'roster_id': roster_id, 'link_id': link, 'stage': 1, 'claimed_at': stamp, 'sent_at': stamp}
                     for roster_id in member_rosters], on_conflict='roster_id,link_id,stage', ignore_duplicates=True))
            except Exception as error:
                failure(error, area='roster_wishes', stage='holiday_invitation_notice')
        summary = [f'Приглашение на оба периода отправлено: {sent} из {len(members)}.']
        if previous:
            summary.append(f'Повторно не отправлял: {previous}.')
        if failed:
            summary.append(f'Не получил подтверждение отправки: {failed}.')
        return '\n'.join(summary)
