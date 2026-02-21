from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from db import get_or_create_user, get_user, update_user, get_google_token, get_salary_days, set_salary_days
from keyboards import main_menu, settings_kb
from google_calendar import get_auth_url

router = Router()


class SettingsState(StatesGroup):
    waiting_salary_day = State()
    waiting_reminder_hour = State()


# ─── СТАРТ ───────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message):
    user = await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )

    name = user.get("first_name") or "друг"
    await message.answer(
        f"Привет, {name}.\n\n"
        f"Я тут буду считать твои деньги — учёт расходов и доходов, напоминания о платежах, календарь. "
        f"И да, буду ненавязчиво пилить, если забудешь что-то внести. Так что давай сразу договоримся: когда у тебя зарплата?\n\n"
        f"Напиши день месяца (например: <b>10</b>)",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "<b>Команды:</b>\n\n"
        "/start — главное меню\n"
        "/stats — статистика\n"
        "/payments — обязательные платежи\n"
        "/budget — управление бюджетом\n"
        "/settings — настройки\n\n"
        "<b>Кнопки:</b>\n"
        "Статистика — сводка за период\n"
        "Платежи — что надо отдать по расписанию\n"
        "Бюджеты — лимиты, которые так приятно превышать\n"
        "Настройки — зарплата, напоминания, календарь",
        parse_mode="HTML"
    )


# ─── НАСТРОЙКИ ───────────────────────────────────────────

@router.message(F.text == "Настройки")
async def settings_menu(message: Message):
    token = await get_google_token(message.from_user.id)
    user = await get_user(message.from_user.id)

    salary_days = await get_salary_days(message.from_user.id)
    salary_str = ", ".join(str(d) for d in salary_days) if salary_days else "не задан"
    reminder_hour = user.get("expense_reminder_hour", 21)

    await message.answer(
        f"<b>Настройки</b>\n\n"
        f"Дни зарплаты: <b>{salary_str}</b>\n"
        f"Час напоминаний: <b>{reminder_hour}:00</b>\n"
        f"Google Calendar: <b>{'подключён' if token else 'не подключён'}</b>",
        parse_mode="HTML",
        reply_markup=settings_kb(has_google=bool(token))
    )


@router.callback_query(F.data == "settings:salary_day")
async def ask_salary_day(callback: CallbackQuery, state: FSMContext):
    current = await get_salary_days(callback.from_user.id)
    current_str = ", ".join(str(d) for d in current) if current else "не задан"
    await callback.message.answer(
        f"Сейчас в базе: <b>{current_str}</b>.\n\n"
        f"Введи дни через запятую или пробел. Например: <i>9, 18, 23, 28</i> или <i>1 и 15</i>",
        parse_mode="HTML"
    )
    await state.set_state(SettingsState.waiting_salary_day)
    await callback.answer()


@router.message(SettingsState.waiting_salary_day)
async def save_salary_day(message: Message, state: FSMContext):
    from categorizer import parse_salary_days
    days = parse_salary_days(message.text)
    if not days:
        await message.answer("Ни одного нормального дня не разглядел. Числа от 1 до 31, например: 9, 18, 23.")
        return
    await set_salary_days(message.from_user.id, days)
    days_str = ", ".join(str(d) for d in days)
    await message.answer(
        f"Принято. Дни зарплаты: <b>{days_str}</b> — в эти даты буду напоминать внести доход.",
        parse_mode="HTML"
    )
    await state.clear()


@router.callback_query(F.data == "settings:reminder_hour")
async def ask_reminder_hour(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("В какой час тебя дёргать? Напиши число от 0 до 23 (например: 21 = 21:00).")
    await state.set_state(SettingsState.waiting_reminder_hour)
    await callback.answer()


@router.message(SettingsState.waiting_reminder_hour)
async def save_reminder_hour(message: Message, state: FSMContext):
    try:
        hour = int(message.text.strip())
        if not 0 <= hour <= 23:
            raise ValueError
        await update_user(message.from_user.id, {"expense_reminder_hour": hour})
        await message.answer(f"Договорились. Буду писать в <b>{hour}:00</b>.", parse_mode="HTML")
        await state.clear()
    except ValueError:
        await message.answer("Так не пойдёт. Нужно число от 0 до 23.")


@router.callback_query(F.data == "settings:google_connect")
async def google_connect(callback: CallbackQuery):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    auth_url = get_auth_url(callback.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подключить Google Calendar", url=auth_url)]
    ])
    await callback.message.answer(
        "Жми на кнопку — откроется Google. Войди, разреши доступ, вернись сюда. Я всё сохраню сам.",
        reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Отменено. Как скажешь.", reply_markup=main_menu())
    await callback.answer()
