from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command

# Убедитесь, что db.py лежит в корне (рядом с main.py)
from db import add_transaction, get_stats, get_recent_transactions, delete_transaction
from keyboards import main_menu, expense_categories_kb, income_categories_kb, stats_period_kb, delete_transaction_kb

router = Router()


class TransactionState(StatesGroup):
    choosing_expense_category = State()
    entering_expense_amount = State()
    entering_expense_desc = State()

    choosing_income_category = State()
    entering_income_amount = State()


# ─── РАСХОДЫ (ручной ввод; основной путь — умный ввод в чате) ─────────────────

@router.message(F.text == "Добавить расход")
async def add_expense_start(message: Message, state: FSMContext):
    await message.answer(
        "Просто напиши в чат, что потратил или получил — тип и категорию определю сам. "
        "Например: <i>кофе 200</i>, <i>получил зарплату 50к</i>, <i>такси 350</i>.",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("cat_exp:"))
async def expense_category_chosen(callback: CallbackQuery, state: FSMContext):
    try:
        category = callback.data.split(":", 1)[1]
        await state.update_data(category=category)
        await callback.message.answer(f"Категория: {category}\n\nВведи сумму расхода (например: 350):")
        await state.set_state(TransactionState.entering_expense_amount)
        await callback.answer()
    except IndexError:
        await callback.answer("Ошибка выбора категории", show_alert=True)


@router.message(TransactionState.entering_expense_amount)
async def expense_amount_entered(message: Message, state: FSMContext):
    try:
        # Заменяем запятую на точку и убираем пробелы
        amount = float(message.text.strip().replace(",", ".").replace(" ", ""))
        if amount <= 0:
            raise ValueError
        await state.update_data(amount=amount)
        await message.answer("Добавь описание или нажми /skip чтобы пропустить:")
        await state.set_state(TransactionState.entering_expense_desc)
    except ValueError:
        await message.answer("Давай по-нормальному: число, например 350 или 1500.50.")


@router.message(TransactionState.entering_expense_desc)
async def expense_desc_entered(message: Message, state: FSMContext):
    desc = None if message.text == "/skip" else message.text
    data = await state.get_data()

    # Сохраняем в БД
    await add_transaction(
        user_id=message.from_user.id,
        type_="expense",
        amount=data["amount"],
        category=data["category"],
        description=desc
    )

    await message.answer(
        f"<b>Списано.</b>\n\n"
        f"Категория: {data['category']}\n"
        f"Сумма: <b>{data['amount']:,.2f} ₽</b>\n"
        f"{desc if desc else ''}",
        parse_mode="HTML",
        reply_markup=main_menu()
    )
    await state.clear()


# ─── ДОХОДЫ ──────────────────────────────────────────────

@router.message(F.text == "Добавить доход")
async def add_income_start(message: Message, state: FSMContext):
    await message.answer(
        "Просто напиши в чат: <i>получил зарплату 50к</i>, <i>аванс 15000</i>, <i>фриланс 20000</i> — запись и категорию сделаю сам.",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("cat_inc:"))
async def income_category_chosen(callback: CallbackQuery, state: FSMContext):
    try:
        category = callback.data.split(":", 1)[1]
        await state.update_data(category=category)
        await callback.message.answer(f"Категория: {category}\n\nВведи сумму дохода:")
        await state.set_state(TransactionState.entering_income_amount)
        await callback.answer()
    except IndexError:
        await callback.answer("Ошибка выбора категории", show_alert=True)


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
            f"<b>Принято.</b>\n\n"
            f"Категория: {data['category']}\n"
            f"Сумма: <b>{amount:,.2f} ₽</b>",
            parse_mode="HTML",
            reply_markup=main_menu()
        )
        await state.clear()
    except ValueError:
        await message.answer("Нужна нормальная сумма, не буквы.")


# ─── СТАТИСТИКА ───────────────────────────────────────────

@router.message(F.text == "Статистика")
async def stats_menu(message: Message):
    await message.answer("Выбери период для статистики:", reply_markup=stats_period_kb())


@router.callback_query(F.data.startswith("stats:"))
async def show_stats(callback: CallbackQuery):
    try:
        period = callback.data.split(":")[1]
        stats = await get_stats(callback.from_user.id, period)

        period_names = {"week": "За неделю", "month": "За месяц", "all": "Всё время"}
        period_label = period_names.get(period, "За период")

        # Расходы по категориям
        cat_text = ""
        by_category = stats.get("by_category", {})
        total_exp = stats.get("expenses", 1)
        if total_exp == 0:
            total_exp = 1
        for cat, amount in by_category.items():
            pct = (amount / total_exp * 100)
            bar_len = int(pct / 10)
            bar = "█" * bar_len + "░" * (10 - bar_len)
            cat_text += f"\n{cat}: <b>{amount:,.0f} ₽</b> ({pct:.0f}%)\n{bar}\n"

        # Доходы по категориям
        income_cat_text = ""
        by_income = stats.get("by_income_category", {})
        total_inc = stats.get("income", 1)
        if total_inc == 0:
            total_inc = 1
        for cat, amount in by_income.items():
            pct = (amount / total_inc * 100)
            bar_len = int(pct / 10)
            bar = "█" * bar_len + "░" * (10 - bar_len)
            income_cat_text += f"\n{cat}: <b>{amount:,.0f} ₽</b> ({pct:.0f}%)\n{bar}\n"

        balance = stats.get("balance", 0)
        if not cat_text:
            cat_text = "\nПока пусто"
        if not income_cat_text:
            income_cat_text = "\nПока пусто"

        await callback.message.answer(
            f"<b>Статистика — {period_label}</b>\n\n"
            f"Доходы: <b>{stats.get('income', 0):,.2f} ₽</b>\n"
            f"Расходы: <b>{stats.get('expenses', 0):,.2f} ₽</b>\n"
            f"Баланс: <b>{balance:,.2f} ₽</b>\n\n"
            f"<b>Расходы по категориям:</b>{cat_text}\n"
            f"<b>Доходы по категориям:</b>{income_cat_text}",
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        print(f"Ошибка в статистике: {e}")
        await callback.answer("Ошибка при получении статистики", show_alert=True)


# ─── ИСТОРИЯ И УДАЛЕНИЕ ТРАНЗАКЦИЙ ───────────────────────────────────────────

@router.message(F.text == "История")
@router.message(Command("history"))
async def show_history(message: Message):
    txs = await get_recent_transactions(message.from_user.id, limit=10)
    if not txs:
        await message.answer("Пока ничего не записано.")
        return
    await message.answer("<b>Последние 10 записей:</b>", parse_mode="HTML")
    for t in txs:
        type_icon = "📤" if t["type"] == "expense" else "📥"
        desc = f" — {t['description'][:30]}" if t.get("description") else ""
        date_str = t["created_at"][:10] if t.get("created_at") else ""
        await message.answer(
            f"{type_icon} <b>{t['amount']:,.0f} ₽</b> · {t['category']}{desc}\n<i>{date_str}</i>",
            parse_mode="HTML",
            reply_markup=delete_transaction_kb(t["id"])
        )


@router.callback_query(F.data.startswith("tx:delete:"))
async def delete_transaction_handler(callback: CallbackQuery):
    tx_id = int(callback.data.split(":")[2])
    await delete_transaction(tx_id, callback.from_user.id)
    await callback.message.edit_text(
        callback.message.text + "\n\n<s>Удалено</s>",
        parse_mode="HTML"
    )
    await callback.answer("Удалено")