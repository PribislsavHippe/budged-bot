from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database.db import add_transaction, get_stats
from utils.keyboards import main_menu, expense_categories_kb, income_categories_kb, stats_period_kb

router = Router()


class TransactionState(StatesGroup):
    choosing_expense_category = State()
    entering_expense_amount = State()
    entering_expense_desc = State()

    choosing_income_category = State()
    entering_income_amount = State()


# ─── РАСХОДЫ ─────────────────────────────────────────────

@router.message(F.text == "💸 Добавить расход")
async def add_expense_start(message: Message, state: FSMContext):
    await message.answer("Выбери категорию расхода:", reply_markup=expense_categories_kb())
    await state.set_state(TransactionState.choosing_expense_category)


@router.callback_query(F.data.startswith("cat_exp:"))
async def expense_category_chosen(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":", 1)[1]
    await state.update_data(category=category)
    await callback.message.answer(f"Категория: {category}\n\nВведи сумму расхода (например: 350):")
    await state.set_state(TransactionState.entering_expense_amount)
    await callback.answer()


@router.message(TransactionState.entering_expense_amount)
async def expense_amount_entered(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",", ".").replace(" ", ""))
        if amount <= 0:
            raise ValueError
        await state.update_data(amount=amount)
        await message.answer("Добавь описание или нажми /skip чтобы пропустить:")
        await state.set_state(TransactionState.entering_expense_desc)
    except ValueError:
        await message.answer("❌ Введи корректную сумму, например: 350 или 1500.50")


@router.message(TransactionState.entering_expense_desc)
async def expense_desc_entered(message: Message, state: FSMContext):
    desc = None if message.text == "/skip" else message.text
    data = await state.get_data()

    transaction = await add_transaction(
        user_id=message.from_user.id,
        type_="expense",
        amount=data["amount"],
        category=data["category"],
        description=desc
    )

    await message.answer(
        f"✅ <b>Расход записан!</b>\n\n"
        f"📂 Категория: {data['category']}\n"
        f"💸 Сумма: <b>{data['amount']:,.2f} ₽</b>\n"
        f"{'📝 ' + desc if desc else ''}",
        parse_mode="HTML",
        reply_markup=main_menu()
    )
    await state.clear()


# ─── ДОХОДЫ ──────────────────────────────────────────────

@router.message(F.text == "💰 Добавить доход")
async def add_income_start(message: Message, state: FSMContext):
    await message.answer("Выбери категорию дохода:", reply_markup=income_categories_kb())
    await state.set_state(TransactionState.choosing_income_category)


@router.callback_query(F.data.startswith("cat_inc:"))
async def income_category_chosen(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":", 1)[1]
    await state.update_data(category=category)
    await callback.message.answer(f"Категория: {category}\n\nВведи сумму дохода:")
    await state.set_state(TransactionState.entering_income_amount)
    await callback.answer()


@router.message(TransactionState.entering_income_amount)
async def income_amount_entered(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",", ".").replace(" ", ""))
        if amount <= 0:
            raise ValueError
        data = await state.get_data()

        await add_transaction(
            user_id=message.from_user.id,
            type_="income",
            amount=amount,
            category=data["category"]
        )

        await message.answer(
            f"✅ <b>Доход записан!</b>\n\n"
            f"📂 Категория: {data['category']}\n"
            f"💰 Сумма: <b>{amount:,.2f} ₽</b>\n\n"
            f"Отличная работа! Продолжай фиксировать всё 💪",
            parse_mode="HTML",
            reply_markup=main_menu()
        )
        await state.clear()
    except ValueError:
        await message.answer("❌ Введи корректную сумму")


# ─── СТАТИСТИКА ───────────────────────────────────────────

@router.message(F.text == "📊 Статистика")
async def stats_menu(message: Message):
    await message.answer("Выбери период для статистики:", reply_markup=stats_period_kb())


@router.callback_query(F.data.startswith("stats:"))
async def show_stats(callback: CallbackQuery):
    period = callback.data.split(":")[1]
    stats = await get_stats(callback.from_user.id, period)

    period_label = {"week": "За неделю", "month": "За месяц", "all": "Всё время"}[period]

    # Формируем разбивку по категориям
    cat_text = ""
    for cat, amount in stats["by_category"].items():
        pct = (amount / stats["expenses"] * 100) if stats["expenses"] > 0 else 0
        bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
        cat_text += f"\n{cat}: <b>{amount:,.0f} ₽</b> ({pct:.0f}%)\n{bar}\n"

    balance_emoji = "🟢" if stats["balance"] >= 0 else "🔴"

    await callback.message.answer(
        f"📊 <b>Статистика — {period_label}</b>\n\n"
        f"💰 Доходы: <b>{stats['income']:,.2f} ₽</b>\n"
        f"💸 Расходы: <b>{stats['expenses']:,.2f} ₽</b>\n"
        f"{balance_emoji} Баланс: <b>{stats['balance']:,.2f} ₽</b>\n\n"
        f"<b>Расходы по категориям:</b>{cat_text if cat_text else '\nПока нет данных'}",
        parse_mode="HTML"
    )
    await callback.answer()
