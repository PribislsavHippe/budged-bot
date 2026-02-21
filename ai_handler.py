import re
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from db import add_transaction, get_stats, get_scheduled_payments
from keyboards import (
    main_menu, confirm_transaction_kb,
    expense_categories_kb, income_categories_kb
)
from categorizer import parse_transaction_local
from ai_service import parse_transaction as ai_parse_transaction

router = Router()


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
    emoji = "💸" if result["type"] == "expense" else "💰"
    type_label = "Расход" if result["type"] == "expense" else "Доход"
    desc = result.get("description", "")
    desc_line = f"\n📝 {desc[:60]}" if desc else ""
    return (
        f"{emoji} <b>{type_label}: {result['amount']:,.0f} ₽</b>\n"
        f"📂 {result['category']}"
        f"{desc_line}\n\n"
        f"Всё верно?"
    )


# ─── СВОБОДНЫЙ ЧАТ ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "ai:chat")
async def start_ai_chat(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AIState.chatting)
    await callback.message.answer(
        "💬 <b>Чат с AI</b>\n\n"
        "Задай вопрос о своих финансах или просто напиши что потратил:\n"
        "— «Хватит ли до зарплаты?»\n"
        "— «На что я трачу больше всего?»\n"
        "— «Купил кофе 180»\n\n"
        "Для выхода напиши /stop",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(AIState.chatting)
async def handle_ai_chat(message: Message, state: FSMContext):
    if message.text == "/stop":
        await state.clear()
        await message.answer("Вышли из чата.", reply_markup=main_menu())
        return

    # Сначала проверяем локально (быстро, без AI)
    result = parse_transaction_local(message.text)

    # Если локально не распознали — пробуем через AI
    if not result:
        try:
            result = await ai_parse_transaction(message.text)
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

    # Обычный вопрос — отвечаем через AI
    await message.answer("🤔 Думаю...")
    try:
        from ai_service import chat_with_ai
        stats = await get_stats(message.from_user.id, "month")
        payments = await get_scheduled_payments(message.from_user.id)
        response = await chat_with_ai(message.text, stats, payments)
        await message.answer(clean_markdown(response))
    except Exception as e:
        logging.error(f"chat error: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")


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
        emoji = "💸" if data["ai_type"] == "expense" else "💰"
        await callback.message.answer(
            f"✅ {emoji} <b>Записано!</b>\n"
            f"{data['ai_amount']:,.0f} ₽ — {data['ai_category']}",
            parse_mode="HTML",
            reply_markup=main_menu() if callback.data == "ai_tx:confirm_exit" else None
        )
        if callback.data == "ai_tx:confirm_chat":
            await state.set_state(AIState.chatting)
        else:
            await state.clear()
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {str(e)}")
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
    await callback.message.answer("Ок, не записываю 👂")
    await callback.answer()


@router.callback_query(F.data == "ai_tx:cancel")
async def cancel_exit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Отменено.", reply_markup=main_menu())
    await callback.answer()


# ─── УМНЫЙ ВВОД (вне чата и вне других состояний) ───────────────────────────

MENU_TEXTS = {
    "📊 Статистика", "📅 Платежи", "🎯 Бюджеты", "⚙️ Настройки",
    "💸 Добавить расход", "💰 Добавить доход",
    "/start", "/help", "/stop", "/skip"
}


@router.message(F.text)
async def smart_input(message: Message, state: FSMContext):
    if not message.text or message.text in MENU_TEXTS or message.text.startswith("/"):
        return

    current_state = await state.get_state()
    if current_state is not None:
        return

    # Сначала локальный парсер (быстро)
    result = parse_transaction_local(message.text)

    # Если локально не вышло — AI
    if not result:
        try:
            result = await ai_parse_transaction(message.text)
        except Exception:
            result = None

    if not result:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Спросить AI", callback_data="ai:chat")]
        ])
        await message.answer(
            "Не понял что записать 🤔\n\n"
            "Напиши например:\n"
            "<i>«купил кофе 180»</i> или <i>«получил зарплату 50000»</i>\n\n"
            "Или задай вопрос AI:",
            parse_mode="HTML",
            reply_markup=kb
        )
        return

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
