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

SUPPORT_URL='https://t.me/lechatsergeev'

FAQ={
    'tips':('Как записать чаевые или расход?',
            'Пришли сумму: <b>чай 1500</b>. Расход укажи с названием: <b>такси 430</b>. '
            'Бот покажет, сколько осталось чистыми.'),
    'edit':('Как исправить запись?',
            'Открой «Статистику» → «История и правки». Если запись относится к смене, '
            'выбери её день в календаре.'),
    'schedule':('Где мой график?',
            'График смен составляет менеджер на сайте ресторана. Когда он опубликует график, я пришлю твои смены сообщением, а весь график будет в «Статистике» → «График». Если такой вкладки нет, менеджер ещё не подтвердил тебя или не опубликовал график.'),
    'wish':('Как передать пожелание?',
            'Нажми «Оставить пожелание» и напиши его одним сообщением. Его прочитает Леша. '
            'Если хочешь выходные в определённые дни недели, просто напиши мне, например: '
            '<b>выходной по вторникам</b>. Программа постарается это учесть.'),
}

class Feedback(StatesGroup):
    text=State()


def help_buttons():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Оставить пожелание',callback_data='ux:wish')],
        [InlineKeyboardButton(text='Частые вопросы',callback_data='ux:faq')],
        [InlineKeyboardButton(text='Написать Леше',url=SUPPORT_URL)]])


def faq_buttons():
    return InlineKeyboardMarkup(inline_keyboard=[
        *[[InlineKeyboardButton(text=title,callback_data=f'ux:faq:{key}')]
          for key,(title,_) in FAQ.items()],
        [InlineKeyboardButton(text='Оставить пожелание',callback_data='ux:wish')],
        [InlineKeyboardButton(text='Написать Леше',url=SUPPORT_URL)]])


def skip_button():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Пропустить',callback_data='ux:skip')]])

def feedback_cancel_button():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Отмена',callback_data='ux:feedback_cancel')]])


def first_action_buttons():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💸 Записать чаевые',callback_data='ux:tip')],
        [InlineKeyboardButton(text='🗓 Где мой график',callback_data='ux:schedule')],
        [InlineKeyboardButton(text='Не сейчас',callback_data='ux:skip')]])


async def begin(message, *, uid=None, repeat=False, prompt=True):
    import db
    uid=uid or message.from_user.id
    user=await db.get_or_create_user(uid)
    if not repeat and user.get('tutorial_step')!='new':return False
    await db._execute(db.supabase.table('users').update({'tutorial_step':'tip','onboarded':True}).eq('id',uid))
    await research.record(uid,'onboarding_started')
    research.track(uid,'onboarding_step',step='tip')
    if prompt:
        await message.answer('Что хочешь сделать сейчас? Выбери вариант или просто пришли запись — '
                             'она сразу сохранится.',reply_markup=first_action_buttons())
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
    await callback.message.answer('График смен составляет менеджер на сайте ресторана. Когда он опубликует график, я пришлю твои смены сообщением, а весь график будет в «Статистике» → «График». Если такой вкладки нет, менеджер ещё не подтвердил тебя или не опубликовал график.\n\n'
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
                             'Продолжай присылать записи. Подсказки — на кнопке ниже.',reply_markup=help_buttons())


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
    await callback.message.answer('Хорошо. Просто присылай записи, когда понадобятся. Если нужна подсказка, нажми кнопку ниже.',reply_markup=help_buttons())


@router.callback_query(F.data=='ux:help')
async def help_button(callback):
    await callback.answer()
    from handlers import send_help
    await send_help(callback.message,callback.from_user.id)


@router.callback_query(F.data=='ux:faq')
async def faq_open(callback):
    await callback.answer()
    await callback.message.answer('Что хочешь узнать?',reply_markup=faq_buttons())


@router.callback_query(F.data.startswith('ux:faq:'))
async def faq_answer(callback):
    key=callback.data.rsplit(':',1)[-1]
    if key not in FAQ:
        await callback.answer('Этот ответ не найден.');return
    await callback.answer()
    title,answer=FAQ[key]
    await callback.message.answer(f'<b>{title}</b>\n\n{answer}',reply_markup=faq_buttons())


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
        [('idea','Есть идея или пожелание'),('broken','Что-то сломалось'),('confusing','Не понимаю, как пользоваться')]]))


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
                                 'Его прочитает владелец бота. Суммы, пароли и скриншоты присылать не нужно.',
                                  reply_markup=feedback_cancel_button())

@router.callback_query(F.data=='ux:feedback_cancel')
async def feedback_cancel_button_pressed(callback,state):
    if await state.get_state()!=Feedback.text.state:
        await callback.answer('Этот вопрос уже закрыт.');return
    await state.clear();await callback.answer()
    await callback.message.edit_text('Обращение отменено.')


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
        await message.answer('Пришли сообщение до 2000 символов.',reply_markup=feedback_cancel_button());return
    from research_api import allowed
    if not allowed(message.from_user.id,'feedback',5):
        await message.answer('Слишком много обращений подряд. Попробуй через минуту.');return
    data=await state.get_data()
    if not await save_feedback(message.from_user.id,data['feedback_category'],text,data['feedback_id']):
        await message.answer('Обращение пока не сохранилось. Отправь его ещё раз.',reply_markup=feedback_cancel_button());return
    await state.clear()
    await message.answer('Спасибо! Сохранил. Леша прочитает.')


async def save_feedback(uid,category,text,feedback_id):
    """Обращение или пожелание в таблицу feedback. Его читает только владелец бота."""
    try:
        import db
        await db.get_or_create_user(uid)
        s=await research.subject(uid)
        if not s:raise RuntimeError('subject unavailable')
        await research.execute(research.client().table('feedback').upsert(
            {'id':feedback_id,'subject_id':s['id'],'category':category,
             'body':text,'screen':'chat','app_version':research.version()},on_conflict='id',ignore_duplicates=True))
    except Exception as error:
        from diagnostics import failure
        failure(error,area='feedback',stage='save')
        return False
    research.track(uid,'problem_reported',screen='help',operation=feedback_id)
    return True


def contact_button():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Написать Леше',url=SUPPORT_URL)]])


async def ask_wish(message,state,uid=None):
    """Пожелание одним сообщением, без выбора категории."""
    uid=uid or message.from_user.id
    await state.clear()
    if not research.enabled():
        await message.answer('Пожелания пока принимает Леша напрямую — напиши ему.',reply_markup=contact_button());return
    await state.set_state(Feedback.text)
    await state.update_data(feedback_category='idea',feedback_id=str(uuid5(NAMESPACE_URL,f'wish:{uid}:{message.message_id}')))
    await message.answer('Напиши пожелание одним сообщением: что удобно, чего не хватает, что мешает. '
                         'Его прочитает Леша.',reply_markup=feedback_cancel_button())


@router.callback_query(F.data=='ux:wish')
async def wish_open(callback,state):
    await callback.answer()
    await ask_wish(callback.message,state,uid=callback.from_user.id)


def looks_like_wish(text):
    """Обычная фраза, а не запись денег: несколько слов из букв."""
    import re
    return len(text.strip())>=12 and len(re.findall(r'[а-яёa-z]{2,}',text.lower()))>=3


async def offer_wish(message,state):
    """Фраза не похожа на запись: не теряем её, а спрашиваем, передать ли как пожелание."""
    await state.update_data(wish_text=(message.text or '').strip()[:2000],wish_message=message.message_id)
    await message.answer('Это не похоже на запись чаевых или расхода, поэтому ничего не записал. '
                         'Передать это сообщение Леше как пожелание?',
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                             InlineKeyboardButton(text='Да, передать',callback_data='ux:wishsend'),
                             InlineKeyboardButton(text='Нет',callback_data='ux:wishdrop')]]))


async def _forget_wish(state):
    data=await state.get_data()
    data.pop('wish_text',None);data.pop('wish_message',None)
    await state.set_data(data)
    return data


@router.callback_query(F.data=='ux:wishsend')
async def wish_send(callback,state):
    data=await state.get_data()
    text=data.get('wish_text')
    if not text:
        await callback.answer('Это сообщение уже не найти. Нажми «Оставить пожелание» и напиши ещё раз.',show_alert=True);return
    await callback.answer()
    uid=callback.from_user.id
    saved=research.enabled() and await save_feedback(uid,'idea',text,str(uuid5(NAMESPACE_URL,f"wish:{uid}:{data.get('wish_message')}")))
    if not saved:
        await callback.message.edit_text('Пожелание не сохранилось в боте. Напиши его Леше напрямую.',reply_markup=contact_button());return
    await _forget_wish(state)
    await callback.message.edit_text('Спасибо! Передал Леше.')


@router.callback_query(F.data=='ux:wishdrop')
async def wish_drop(callback,state):
    await _forget_wish(state)
    await callback.answer()
    await callback.message.edit_text('Хорошо, не передаю.')


class ActivityMiddleware(BaseMiddleware):
    async def __call__(self,handler,event,data):
        user=getattr(event,'from_user',None)
        chat=getattr(event,'chat',None) or getattr(getattr(event,'message',None),'chat',None)
        if user and chat and chat.type=='private':research.track(user.id,'activity')
        return await handler(event,data)


@router.message(Feedback.text)
async def feedback_nontext(message):
    await message.answer('Опиши проблему текстом, без скриншотов.',reply_markup=feedback_cancel_button())
