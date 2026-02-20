import re
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from db import add_transaction, get_stats, get_scheduled_payments
from keyboards import main_menu
from ai_service import parse_transaction, chat_with_ai


router = Router()


class AIState(StatesGroup):
    chatting = State()
    confirming_transaction = State()


def clean_markdown(text: str) -> str:
    """Убирает markdown-форматирование из ответа AI."""
    # Убираем жирный и курсив
    text = re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', text)
    # Убираем заголовки ##
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Убираем inline code `код`
    text = re.sub(r'`(.+?)`', r'\1', text)
    # Убираем горизонтальные линии ---
    text = re.sub(r'^[-_*]{3,}$', '', text, flags=re.MULTILINE)
    # Убираем двойные пустые строки
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ─── СВОБОДНЫЙ ЧАТ ───────────────────────────────────────────────────────────

@router.message(F.text == "🤖 AI Советник")
async def ai_advisor_button(message: Message, state: FSMContext):
    """Кнопка AI Советник — на случай если осталась у кого-то в кэше."""
    await start_chat(message, state)


async def start_chat(message: Message, state: FSMContext):
    await state.set_state(AIState.chatting)
    await message.answer(
        "💬 <b>Чат с AI финансистом</b>\n\n"
        "Задай любой вопрос или просто напиши что потратил:\n"
        "— «Хватит ли мне до зарплаты?»\n"
        "— «На что я трачу больше всего?»\n"
        "— «Потратил 500 на такси»\n\n"
        "Для выхода напиши /stop",
        parse_mode="HTML"
    )


@router.callback_query(F.data == "ai:chat")
async def start_ai_chat(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AIState.chatting)
    await callback.message.answer(
        "💬 <b>Чат с AI финансистом</b>\n\n"
        "Задай любой вопрос или просто напиши что потратил:\n"
        "— «Хватит ли мне до зарплаты?»\n"
        "— «На что я трачу больше всего?»\n"
        "— «Потратил 500 на такси»\n\n"
        "Для выхода напиши /stop",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(AIState.chatting)
async def handle_ai_chat(message: Message, state: FSMContext):
    if message.text == "/stop":
        await state.clear()
        await message.answer("Вышли из чата. Чем ещё помочь?", reply_markup=main_menu())
        return

    await message.answer("🤔 Думаю...")

    try:
        # Сначала проверяем — вдруг это транзакция
        transaction = await parse_transaction(message.text)

        if transaction:
            # Распознали транзакцию — предлагаем записать
            t_type = "💸 Расход" if transaction["type"] == "expense" else "💰 Доход"
            amount = transaction.get("amount", 0)
            category = transaction.get("category", "🛒 Прочее")
            description = transaction.get("description", "")

            await state.update_data(
                ai_type=transaction["type"],
                ai_amount=amount,
                ai_category=category,
                ai_description=description,
                prev_state="chatting"
            )
            await state.set_state(AIState.confirming_transaction)

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Записать", callback_data="ai_tx:confirm"),
                    InlineKeyboardButton(text="❌ Не надо", callback_data="ai_tx:cancel_chat"),
                ]
            ])
            await message.answer(
                f"🧠 Распознал транзакцию:\n\n"
                f"{t_type} — <b>{amount:,.0f} ₽</b>\n"
                f"📂 {category}"
                f"{chr(10) + '📝 ' + description if description else ''}\n\n"
                f"Записать?",
                parse_mode="HTML",
                reply_markup=kb
            )
            return

        # Обычный вопрос — отвечаем через AI
        stats = await get_stats(message.from_user.id, "month")
        payments = await get_scheduled_payments(message.from_user.id)
        response = await chat_with_ai(message.text, stats, payments)
        clean_response = clean_markdown(response)
        await message.answer(clean_response)

    except Exception as e:
        logging.error(f"handle_ai_chat error: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")


@router.callback_query(F.data == "ai_tx:confirm")
async def confirm_ai_transaction(callback: CallbackQuery, state: FSMContext):
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
            f"{data['ai_amount']:,.0f} ₽ — {data['ai_category']}\n\n"
            f"Продолжай, я слушаю 👂",
            parse_mode="HTML"
        )
        # Возвращаемся в режим чата
        await state.set_state(AIState.chatting)
    except Exception as e:
        logging.error(f"confirm_ai_transaction error: {e}")
        await callback.message.answer(f"❌ Ошибка сохранения: {str(e)}")
    await callback.answer()


@router.callback_query(F.data == "ai_tx:cancel_chat")
async def cancel_ai_transaction_chat(callback: CallbackQuery, state: FSMContext):
    """Отмена записи транзакции с возвратом в чат."""
    await state.set_state(AIState.chatting)
    await callback.message.answer("Ок, не записываю. Продолжай 👂")
    await callback.answer()


# ─── УМНЫЙ ВВОД (вне чата) ───────────────────────────────────────────────────

MENU_TEXTS = {
    "💸 Добавить расход", "💰 Добавить доход",
    "📊 Статистика", "📅 Платежи",
    "🎯 Бюджеты", "⚙️ Настройки", "🤖 AI Советник",
    "/start", "/help", "/stop", "/skip"
}


@router.message(F.text)
async def smart_input(message: Message, state: FSMContext):
    """Ловит свободный текст вне меню — пробует распознать транзакцию."""
    if message.text in MENU_TEXTS or message.text.startswith("/"):
        return

    current_state = await state.get_state()
    if current_state is not None:
        return

    try:
        result = await parse_transaction(message.text)
    except Exception as e:
        logging.error(f"smart_input parse error: {e}")
        result = None

    if not result:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Спросить AI", callback_data="ai:chat")],
        ])
        await message.answer(
            "Не понял что записать 🤔\n\n"
            "Попробуй написать например:\n"
            "<i>«потратил 500 на такси»</i>\n"
            "<i>«кофе 180»</i>\n"
            "<i>«получил зарплату 80000»</i>\n\n"
            "Или задай вопрос AI:",
            parse_mode="HTML",
            reply_markup=kb
        )
        return

    amount = result.get("amount", 0)
    category = result.get("category", "🛒 Прочее")
    description = result.get("description", "")
    t_type = "💸 Расход" if result["type"] == "expense" else "💰 Доход"

    await state.update_data(
        ai_type=result["type"],
        ai_amount=amount,
        ai_category=category,
        ai_description=description,
        prev_state="none"
    )
    await state.set_state(AIState.confirming_transaction)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Верно, сохранить", callback_data="ai_tx:confirm_exit"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="ai_tx:cancel"),
        ]
    ])
    await message.answer(
        f"🧠 <b>Распознал транзакцию:</b>\n\n"
        f"{t_type} — <b>{amount:,.0f} ₽</b>\n"
        f"📂 {category}"
        f"{chr(10) + '📝 ' + description if description else ''}\n\n"
        f"Всё верно?",
        parse_mode="HTML",
        reply_markup=kb
    )


@router.callback_query(F.data == "ai_tx:confirm_exit")
async def confirm_ai_transaction_exit(callback: CallbackQuery, state: FSMContext):
    """Подтверждение транзакции с возвратом в главное меню."""
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
            reply_markup=main_menu()
        )
    except Exception as e:
        logging.error(f"confirm error: {e}")
        await callback.message.answer(f"❌ Ошибка: {str(e)}", reply_markup=main_menu())
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "ai_tx:cancel")
async def cancel_ai_transaction(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Отменено.", reply_markup=main_menu())
    await callback.answer()
