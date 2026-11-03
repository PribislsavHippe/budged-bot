"""Роли в ресторане: владелец, управляющий и менеджеры.

Владелец — администратор бота, создавший ресторан: ему доступно всё, включая внутренности бота.
Управляющий (один на ресторан) — сотрудники, график, продажи каждого и план.
Менеджеры (сколько угодно) — сотрудники и график; продажи и план им закрыты.
Роль живёт в employee_links.role (migration_v31); до миграции работает прежнее право can_schedule.
"""
from uuid import UUID

from postgrest.exceptions import APIError

import db
import identity

MISSING = ('42P01', 'PGRST205', '42703', 'PGRST204')
LABELS = {'owner': 'владелец', 'director': 'управляющий', 'manager': 'менеджер'}
# Что открывает роль. Внутренности бота (/admin, исследования) — только ADMIN_ID, не роль.
SALES = {'owner', 'director'}
APPOINT = {'owner': {'director', 'manager', 'staff'}, 'director': {'manager', 'staff'}}


async def _links(uid):
    query = lambda fields: db.supabase.table('employee_links').select(fields).eq('user_id', uid).eq('status', 'approved')
    try:
        rows = (await db._execute(query('restaurant_id,role'))).data
        if not rows or 'role' in rows[0]:
            return [(r['restaurant_id'], r['role']) for r in rows if r.get('role') in ('manager', 'director')]
    except APIError as error:
        if error.code not in MISSING:
            raise
    try:
        rows = (await db._execute(query('restaurant_id,can_schedule'))).data
        return [(r['restaurant_id'], 'manager') for r in rows if r.get('can_schedule')]
    except APIError as error:
        if error.code not in MISSING:
            raise
        return []


async def places(uid):
    """Рестораны, которыми человек управляет, с его ролью в каждом."""
    owned = [{**r, 'role': 'owner'} for r in await identity.owner_restaurants(uid)]
    mine = {r['id'] for r in owned}
    links = [(rid, role) for rid, role in await _links(uid) if rid not in mine]
    if links:
        names = (await db._execute(db.supabase.table('restaurants').select('id,name')
                                   .in_('id', [rid for rid, _ in links]))).data
        by_id = {r['id']: r for r in names}
        owned += [{**by_id[rid], 'role': role} for rid, role in links if rid in by_id]
    return owned


async def place(uid, restaurant_id=None):
    """Один ресторан с ролью; без выбора — единственный, иначе просьба выбрать."""
    found = await places(uid)
    if restaurant_id is not None:
        wanted = str(UUID(str(restaurant_id)))
        return next((r for r in found if r['id'] == wanted), None)
    if len(found) > 1:
        raise ValueError('Выбери ресторан в миниаппе — у каждого свой список сотрудников.')
    return found[0] if found else None
