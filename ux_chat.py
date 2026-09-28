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


async def begin(message):
    uid=message.from_user.id
    s=await research.subject(uid)
    if not s or s['cohort']!='new' or s['onboarding_state']!='not_started':return False
    result=await research.record(uid,'onboarding_started')
    if not result:return False
    research.track(uid,'onboarding_step',step='tip')
    await message.answer('Здесь можно считать, сколько ты реально зарабатываешь на сменах.\n\n'
                         'Запишем первые чаевые? Отправь, например: <b>чай 1500</b>. '
                         'Укажи свою сумму — это будет настоящая запись.',reply_markup=skip_button())
    return True


async def value_saved(message,entry):
    """After successful ordinary persistence; no amount or text crosses telemetry boundary."""
    if entry.get('kind') not in {'income','expense'}:return
    event='tip_added' if entry['kind']=='income' else 'expense_added'
    operation=str(uuid5(NAMESPACE_URL,f"ux-entry:{message.from_user.id}:{entry['id']}"))
    # Optional state transition, uses short-timeout client, catches all backend errors.
    result=await research.record(message.from_user.id,event,operation=operation)
    if result.get('was_learning') and result.get('first_value'):
        if entry['kind']=='income':
            research.track(message.from_user.id,'onboarding_step',step='expense')
            await message.answer('Готово! Теперь можно вычесть расход: например, <b>такси 430</b>. '
                                 'Записывай только настоящие траты.\n\n'
                                 'Итог — в «Статистике». Если нужна подсказка, открой /help.',reply_markup=help_buttons())
        else:
            await message.answer('Первая запись готова! Чаевые можно добавить так: <b>чай 1500</b>. '
                                 'Итог — в «Статистике», подсказки — /help.')


@router.callback_query(F.data=='ux:skip')
async def skip(callback):
    await research.record(callback.from_user.id,'onboarding_skipped')
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer('Хорошо. Просто присылай записи, когда понадобятся. Подсказки всегда есть в /help.')


@router.message(Command('learn'))
async def learn_message(message):
    research.track(message.from_user.id,'help_opened')
    await message.answer('Попробуй записать настоящие чаевые: <b>чай 1500</b>, со своей суммой. '
                         'Для расхода — <b>такси 430</b>. Ошибочную запись можно отменить кнопкой под ней.')


@router.callback_query(F.data=='ux:learn')
async def learn(callback):
    await callback.answer()
    research.track(callback.from_user.id,'help_opened')
    await callback.message.answer('Запиши свои чаевые сообщением: <b>чай 1500</b>. '
                                 'Это настоящая запись, поэтому укажи свою сумму. '
                                 'Для расхода — <b>такси 430</b>. Отменить запись можно кнопкой под ней.')


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
