import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from db import set_budget, get_budgets, get_stats, get_scheduled_payments, get_salary_days
from keyboards import main_menu, EXPENSE_CATEGORIES, cancel_kb

router = Router()


class BudgetState(StatesGroup):
    choosing_category = State()
    entering_limit    = State()
    editing_limit     = State()


def budget_categories_kb(label: str = "budget_cat") -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for cat in EXPENSE_CATEGORIES:
        row.append(InlineKeyboardButton(text=cat, callback_data=f"{label}:{cat}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def budgets_list_kb(budgets: list) -> InlineKeyboardMarkup:
    """Клавиатура со списком бюджетов для редактирования."""
    buttons = []
    for b in budgets:
        buttons.append([InlineKeyboardButton(
            text=f"✏️ {b['category']}: {b['limit_amount']:,.0f} ₽",
            callback_data=f"budget_edit:{b['category']}"
        )])
    buttons.append([InlineKeyboardButton(text="+ Добавить категорию", callback_data="budget:add")])
    buttons.append([InlineKeyboardButton(text="⚡ Авто-бюджет из дохода", callback_data="budget:auto")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def budgets_empty_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Авто-бюджет из дохода", callback_data="budget:auto")],
        [InlineKeyboardButton(text="Добавить вручную",         callback_data="budget:add")],
    ])


@router.message(F.text == "Бюджеты")
async def budgets_menu(message: Message):
    budgets = await get_budgets(message.from_user.id)
    stats   = await get_stats(message.from_user.id, "month")

    if not budgets:
        await message.answer(
            "<b>Бюджеты по категориям</b>\n\n"
            "Лимитов пока нет.\n\n"
            "Авто-бюджет рассчитает лимиты исходя из твоего дохода — нажми кнопку.\n"
            "Или добавь категории вручную.",
            parse_mode="HTML",
            reply_markup=budgets_empty_kb()
        )
        return

    from datetime import date
    now = date.today()
    month_name = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                  "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"][now.month]

    total_limit = sum(b["limit_amount"] for b in budgets)
    total_spent = sum(stats["by_category"].get(b["category"], 0) for b in budgets)

    text = f"<b>Бюджеты — {month_name} {now.year}</b>\n"
    text += f"Потрачено всего: {total_spent:,.0f} из {total_limit:,.0f} ₽\n\n"

    alerts = []
    for b in sorted(budgets, key=lambda x: -stats["by_category"].get(x["category"], 0) / max(x["limit_amount"], 1)):
        spent = stats["by_category"].get(b["category"], 0)
        limit = b["limit_amount"]
        pct   = min(spent / limit * 100, 100) if limit > 0 else 0
        remaining = max(limit - spent, 0)

        if pct >= 100:
            status = "ПРЕВЫШЕН"
            alerts.append(b["category"])
        elif pct >= 80:
            status = f"осталось {remaining:,.0f} ₽"
        elif pct >= 50:
            status = f"осталось {remaining:,.0f} ₽"
        else:
            status = f"осталось {remaining:,.0f} ₽"

        bar_filled = int(pct / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)

        text += (
            f"<b>{b['category']}</b>\n"
            f"{bar} {pct:.0f}%\n"
            f"Потрачено: {spent:,.0f} / {limit:,.0f} ₽  ({status})\n\n"
        )

    if alerts:
        text += f"Превышены: {', '.join(alerts)}\n"

    await message.answer(text, parse_mode="HTML", reply_markup=budgets_list_kb(budgets))


# ─── АВТО-БЮДЖЕТ ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "budget:auto")
async def auto_budget_start(callback: CallbackQuery, state: FSMContext):
    """Запускает автоматическое формирование бюджетов."""
    await callback.message.answer(
        "Сколько зарабатываешь в месяц?\n\n"
        "Напиши одно число: <i>«85000»</i> или <i>«85к»</i>",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )
    await state.set_state(BudgetState.entering_limit)
    await state.update_data(auto_mode=True)
    await callback.answer()


@router.message(BudgetState.entering_limit)
async def budget_limit_entered(message: Message, state: FSMContext):
    data = await state.get_data()

    if data.get("auto_mode"):
        # Режим авто-бюджета — парсим доход
        import re
        text = (message.text or "").strip().lower()
        nums = re.findall(r'(\d+(?:[.,]\d+)?)', text)
        amounts = []
        for n in nums:
            val = float(n.replace(",", "."))
            if "к" in text or "k" in text.lower():
                val *= 1000
            if 5000 <= val <= 5_000_000:
                amounts.append(val)

        if not amounts:
            await message.answer("Нужна сумма числом: 85000 или 85к.", reply_markup=cancel_kb())
            return

        monthly_income = sum(amounts) / len(amounts)
        await message.answer("Считаю бюджеты...")

        try:
            payments = await get_scheduled_payments(message.from_user.id)
            from weekly_advice import generate_initial_budgets
            budgets = await generate_initial_budgets(monthly_income, payments)
        except Exception as e:
            logging.error(f"auto_budget error: {e}")
            budgets = {}

        if not budgets:
            await message.answer(
                "Не удалось рассчитать автоматически. Добавь категории вручную.",
                reply_markup=main_menu()
            )
            await state.clear()
            return

        # Сохраняем для подтверждения
        await state.update_data(generated_budgets=budgets, monthly_income=monthly_income, auto_mode=False)
        lines = [f"• {cat}: {amount:,.0f} ₽/мес" for cat, amount in budgets.items()]
        await message.answer(
            f"<b>Рассчитанные бюджеты (доход {monthly_income:,.0f} ₽):</b>\n\n"
            + "\n".join(lines)
            + "\n\nСохранить или настроить?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="Сохранить всё",  callback_data="budget:auto_confirm"),
                    InlineKeyboardButton(text="Настроить",      callback_data="budget:auto_edit"),
                ],
                [InlineKeyboardButton(text="Отмена", callback_data="cancel")],
            ])
        )
        return

    # Ручной режим — просто сохраняем лимит
    try:
        limit = float(message.text.strip().replace(",", ".").replace(" ", ""))
        if limit <= 0:
            raise ValueError
        cat = data.get("category") or data.get("edit_category")
        await set_budget(message.from_user.id, cat, limit)
        await message.answer(
            f"<b>Лимит сохранён.</b> {cat}: <b>{limit:,.0f} ₽/мес</b>",
            parse_mode="HTML",
            reply_markup=main_menu()
        )
        await state.clear()
    except ValueError:
        await message.answer("Нужна сумма числом: 15000", reply_markup=cancel_kb())


@router.callback_query(F.data == "budget:auto_confirm")
async def auto_budget_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    budgets = data.get("generated_budgets", {})
    saved = 0
    for cat, amount in budgets.items():
        try:
            await set_budget(callback.from_user.id, cat, amount)
            saved += 1
        except Exception as e:
            logging.error(f"save budget error: {e}")
    await callback.message.answer(
        f"<b>Сохранено {saved} бюджетов.</b>\n\nМожешь скорректировать любой — нажми на него в разделе Бюджеты.",
        parse_mode="HTML",
        reply_markup=main_menu()
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "budget:auto_edit")
async def auto_budget_edit(callback: CallbackQuery, state: FSMContext):
    """Показываем список для редактирования перед сохранением."""
    data = await state.get_data()
    budgets = data.get("generated_budgets", {})
    # Сначала сохраняем всё, потом пользователь может редактировать
    for cat, amount in budgets.items():
        try:
            await set_budget(callback.from_user.id, cat, amount)
        except Exception as e:
            logging.error(f"save budget error: {e}")

    await callback.message.answer(
        "Бюджеты сохранены. Нажми на любую категорию чтобы изменить лимит.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"✏️ {cat}: {amt:,.0f} ₽", callback_data=f"budget_edit:{cat}")]
            for cat, amt in budgets.items()
        ] + [[InlineKeyboardButton(text="Готово", callback_data="cancel")]])
    )
    await state.clear()
    await callback.answer()


# ─── РУЧНОЕ ДОБАВЛЕНИЕ ───────────────────────────────────────────────────────

@router.callback_query(F.data == "budget:add")
async def budget_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Выбери категорию:", reply_markup=budget_categories_kb())
    await state.set_state(BudgetState.choosing_category)
    await callback.answer()


@router.callback_query(F.data.startswith("budget_cat:"), BudgetState.choosing_category)
async def budget_category_chosen(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":", 1)[1]
    await state.update_data(category=category)
    await callback.message.answer(
        f"Категория: <b>{category}</b>\n\nМесячный лимит в рублях:",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )
    await state.set_state(BudgetState.entering_limit)
    await callback.answer()


# ─── РЕДАКТИРОВАНИЕ СУЩЕСТВУЮЩЕГО ЛИМИТА ─────────────────────────────────────

@router.callback_query(F.data.startswith("budget_edit:"))
async def budget_edit_start(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":", 1)[1]
    await state.update_data(edit_category=category)
    await callback.message.answer(
        f"Изменяем лимит для <b>{category}</b>.\n\nНовая сумма в рублях:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Удалить лимит", callback_data=f"budget_delete:{category}")],
            [InlineKeyboardButton(text="Отмена", callback_data="cancel")],
        ])
    )
    await state.set_state(BudgetState.entering_limit)
    await callback.answer()


@router.callback_query(F.data.startswith("budget_delete:"))
async def budget_delete(callback: CallbackQuery):
    from db import supabase
    category = callback.data.split(":", 1)[1]
    try:
        supabase.table("budgets")\
            .delete()\
            .eq("user_id", callback.from_user.id)\
            .eq("category", category)\
            .execute()
        await callback.message.answer(f"Лимит для {category} удалён.", reply_markup=main_menu())
    except Exception as e:
        logging.error(f"budget_delete error: {e}")
        await callback.message.answer("Ошибка при удалении.")
    await callback.answer()
