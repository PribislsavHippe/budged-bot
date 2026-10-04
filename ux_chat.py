"""Optional learn-by-doing and explicit, separately stored support messages."""
from uuid import uuid5,NAMESPACE_URL
from aiogram import Router,F,BaseMiddleware
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup,InlineKeyboardButton
from aiogram.fsm.state import State,StatesGroup
import research

router=Router()
router.message.filter(F.chat.type=='private')
router.callback_query.filter(F.message.chat.type=='private')

class Feedback(StatesGroup):
    text=State()


def help_buttons():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Сообщить о проблеме',callback_data='ux:feedback')],
        [InlineKeyboardButton(text='Попробовать на деле',callback_data='ux:learn')]])


def skip_button():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Пропустить',callback_data='ux:skip')]])


def first_action_buttons():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💸 Записать чаевые',callback_data='ux:tip')],
        [InlineKeyboardButton(text='🗓 Как добавить график',callback_data='ux:schedule')],
        [InlineKeyboardButton(text='Не сейчас',callback_data='ux:skip')]])


async def begin(message, *, uid=None, repeat=False):
    import db
    uid=uid or message.from_user.id
    user=await db.get_or_create_user(uid)
    if not repeat and user.get('tutorial_step')!='new':return False
    await db._execute(db.supabase.table('users').update({'tutorial_step':'tip','onboarded':True}).eq('id',uid))
    await research.record(uid,'onboarding_started')
    research.track(uid,'onboarding_step',step='tip')
    await message.answer('<b>Твои смены — в одном месте</b>\n\n'
                         '💸 Чаевые и расходы → сколько осталось чистыми\n'
                         '🗓 График → напоминание накануне\n'
                         '📊 Статистика → итоги по неделям и месяцам\n\n'
                         'С чего начнём? Можно сразу написать боту — это будет настоящая запись.',
                         reply_markup=first_action_buttons())
    return True


@router.callback_query(F.data=='ux:tip')
async def choose_tip(callback):
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer('Напиши свою сумму, например: <b>чай 1500</b>. '
                                  'Запись появится в статистике.',reply_markup=skip_button())


@router.callback_query(F.data=='ux:schedule')
async def choose_schedule(callback):
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer('Пришли фото графика или ссылку на открытую для просмотра Google Таблицу. '
                                  'Я помогу выбрать твою строку и покажу смены перед сохранением.\n\n'
                                  'Когда захочешь, можно записать чаевые сообщением <b>чай 1500</b>.',
                                  reply_markup=skip_button())


async def value_saved(message,entry):
    if entry.get('kind') not in {'income','expense'}:return
    uid=message.from_user.id
    event='tip_added' if entry['kind']=='income' else 'expense_added'
    operation=str(uuid5(NAMESPACE_URL,f"ux-entry:{uid}:{entry['id']}"))
    await research.record(uid,event,operation=operation)
    import db
    try:
        user=await db.get_or_create_user(uid)
        step=user.get('tutorial_step')
        if step not in {'tip','expense'}:return
        if step=='expense' and entry['kind']!='expense':return
        following='expense' if step=='tip' and entry['kind']=='income' else None
        await db._execute(db.supabase.table('users').update({'tutorial_step':following}).eq('id',uid))
    except Exception as error:
        from diagnostics import failure
        failure(error,area='onboarding',stage='advance')
        return
    if following:
        research.track(uid,'onboarding_step',step='expense')
        await message.answer('Готово, чаевые записаны.\n\nБыли расходы на смене? '
                             'Напиши, например, <b>такси 430</b> — посчитаю, сколько осталось чистыми. '
                             'Если расходов не было, пропусти.',reply_markup=skip_button())
    else:
        await message.answer('Готово! В «Статистике» уже виден результат. '
                             'Продолжай присылать записи, а за подсказками приходи в /help.',reply_markup=help_buttons())


async def schedule_saved(uid):
    """A saved schedule completes the schedule branch of first-use guidance."""
    import db
    user=await db.get_or_create_user(uid)
    if user.get('tutorial_step')=='tip':
        await db._execute(db.supabase.table('users').update({'tutorial_step':None}).eq('id',uid))


@router.callback_query(F.data=='ux:skip')
async def skip(callback):
    import db
    await db._execute(db.supabase.table('users').update({'tutorial_step':None}).eq('id',callback.from_user.id))
    await research.record(callback.from_user.id,'onboarding_skipped')
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer('Хорошо. Просто присылай записи, когда понадобятся. Подсказки всегда есть в /help.')


@router.message(Command('learn'))
async def learn_message(message,state=None):
    if state is not None:await state.clear()
    research.track(message.from_user.id,'help_opened')
    await begin(message,repeat=True)


@router.callback_query(F.data=='ux:learn')
async def learn(callback,state=None):
    if state is not None:await state.clear()
    await callback.answer()
    research.track(callback.from_user.id,'help_opened')
    await begin(callback.message,uid=callback.from_user.id,repeat=True)


async def categories(message,state):
    await state.clear()
    await message.answer('Чем помочь?',reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label,callback_data='ux:feedback:'+key)] for key,label in
        [('broken','Что-то сломалось'),('confusing','Не понимаю, как пользоваться'),('idea','Есть идея')]]))


@router.message(Command('feedback'))
async def feedback_command(message,state):
    await categories(message,state)


@router.callback_query(F.data=='ux:feedback')
async def feedback_open(callback,state):
    await callback.answer();await categories(callback.message,state)


@router.callback_query(F.data.startswith('ux:feedback:'))
async def feedback_category(callback,state):
    category=callback.data.rsplit(':',1)[-1]
    if category not in {'broken','confusing','idea'}:return
    await callback.answer()
    if not research.enabled():
        await callback.message.answer('Обратная связь пока не подключена. Попробуй чуть позже.');return
    await state.set_state(Feedback.text)
    await state.update_data(feedback_category=category,feedback_id=str(uuid5(NAMESPACE_URL,
                            f'feedback:{callback.from_user.id}:{callback.id}')))
    await callback.message.answer('Напиши одним сообщением, что случилось или что хочешь предложить. '
                                 'Его прочитает владелец бота. Суммы, пароли и скриншоты присылать не нужно.\n\n/cancel — отменить.')


@router.message(Feedback.text,F.text.startswith('/'))
async def feedback_cancel(message,state):
    from aiogram.dispatcher.event.bases import UNHANDLED
    await state.clear()
    if (message.text or '').split()[0]=='/cancel':
        await message.answer('Обращение отменено.');return
    return UNHANDLED


@router.message(Feedback.text,F.text)
async def feedback_text(message,state):
    text=(message.text or '').strip()
    if not 1<=len(text)<=2000:
        await message.answer('Пришли сообщение до 2000 символов. /cancel — отменить.');return
    from research_api import allowed
    if not allowed(message.from_user.id,'feedback',5):
        await message.answer('Слишком много обращений подряд. Попробуй через минуту.');return
    data=await state.get_data()
    try:
        import db
        await db.get_or_create_user(message.from_user.id)
        s=await research.subject(message.from_user.id)
        if not s:raise RuntimeError('subject unavailable')
        await research.execute(research.client().table('feedback').upsert(
            {'id':data['feedback_id'],'subject_id':s['id'],'category':data['feedback_category'],
             'body':text,'screen':'chat','app_version':research.version()},on_conflict='id',ignore_duplicates=True))
    except Exception as error:
        from diagnostics import failure
        failure(error,area='feedback',stage='save')
        await message.answer('Обращение пока не сохранилось. Отправь его ещё раз или нажми /cancel.');return
    await state.clear()
    research.track(message.from_user.id,'problem_reported',screen='help',operation=data['feedback_id'])
    await message.answer('Спасибо! Обращение сохранилось. Оно поможет улучшить бота.')


class ActivityMiddleware(BaseMiddleware):
    async def __call__(self,handler,event,data):
        user=getattr(event,'from_user',None)
        chat=getattr(event,'chat',None) or getattr(getattr(event,'message',None),'chat',None)
        if user and chat and chat.type=='private':research.track(user.id,'activity')
        return await handler(event,data)


@router.message(Feedback.text)
async def feedback_nontext(message):
    await message.answer('Опиши проблему текстом, без скриншотов. /cancel — отменить.')
