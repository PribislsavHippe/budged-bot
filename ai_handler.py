import re
import logging
from datetime import date, timedelta
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command

from db import add_transaction, get_stats, get_scheduled_payments, get_salary_days, get_budgets, get_planned_income
from keyboards import (
    main_menu, confirm_transaction_kb,
    expense_categories_kb, income_categories_kb
)
from categorizer import parse_transaction_local, looks_like_question
from ai_service import parse_transaction as ai_parse_transaction

router = Router()


def _build_reminders_context(salary_days: list, payments: list) -> str:
    parts = []
    if salary_days:
        parts.append(f"Дни зарплаты пользователя: {', '.join(map(str, sorted(salary_days)))}-е числа.")
    if payments:
        by_day = sorted(payments, key=lambda p: p["day_of_month"])
        lines = [f"{p['day_of_month']}-е: {p['name']} {p['amount']:,.0f} ₽" for p in by_day]
        parts.append("Регулярные платежи по дням месяца: " + "; ".join(lines))
    return " ".join(parts) if parts else ""


class AIState(StatesGroup):
    chatting = State()
    confirming_transaction = State()
    editing_category = State()


def clean_markdown(text: str) -> str:
    text = re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'^[-_*]{3,}$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def format_confirmation(result: dict) -> str:
    type_label = "Расход" if result["type"] == "expense" else "Доход"
    desc = result.get("description", "")
    desc_line = f"\n{desc[:60]}" if desc else ""
    return (
        f"<b>{type_label}: {result['amount']:,.0f} ₽</b>\n"
        f"Категория: {result['category']}"
        f"{desc_line}\n\n"
        f"Записываем так?"
    )


async def _get_ai_context(user_id: int):
    stats = await get_stats(user_id, "month")
    payments = await get_scheduled_payments(user_id)
    salary_days = await get_salary_days(user_id)
    budgets = await get_budgets(user_id)
    now = date.today()
    try:
        planned = await get_planned_income(user_id, from_date=now.isoformat(), to_date=(now + timedelta(days=365)).isoformat())
    except Exception:
        planned = []
    return stats, payments, salary_days, budgets, planned


# ─── СВОБОДНЫЙ ЧАТ ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "ai:chat")
async def start_ai_chat_callback(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AIState.chatting)
    await callback.message.answer(
        "<b>Режим ИИ-чата</b>\n\n"
        "Пиши что потратил или спрашивай про финансы:\n"
        "— <i>«хватит ли до зарплаты?»</i>\n"
        "— <i>«на что улетает больше всего?»</i>\n"
        "— <i>«кофе 180»</i>\n\n"
        "Выйти: /stop или /cancel",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(F.text == "ИИ-чат")
async def start_ai_chat_button(message: Message, state: FSMContext):
    current = await state.get_state()
    if current == AIState.chatting:
        await message.answer("Уже в режиме чата. Пиши что надо. /stop для выхода.")
        return
    await state.set_state(AIState.chatting)
    await message.answer(
        "<b>Режим ИИ-чата</b>\n\n"
        "Пиши что потратил или спрашивай про финансы:\n"
        "— <i>«хватит ли до зарплаты?»</i>\n"
        "— <i>«на что улетает больше всего?»</i>\n"
        "— <i>«кофе 180»</i>\n\n"
        "Выйти: /stop или /cancel",
        parse_mode="HTML"
    )


@router.message(Command("stop"), AIState.chatting)
@router.message(Command("cancel"), AIState.chatting)
async def stop_ai_chat(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Вышли из чата.", reply_markup=main_menu())


@router.message(AIState.chatting)
async def handle_ai_chat(message: Message, state: FSMContext):
    text = message.text or ""

    if looks_like_question(text):
        await _answer_with_ai(message, text)
        return

    result = parse_transaction_local(text)
    if not result:
        try:
            result = await ai_parse_transaction(text)
        except Exception:
            result = None

    if result:
        await state.update_data(
            ai_type=result["type"],
            ai_amount=result["amount"],
            ai_category=result["category"],
            ai_description=result.get("description", ""),
            prev_state="chatting"
        )
        await state.set_state(AIState.confirming_transaction)
        await message.answer(
            format_confirmation(result),
            parse_mode="HTML",
            reply_markup=confirm_transaction_kb(
                confirm_cb="ai_tx:confirm_chat",
                cancel_cb="ai_tx:cancel_chat",
                edit_cb="ai_tx:edit_chat"
            )
        )
        return

    await _answer_with_ai(message, text)


async def _answer_with_ai(message: Message, text: str):
    await message.answer("Щас подумаю...")
    try:
        from ai_service import chat_with_ai
        stats, payments, salary_days, budgets, planned = await _get_ai_context(message.from_user.id)
        context_extra = _build_reminders_context(salary_days, payments)
        response = await chat_with_ai(
            text, stats, payments,
            context_extra=context_extra,
            budgets=budgets,
            planned_income=planned[:20],
        )
        await message.answer(clean_markdown(response))
    except Exception as e:
        logging.error(f"ai chat error: {e}")
        await message.answer(f"Что-то пошло не так: {str(e)}")


# ─── ПОДТВЕРЖДЕНИЕ / РЕДАКТИРОВАНИЕ КАТЕГОРИИ ────────────────────────────────

@router.callback_query(F.data.in_({"ai_tx:confirm_chat", "ai_tx:confirm_exit"}))
async def confirm_transaction(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        await add_transaction(
            user_id=callback.from_user.id,
            type_=data["ai_type"],
            amount=data["ai_amount"],
            category=data["ai_category"],
            description=data.get("ai_description")
        )
        await callback.message.answer(
            f"<b>Записал.</b> {data['ai_amount']:,.0f} ₽ — {data['ai_category']}",
            parse_mode="HTML",
            reply_markup=main_menu() if callback.data == "ai_tx:confirm_exit" else None
        )
        if callback.data == "ai_tx:confirm_chat":
            await state.set_state(AIState.chatting)
        else:
            await state.clear()
    except Exception as e:
        await callback.message.answer(f"Сломалось: {str(e)}")
        await state.clear()
    await callback.answer()


@router.callback_query(F.data.in_({"ai_tx:edit_chat", "ai_tx:edit_exit"}))
async def edit_category(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(edit_source=callback.data)
    await state.set_state(AIState.editing_category)

    if data.get("ai_type") == "income":
        await callback.message.answer("Выбери категорию дохода:", reply_markup=income_categories_kb("edit_cat"))
    else:
        await callback.message.answer("Выбери категорию расхода:", reply_markup=expense_categories_kb("edit_cat"))
    await callback.answer()


@router.callback_query(F.data.startswith("edit_cat:"), AIState.editing_category)
async def apply_edited_category(callback: CallbackQuery, state: FSMContext):
    new_category = callback.data.split(":", 1)[1]
    data = await state.get_data()
    await state.update_data(ai_category=new_category)

    source = data.get("edit_source", "ai_tx:edit_exit")
    confirm_cb = "ai_tx:confirm_chat" if "chat" in source else "ai_tx:confirm_exit"
    cancel_cb = "ai_tx:cancel_chat" if "chat" in source else "ai_tx:cancel"
    edit_cb = "ai_tx:edit_chat" if "chat" in source else "ai_tx:edit_exit"

    updated = {**data, "ai_category": new_category}
    await callback.message.answer(
        format_confirmation(updated),
        parse_mode="HTML",
        reply_markup=confirm_transaction_kb(confirm_cb, cancel_cb, edit_cb)
    )
    await state.set_state(AIState.confirming_transaction)
    await callback.answer()


@router.callback_query(F.data == "ai_tx:cancel_chat")
async def cancel_chat(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AIState.chatting)
    await callback.message.answer("Не записал. Продолжай.")
    await callback.answer()


@router.callback_query(F.data == "ai_tx:cancel")
async def cancel_exit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Отменено.", reply_markup=main_menu())
    await callback.answer()


# ─── УМНЫЙ ВВОД (вне чата и вне других состояний) ───────────────────────────

MENU_TEXTS = {
    "Статистика", "Платежи", "Бюджеты", "Настройки", "Доходы", "Цели", "История",
    "ИИ-чат", "/start", "/help", "/stop", "/skip", "/history", "/cancel"
}

import re as _re


def _looks_like_date_entry(text: str) -> bool:
    return bool(_re.match(r'^\d{1,2}[./]\d{1,2}', text.strip()))


class PlannedEntryState(StatesGroup):
    waiting_description = State()


@router.message(PlannedEntryState.waiting_description)
async def planned_entry_description(message: Message, state: FSMContext):
    from goals_income import _detect_type_from_desc, _save_planned, ask_type_kb, PlannedIncomeState
    text = message.text.strip() if message.text else ""
    desc = None if text in ("/skip", "") else text
    await state.update_data(description=desc)
    if desc:
        type_ = _detect_type_from_desc(desc)
        await _save_planned(message, state, type_=type_)
    else:
        await message.answer("Это доход или расход?", reply_markup=ask_type_kb())
        await state.set_state(PlannedIncomeState.waiting_type)


@router.message(F.text)
async def smart_input(message: Message, state: FSMContext):
    if not message.text or message.text in MENU_TEXTS or message.text.startswith("/"):
        return

    current_state = await state.get_state()
    if current_state is not None:
        return

    text = message.text.strip()

    # Дата в начале → планируемая запись
    if _looks_like_date_entry(text):
        from goals_income import _parse_planned_entry, _detect_type_from_desc, _save_planned, ask_type_kb, PlannedIncomeState
        parsed = _parse_planned_entry(text)
        if parsed:
            found_date, amount, desc = parsed
            await state.update_data(
                expected_date=found_date.isoformat(),
                amount=amount,
                description=desc,
            )
            if desc:
                type_ = _detect_type_from_desc(desc)
                await _save_planned(message, state, type_=type_)
            else:
                await state.set_state(PlannedEntryState.waiting_description)
                await message.answer(
                    f"📅 <b>{found_date.strftime('%d.%m.%Y')}</b>  {amount:,.0f} ₽\n\n"
                    "Что это? Напиши назначение или /skip:",
                    parse_mode="HTML",
                )
        else:
            await state.set_state(PlannedIncomeState.waiting_input)
            await message.answer(
                "Не нашёл сумму. Напиши полностью: <i>25.03 50000 зарплата</i>",
                parse_mode="HTML",
            )
        return

    # Вопросы → ИИ
    if looks_like_question(text):
        await _answer_with_ai(message, text)
        return

    # Пробуем распознать транзакцию
    result = parse_transaction_local(text)
    if not result:
        try:
            result = await ai_parse_transaction(text)
        except Exception:
            result = None

    if result:
        await state.update_data(
            ai_type=result["type"],
            ai_amount=result["amount"],
            ai_category=result["category"],
            ai_description=result.get("description", ""),
            prev_state="none"
        )
        await state.set_state(AIState.confirming_transaction)
        await message.answer(
            format_confirmation(result),
            parse_mode="HTML",
            reply_markup=confirm_transaction_kb(
                confirm_cb="ai_tx:confirm_exit",
                cancel_cb="ai_tx:cancel",
                edit_cb="ai_tx:edit_exit"
            )
        )
        return

    # Не распознали — идём к ИИ
    await _answer_with_ai(message, text)
