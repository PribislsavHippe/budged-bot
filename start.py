import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from db import (
    get_or_create_user, get_user, update_user,
    get_google_token, get_salary_days, set_salary_days,
    add_scheduled_payment, set_budget, delete_all_user_data,
)
from keyboards import main_menu, settings_kb, cancel_kb
from google_calendar import get_auth_url

router = Router()


class SettingsState(StatesGroup):
    waiting_salary_day = State()
    waiting_reminder_hour = State()


class OnboardingState(StatesGroup):
    step_salary       = State()   # Шаг 1: дни зарплаты
    step_payments     = State()   # Шаг 2: регулярные платежи
    step_payments_confirm = State()
    step_income       = State()   # Шаг 3: размер дохода
    step_brief        = State()   # Шаг 4: описание трат


# ─── ОНБОРДИНГ ────────────────────────────────────────────────────────────────

async def _send_onboarding_start(message, name: str):
    await message.answer(
        f"Привет, {name}.\n\n"
        f"Я твой финансовый трекер. Считаю деньги, напоминаю о платежах, "
        f"предупреждаю когда что-то идёт не так.\n\n"
        f"Четыре вопроса для старта — займёт 2 минуты.\n\n"
        f"<b>Шаг 1/4.</b> В какие числа месяца получаешь зарплату или основной доход?\n\n"
        f"Можно несколько: <i>«9 и 23»</i>, <i>«1, 15, 28»</i>",
        parse_mode="HTML",
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
    salary_days = await get_salary_days(message.from_user.id)
    if not salary_days:
        await state.set_state(OnboardingState.step_salary)
        await _send_onboarding_start(message, name)
    else:
        await message.answer(f"Снова здарова, {name}.", reply_markup=main_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current = await state.get_state()
    await state.clear()
    if current:
        await message.answer("Отменено.", reply_markup=main_menu())
    else:
        await message.answer("Нечего отменять.", reply_markup=main_menu())


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "<b>Как работать со мной</b>\n\n"
        "Просто пиши в чат что потратил или получил:\n"
        "<i>«кофе 200»</i>, <i>«зарплата 50к»</i>, <i>«такси 350»</i>\n\n"
        "Можно списком за весь день:\n"
        "<i>«кофе 200, такси 350, обед 600, продукты 2300»</i>\n\n"
        "Или задавай вопросы:\n"
        "<i>«хватит ли до зарплаты?»</i>, <i>«сколько потратил на еду?»</i>\n\n"
        "<b>/week</b> — подробный финансовый анализ на неделю и месяц\n"
        "<b>/cancel</b> — отменить текущее действие\n"
        "<b>/history</b> — последние транзакции",
        parse_mode="HTML"
    )


# ─── ШАГ 1: ДЕНЬ ЗАРПЛАТЫ ────────────────────────────────────────────────────

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
        f"<b>Шаг 2/4.</b> Расскажи про регулярные платежи — аренда, ипотека, подписки, кредиты.\n\n"
        f"Всё одним сообщением:\n"
        f"<i>«Аренда 35000 первого, Netflix 699, интернет 600 10-го»</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Нет регулярных платежей", callback_data="onboarding:skip_payments")]
        ])
    )
    await state.set_state(OnboardingState.step_payments)


@router.callback_query(F.data == "onboarding:skip_salary")
async def onboarding_skip_salary(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "<b>Шаг 2/4.</b> Регулярные платежи — аренда, ипотека, подписки, кредиты.\n\n"
        "<i>«Аренда 35000 первого, Netflix 699, интернет 600 10-го»</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Нет регулярных платежей", callback_data="onboarding:skip_payments")]
        ])
    )
    await state.set_state(OnboardingState.step_payments)
    await callback.answer()


# ─── ШАГ 2: ПЛАТЕЖИ ──────────────────────────────────────────────────────────

@router.message(OnboardingState.step_payments)
async def onboarding_payments(message: Message, state: FSMContext):
    await message.answer("Разбираю...")
    try:
        from ai_service import parse_onboarding_payments
        payments = await parse_onboarding_payments(message.text or "")
    except Exception as e:
        logging.error(f"onboarding parse payments error: {e}")
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

    await state.update_data(parsed_payments=payments)
    lines = [f"• {p['name']} — {p['amount']:,.0f} ₽, {p['day']}-е число" for p in payments]
    await message.answer(
        f"Вот что понял:\n\n{chr(10).join(lines)}\n\nВерно?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data="onboarding:payments_ok"),
                InlineKeyboardButton(text="Переписать", callback_data="onboarding:payments_retry"),
            ]
        ])
    )
    await state.set_state(OnboardingState.step_payments_confirm)


@router.callback_query(F.data == "onboarding:payments_ok", OnboardingState.step_payments_confirm)
async def onboarding_payments_confirmed(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    payments = data.get("parsed_payments", [])
    for p in payments:
        try:
            await add_scheduled_payment(
                user_id=callback.from_user.id,
                name=p["name"],
                amount=float(p["amount"]),
                day=int(p.get("day", 1))
            )
        except Exception as e:
            logging.error(f"onboarding save payment error: {e}")

    await callback.message.answer(
        f"Сохранено {len(payments)} платежей.\n\n"
        f"<b>Шаг 3/4.</b> Сколько примерно зарабатываешь в месяц?\n\n"
        f"Напиши одно число: <i>«85000»</i> или <i>«80-90к»</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="onboarding:skip_income")]
        ])
    )
    await state.set_state(OnboardingState.step_income)
    await callback.answer()


@router.callback_query(F.data == "onboarding:payments_retry", OnboardingState.step_payments_confirm)
async def onboarding_payments_retry(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Напиши ещё раз:\n<i>«Аренда 35000 первого, Netflix 699, спортзал 2500 15-го»</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="onboarding:skip_payments")]
        ])
    )
    await state.set_state(OnboardingState.step_payments)
    await callback.answer()


@router.callback_query(F.data == "onboarding:skip_payments")
async def onboarding_skip_payments(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "<b>Шаг 3/4.</b> Сколько примерно зарабатываешь в месяц?\n\n"
        "Напиши одно число: <i>«85000»</i> или <i>«80к»</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="onboarding:skip_income")]
        ])
    )
    await state.set_state(OnboardingState.step_income)
    await callback.answer()


# ─── ШАГ 3: ДОХОД ────────────────────────────────────────────────────────────

@router.message(OnboardingState.step_income)
async def onboarding_income(message: Message, state: FSMContext):
    import re
    text = (message.text or "").strip().lower()
    # Парсим "80к", "80000", "80-90к" → берём среднее
    nums = re.findall(r'(\d+(?:[.,]\d+)?)\s*[кk]?', text)
    amounts = []
    for n in nums:
        val = float(n.replace(",", "."))
        if "к" in text[text.find(n):text.find(n)+6] or "k" in text[text.find(n):text.find(n)+6]:
            val *= 1000
        if 5000 <= val <= 5_000_000:
            amounts.append(val)

    if not amounts:
        await message.answer(
            "Не разобрал. Напиши числом: <i>«85000»</i> или <i>«85к»</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Пропустить", callback_data="onboarding:skip_income")]
            ])
        )
        return

    monthly_income = sum(amounts) / len(amounts)
    await state.update_data(monthly_income=monthly_income)
    await message.answer(
        f"Понял — {monthly_income:,.0f} ₽/мес.\n\n"
        f"<b>Шаг 4/4.</b> Последний вопрос — на что обычно тратишься?\n\n"
        f"Можно в свободной форме:\n"
        f"<i>«Много трачу на еду и кафе, раз в месяц одежда, иногда развлечения»</i>\n\n"
        f"Это нужно, чтобы сразу сформировать бюджет под тебя.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить — настрою сам", callback_data="onboarding:skip_brief")]
        ])
    )
    await state.set_state(OnboardingState.step_brief)


@router.callback_query(F.data == "onboarding:skip_income")
async def onboarding_skip_income(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "<b>Шаг 4/4.</b> На что обычно тратишься?\n\n"
        "<i>«Много трачу на еду и кафе, раз в месяц одежда, иногда развлечения»</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить — настрою сам", callback_data="onboarding:skip_brief")]
        ])
    )
    await state.set_state(OnboardingState.step_brief)
    await callback.answer()


# ─── ШАГ 4: БРИФ И ФОРМИРОВАНИЕ БЮДЖЕТОВ ────────────────────────────────────

@router.message(OnboardingState.step_brief)
async def onboarding_brief(message: Message, state: FSMContext):
    description = message.text or ""
    await state.update_data(spending_description=description)
    await message.answer("Формирую бюджеты...")
    await _generate_and_confirm_budgets(message, state)


@router.callback_query(F.data == "onboarding:skip_brief")
async def onboarding_skip_brief(callback: CallbackQuery, state: FSMContext):
    await _generate_and_confirm_budgets(callback.message, state, user_id=callback.from_user.id)
    await callback.answer()


async def _generate_and_confirm_budgets(message, state: FSMContext, user_id: int = None):
    """Генерирует начальные бюджеты и показывает для подтверждения."""
    data = await state.get_data()
    monthly_income = data.get("monthly_income", 0)
    description = data.get("spending_description", "")
    uid = user_id or message.chat.id

    budgets = {}
    if monthly_income > 0:
        try:
            from db import get_scheduled_payments
            payments = await get_scheduled_payments(uid)
            from weekly_advice import generate_initial_budgets
            budgets = await generate_initial_budgets(monthly_income, payments, description)
        except Exception as e:
            logging.error(f"generate_initial_budgets error: {e}")

    if not budgets:
        # Нет дохода или ошибка — сразу финишируем
        await _onboarding_finish(message, state, budgets={})
        return

    await state.update_data(generated_budgets=budgets)

    lines = [f"• {cat}: {amount:,.0f} ₽/мес" for cat, amount in budgets.items()]
    await message.answer(
        f"<b>Вот начальные бюджеты на основе твоего дохода:</b>\n\n"
        + "\n".join(lines)
        + "\n\nСохранить или настроить вручную?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Сохранить", callback_data="onboarding:budgets_ok"),
                InlineKeyboardButton(text="Настрою сам", callback_data="onboarding:budgets_skip"),
            ]
        ])
    )


@router.callback_query(F.data == "onboarding:budgets_ok")
async def onboarding_budgets_confirmed(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    budgets = data.get("generated_budgets", {})
    saved = 0
    for cat, amount in budgets.items():
        try:
            await set_budget(callback.from_user.id, cat, amount)
            saved += 1
        except Exception as e:
            logging.error(f"save budget error: {e}")
    await _onboarding_finish(callback.message, state, budgets=budgets, user_id=callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == "onboarding:budgets_skip")
async def onboarding_budgets_skip(callback: CallbackQuery, state: FSMContext):
    await _onboarding_finish(callback.message, state, budgets=None, user_id=callback.from_user.id)
    await callback.answer()


async def _onboarding_finish(message, state: FSMContext, budgets: dict = None, user_id: int = None):
    uid = user_id or message.chat.id
    user = await get_user(uid)
    name = (user.get("first_name") or "друг") if user else "друг"
    salary_days = await get_salary_days(uid)

    parts = []
    if salary_days:
        parts.append(f"Дни зарплаты: {', '.join(map(str, salary_days))}-е числа")
    if budgets:
        parts.append(f"Бюджеты: {len(budgets)} категорий")

    setup_str = (", ".join(parts) + " — готово.") if parts else ""

    budgets_hint = ""
    if budgets:
        budgets_hint = "\nБюджеты можно скорректировать через кнопку Бюджеты в меню."

    await message.answer(
        f"<b>Всё настроено.</b> {setup_str}\n\n"
        f"Теперь просто пиши в чат что потратил или получил:\n"
        f"<i>«кофе 200»</i>, <i>«зарплата 50к»</i>, <i>«такси 350»</i>\n\n"
        f"Или список за день:\n"
        f"<i>«кофе 200, такси 350, обед 600»</i>\n\n"
        f"Или спрашивай: <i>«хватит ли до зарплаты?»</i>\n"
        f"/week — подробный анализ бюджета."
        f"{budgets_hint}\n\n"
        f"Погнали.",
        parse_mode="HTML",
        reply_markup=main_menu()
    )
    await state.clear()


# ─── НАСТРОЙКИ ────────────────────────────────────────────────────────────────

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


@router.callback_query(F.data == "settings:restart_onboarding")
async def restart_onboarding(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(OnboardingState.step_salary)
    user = await get_user(callback.from_user.id)
    name = (user.get("first_name") or "друг") if user else "друг"
    await callback.message.answer(
        "Перезапускаем настройку.\n\n"
        "<b>Шаг 1/4.</b> В какие числа получаешь зарплату?\n\n"
        "<i>«9 и 23»</i>, <i>«1, 15, 28»</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="onboarding:skip_salary")]
        ])
    )
    await callback.answer()


@router.callback_query(F.data == "settings:salary_day")
async def ask_salary_day(callback: CallbackQuery, state: FSMContext):
    current = await get_salary_days(callback.from_user.id)
    current_str = ", ".join(str(d) for d in current) if current else "не задан"
    await callback.message.answer(
        f"Сейчас: <b>{current_str}</b>.\n\nНапиши дни через запятую: <i>9, 18, 23</i>",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )
    await state.set_state(SettingsState.waiting_salary_day)
    await callback.answer()


@router.message(SettingsState.waiting_salary_day)
async def save_salary_day(message: Message, state: FSMContext):
    from categorizer import parse_salary_days
    days = parse_salary_days(message.text)
    if not days:
        await message.answer("Не разобрал. Числа от 1 до 31, например: 9, 18, 23.", reply_markup=cancel_kb())
        return
    await set_salary_days(message.from_user.id, days)
    days_str = ", ".join(str(d) for d in days)
    await message.answer(f"Готово. Дни зарплаты: <b>{days_str}</b>", parse_mode="HTML", reply_markup=main_menu())
    await state.clear()


@router.callback_query(F.data == "settings:reminder_hour")
async def ask_reminder_hour(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("В какой час напоминать? Число от 0 до 23 (21 = 21:00).", reply_markup=cancel_kb())
    await state.set_state(SettingsState.waiting_reminder_hour)
    await callback.answer()


@router.message(SettingsState.waiting_reminder_hour)
async def save_reminder_hour(message: Message, state: FSMContext):
    try:
        hour = int(message.text.strip())
        if not 0 <= hour <= 23:
            raise ValueError
        await update_user(message.from_user.id, {"expense_reminder_hour": hour})
        await message.answer(f"Буду писать в <b>{hour}:00</b>.", parse_mode="HTML", reply_markup=main_menu())
        await state.clear()
    except ValueError:
        await message.answer("Число от 0 до 23.", reply_markup=cancel_kb())


@router.callback_query(F.data == "settings:google_connect")
async def google_connect(callback: CallbackQuery):
    auth_url = get_auth_url(callback.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подключить Google Calendar", url=auth_url)]
    ])
    await callback.message.answer(
        "Жми — откроется Google. Войди, разреши доступ, вернись сюда.",
        reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data == "settings:google_info")
async def google_info(callback: CallbackQuery):
    await callback.answer(
        "Google Calendar подключён. Платежи и зарплатные дни добавляются автоматически.",
        show_alert=True
    )


@router.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Отменено.", reply_markup=main_menu())
    await callback.answer()


# ─── СБРОС ВСЕХ ДАННЫХ ────────────────────────────────────────────────────────

@router.callback_query(F.data == "settings:reset_all_data")
async def reset_all_data_confirm(callback: CallbackQuery):
    await callback.message.answer(
        "<b>Удалить ВСЕ мои данные?</b>\n\n"
        "Будет удалено:\n"
        "— Все транзакции и история\n"
        "— Все регулярные платежи\n"
        "— Бюджеты и лимиты\n"
        "— Цели накопления\n"
        "— Планируемые записи\n"
        "— Настройки дней зарплаты\n\n"
        "Это действие необратимо.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, удалить всё", callback_data="confirm_reset_all"),
                InlineKeyboardButton(text="Отмена",          callback_data="cancel"),
            ]
        ])
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_reset_all")
async def reset_all_data_execute(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    wait_msg = await callback.message.answer("Удаляю данные...")
    try:
        counts = await delete_all_user_data(callback.from_user.id)
        total = sum(v for v in counts.values() if v > 0)
        await wait_msg.delete()
        await callback.message.answer(
            f"<b>Готово. Всё удалено.</b> Записей: {total}\n\nТеперь ты — новый пользователь.",
            parse_mode="HTML",
        )
        # Запускаем онбординг
        user = await get_user(callback.from_user.id)
        name = (user.get("first_name") or "друг") if user else "друг"
        await state.set_state(OnboardingState.step_salary)
        await _send_onboarding_start(callback.message, name)
    except Exception as e:
        logging.error(f"reset_all_data error: {e}")
        await wait_msg.delete()
        await callback.message.answer(
            f"Что-то пошло не так: {str(e)}\nПопробуй ещё раз.",
            reply_markup=main_menu()
        )
    await callback.answer()
