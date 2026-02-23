import logging
import re
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from db import (
    get_or_create_user, get_user, update_user,
    get_google_token, get_salary_days, set_salary_days,
    add_scheduled_payment, set_budget, delete_all_user_data,
    set_current_balance, get_current_balance,
)
from keyboards import main_menu, settings_kb, cancel_kb
from google_calendar import get_auth_url

router = Router()


class SettingsState(StatesGroup):
    waiting_salary_day    = State()
    waiting_reminder_hour = State()


class OnboardingState(StatesGroup):
    step_combined         = State()   # Шаг 1: зарплата + дни + баланс (объединённый)
    step_payments         = State()   # Шаг 2: регулярные платежи
    step_payments_confirm = State()
    step_brief            = State()   # Шаг 3: описание трат
    # Compat
    step_salary_amount    = State()
    step_salary_days      = State()
    step_income           = State()


# ─── ПАРСИНГ ОБЪЕДИНЁННОГО ШАГА 1 ────────────────────────────────────────────

def _parse_combined_step(text: str) -> dict:
    """
    Разбирает: дни зарплаты + текущий баланс + планируемый доход + средний доход.
    «9-го и 23-го, на счету 12000, планирую 40000, средний 55000»
    «получаю в среднем 60к, сейчас 5000 в кармане, жду 30000 в этом месяце»
    """
    from categorizer import parse_salary_days
    result = {"days": [], "planned_income": None, "avg_income": None, "balance": None}
    lower = text.lower()

    def _normalize_k(s: str) -> str:
        return re.sub(
            r'(\d+(?:[.,]\d+)?)\s*(?:тысяч(?:и|а)?|тыс\.?|к)\b',
            lambda m: str(int(float(m.group(1).replace(',', '.')) * 1000)),
            s
        )

    def _extract_amount(pattern: str, src: str) -> tuple[float | None, str]:
        """Извлекает первую сумму по паттерну и возвращает (сумма, очищенный текст)."""
        m = re.search(pattern, src)
        if m:
            raw = _normalize_k(m.group(0))
            nums = re.findall(r'\d[\d\s]*(?:[.,]\d{1,2})?', raw)
            for n in nums:
                try:
                    val = float(n.replace(' ', '').replace(',', '.'))
                    if 500 <= val <= 50_000_000:
                        cleaned = src[:m.start()] + " " + src[m.end():]
                        return val, cleaned
                except Exception:
                    pass
        return None, src

    # 1. Текущий баланс (на счету, в кармане, сейчас)
    bal_pat = (
        r'(?:на\s+счету|на\s+счёте|в\s+кармане|баланс\s*:?|осталось|счёт\s*:?|кошелёк\s*:?|кошелек\s*:?)'
        r'\s*(?:сейчас\s*)?(\d[\d\s]*(?:[.,]\d{1,2})?(?:\s*(?:тыс\.?|тысяч(?:и|а)?|к)\b)?)'
    )
    bal_val, lower_tmp = _extract_amount(bal_pat, lower)
    if bal_val is not None and 0 <= bal_val <= 50_000_000:
        result["balance"] = bal_val
        lower = lower_tmp
        text = lower

    # 2. Средний доход (в среднем, обычно, средний, стабильный)
    avg_pat = (
        r'(?:в\s+среднем|средний\s+доход|обычно\s+(?:получаю|зарабатываю)|'
        r'стабильно|примерно\s+(?:получаю|зарабатываю)|обычно)\s*'
        r'(\d[\d\s]*(?:[.,]\d{1,2})?(?:\s*(?:тыс\.?|тысяч(?:и|а)?|к)\b)?)'
    )
    avg_val, lower_tmp = _extract_amount(avg_pat, lower)
    if avg_val is not None and 5_000 <= avg_val <= 5_000_000:
        result["avg_income"] = round(avg_val)
        lower = lower_tmp
        text = lower

    # 3. Планируемый доход (планирую, жду, ожидаю, получу)
    plan_pat = (
        r'(?:планирую|жду|ожидаю|получу|собираюсь\s+получить|рассчитываю)\s*'
        r'(?:заработать\s*|получить\s*)?(\d[\d\s]*(?:[.,]\d{1,2})?(?:\s*(?:тыс\.?|тысяч(?:и|а)?|к)\b)?)'
    )
    plan_val, lower_tmp = _extract_amount(plan_pat, lower)
    if plan_val is not None and 1_000 <= plan_val <= 5_000_000:
        result["planned_income"] = round(plan_val)
        lower = lower_tmp
        text = lower

    # 4. Дни зарплаты (из оставшегося текста)
    result["days"] = parse_salary_days(text)

    # 5. Если avg_income не нашли — пробуем общий паттерн "получаю/зарабатываю N"
    if result["avg_income"] is None:
        general_pat = (
            r'(?:получаю|зарабатываю|доход|зп|зарплата)\s*'
            r'(\d[\d\s]*(?:[.,]\d{1,2})?(?:\s*(?:тыс\.?|тысяч(?:и|а)?|к)\b)?)'
        )
        gen_val, _ = _extract_amount(general_pat, lower)
        if gen_val and 5_000 <= gen_val <= 5_000_000:
            result["avg_income"] = round(gen_val)

    return result


# ─── СТАРТОВОЕ СООБЩЕНИЕ ОНБОРДИНГА ─────────────────────────────────────────

async def _send_step1(message, name: str):
    await message.answer(
        f"Привет, {name}.\n\n"
        "Я веду бюджет, слежу за тратами и предупреждаю до того, как деньги закончатся.\n\n"
        "<b>Шаг 1/3.</b> Напиши в одном сообщении:\n"
        "— <b>Дни зарплаты</b> — поставлю напоминание, даже если сумма ещё неизвестна\n"
        "— <b>Сколько сейчас на счету</b> — отправная точка\n"
        "— <b>Сколько планируешь заработать в ближайшее время</b> — буду считать на это\n"
        "— <b>Средний доход в месяц</b> — чтобы Gemini видел общую картину\n\n"
        "Пример:\n"
        "<i>«Зарплата 9-го и 23-го. На счету 12000. Планирую получить 40000. В среднем 65000 в месяц»</i>\n"
        "<i>«Получаю 1-го и 15-го. Сейчас в кармане 5000. Жду 30000. Обычно зарабатываю 50к»</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="onboarding:skip_step1")]
        ])
    )


# ─── /start ──────────────────────────────────────────────────────────────────

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
        await state.set_state(OnboardingState.step_combined)
        await _send_step1(message, name)
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
        "Просто пиши что потратил или получил:\n"
        "<i>«кофе 200»</i>, <i>«зарплата 50к»</i>, <i>«такси 350»</i>\n\n"
        "Списком за день:\n"
        "<i>«кофе 200, такси 350, обед 600, продукты 2300»</i>\n\n"
        "Или задавай вопросы:\n"
        "<i>«хватит ли до зарплаты?»</i>, <i>«сколько потратил на еду?»</i>\n\n"
        "<b>/week</b> — подробный анализ бюджета\n"
        "<b>/cancel</b> — отменить текущее действие\n"
        "<b>/history</b> — последние транзакции",
        parse_mode="HTML"
    )


# ─── ШАГ 1: ОБЪЕДИНЁННЫЙ ─────────────────────────────────────────────────────

@router.message(OnboardingState.step_combined)
async def onboarding_combined(message: Message, state: FSMContext):
    parsed = _parse_combined_step(message.text or "")
    if not parsed["days"] and not parsed["avg_income"] and not parsed["planned_income"] and parsed["balance"] is None:
        await message.answer(
            "Не разобрал. Попробуй так:\n"
            "<i>«Зарплата 9-го и 23-го. На счету 12000. Планирую 40000. В среднем 65000»</i>",
            parse_mode="HTML"
        )
        return

    # Дни зарплаты → сохранить и поставить напоминания
    if parsed["days"]:
        await set_salary_days(message.from_user.id, parsed["days"])

    # Средний доход → используется для формирования бюджетов
    if parsed["avg_income"]:
        await update_user(message.from_user.id, {
            "monthly_income": parsed["avg_income"],
            "average_income": parsed["avg_income"],
        })
        await state.update_data(monthly_income=float(parsed["avg_income"]))

    # Прогнозируемый доход → отдельный учёт
    if parsed["planned_income"]:
        await update_user(message.from_user.id, {"predicted_income": parsed["planned_income"]})
        await state.update_data(planned_income=float(parsed["planned_income"]))
        # Записываем как планируемый доход в таблицу
        try:
            from db import add_planned_income
            from datetime import date, timedelta
            await add_planned_income(
                user_id=message.from_user.id,
                amount=float(parsed["planned_income"]),
                expected_date=(date.today() + timedelta(days=7)).isoformat(),
                description="Прогнозируемый доход (онбординг)",
                type_="income",
            )
        except Exception as e:
            logging.error(f"add_planned_income error: {e}")

    # Текущий баланс → фиксируем и пишем транзакцию "Начальный баланс"
    if parsed["balance"] is not None:
        await set_current_balance(message.from_user.id, parsed["balance"])
        await state.update_data(current_balance=float(parsed["balance"]))
        if parsed["balance"] > 0:
            try:
                from db import add_transaction
                await add_transaction(
                    user_id=message.from_user.id,
                    type_="income",
                    amount=float(parsed["balance"]),
                    category="Доходы",
                    description="Начальный баланс (онбординг)",
                )
            except Exception as e:
                logging.error(f"balance transaction error: {e}")

    parts = []
    if parsed["days"]:
        parts.append(f"Дни зарплаты: {', '.join(map(str, parsed['days']))}-е числа — <b>напоминания поставлены</b>")
    if parsed["avg_income"]:
        parts.append(f"Средний доход: {parsed['avg_income']:,.0f} ₽/мес")
    if parsed["planned_income"]:
        parts.append(f"Прогнозируемый доход (ближайший): {parsed['planned_income']:,.0f} ₽")
    if parsed["balance"] is not None:
        parts.append(f"Баланс сейчас: {parsed['balance']:,.0f} ₽ — записан в доходы")

    confirm_text = "\n".join(f"• {p}" for p in parts)

    await message.answer(
        f"Принял:\n{confirm_text}\n\n"
        "<b>Шаг 2/3.</b> Кредиты и ипотека.\n\n"
        "Напиши все свои кредиты, займы, рассрочки:\n"
        "<i>«Кредит Сбер 12000, ипотека 45000 первого, рассрочка телефон 3500»</i>\n\n"
        "Из них автоматически сформируется бюджет Кредиты.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Нет кредитов", callback_data="onboarding:skip_payments")]
        ])
    )
    await state.set_state(OnboardingState.step_payments)


@router.callback_query(F.data == "onboarding:skip_step1")
async def onboarding_skip_step1(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "<b>Шаг 2/3.</b> Кредиты и ипотека.\n\n"
        "Напиши все свои кредиты, займы, рассрочки:\n"
        "<i>«Кредит Сбер 12000, ипотека 45000 первого, рассрочка телефон 3500»</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Нет кредитов", callback_data="onboarding:skip_payments")]
        ])
    )
    await state.set_state(OnboardingState.step_payments)
    await callback.answer()


# ─── ШАГ 2: РЕГУЛЯРНЫЕ ПЛАТЕЖИ ───────────────────────────────────────────────

CREDIT_KEYWORDS = {"кредит", "ипотека", "займ", "рассрочка", "долг"}


@router.message(OnboardingState.step_payments)
async def onboarding_payments(message: Message, state: FSMContext):
    await message.answer("Разбираю...")
    try:
        from ai_service import parse_onboarding_payments
        payments = await parse_onboarding_payments(message.text or "")
    except Exception as e:
        logging.error(f"onboarding parse payments error: {e}")
        payments = []

    # Фильтруем только кредиты/ипотеку/займы
    credit_payments = [
        p for p in payments
        if any(kw in p["name"].lower() for kw in CREDIT_KEYWORDS)
    ]
    # Если ничего не распознали как кредит — считаем всё кредитами на этом шаге
    if not credit_payments and payments:
        credit_payments = payments

    if not credit_payments:
        await message.answer(
            "Не смог разобрать. Попробуй иначе:\n"
            "<i>«Кредит Сбер 12000, ипотека 45000, рассрочка 3500»</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Пропустить", callback_data="onboarding:skip_payments")]
            ])
        )
        return

    # Принудительно метим все как кредиты (шаг только для кредитов)
    for p in credit_payments:
        p["category"] = "Кредиты"
        if not any(kw in p["name"].lower() for kw in CREDIT_KEYWORDS):
            p["name"] = "Кредит " + p["name"]

    await state.update_data(parsed_payments=credit_payments)

    credit_sum = sum(float(p["amount"]) for p in credit_payments)
    lines = []
    for p in credit_payments:
        lines.append(f"• {p['name']} — {p['amount']:,.0f} ₽, {p['day']}-е числа → бюджет Кредиты")

    await message.answer(
        f"Вот что понял:\n\n" + "\n".join(lines) +
        f"\n\n💳 Бюджет Кредиты: {credit_sum:,.0f} ₽/мес будет создан автоматически.\n\nВерно?",
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
    credit_sum = 0.0

    for p in payments:
        try:
            # На этом шаге все платежи — кредиты
            cat = "Кредиты"
            await add_scheduled_payment(
                user_id=callback.from_user.id,
                name=p["name"],
                amount=float(p["amount"]),
                day=int(p.get("day", 1)),
                category=cat,
            )
            credit_sum += float(p["amount"])

            # Записываем как расход в транзакции (Обязательные платежи)
            try:
                from db import add_transaction
                await add_transaction(
                    user_id=callback.from_user.id,
                    type_="expense",
                    amount=float(p["amount"]),
                    category="Кредиты",
                    description=f"Обязательный платёж: {p['name']} (онбординг)",
                )
            except Exception as e:
                logging.error(f"credit transaction record error: {e}")

        except Exception as e:
            logging.error(f"onboarding save payment error: {e}")

    if credit_sum > 0:
        try:
            await set_budget(callback.from_user.id, "Кредиты", credit_sum)
            await state.update_data(auto_budget_credits=credit_sum)
        except Exception as e:
            logging.error(f"auto budget credits error: {e}")

    await callback.message.answer(
        f"Сохранено {len(payments)} кредитов. Бюджет Кредиты: {credit_sum:,.0f} ₽/мес.\n\n"
        "<b>Шаг 3/3.</b> На что тратишься каждый месяц?\n\n"
        "Пиши с конкретными суммами:\n"
        "<i>«Еда 20000, такси 5000, спортзал 6900, подписки 1500, "
        "кофе 3000, иногда одежда»</i>\n\n"
        "Если пишешь «еда 20000» — бюджет будет 20000, а не меньше.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить — настрою сам", callback_data="onboarding:skip_brief")]
        ])
    )
    await state.set_state(OnboardingState.step_brief)
    await callback.answer()


@router.callback_query(F.data == "onboarding:payments_retry", OnboardingState.step_payments_confirm)
async def onboarding_payments_retry(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Напиши ещё раз:\n<i>«Кредит Сбер 12000, ипотека 45000, рассрочка 3500»</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Нет кредитов", callback_data="onboarding:skip_payments")]
        ])
    )
    await state.set_state(OnboardingState.step_payments)
    await callback.answer()


@router.callback_query(F.data == "onboarding:skip_payments")
async def onboarding_skip_payments(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "<b>Шаг 3/3.</b> На что тратишься?\n\n"
        "Пиши с конкретными суммами:\n"
        "<i>«Еда 20000, такси 5000, спортзал 6900, подписки 1500»</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить — настрою сам", callback_data="onboarding:skip_brief")]
        ])
    )
    await state.set_state(OnboardingState.step_brief)
    await callback.answer()


# ─── ШАГ 3: УМНОЕ ФОРМИРОВАНИЕ БЮДЖЕТОВ ─────────────────────────────────────

@router.message(OnboardingState.step_brief)
async def onboarding_brief(message: Message, state: FSMContext):
    await state.update_data(spending_description=message.text or "")
    await message.answer("Анализирую...")
    await _generate_and_confirm_budgets(message, state)


@router.callback_query(F.data == "onboarding:skip_brief")
async def onboarding_skip_brief(callback: CallbackQuery, state: FSMContext):
    await _generate_and_confirm_budgets(callback.message, state, user_id=callback.from_user.id)
    await callback.answer()


async def _generate_and_confirm_budgets(message, state: FSMContext, user_id: int = None):
    data = await state.get_data()
    monthly_income = data.get("monthly_income", 0)
    planned_income_val = data.get("planned_income", 0)
    description = data.get("spending_description", "")
    auto_credits = data.get("auto_budget_credits", 0)
    uid = user_id or message.chat.id

    if not monthly_income:
        user = await get_user(uid)
        if user:
            monthly_income = float(user.get("monthly_income") or 0)
            if not planned_income_val:
                planned_income_val = float(user.get("predicted_income") or 0)

    # Для расчёта бюджетов используем средний доход (если есть), иначе прогнозируемый
    budget_income = monthly_income or planned_income_val

    result_obj = None
    budgets: dict[str, float] = {}

    if budget_income > 0:
        try:
            from db import get_scheduled_payments
            from budget_engine import parse_user_spending_ai, build_budgets
            payments = await get_scheduled_payments(uid)
            user_expenses = None
            if description:
                user_expenses = await parse_user_spending_ai(description)
            result_obj = build_budgets(
                monthly_income=budget_income,
                scheduled_payments=payments,
                user_expenses=user_expenses,
            )
            budgets = result_obj.budgets
        except Exception as e:
            logging.error(f"onboarding budgets error: {e}")

    if not budgets:
        await _onboarding_finish(message, state, budgets={}, uid=uid)
        return

    await state.update_data(generated_budgets=budgets)

    strict_set = set(result_obj.strict_categories) if result_obj else set()
    lines = []
    for cat, amount in sorted(budgets.items(), key=lambda x: x[1], reverse=True):
        mark = " ✓" if cat in strict_set else ""
        lines.append(f"• {cat}: {amount:,.0f} ₽/мес{mark}")

    if auto_credits > 0 and "Кредиты" not in budgets:
        lines.insert(0, f"• Кредиты: {auto_credits:,.0f} ₽/мес ✓")

    income_note = ""
    if planned_income_val and monthly_income and planned_income_val != monthly_income:
        income_note = (
            f"\n\nИспользован средний доход {monthly_income:,.0f} ₽ для расчёта. "
            f"Прогнозируемый ({planned_income_val:,.0f} ₽) учтён отдельно."
        )

    deficit_warn = "\n\nВнимание: расходы превышают доход. Категории урезаны по иерархии." if (result_obj and result_obj.is_deficit) else ""
    skipped_note = f"\nНе включено (нет бюджета): {', '.join(result_obj.skipped_categories)}" if (result_obj and result_obj.skipped_categories) else ""

    await message.answer(
        "<b>Бюджеты на основе твоих данных:</b>\n\n"
        + "\n".join(lines)
        + income_note
        + deficit_warn
        + skipped_note
        + "\n\n✓ = конкретная сумма из твоего описания, не урезана."
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
    for cat, amount in budgets.items():
        try:
            await set_budget(callback.from_user.id, cat, amount)
        except Exception as e:
            logging.error(f"save budget error: {e}")
    await _onboarding_finish(callback.message, state, budgets=budgets, uid=callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == "onboarding:budgets_skip")
async def onboarding_budgets_skip(callback: CallbackQuery, state: FSMContext):
    await _onboarding_finish(callback.message, state, budgets=None, uid=callback.from_user.id)
    await callback.answer()


async def _onboarding_finish(message, state: FSMContext, budgets: dict = None, uid: int = None):
    user_id = uid or message.chat.id
    user = await get_user(user_id)
    salary_days = await get_salary_days(user_id)
    data = await state.get_data()
    monthly_income = data.get("monthly_income", 0)
    planned_income_val = data.get("planned_income", 0)

    parts = []
    if salary_days:
        parts.append(f"Дни зарплаты: {', '.join(map(str, salary_days))}-е числа")
    if budgets:
        parts.append(f"Бюджеты: {len(budgets)} категорий")

    setup_str = (", ".join(parts) + " — готово.") if parts else ""
    budgets_hint = "\nБюджеты можно скорректировать через кнопку Бюджеты." if budgets else ""

    await message.answer(
        f"<b>Всё настроено.</b> {setup_str}\n\n"
        "Теперь просто пиши что потратил или получил:\n"
        "<i>«кофе 200»</i>, <i>«зарплата 50к»</i>, <i>«такси 350»</i>\n\n"
        "Или спрашивай:\n<i>«хватит ли до зарплаты?»</i>\n\n"
        "<b>/week</b> — подробный анализ бюджета."
        f"{budgets_hint}\n\nПогнали.",
        parse_mode="HTML",
        reply_markup=main_menu()
    )
    await state.clear()

    if (monthly_income > 0 or planned_income_val > 0) and budgets:
        try:
            from weekly_advice import generate_onboarding_gemini_analysis
            from db import get_scheduled_payments
            payments = await get_scheduled_payments(user_id)
            avg_income = float(user.get("average_income") or monthly_income) if user else monthly_income
            analysis_text = await generate_onboarding_gemini_analysis(
                user_id=user_id,
                monthly_income=monthly_income or planned_income_val,
                budgets=budgets,
                payments=payments,
                salary_days=salary_days or [],
                planned_income=planned_income_val,
                average_income=avg_income,
            )
            if analysis_text:
                await message.answer(
                    f"<b>Предварительный финансовый анализ</b>\n\n{analysis_text}",
                    parse_mode="HTML",
                )
        except Exception as e:
            logging.error(f"onboarding gemini analysis error: {e}")


# ─── COMPAT (старые FSM-состояния) ───────────────────────────────────────────

@router.message(OnboardingState.step_salary_amount)
@router.message(OnboardingState.step_income)
async def compat_old_salary(message: Message, state: FSMContext):
    await state.set_state(OnboardingState.step_combined)
    await onboarding_combined(message, state)


@router.message(OnboardingState.step_salary_days)
async def compat_salary_days(message: Message, state: FSMContext):
    from categorizer import parse_salary_days
    days = parse_salary_days(message.text or "")
    if days:
        await set_salary_days(message.from_user.id, days)
    await state.set_state(OnboardingState.step_payments)
    await message.answer(
        "<b>Шаг 2/3.</b> Регулярные платежи:\n"
        "<i>«Аренда 35000, кредит 12000, Netflix 699»</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Нет", callback_data="onboarding:skip_payments")]
        ])
    )


# ─── НАСТРОЙКИ ────────────────────────────────────────────────────────────────

@router.message(F.text == "Настройки")
async def settings_menu(message: Message):
    token = await get_google_token(message.from_user.id)
    user = await get_user(message.from_user.id)
    salary_days = await get_salary_days(message.from_user.id)
    salary_str = ", ".join(str(d) for d in salary_days) if salary_days else "не задан"
    reminder_hour = user.get("expense_reminder_hour", 21) if user else 21
    balance = await get_current_balance(message.from_user.id)
    balance_str = f"{balance:,.0f} ₽" if balance is not None else "не указан"

    await message.answer(
        f"<b>Настройки</b>\n\n"
        f"Дни зарплаты: <b>{salary_str}</b>\n"
        f"Напоминания: <b>{reminder_hour}:00</b>\n"
        f"Баланс (заявленный): <b>{balance_str}</b>\n"
        f"Google Calendar: <b>{'подключён' if token else 'не подключён'}</b>",
        parse_mode="HTML",
        reply_markup=settings_kb(has_google=bool(token))
    )


@router.callback_query(F.data == "settings:restart_onboarding")
async def restart_onboarding(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(OnboardingState.step_combined)
    user = await get_user(callback.from_user.id)
    name = (user.get("first_name") or "друг") if user else "друг"
    await callback.message.answer("Перезапускаем настройку.")
    await _send_step1(callback.message, name)
    await callback.answer()


@router.callback_query(F.data == "settings:salary_day")
async def ask_salary_day(callback: CallbackQuery, state: FSMContext):
    current = await get_salary_days(callback.from_user.id)
    current_str = ", ".join(str(d) for d in current) if current else "не задан"
    await callback.message.answer(
        f"Сейчас: <b>{current_str}</b>.\n\nНапиши дни: <i>9, 18, 23</i>",
        parse_mode="HTML", reply_markup=cancel_kb()
    )
    await state.set_state(SettingsState.waiting_salary_day)
    await callback.answer()


@router.message(SettingsState.waiting_salary_day)
async def save_salary_day(message: Message, state: FSMContext):
    from categorizer import parse_salary_days
    days = parse_salary_days(message.text)
    if not days:
        await message.answer("Не разобрал. Числа от 1 до 31.", reply_markup=cancel_kb())
        return
    await set_salary_days(message.from_user.id, days)
    await message.answer(
        f"Готово. Дни зарплаты: <b>{', '.join(map(str, days))}</b>",
        parse_mode="HTML", reply_markup=main_menu()
    )
    await state.clear()


@router.callback_query(F.data == "settings:reminder_hour")
async def ask_reminder_hour(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("В какой час напоминать? Число от 0 до 23.", reply_markup=cancel_kb())
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
    await callback.message.answer("Жми — откроется Google. Войди, разреши доступ, вернись.", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "settings:google_info")
async def google_info(callback: CallbackQuery):
    await callback.answer("Google Calendar подключён. Платежи добавляются автоматически.", show_alert=True)


@router.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Отменено.", reply_markup=main_menu())
    await callback.answer()


# ─── СБРОС ДАННЫХ ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "settings:reset_all_data")
async def reset_all_data_confirm(callback: CallbackQuery):
    await callback.message.answer(
        "<b>Удалить ВСЕ мои данные?</b>\n\n"
        "Транзакции, платежи, бюджеты, цели, настройки.\nДействие необратимо.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, удалить всё", callback_data="confirm_reset_all"),
                InlineKeyboardButton(text="Отмена", callback_data="cancel"),
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
            f"<b>Готово. Всё удалено.</b> Записей: {total}",
            parse_mode="HTML",
        )
        user = await get_user(callback.from_user.id)
        name = (user.get("first_name") or "друг") if user else "друг"
        await state.set_state(OnboardingState.step_combined)
        await _send_step1(callback.message, name)
    except Exception as e:
        logging.error(f"reset_all_data error: {e}")
        await wait_msg.delete()
        await callback.message.answer(f"Что-то пошло не так: {str(e)}", reply_markup=main_menu())
    await callback.answer()
