"""Планируемые доходы и цели накопления."""
import re
from datetime import datetime, date, timedelta
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from db import (
    get_planned_income,
    add_planned_income,
    delete_planned_income,
    get_goals,
    add_goal,
    set_goal_inactive,
    get_scheduled_payments,
    add_scheduled_payment,
    get_salary_days,
    get_stats,
)

from keyboards import main_menu, planned_income_menu_kb, planned_income_actions_kb, goals_menu_kb, goal_actions_kb

router = Router()


# ─── УТИЛИТЫ ─────────────────────────────────────────────────────────────────

def _parse_date(s: str) -> date | None:
    s = s.strip().replace(",", ".").replace("/", ".")
    parts = s.split(".")
    if len(parts) == 2:
        try:
            d, m = int(parts[0]), int(parts[1])
            now = date.today()
            year = now.year if m >= now.month else now.year + 1
            return date(year, m, d)
        except (ValueError, IndexError):
            pass
    if len(parts) == 3:
        try:
            d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
            if y < 100:
                y += 2000
            return date(y, m, d)
        except (ValueError, IndexError):
            pass
    return None


def _normalize_amount(text: str) -> str:
    t = text.lower()
    t = re.sub(
        r'(\d+(?:[.,]\d+)?)\s*(?:тысяч(?:и|а)?|тыс\.?|к)\b',
        lambda m: str(int(float(m.group(1).replace(',', '.')) * 1000)), t
    )
    t = re.sub(
        r'(\d+(?:[.,]\d+)?)\s*(?:млн|миллион)\b',
        lambda m: str(int(float(m.group(1).replace(',', '.')) * 1_000_000)), t
    )
    return t


def _extract_amount(text: str) -> float | None:
    normalized = _normalize_amount(text)
    matches = re.findall(r'\b(\d[\d\s]{0,6}(?:[.,]\d{1,2})?)\b', normalized)
    for m in matches:
        try:
            val = float(m.strip().replace(' ', '').replace(',', '.'))
            if 1 <= val <= 10_000_000:
                return val
        except Exception:
            pass
    return None


def _parse_planned_entry(text: str):
    """
    'дд.мм сумма описание' → (date, amount, description_or_None)
    Дата вырезается из текста ДО поиска суммы — чтобы цифры даты не путались с суммой.
    Returns None if not recognized.
    """
    text = text.strip()
    # Дата должна быть в начале строки
    m = re.match(r'^(\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)\s+(.+)$', text)
    if not m:
        return None

    date_str = m.group(1)
    rest = m.group(2).strip()   # всё ПОСЛЕ даты — здесь ищем сумму

    parsed_date = _parse_date(date_str)
    if not parsed_date:
        return None

    # Ищем сумму только в rest (дата уже отрезана)
    amount = _extract_amount(rest)
    if not amount:
        return None

    # Описание — rest без числа суммы
    normalized_rest = _normalize_amount(rest)
    desc = re.sub(r'\b\d[\d\s]{0,6}(?:[.,]\d{1,2})?\b', '', normalized_rest).strip()
    desc = re.sub(r'\s+', ' ', desc).strip() or None

    return parsed_date, amount, desc


def _detect_type_from_desc(desc: str) -> str:
    from categorizer import detect_type
    return detect_type(desc) or "income"


def ask_type_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📥 Доход", callback_data="pi_type:income"),
        InlineKeyboardButton(text="📤 Расход", callback_data="pi_type:expense"),
    ]])


# ─── PLANNED INCOME HANDLERS ─────────────────────────────────────────────────

class PlannedIncomeState(StatesGroup):
    waiting_input = State()
    waiting_type = State()


@router.message(F.text == "Доходы")
async def planned_income_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "<b>Планируемые записи</b>\n\n"
        "Вноси ожидаемые доходы и расходы по датам.\n"
        "Напиши одной строкой: <b>дд.мм сумма назначение</b>\n\n"
        "Примеры:\n"
        "• <i>25.03 50000 зарплата</i>\n"
        "• <i>10.04 15000 аренда</i>\n"
        "• <i>01.05 8000</i> — уточню тип",
        parse_mode="HTML",
        reply_markup=planned_income_menu_kb(),
    )


@router.callback_query(F.data == "planned_income:list")
async def planned_income_list(callback: CallbackQuery):
    user_id = callback.from_user.id
    now = date.today()
    to = now + timedelta(days=90)
    items = await get_planned_income(user_id, from_date=now.isoformat(), to_date=to.isoformat())
    if not items:
        await callback.message.answer("Планируемых записей на ближайшие 90 дней нет.")
        await callback.answer()
        return
    for item in items:
        exp = item["expected_date"][:10] if isinstance(item["expected_date"], str) else str(item["expected_date"])
        desc = f"\n{item['description']}" if item.get("description") else ""
        type_icon = "📥" if item.get("type") == "income" else "📤" if item.get("type") == "expense" else "📋"
        await callback.message.answer(
            f"{type_icon} <b>{exp}</b> — {item['amount']:,.0f} ₽{desc}",
            parse_mode="HTML",
            reply_markup=planned_income_actions_kb(item["id"]),
        )
    await callback.answer()


@router.callback_query(F.data == "planned_income:add")
async def planned_income_add_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PlannedIncomeState.waiting_input)
    await callback.message.answer(
        "Напиши одной строкой: <b>дата сумма назначение</b>\n\n"
        "Примеры:\n"
        "• <i>25.03 50000 зарплата</i>\n"
        "• <i>10.04 15000 аренда</i>\n"
        "• <i>01.05 8000</i> — уточню тип",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(PlannedIncomeState.waiting_input)
async def planned_income_text_input(message: Message, state: FSMContext):
    parsed = _parse_planned_entry(message.text or "")

    if not parsed:
        await message.answer(
            "Не разобрал. Напиши: <i>25.03 50000 зарплата</i> или <i>25.03 50000</i>",
            parse_mode="HTML"
        )
        return

    parsed_date, amount, desc = parsed

    if parsed_date < date.today():
        await message.answer("Дата уже прошла. Укажи будущую дату, например 25.03.")
        return

    await state.update_data(
        expected_date=parsed_date.isoformat(),
        amount=amount,
        description=desc,
    )

    if desc:
        type_ = _detect_type_from_desc(desc)
        await _save_planned(message, state, type_=type_)
    else:
        await message.answer(
            f"Дата: <b>{parsed_date.strftime('%d.%m.%Y')}</b>, сумма: <b>{amount:,.0f} ₽</b>\n\n"
            "Это доход или расход?",
            parse_mode="HTML",
            reply_markup=ask_type_kb(),
        )
        await state.set_state(PlannedIncomeState.waiting_type)


async def _save_planned(msg_or_cb, state: FSMContext, type_: str):
    """Сохраняет планируемую запись."""
    is_cb = isinstance(msg_or_cb, CallbackQuery)
    user_id = msg_or_cb.from_user.id
    send = msg_or_cb.message.answer if is_cb else msg_or_cb.answer

    data = await state.get_data()
    amount = data["amount"]
    expected_date = data["expected_date"]
    desc = data.get("description")

    try:
        type_label = "Доход" if type_ == "income" else "Расход"
        icon = "📥" if type_ == "income" else "📤"
        # Храним тип в description, т.к. в таблице нет поля type
        stored_desc = f"[{type_label}] {desc}" if desc else f"[{type_label}]"
        await add_planned_income(
            user_id=user_id,
            amount=amount,
            expected_date=expected_date,
            description=stored_desc,
        )
        await send(
            f"{icon} <b>Добавлено как {type_label.lower()}.</b>\n"
            f"{expected_date} — {amount:,.0f} ₽" + (f" ({desc})" if desc else ""),
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
    except Exception as e:
        await send(f"Ошибка при сохранении: {e}")

    await state.clear()


@router.callback_query(PlannedIncomeState.waiting_type, F.data.in_({"pi_type:income", "pi_type:expense"}))
async def planned_income_type_chosen(callback: CallbackQuery, state: FSMContext):
    type_ = callback.data.split(":")[1]
    await _save_planned(callback, state, type_=type_)
    await callback.answer()


@router.callback_query(F.data.startswith("planned_income:delete:"))
async def planned_income_delete(callback: CallbackQuery):
    income_id = int(callback.data.split(":")[2])
    await delete_planned_income(income_id, callback.from_user.id)
    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n<s>Удалено</s>",
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer("Удалено.")
    await callback.answer("Удалено")


# ─── GOALS ───────────────────────────────────────────────────────────────────

class GoalState(StatesGroup):
    waiting_name = State()
    waiting_amount = State()
    waiting_months = State()


@router.message(F.text == "Цели")
async def goals_menu(message: Message):
    await message.answer(
        "<b>Цели накопления</b>\n\n"
        "Ставь цель — накопить сумму за N месяцев. ИИ оценит реалистичность.",
        parse_mode="HTML",
        reply_markup=goals_menu_kb(),
    )


@router.callback_query(F.data == "goal:list")
async def goals_list(callback: CallbackQuery):
    goals = await get_goals(callback.from_user.id)
    if not goals:
        await callback.message.answer("Целей пока нет. Создай первую.")
        await callback.answer()
        return
    for g in goals:
        await callback.message.answer(
            f"<b>{g['name']}</b>\n"
            f"Цель: {g['target_amount']:,.0f} ₽ за {g['target_months']} мес. "
            f"В месяц: {g['monthly_amount']:,.0f} ₽",
            parse_mode="HTML",
            reply_markup=goal_actions_kb(g["id"]),
        )
    await callback.answer()


@router.callback_query(F.data == "goal:add")
async def goal_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Название цели (например: «Отпуск», «Ноутбук»):")
    await state.set_state(GoalState.waiting_name)
    await callback.answer()


@router.message(GoalState.waiting_name)
async def goal_name_entered(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("Сумма, которую хочешь накопить (руб):")
    await state.set_state(GoalState.waiting_amount)


def _parse_amount_goal(s: str) -> float | None:
    s = s.strip().replace(" ", "").replace(",", ".")
    mult = 1
    if s.lower().endswith("к") or s.lower().endswith("k"):
        s = s[:-1]; mult = 1000
    if s.lower().endswith("млн"):
        s = s[:-3]; mult = 1_000_000
    try:
        return float(s) * mult
    except ValueError:
        return None


@router.message(GoalState.waiting_amount)
async def goal_amount_entered(message: Message, state: FSMContext):
    amount = _parse_amount_goal(message.text)
    if amount is None or amount <= 0:
        await message.answer("Введи число, например 100000 или 50к.")
        return
    await state.update_data(target_amount=amount)
    await message.answer("За сколько месяцев хочешь накопить? (число):")
    await state.set_state(GoalState.waiting_months)


@router.message(GoalState.waiting_months)
async def goal_months_entered(message: Message, state: FSMContext):
    try:
        months = int(message.text.strip())
        if months < 1 or months > 120:
            raise ValueError
    except ValueError:
        await message.answer("Введи число месяцев от 1 до 120.")
        return
    data = await state.get_data()
    target = data["target_amount"]
    monthly = round(target / months, 2)
    try:
        from ai_service import evaluate_goal
        stats = await get_stats(message.from_user.id, "month")
        payments = await get_scheduled_payments(message.from_user.id)
        salary_days = await get_salary_days(message.from_user.id)
        now = date.today()
        planned = await get_planned_income(
            message.from_user.id,
            from_date=now.isoformat(),
            to_date=(now + timedelta(days=365)).isoformat(),
        )
        analysis = await evaluate_goal(
            stats=stats, payments=payments, planned_income=planned,
            target_amount=target, target_months=months,
            monthly_amount=monthly, salary_days=salary_days or [1],
        )
        await message.answer(
            f"<b>{data['name']}</b>\n"
            f"Цель: {target:,.0f} ₽ за {months} мес. → <b>{monthly:,.0f} ₽/мес</b>\n\n{analysis}",
            parse_mode="HTML",
        )
    except Exception:
        await message.answer(
            f"<b>{data['name']}</b>\n"
            f"Цель: {target:,.0f} ₽ за {months} мес. → <b>{monthly:,.0f} ₽/мес</b>",
            parse_mode="HTML",
        )
    await add_goal(
        user_id=message.from_user.id, name=data["name"],
        target_amount=target, target_months=months, monthly_amount=monthly,
    )
    await message.answer(
        "Цель создана. Зайди в «Мои цели» — там можно выставить напоминания в дни зарплаты.",
        reply_markup=main_menu()
    )
    await state.clear()


@router.callback_query(F.data.startswith("goal:create_reminders:"))
async def goal_create_reminders(callback: CallbackQuery):
    goal_id = int(callback.data.split(":")[2])
    goals = await get_goals(callback.from_user.id, active_only=True)
    goal = next((g for g in goals if g["id"] == goal_id), None)
    if not goal:
        await callback.answer("Цель не найдена.", show_alert=True)
        return
    salary_days = await get_salary_days(callback.from_user.id)
    if not salary_days:
        await callback.message.answer("Сначала укажи дни зарплаты в Настройках.")
        await callback.answer()
        return
    monthly = goal["monthly_amount"]
    per_day = round(monthly / len(salary_days), 2)
    for day in sorted(salary_days):
        await add_scheduled_payment(
            user_id=callback.from_user.id,
            name=f"Накопление: {goal['name']}",
            amount=per_day, day=day, category="Прочее", remind_days=0,
        )
    await callback.message.answer(
        f"Готово. Поставил <b>{len(salary_days)}</b> напоминаний "
        f"({', '.join(map(str, sorted(salary_days)))}-е числа): по {per_day:,.0f} ₽.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("goal:done:"))
async def goal_done(callback: CallbackQuery):
    goal_id = int(callback.data.split(":")[2])
    await set_goal_inactive(goal_id, callback.from_user.id)
    await callback.message.answer("Цель отмечена как завершённая.")
    await callback.answer()
