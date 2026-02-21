import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from db import (
    get_or_create_user, get_user, update_user,
    get_google_token, get_salary_days, set_salary_days,
    add_scheduled_payment
)
from keyboards import main_menu, settings_kb
from google_calendar import get_auth_url

router = Router()


class SettingsState(StatesGroup):
    waiting_salary_day = State()
    waiting_reminder_hour = State()


# ─── ОНБОРДИНГ ───────────────────────────────────────────────────────────────

class OnboardingState(StatesGroup):
    step_salary = State()        # Спрашиваем день(и) зарплаты
    step_payments = State()      # Спрашиваем обязательные платежи
    step_payments_confirm = State()  # Подтверждение распознанных платежей


async def _send_onboarding_start(message: Message, name: str):
    await message.answer(
        f"Привет, {name}.\n\n"
        f"Я твой финансовый трекер. Считаю деньги, напоминаю о платежах, предупреждаю когда "
        f"что-то идёт не так — и делаю это с характером.\n\n"
        f"Три вопроса для старта — это займёт минуту.\n\n"
        f"<b>Шаг 1/3.</b> В какие числа месяца получаешь зарплату или основной доход?\n\n"
        f"Можно несколько: <i>«9 и 23»</i>, <i>«1, 15, 28»</i> — как удобно.",
        parse_mode="HTML",
    )
    await message.bot.send_message(
        message.chat.id,
        "Или нажми «Пропустить» — настроишь позже в Настройках.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="onboarding:skip_salary")]
        ])
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )
    name = user.get("first_name") or "друг"

    # Проверяем: новый пользователь или нет (нет дней зарплаты = новый)
    salary_days = await get_salary_days(message.from_user.id)
    if not salary_days:
        # Новый пользователь — запускаем онбординг
        await state.set_state(OnboardingState.step_salary)
        await _send_onboarding_start(message, name)
    else:
        # Уже настроен
        await message.answer(
            f"Снова здарова, {name}. Я тут, считаю твои деньги.",
            reply_markup=main_menu()
        )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "<b>Как работать со мной</b>\n\n"
        "Просто пиши в чат что потратил или получил:\n"
        "<i>«кофе 200»</i>, <i>«получил зарплату 50к»</i>, <i>«такси 350»</i>\n\n"
        "Или задавай вопросы:\n"
        "<i>«сколько потратил на еду?»</i>, <i>«хватит ли до зарплаты?»</i>\n\n"
        "<b>Кнопки:</b>\n"
        "Статистика — сводка за период\n"
        "Платежи — регулярные расходы по расписанию\n"
        "Бюджеты — лимиты по категориям\n"
        "Настройки — зарплата, напоминания, Google Calendar\n"
        "Доходы — ожидаемые поступления по датам\n"
        "Цели — накопить сумму за N месяцев\n"
        "История — последние записи",
        parse_mode="HTML"
    )


# ─── ОНБОРДИНГ: ШАГ 1 — ДЕНЬ ЗАРПЛАТЫ ────────────────────────────────────────

@router.message(OnboardingState.step_salary)
async def onboarding_salary(message: Message, state: FSMContext):
    from categorizer import parse_salary_days
    days = parse_salary_days(message.text or "")
    if not days:
        await message.answer(
            "Не разобрал. Напиши числа, например: <i>«9 и 23»</i> или <i>«1, 15»</i>",
            parse_mode="HTML"
        )
        return
    await set_salary_days(message.from_user.id, days)
    days_str = ", ".join(str(d) for d in days)
    await message.answer(
        f"Принято — {days_str}-е числа.\n\n"
        f"<b>Шаг 2/3.</b> Теперь расскажи про регулярные платежи — аренда, ипотека, подписки, кредиты.\n\n"
        f"Можно всё одним сообщением, как удобно:\n"
        f"<i>«Аренда 35000 первого числа, Netflix 699, интернет 600 10-го»</i>\n\n"
        f"Разберу сам.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="У меня нет регулярных платежей", callback_data="onboarding:skip_payments")]
        ])
    )
    await state.set_state(OnboardingState.step_payments)


@router.callback_query(F.data == "onboarding:skip_salary")
async def onboarding_skip_salary(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "<b>Шаг 2/3.</b> Расскажи про регулярные платежи — аренда, ипотека, подписки, кредиты.\n\n"
        "Можно всё одним сообщением:\n"
        "<i>«Аренда 35000 первого числа, Netflix 699, интернет 600 10-го»</i>\n\n"
        "Разберу сам.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="У меня нет регулярных платежей", callback_data="onboarding:skip_payments")]
        ])
    )
    await state.set_state(OnboardingState.step_payments)
    await callback.answer()


@router.message(OnboardingState.step_payments)
async def onboarding_payments(message: Message, state: FSMContext):
    text = message.text or ""
    await message.answer("Разбираю...")

    try:
        from ai_service import parse_onboarding_payments
        payments = await parse_onboarding_payments(text)
    except Exception as e:
        logging.error(f"onboarding parse error: {e}")
        payments = []

    if not payments:
        await message.answer(
            "Не смог разобрать. Попробуй иначе:\n"
            "<i>«Аренда 35000, Netflix 699, интернет 600»</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Пропустить", callback_data="onboarding:skip_payments")]
            ])
        )
        return

    # Показываем что распознали
    await state.update_data(parsed_payments=payments)
    lines = [f"• {p['name']} — {p['amount']:,.0f} ₽, {p['day']}-е число" for p in payments]
    text_confirm = "\n".join(lines)

    await message.answer(
        f"Вот что разобрал:\n\n{text_confirm}\n\n"
        f"Всё верно?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, сохранить", callback_data="onboarding:payments_ok"),
                InlineKeyboardButton(text="Не всё верно", callback_data="onboarding:payments_retry"),
            ]
        ])
    )
    await state.set_state(OnboardingState.step_payments_confirm)


@router.callback_query(F.data == "onboarding:payments_ok", OnboardingState.step_payments_confirm)
async def onboarding_payments_confirmed(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    payments = data.get("parsed_payments", [])
    saved = 0
    for p in payments:
        try:
            await add_scheduled_payment(
                user_id=callback.from_user.id,
                name=p["name"],
                amount=float(p["amount"]),
                day=int(p.get("day", 1))
            )
            saved += 1
        except Exception as e:
            logging.error(f"onboarding save payment error: {e}")

    await _onboarding_finish(callback.message, state, saved_payments=saved)
    await callback.answer()


@router.callback_query(F.data == "onboarding:payments_retry", OnboardingState.step_payments_confirm)
async def onboarding_payments_retry(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Напиши ещё раз — постараюсь разобрать лучше.\n\n"
        "<i>«Аренда 35000 первого, Netflix 699, спортзал 2500 15-го»</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="onboarding:skip_payments")]
        ])
    )
    await state.set_state(OnboardingState.step_payments)
    await callback.answer()


@router.callback_query(F.data == "onboarding:skip_payments")
async def onboarding_skip_payments(callback: CallbackQuery, state: FSMContext):
    await _onboarding_finish(callback.message, state, saved_payments=0)
    await callback.answer()


async def _onboarding_finish(message: Message, state: FSMContext, saved_payments: int = 0):
    """Завершение онбординга."""
    user = await get_user(message.chat.id)
    name = user.get("first_name") or "друг" if user else "друг"
    salary_days = await get_salary_days(message.chat.id)

    parts = []
    if salary_days:
        parts.append(f"дни зарплаты: {', '.join(map(str, salary_days))}-е числа")
    if saved_payments:
        parts.append(f"платежи: {saved_payments} шт.")

    setup_str = (", ".join(parts) + " — готово.") if parts else ""

    await message.answer(
        f"<b>Всё, настроено.</b> {setup_str}\n\n"
        f"Теперь просто пиши в чат что потратил или получил:\n"
        f"<i>«кофе 200»</i>, <i>«получил зарплату 50к»</i>, <i>«такси 350»</i>\n\n"
        f"Или спрашивай: <i>«хватит ли до зарплаты?»</i>, <i>«сколько потратил на еду?»</i>\n\n"
        f"Погнали.",
        parse_mode="HTML",
        reply_markup=main_menu()
    )
    await state.clear()


# ─── НАСТРОЙКИ ───────────────────────────────────────────────────────────────

@router.message(F.text == "Настройки")
async def settings_menu(message: Message):
    token = await get_google_token(message.from_user.id)
    user = await get_user(message.from_user.id)
    salary_days = await get_salary_days(message.from_user.id)
    salary_str = ", ".join(str(d) for d in salary_days) if salary_days else "не задан"
    reminder_hour = user.get("expense_reminder_hour", 21) if user else 21

    await message.answer(
        f"<b>Настройки</b>\n\n"
        f"Дни зарплаты: <b>{salary_str}</b>\n"
        f"Напоминания: <b>{reminder_hour}:00</b>\n"
        f"Google Calendar: <b>{'подключён' if token else 'не подключён'}</b>",
        parse_mode="HTML",
        reply_markup=settings_kb(has_google=bool(token))
    )


@router.callback_query(F.data == "settings:salary_day")
async def ask_salary_day(callback: CallbackQuery, state: FSMContext):
    current = await get_salary_days(callback.from_user.id)
    current_str = ", ".join(str(d) for d in current) if current else "не задан"
    await callback.message.answer(
        f"Сейчас: <b>{current_str}</b>.\n\n"
        f"Напиши дни через запятую: <i>9, 18, 23</i> или <i>1 и 15</i>",
        parse_mode="HTML"
    )
    await state.set_state(SettingsState.waiting_salary_day)
    await callback.answer()


@router.message(SettingsState.waiting_salary_day)
async def save_salary_day(message: Message, state: FSMContext):
    from categorizer import parse_salary_days
    days = parse_salary_days(message.text)
    if not days:
        await message.answer("Не разобрал. Числа от 1 до 31, например: 9, 18, 23.")
        return
    await set_salary_days(message.from_user.id, days)
    days_str = ", ".join(str(d) for d in days)
    await message.answer(
        f"Готово. Дни зарплаты: <b>{days_str}</b>",
        parse_mode="HTML"
    )
    await state.clear()


@router.callback_query(F.data == "settings:reminder_hour")
async def ask_reminder_hour(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("В какой час напоминать? Число от 0 до 23 (например: 21 = 21:00).")
    await state.set_state(SettingsState.waiting_reminder_hour)
    await callback.answer()


@router.message(SettingsState.waiting_reminder_hour)
async def save_reminder_hour(message: Message, state: FSMContext):
    try:
        hour = int(message.text.strip())
        if not 0 <= hour <= 23:
            raise ValueError
        await update_user(message.from_user.id, {"expense_reminder_hour": hour})
        await message.answer(f"Буду писать в <b>{hour}:00</b>.", parse_mode="HTML")
        await state.clear()
    except ValueError:
        await message.answer("Число от 0 до 23, без фокусов.")


@router.callback_query(F.data == "settings:google_connect")
async def google_connect(callback: CallbackQuery):
    auth_url = get_auth_url(callback.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подключить Google Calendar", url=auth_url)]
    ])
    await callback.message.answer(
        "Жми — откроется Google. Войди, разреши доступ, вернись сюда. Сохраню всё сам.",
        reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data == "settings:google_info")
async def google_info(callback: CallbackQuery):
    await callback.answer("Google Calendar подключён. Платежи и зарплатные дни добавляются в календарь автоматически.", show_alert=True)


@router.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Отменено.", reply_markup=main_menu())
    await callback.answer()
