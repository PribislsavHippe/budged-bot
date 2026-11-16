"""Тренинги в общем чате: подключение чата (объявления, приглашения и отчёты — в `trainings`).

Бота добавляет в общий чат управляющий или менеджер — так бот узнаёт, чей это чат. Таблица тренингов одна,
ссылка на неё в настройках бота (`TRAINING_SHEET`). Остальную переписку группы бот не читает. Тексты — ЧЕРНОВИКИ.
"""
from aiogram import F, Router
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardButton as Button, \
    InlineKeyboardMarkup as Markup

import db
import roles
import trainings

router = Router()
GROUPS = {'group', 'supergroup'}

BOUND = 'Привет! Буду писать сюда, когда открывается запись на тренинги.'
NOT_MANAGER = 'Подключить меня к чату может управляющий или менеджер ресторана — пусть добавит меня сюда сам.'
TAKEN = 'Этот чат уже подключён к другому ресторану.'
CHOOSE = 'Для какого ресторана этот чат?'


async def _bind(chat_id, restaurant_id, uid):
    other = (await db._execute(db.supabase.table('training_chats').select('restaurant_id')
                               .eq('chat_id', chat_id).neq('restaurant_id', restaurant_id))).data
    if other:
        return False
    await db._execute(db.supabase.table('training_chats').upsert({
        'restaurant_id': restaurant_id, 'chat_id': chat_id, 'active': True, 'bound_by': uid}, on_conflict='restaurant_id'))
    return True


@router.my_chat_member(F.chat.type.in_(GROUPS))
async def membership(event: ChatMemberUpdated):
    status = event.new_chat_member.status
    if status in ('left', 'kicked'):
        # Бота убрали — чат больше не получает сообщений; привязку можно вернуть, добавив бота снова.
        await db._execute(db.supabase.table('training_chats').update({'active': False}).eq('chat_id', event.chat.id))
        return
    if event.old_chat_member.status not in ('left', 'kicked') or status not in ('member', 'administrator'):
        return
    places = await roles.places(event.from_user.id)
    if not places:
        await event.bot.send_message(event.chat.id, NOT_MANAGER)
        return
    if len(places) > 1:
        await event.bot.send_message(event.chat.id, CHOOSE, reply_markup=Markup(inline_keyboard=[
            [Button(text=p['name'], callback_data=f"trb:{p['id']}")] for p in places[:8]]))
        return
    if await _bind(event.chat.id, places[0]['id'], event.from_user.id):
        await event.bot.send_message(event.chat.id, BOUND)
        await _first_sync(event.bot, places[0]['id'])
    else:
        await event.bot.send_message(event.chat.id, TAKEN)


@router.callback_query(F.message.chat.type.in_(GROUPS), F.data.startswith('trb:'))
async def choose(query: CallbackQuery):
    place = await roles.place(query.from_user.id, query.data[4:])
    if not place:
        await query.answer(NOT_MANAGER, show_alert=True)
        return
    ok = await _bind(query.message.chat.id, place['id'], query.from_user.id)
    await query.message.edit_text(BOUND if ok else TAKEN)
    await query.answer()
    if ok:
        await _first_sync(query.bot, place['id'])


async def _first_sync(bot, restaurant_id):
    """Сразу после подключения — не ждать получаса."""
    try:
        bound = await trainings.binding(restaurant_id=restaurant_id)
        if bound:
            await trainings.sync(bot, bound)
    except Exception as error:
        from diagnostics import failure
        failure(error, area='trainings', stage='first_sync')
