from datetime import date, timedelta
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command

from db import (
    add_transaction, get_stats, get_recent_transactions, delete_transaction,
    get_scheduled_payments, get_planned_income, get_salary_days
)
from keyboards import (
    main_menu, expense_categories_kb, income_categories_kb,
    stats_period_kb, delete_transaction_kb, cancel_kb, skip_cancel_kb
)

router = Router()


class TransactionState(StatesGroup):
    choosing_expense_category = State()
    entering_expense_amount = State()
    entering_expense_desc = State()

    choosing_income_category = State()
    entering_income_amount = State()


# ─── РАСХОДЫ (ручной ввод) ────────────────────────────────────────────────────

@router.message(F.text == "Добавить расход")
async def add_expense_start(message: Message, state: FSMContext):
    await message.answer(
        "Просто пиши в чат что потратил: <i>«кофе 200»</i>, <i>«такси 350»</i> — разберу сам.\n\n"
        "Или выбери категорию вручную:",
        parse_mode="HTML",
        reply_markup=expense_categories_kb()
    )
    await state.set_state(TransactionState.choosing_expense_category)


@router.callback_query(F.data.startswith("cat_exp:"))
async def expense_category_chosen(callback: CallbackQuery, state: FSMContext):
    try:
        category = callback.data.split(":", 1)[1]
        await state.update_data(category=category)
        await callback.message.answer(
            f"Категория: <b>{category}</b>\n\nСумма:",
            parse_mode="HTML",
            reply_markup=cancel_kb()
        )
        await state.set_state(TransactionState.entering_expense_amount)
        await callback.answer()
    except IndexError:
        await callback.answer("Ошибка выбора категории", show_alert=True)


@router.message(TransactionState.entering_expense_amount)
async def expense_amount_entered(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",", ".").replace(" ", ""))
        if amount <= 0:
            raise ValueError
        await state.update_data(amount=amount)
        await message.answer(
            "Описание (необязательно):",
            reply_markup=skip_cancel_kb()
        )
        await state.set_state(TransactionState.entering_expense_desc)
    except ValueError:
        await message.answer("Сумма числом, например 350.", reply_markup=cancel_kb())


@router.message(TransactionState.entering_expense_desc)
async def expense_desc_entered(message: Message, state: FSMContext):
    desc = None if message.text == "/skip" else message.text
    data = await state.get_data()
    await add_transaction(
        user_id=message.from_user.id,
        type_="expense",
        amount=data["amount"],
        category=data["category"],
        description=desc
    )
    await message.answer(
        f"<b>Записал.</b> {data['category']}: <b>{data['amount']:,.2f} ₽</b>",
        parse_mode="HTML",
        reply_markup=main_menu()
    )
    await state.clear()


@router.callback_query(F.data == "skip", TransactionState.entering_expense_desc)
async def expense_desc_skipped(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await add_transaction(
        user_id=callback.from_user.id,
        type_="expense",
        amount=data["amount"],
        category=data["category"],
    )
    await callback.message.answer(
        f"<b>Записал.</b> {data['category']}: <b>{data['amount']:,.2f} ₽</b>",
        parse_mode="HTML",
        reply_markup=main_menu()
    )
    await state.clear()
    await callback.answer()


# ─── ДОХОДЫ ──────────────────────────────────────────────────────────────────

@router.message(F.text == "Добавить доход")
async def add_income_start(message: Message, state: FSMContext):
    await message.answer(
        "Пиши: <i>«получил зарплату 50к»</i>, <i>«аванс 15000»</i> — разберу сам.\n\n"
        "Или выбери категорию вручную:",
        parse_mode="HTML",
        reply_markup=income_categories_kb()
    )
    await state.set_state(TransactionState.choosing_income_category)


@router.callback_query(F.data.startswith("cat_inc:"))
async def income_category_chosen(callback: CallbackQuery, state: FSMContext):
    try:
        category = callback.data.split(":", 1)[1]
        await state.update_data(category=category)
        await callback.message.answer(
            f"Категория: <b>{category}</b>\n\nСумма:",
            parse_mode="HTML",
            reply_markup=cancel_kb()
        )
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
            f"<b>Записал.</b> {data['category']}: <b>{amount:,.2f} ₽</b>",
            parse_mode="HTML",
            reply_markup=main_menu()
        )
        await state.clear()
    except ValueError:
        await message.answer("Нужна сумма числом.", reply_markup=cancel_kb())


# ─── СТАТИСТИКА ───────────────────────────────────────────────────────────────

@router.message(F.text == "Статистика")
async def stats_menu(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="За неделю", callback_data="stats:week"),
            InlineKeyboardButton(text="За месяц",  callback_data="stats:month"),
        ],
        [InlineKeyboardButton(text="Всё время", callback_data="stats:all")],
        [InlineKeyboardButton(text="Умный анализ", callback_data="stats:dashboard")],
    ])
    await message.answer("Выбери:", reply_markup=kb)


def _bar(pct: float, width: int = 10) -> str:
    filled = int(min(pct, 100) / (100 / width))
    return "█" * filled + "░" * (width - filled)


@router.callback_query(F.data.startswith("stats:"))
async def show_stats(callback: CallbackQuery):
    try:
        period = callback.data.split(":")[1]
        user_id = callback.from_user.id
        stats = await get_stats(user_id, period)

        period_names = {"week": "Неделя", "month": "Месяц", "all": "Всё время"}
        period_label = period_names.get(period, "Период")

        income = stats.get("income", 0)
        expenses = stats.get("expenses", 0)
        balance = stats.get("balance", 0)
        by_category = stats.get("by_category", {})
        by_income_cat = stats.get("by_income_category", {})

        balance_sign = "+" if balance >= 0 else ""
        balance_emoji = "🟢" if balance >= 0 else "🔴"
        header = (
            f"<b>Статистика — {period_label}</b>\n\n"
            f"Доходы:  <b>{income:,.0f} ₽</b>\n"
            f"Расходы: <b>{expenses:,.0f} ₽</b>\n"
            f"{balance_emoji} Баланс:  <b>{balance_sign}{balance:,.0f} ₽</b>"
        )

        # Прогноз на конец месяца (только для месячного периода)
        forecast_text = ""
        if period == "month":
            try:
                today = date.today()
                payments = await get_scheduled_payments(user_id)
                salary_days = await get_salary_days(user_id)

                future_payments = [p for p in payments if p["day_of_month"] > today.day]
                future_payments_sum = sum(p["amount"] for p in future_payments)

                from_d = today.isoformat()
                to_d = (today + timedelta(days=30)).isoformat()
                planned = await get_planned_income(user_id, from_date=from_d, to_date=to_d)
                planned_income_sum = sum(
                    p["amount"] for p in planned if p.get("type") == "income" or
                    (p.get("type") is None and "[Доход]" in (p.get("description") or ""))
                )
                planned_expense_sum = sum(
                    p["amount"] for p in planned if p.get("type") == "expense" or
                    (p.get("type") is None and "[Расход]" in (p.get("description") or ""))
                )

                projected = balance - future_payments_sum + planned_income_sum - planned_expense_sum

                parts = []
                if future_payments_sum > 0:
                    parts.append(f"Платежи впереди: −{future_payments_sum:,.0f} ₽")
                if planned_income_sum > 0:
                    parts.append(f"Ожидаемые доходы: +{planned_income_sum:,.0f} ₽")
                if planned_expense_sum > 0:
                    parts.append(f"Ожидаемые расходы: −{planned_expense_sum:,.0f} ₽")

                if parts or future_payments_sum > 0:
                    proj_sign = "+" if projected >= 0 else ""
                    proj_emoji = "🟢" if projected >= 0 else "🔴"
                    forecast_text = "\n\n<b>Прогноз на конец месяца:</b>\n"
                    for p in parts:
                        forecast_text += f"  {p}\n"
                    forecast_text += f"{proj_emoji} Итого: <b>{proj_sign}{projected:,.0f} ₽</b>"

                if salary_days:
                    next_salary = next((d for d in sorted(salary_days) if d > today.day), sorted(salary_days)[0])
                    days_left = (next_salary - today.day) if next_salary > today.day else (31 - today.day + next_salary)
                    forecast_text += f"\n\nДо зарплаты ({next_salary}-го): <b>{days_left} дн.</b>"
                    if days_left > 0 and balance > 0:
                        daily = (balance - future_payments_sum) / days_left
                        if daily > 0:
                            forecast_text += f"  →  <b>{daily:,.0f} ₽/день</b>"
            except Exception:
                pass

        cat_text = ""
        if by_category:
            total_exp = expenses or 1
            cat_text = "\n\n<b>Расходы по категориям:</b>\n"
            for cat, amount in by_category.items():
                pct = amount / total_exp * 100
                cat_text += f"{cat}: <b>{amount:,.0f} ₽</b> ({pct:.0f}%)\n{_bar(pct)}\n"
        else:
            cat_text = "\n\nРасходов нет."

        inc_text = ""
        if by_income_cat:
            total_inc = income or 1
            inc_text = "\n<b>Доходы по категориям:</b>\n"
            for cat, amount in by_income_cat.items():
                pct = amount / total_inc * 100
                inc_text += f"{cat}: <b>{amount:,.0f} ₽</b> ({pct:.0f}%)\n{_bar(pct)}\n"

        await callback.message.answer(
            header + forecast_text + cat_text + inc_text,
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        import logging
        logging.error(f"stats error: {e}")
        await callback.answer("Ошибка при получении статистики", show_alert=True)


# ─── ИСТОРИЯ И УДАЛЕНИЕ ───────────────────────────────────────────────────────

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
    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n<s>Удалено</s>",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer("Удалено")
