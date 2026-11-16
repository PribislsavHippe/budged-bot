"""Тренинги в чатах: подключение общего чата, ссылка на таблицу и кнопка «Записаться» в личном чате.

Бота добавляет в общий чат управляющий или менеджер — так бот узнаёт, чей это чат. Ссылку на таблицу присылают
один раз ответом на сообщение бота (в группах бот не читает остальную переписку); новые листы того же файла бот
находит сам. Записываются из личного приглашения. Тексты — ЧЕРНОВИКИ.
"""
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardButton as Button, \
    InlineKeyboardMarkup as Markup, Message

import db
import roles
import training_sheet as sheet
import trainings

router = Router()
GROUPS = {'group', 'supergroup'}

BOUND = ('Привет! Буду писать сюда, когда открывается запись на тренинги, а тем, у кого в день тренинга выходной, — '
         'лично, с кнопкой «Записаться». Ответь на это сообщение ссылкой на таблицу записи на тренинги.')
NOT_MANAGER = 'Подключить меня к чату может управляющий или менеджер ресторана — пусть добавит меня сюда сам.'
TAKEN = 'Этот чат уже подключён к другому ресторану.'
CHOOSE = 'Для какого ресторана этот чат?'
SHEET_OK = 'Таблица подключена. Тренингов впереди: {count}. Новые листы в этой таблице найду сам.'
SHEET_WRITE = '\nЧтобы бот мог записывать людей, открой таблице доступ на редактирование для {email}.'
SHEET_BAD = 'Не получилось прочитать таблицу. Проверь, что она открыта по ссылке, и пришли её ещё раз ответом на это сообщение.'


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


@router.message(F.chat.type.in_(GROUPS), F.reply_to_message, F.text)
async def sheet_link(message: Message):
    replied = message.reply_to_message
    if not replied.from_user or replied.from_user.id != message.bot.id:
        return
    document = sheet.sheet_id(message.text)
    bound = await trainings.binding(chat_id=message.chat.id)
    if not document or not bound or not await roles.place(message.from_user.id, bound['restaurant_id']):
        return
    try:
        from workday import op_today
        found = trainings.upcoming(await sheet.read(document, op_today()), op_today())
    except Exception as error:
        logging.warning('training sheet unreadable: %s', type(error).__name__)
        await message.reply(SHEET_BAD)
        return
    await db._execute(db.supabase.table('training_chats').update({'sheet_id': document})
                      .eq('restaurant_id', bound['restaurant_id']))
    answer = SHEET_OK.format(count=len([s for s in found if not s['cancelled']]))
    if sheet.configured():
        answer += SHEET_WRITE.format(email=sheet._account()['client_email'])
    await message.reply(answer)
    try:
        await trainings.sync(message.bot, {**bound, 'sheet_id': document})
    except Exception as error:
        from diagnostics import failure
        failure(error, area='trainings', stage='first_sync')


@router.callback_query(F.message.chat.type == 'private', F.data.startswith(trainings.PREFIX))
async def sign_up(query: CallbackQuery):
    try:
        answer, session = await trainings.sign(query.from_user.id, query.data[len(trainings.PREFIX):])
    except Exception as error:
        from diagnostics import failure
        failure(error, area='trainings', stage='sign')
        answer, session = 'Не получилось записать. Попробуй ещё раз чуть позже.', None
    await query.answer(answer[:200], show_alert=session is None)
    if session is None or not query.message:
        return
    markup = query.message.reply_markup
    rows = [row for row in (markup.inline_keyboard if markup else []) if row[0].callback_data != query.data]
    try:
        await query.message.edit_text(f'{query.message.text}\n{answer}', reply_markup=Markup(inline_keyboard=rows))
    except Exception as error:
        logging.warning('training invitation not updated: %s', type(error).__name__)
