"""Restaurant membership commands. All authority is rechecked by the database."""
import html
import os
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
import identity

router=Router()


def enabled():
    return os.getenv('IDENTITY_ENABLED','0') == '1'


def enabled_event(event):
    return enabled()


router.message.filter(F.chat.type=='private',enabled_event)
router.callback_query.filter(F.message.chat.type=='private',enabled_event)


class Identification(StatesGroup):
    name=State()


def buttons(rows):
    return Markup(inline_keyboard=[[Button(text=label,callback_data=data) for label,data in row] for row in rows])


async def error(message, exception):
    text=str(exception) if isinstance(exception,ValueError) else 'Сейчас не получается открыть данные ресторана. Попробуй чуть позже.'
    await message.answer(html.escape(text))


@router.message(Command('restaurant'))
async def create_restaurant(message, command):
    try:
        result=await identity.create(message.from_user.id,command.args or '')
        await message.answer(f"Ресторан создан: <b>{html.escape(result['name'])}</b>. Что дальше?",
                             reply_markup=buttons([[('Создать приглашение','ident:showinvite')],[('Сотрудники и заявки','ident:showteam')]]))
    except Exception as exc: await error(message,exc)


@router.message(Command('invite'))
async def invite(message,uid=None):
    try:
        restaurant,token=await identity.invite(uid or message.from_user.id)
        bot=await message.bot.get_me()
        await message.answer(f"Приглашение в {html.escape(restaurant['name'])}:\n"
                             f"https://t.me/{bot.username}?start=team_{token}\n\n"
                             'Действует 7 дней. Предыдущая ссылка отключена. Вход требует подтверждения администратора.')
    except Exception as exc: await error(message,exc)


@router.message(CommandStart(deep_link=True))
async def start_invite(message,command,state):
    if not command.args.startswith('team_'): return UNHANDLED
    token=command.args[5:]
    if not token or len(token)>64:
        await message.answer('Приглашение недействительно.')
        return
    import db, research
    await db.get_or_create_user(message.from_user.id)
    research.track(message.from_user.id,'user_started')
    # Do not publish the employee directory to invite holders.
    from report_photo import drafts, expire
    draft=drafts.get(message.from_user.id)
    if draft and (draft.lock.locked() or draft.phase == 'uncertain'):
        await message.answer('Заверши сохранение фотоотчёта и открой приглашение ещё раз.')
        return
    if draft: expire(message.from_user.id,draft)
    await state.clear()
    await state.set_state(Identification.name)
    await state.update_data(invite_token=token)
    await message.answer('Как тебя зовут в отчёте? Администратор проверит имя и увидит твои планы и продажи, записанные после присоединения. Личные чаевые и расходы останутся доступны только тебе в боте.',
                         reply_markup=buttons([[('Отмена','ident:cancelname')]]))


@router.message(Identification.name, F.photo | F.document)
async def cancel_for_photo(message,state):
    await state.clear()
    return UNHANDLED


@router.message(Identification.name,Command('cancel'))
async def cancel_registration(message,state):
    await state.clear()
    await message.answer('Заявка отменена.')

@router.callback_query(F.data=='ident:cancelname')
async def cancel_registration_button(query,state):
    if await state.get_state()!=Identification.name.state:
        await query.answer('Этот вопрос уже закрыт.');return
    await state.clear();await query.answer()
    await query.message.edit_text('Заявка отменена.')


@router.message(Identification.name,F.text,~F.text.startswith('/'))
async def register_name(message,state):
    data=await state.get_data()
    try:
        result=await identity.request(message.from_user.id,data.get('invite_token',''),message.text)
        await state.clear()
        await message.answer(f"{identity.STATUSES[result['status']]}. Посмотреть свой ресторан можно по кнопке ниже.",
                             reply_markup=buttons([[('Мой ресторан','ident:showprofile')]]))
        from ux_chat import begin
        await begin(message)
    except Exception as exc: await error(message,exc)


@router.message(Command('profile'))
async def profile(message,uid=None):
    try:
        row=await identity.profile(uid or message.from_user.id)
        if not row:
            await message.answer('Ты ещё не присоединился к ресторану. Открой ссылку, которую прислал администратор.')
            return
        note='' if row['has_admin'] else '\nУ ресторана нет действующего администратора.'
        await message.answer(f"<b>{html.escape(row['restaurant_name'])}</b>\n"
                             f"В отчёте: {html.escape(row['report_name'])}\n{identity.STATUSES[row['status']]}{note}",
                             reply_markup=buttons([[('Выйти из ресторана',f"ident:leaveask:{row['id']}")]]))
    except Exception as exc: await error(message,exc)


async def show_team(message,uid,page=0):
    restaurant,rows,more=await identity.team(uid,page)
    await message.answer(f"<b>{html.escape(restaurant['name'])}</b> · сотрудники")
    if not rows: await message.answer('Заявок пока нет.')
    for row in rows:
        actions=[]
        if row['status']=='pending':
            actions=[('Подтвердить',f"ident:approve:{row['id']}"),('Отклонить',f"ident:reject:{row['id']}")]
        elif row['status']=='approved':actions=[('Отозвать',f"ident:revokeask:{row['id']}")]
        await message.answer(f"{html.escape(row['report_name'])}\nTelegram ID: <code>{row['user_id']}</code>\n{identity.STATUSES[row['status']]}",
                             reply_markup=buttons([actions]) if actions else None)
    nav=[]
    if page:nav.append(('Назад',f'ident:page:{page-1}'))
    if more:nav.append(('Далее',f'ident:page:{page+1}'))
    if nav:await message.answer('Страницы',reply_markup=buttons([nav]))


@router.message(Command('team'))
async def team(message):
    try: await show_team(message,message.from_user.id)
    except Exception as exc: await error(message,exc)


@router.callback_query(F.data.in_({'ident:showinvite','ident:showteam','ident:showprofile'}))
async def open_restaurant_action(query):
    await query.answer()
    if query.data=='ident:showinvite':await invite(query.message,query.from_user.id)
    elif query.data=='ident:showteam':
        try:await show_team(query.message,query.from_user.id)
        except Exception as exc:await error(query.message,exc)
    else:await profile(query.message,query.from_user.id)


@router.callback_query(F.data.startswith('ident:'))
async def callback(query):
    await query.answer()
    try:
        _,action,target=query.data.split(':')
        if action=='page':
            page=int(target)
            if not 0<=page<=1000:raise ValueError('Не получилось открыть страницу. Обнови список.')
            await show_team(query.message,query.from_user.id,page)
            return
        target=str(UUID(target))
        if action in ('leaveask','leave'):
            row=await identity.profile(query.from_user.id)
            if not row or row['id']!=target:raise ValueError('Данные ресторана уже изменились. Открой профиль ещё раз.')
            if action=='leaveask':
                await query.message.answer('Выйти из ресторана? Личные записи и отчёты останутся.',reply_markup=buttons([
                    [('Отключить',f'ident:leave:{target}'),('Отмена',f'ident:cancel:{target}')]]))
            else:
                await identity.action(query.from_user.id,'leave',{'id':target})
                await query.message.edit_text('Ты вышел из ресторана. Твои записи остались.')
        elif action=='cancel':await query.message.edit_text('Отмена.')
        elif action=='revokeask':
            # The final mutation checks owner, restaurant and current status atomically.
            if not await identity.owner_restaurant(query.from_user.id):raise ValueError('Нет прав.')
            await query.message.answer('Отозвать подтверждение сотрудника?',reply_markup=buttons([
                [('Отозвать',f'ident:revoke:{target}'),('Отмена',f'ident:cancel:{target}')]]))
        elif action in ('approve','reject','revoke'):
            row=await identity.action(query.from_user.id,action,{'id':target})
            await query.message.edit_text(f"{html.escape(row['report_name'])}: {identity.STATUSES[row['status']]}")
    except Exception as exc:await error(query.message,exc)
