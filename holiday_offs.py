"""One combined invitation for the two New Year rosters at Italianskaya."""
import asyncio
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from workday import MSK

PERIODS = (('2026-12-16', '2026-12-31'), ('2027-01-01', '2027-01-15'))
INVITATION = ('Друзья, кому важно отдыхать в новогодний период — отмечайте нужный вам период. '
              'По кнопке откроется календарь с 16 декабря по 15 января. Когда соберем, сможем найти решение.')
_launch_lock = asyncio.Lock()


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
        places = [place for place in await roles.places(actor)
                  if place['name'].strip().casefold() in {'итали', 'итальянская'}]
        if len(places) != 1:
            return 'Не получилось однозначно найти Итальянскую. Приглашения не отправлены.'
        restaurant_id = places[0]['id']
        rows = (await db._execute(db.supabase.table('rosters').select('*').eq('restaurant_id', restaurant_id)
                                  .in_('start_date', [start for start, _ in PERIODS]))).data
        by_dates = {(row['start_date'], row['end_date']): row for row in rows}
        if any(period not in by_dates for period in PERIODS):
            return 'Создай черновики на 16–31 декабря и 1–15 января в разделе «График». Приглашения не отправлены.'
        periods = [by_dates[period] for period in PERIODS]
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
