from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from db import get_or_create_user, get_user, update_user, get_google_token
from utils.keyboards import main_menu, settings_kb
from services.google_calendar import get_auth_url

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
        f"Привет, {name}! 👋\n\n"
        f"Я твой персональный финансовый бот. Буду помогать:\n"
        f"• 📊 Вести учёт доходов и расходов\n"
        f"• 🔔 Напоминать об обязательных платежах\n"
        f"• 📅 Создавать события в Google Calendar\n"
        f"• 💪 Следить, чтобы ты не забывал вносить данные\n\n"
        f"Начнём с настройки — когда ты получаешь зарплату?\n"
        f"Напиши день месяца (например: <b>10</b>)",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Команды:</b>\n\n"
        "/start — главное меню\n"
        "/stats — статистика\n"
        "/payments — обязательные платежи\n"
        "/budget — управление бюджетом\n"
        "/settings — настройки\n\n"
        "<b>Кнопки в меню:</b>\n"
        "💸 Добавить расход — быстро внести трату\n"
        "💰 Добавить доход — зафиксировать поступление\n"
        "📊 Статистика — сводка за неделю/месяц\n"
        "📅 Платежи — список обязательных платежей\n"
        "🎯 Бюджеты — лимиты по категориям\n"
        "⚙️ Настройки — день зарплаты, напоминания, Google Calendar",
        parse_mode="HTML"
    )


# ─── НАСТРОЙКИ ───────────────────────────────────────────

@router.message(F.text == "⚙️ Настройки")
async def settings_menu(message: Message):
    token = await get_google_token(message.from_user.id)
    user = await get_user(message.from_user.id)

    salary_day = user.get("salary_day", "не задан")
    reminder_hour = user.get("expense_reminder_hour", 21)

    await message.answer(
        f"⚙️ <b>Настройки</b>\n\n"
        f"📅 День зарплаты: <b>{salary_day}</b>\n"
        f"🔔 Час напоминаний: <b>{reminder_hour}:00</b>\n"
        f"📅 Google Calendar: <b>{'подключён ✅' if token else 'не подключён ❌'}</b>",
        parse_mode="HTML",
        reply_markup=settings_kb(has_google=bool(token))
    )


@router.callback_query(F.data == "settings:salary_day")
async def ask_salary_day(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("📅 Введи день месяца получения зарплаты (1–31):")
    await state.set_state(SettingsState.waiting_salary_day)
    await callback.answer()


@router.message(SettingsState.waiting_salary_day)
async def save_salary_day(message: Message, state: FSMContext):
    try:
        day = int(message.text.strip())
        if not 1 <= day <= 31:
            raise ValueError
        await update_user(message.from_user.id, {"salary_day": day})
        await message.answer(f"✅ Готово! День зарплаты: <b>{day}</b>-е число", parse_mode="HTML")
        await state.clear()
    except ValueError:
        await message.answer("❌ Введи число от 1 до 31")


@router.callback_query(F.data == "settings:reminder_hour")
async def ask_reminder_hour(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🔔 Введи час для ежедневных напоминаний (0–23):\nНапример: 21 = 21:00")
    await state.set_state(SettingsState.waiting_reminder_hour)
    await callback.answer()


@router.message(SettingsState.waiting_reminder_hour)
async def save_reminder_hour(message: Message, state: FSMContext):
    try:
        hour = int(message.text.strip())
        if not 0 <= hour <= 23:
            raise ValueError
        await update_user(message.from_user.id, {"expense_reminder_hour": hour})
        await message.answer(f"✅ Буду напоминать в <b>{hour}:00</b>", parse_mode="HTML")
        await state.clear()
    except ValueError:
        await message.answer("❌ Введи число от 0 до 23")


@router.callback_query(F.data == "settings:google_connect")
async def google_connect(callback: CallbackQuery):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    auth_url = get_auth_url(callback.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Подключить Google Calendar", url=auth_url)]
    ])
    await callback.message.answer(
        "Нажми кнопку ниже, чтобы подключить Google Calendar.\n"
        "После авторизации вернись в бот — он автоматически сохранит доступ.",
        reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("❌ Отменено", reply_markup=main_menu())
    await callback.answer()
