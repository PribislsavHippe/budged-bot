from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from db import set_budget, get_budgets, get_stats
from keyboards import main_menu, EXPENSE_CATEGORIES

router = Router()


class BudgetState(StatesGroup):
    choosing_category = State()
    entering_limit = State()


def budget_categories_kb():
    buttons = []
    row = []
    for i, cat in enumerate(EXPENSE_CATEGORIES):
        row.append(InlineKeyboardButton(text=cat, callback_data=f"budget_cat:{cat}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(F.text == "Бюджеты")
async def budgets_menu(message: Message):
    budgets = await get_budgets(message.from_user.id)
    stats = await get_stats(message.from_user.id, "month")

    if not budgets:
        await message.answer(
            "<b>Бюджеты по категориям</b>\n\n"
            "Лимитов пока нет. Поставь — буду нервировать, когда будешь подбираться к потолку.\n\n"
            "Выбери категорию:",
            parse_mode="HTML",
            reply_markup=budget_categories_kb()
        )
        return

    text = "<b>Твои лимиты на месяц:</b>\n\n"
    for b in budgets:
        spent = stats["by_category"].get(b["category"], 0)
        limit = b["limit_amount"]
        pct = min(spent / limit * 100, 100) if limit > 0 else 0

        if pct >= 100:
            status = "ПРЕВЫШЕН"
        elif pct >= 80:
            status = "Почти лимит"
        else:
            status = "В норме"

        bar_filled = int(pct / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)

        text += (
            f"{b['category']}\n"
            f"{bar} {status}\n"
            f"Потрачено: {spent:,.0f} / {limit:,.0f} ₽\n\n"
        )

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Добавить или изменить лимит", callback_data="budget:add")]
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "budget:add")
async def budget_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Выбери категорию:", reply_markup=budget_categories_kb())
    await state.set_state(BudgetState.choosing_category)
    await callback.answer()


@router.callback_query(F.data.startswith("budget_cat:"))
async def budget_category_chosen(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":", 1)[1]
    await state.update_data(category=category)
    await callback.message.answer(f"Категория: {category}\n\nВведи месячный лимит в рублях:")
    await state.set_state(BudgetState.entering_limit)
    await callback.answer()


@router.message(BudgetState.entering_limit)
async def budget_limit_entered(message: Message, state: FSMContext):
    try:
        limit = float(message.text.strip().replace(",", ".").replace(" ", ""))
        data = await state.get_data()
        await set_budget(message.from_user.id, data["category"], limit)
        await message.answer(
            f"<b>Лимит поставлен.</b> {data['category']}: <b>{limit:,.0f} ₽/месяц</b>. "
            f"Пилить буду на 80% и когда перейдёшь черту.",
            parse_mode="HTML",
            reply_markup=main_menu()
        )
        await state.clear()
    except ValueError:
        await message.answer("Нужна сумма числом. Без фантазий.")
