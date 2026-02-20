from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from db import add_transaction, get_stats, get_scheduled_payments
from keyboards import main_menu
from ai_service import parse_transaction, get_ai_advice, chat_with_ai

router = Router()


class AIState(StatesGroup):
    chatting = State()
    confirming_transaction = State()


# ─── AI СОВЕТНИК (кнопка в меню) ─────────────────────────────────────────────

@router.message(F.text == "🤖 AI Советник")
async def ai_advisor(message: Message):
    await message.answer("🤖 Анализирую твои финансы...")

    stats = await get_stats(message.from_user.id, "month")
    advice = await get_ai_advice(stats, message.from_user.first_name or "друг")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Задать вопрос AI", callback_data="ai:chat")],
    ])
    await message.answer(f"🧠 <b>Персональный анализ:</b>\n\n{advice}", parse_mode="HTML", reply_markup=kb)


# ─── ОТКРЫТЬ СВОБОДНЫЙ ЧАТ ───────────────────────────────────────────────────

@router.callback_query(F.data == "ai:chat")
async def start_ai_chat(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AIState.chatting)
    await callback.message.answer(
        "💬 <b>Режим чата с AI</b>\n\n"
        "Задай любой вопрос о своих финансах:\n"
        "— «Хватит ли мне до зарплаты?»\n"
        "— «На что я трачу больше всего?»\n"
        "— «Как сэкономить на еде?»\n\n"
        "Для выхода нажми /stop",
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
    stats = await get_stats(message.from_user.id, "month")
    payments = await get_scheduled_payments(message.from_user.id)
    response = await chat_with_ai(message.text, stats, payments)
    await message.answer(response)


# ─── УМНЫЙ ВВОД ──────────────────────────────────────────────────────────────
# Ловит любое текстовое сообщение, которое не совпало с кнопками меню,
# и пробует распознать его как транзакцию

MENU_TEXTS = {
    "💸 Добавить расход", "💰 Добавить доход",
    "📊 Статистика", "📅 Платежи",
    "🎯 Бюджеты", "⚙️ Настройки", "🤖 AI Советник",
    "/start", "/help", "/stop", "/skip"
}

@router.message(F.text)
async def smart_input(message: Message, state: FSMContext):
    # Пропускаем кнопки меню и команды
    if message.text in MENU_TEXTS or message.text.startswith("/"):
        return

    # Не перехватываем если уже в каком-то состоянии
    current_state = await state.get_state()
    if current_state is not None:
        return

    # Пробуем распознать транзакцию
    result = await parse_transaction(message.text)

    if not result:
        # Не похоже на транзакцию — предлагаем AI чат
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🤖 Спросить AI", callback_data="ai:chat")],
        ])
        await message.answer(
            "Не понял что записать 🤔\n\n"
            "Попробуй написать например:\n"
            "<i>«потратил 500 на такси»</i>\n"
            "<i>«кофе 180»</i>\n"
            "<i>«получил зарплату 80000»</i>\n\n"
            "Или задай вопрос AI советнику:",
            parse_mode="HTML",
            reply_markup=kb
        )
        return

    # Распознали транзакцию — показываем на подтверждение
    t_type = "💸 Расход" if result["type"] == "expense" else "💰 Доход"
    amount = result.get("amount", 0)
    category = result.get("category", "🛒 Прочее")
    description = result.get("description", "")

    # Сохраняем в state для подтверждения
    await state.update_data(
        ai_type=result["type"],
        ai_amount=amount,
        ai_category=category,
        ai_description=description
    )
    await state.set_state(AIState.confirming_transaction)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Верно, сохранить", callback_data="ai_tx:confirm"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="ai_tx:cancel"),
        ]
    ])

    await message.answer(
        f"🧠 <b>Распознал транзакцию:</b>\n\n"
        f"{t_type}\n"
        f"💰 Сумма: <b>{amount:,.0f} ₽</b>\n"
        f"📂 Категория: <b>{category}</b>\n"
        f"{'📝 ' + description if description else ''}\n\n"
        f"Всё верно?",
        parse_mode="HTML",
        reply_markup=kb
    )


@router.callback_query(F.data == "ai_tx:confirm")
async def confirm_ai_transaction(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()

    await add_transaction(
        user_id=callback.from_user.id,
        type_=data["ai_type"],
        amount=data["ai_amount"],
        category=data["ai_category"],
        description=data.get("ai_description")
    )

    emoji = "💸" if data["ai_type"] == "expense" else "💰"
    await callback.message.answer(
        f"✅ {emoji} <b>Записано!</b>\n\n"
        f"<b>{data['ai_amount']:,.0f} ₽</b> — {data['ai_category']}",
        parse_mode="HTML",
        reply_markup=main_menu()
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "ai_tx:cancel")
async def cancel_ai_transaction(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Отменено.", reply_markup=main_menu())
    await callback.answer()
