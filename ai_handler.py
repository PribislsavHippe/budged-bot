import re
import io
import logging
from datetime import date, timedelta
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from db import (
    add_transaction, get_stats, get_scheduled_payments,
    get_salary_days, get_budgets, get_planned_income
)
from keyboards import (
    main_menu, confirm_transaction_kb,
    expense_categories_kb, income_categories_kb
)
from categorizer import parse_transaction_local, looks_like_question
from ai_service import (
    parse_transaction as ai_parse_transaction,
    parse_bulk_transactions,
    transcribe_voice,
    chat_with_ai,
    get_smart_dashboard,
)

router = Router()


class AIState(StatesGroup):
    chatting = State()
    confirming_transaction = State()
    editing_category = State()
    confirming_bulk = State()


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


def format_bulk_preview(items: list[dict]) -> str:
    lines = []
    total_exp = sum(i["amount"] for i in items if i["type"] == "expense")
    total_inc = sum(i["amount"] for i in items if i["type"] == "income")

    for i, item in enumerate(items, 1):
        icon = "📤" if item["type"] == "expense" else "📥"
        desc = f" — {item['description'][:25]}" if item.get("description") else ""
        lines.append(f"{i}. {icon} {item['category']}: <b>{item['amount']:,.0f} ₽</b>{desc}")

    text = "\n".join(lines)
    if total_exp > 0:
        text += f"\n\n<b>Расходов: {total_exp:,.0f} ₽</b>"
    if total_inc > 0:
        text += f"\n<b>Доходов: {total_inc:,.0f} ₽</b>"
    return text


async def _get_ai_context(user_id: int):
    stats = await get_stats(user_id, "month")
    payments = await get_scheduled_payments(user_id)
    salary_days = await get_salary_days(user_id)
    budgets = await get_budgets(user_id)
    now = date.today()
    try:
        planned = await get_planned_income(
            user_id,
            from_date=now.isoformat(),
            to_date=(now + timedelta(days=365)).isoformat()
        )
    except Exception:
        planned = []
    return stats, payments, salary_days, budgets, planned


def _build_reminders_context(salary_days: list, payments: list) -> str:
    parts = []
    if salary_days:
        parts.append(f"Дни зарплаты: {', '.join(map(str, sorted(salary_days)))}-е числа.")
    if payments:
        by_day = sorted(payments, key=lambda p: p["day_of_month"])
        lines = [f"{p['day_of_month']}-е: {p['name']} {p['amount']:,.0f} ₽" for p in by_day]
        parts.append("Регулярные платежи: " + "; ".join(lines))
    return " ".join(parts) if parts else ""


# ─── ГОЛОСОВЫЕ СООБЩЕНИЯ ─────────────────────────────────────────────────────

@router.message(F.voice)
async def handle_voice(message: Message, state: FSMContext, bot: Bot):
    """Транскрибирует голосовое сообщение и обрабатывает как текст."""
    await message.answer("Слушаю...")
    try:
        file = await bot.get_file(message.voice.file_id)
        audio_bytes = await bot.download_file(file.file_path)
        audio_data = audio_bytes.read() if hasattr(audio_bytes, 'read') else bytes(audio_bytes)

        text = await transcribe_voice(audio_data, "voice.ogg")
        if not text:
            await message.answer("Не смог разобрать голосовое. Попробуй написать текстом.")
            return

        await message.answer(f"<i>Распознал: «{text}»</i>", parse_mode="HTML")

        # Передаём дальше как текст
        message.text = text
        await _process_text_input(message, state, text)

    except Exception as e:
        logging.error(f"voice handler error: {e}")
        await message.answer(f"Ошибка с голосовым: {str(e)}")


# ─── УМНЫЙ ДАШБОРД ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "stats:dashboard")
async def smart_dashboard(callback: CallbackQuery):
    await callback.message.answer("Считаю...")
    try:
        stats, payments, salary_days, _, planned = await _get_ai_context(callback.from_user.id)
        text = await get_smart_dashboard(stats, payments, salary_days, planned)
        if text:
            await callback.message.answer(clean_markdown(text))
        else:
            await callback.message.answer("Пока данных маловато. Вноси пару дней — тогда дам нормальный расклад.")
    except Exception as e:
        logging.error(f"dashboard error: {e}")
        await callback.message.answer(f"Ошибка: {str(e)}")
    await callback.answer()


# ─── СВОБОДНЫЙ ЧАТ ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "ai:chat")
async def start_ai_chat_callback(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AIState.chatting)
    await callback.message.answer(
        "<b>Режим ИИ-чата</b>\n\n"
        "Пиши что потратил или задавай вопросы:\n"
        "— <i>«хватит ли до зарплаты?»</i>\n"
        "— <i>«кофе 180, такси 350, обед 600»</i>\n"
        "— <i>«на что улетает больше всего?»</i>\n\n"
        "Выйти: /stop",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(F.text == "ИИ-чат")
async def start_ai_chat_button(message: Message, state: FSMContext):
    current = await state.get_state()
    if current == AIState.chatting:
        await message.answer("Уже в чате. /stop для выхода.")
        return
    await state.set_state(AIState.chatting)
    await message.answer(
        "<b>Режим ИИ-чата</b>\n\n"
        "Пиши что потратил или задавай вопросы:\n"
        "— <i>«хватит ли до зарплаты?»</i>\n"
        "— <i>«кофе 180, такси 350, обед 600»</i>\n"
        "— <i>«на что улетает больше всего?»</i>\n\n"
        "Выйти: /stop",
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
    await _process_text_input(message, state, text, in_chat=True)


# ─── ОСНОВНАЯ ЛОГИКА ОБРАБОТКИ ТЕКСТА ────────────────────────────────────────

async def _process_text_input(message: Message, state: FSMContext, text: str, in_chat: bool = False):
    """Общая логика для текста и голоса: вопрос → AI, транзакции → bulk или одиночная."""

    if looks_like_question(text):
        await _answer_with_ai(message, text)
        return

    # Сначала быстрый локальный парсер
    single = parse_transaction_local(text)

    if single:
        # Один расход/доход распознан локально
        await _confirm_single(message, state, single, in_chat)
        return

    # Пробуем bulk через AI (может быть несколько трат)
    try:
        bulk = await parse_bulk_transactions(text)
    except Exception:
        bulk = []

    if len(bulk) > 1:
        await _show_bulk_preview(message, state, bulk, in_chat)
        return

    if len(bulk) == 1:
        await _confirm_single(message, state, bulk[0], in_chat)
        return

    # Совсем ничего не распознали → AI
    await _answer_with_ai(message, text)


async def _confirm_single(message: Message, state: FSMContext, result: dict, in_chat: bool):
    confirm_cb = "ai_tx:confirm_chat" if in_chat else "ai_tx:confirm_exit"
    cancel_cb = "ai_tx:cancel_chat" if in_chat else "ai_tx:cancel"
    edit_cb = "ai_tx:edit_chat" if in_chat else "ai_tx:edit_exit"

    await state.update_data(
        ai_type=result["type"],
        ai_amount=result["amount"],
        ai_category=result["category"],
        ai_description=result.get("description", ""),
        prev_state="chatting" if in_chat else "none"
    )
    await state.set_state(AIState.confirming_transaction)
    await message.answer(
        format_confirmation(result),
        parse_mode="HTML",
        reply_markup=confirm_transaction_kb(confirm_cb, cancel_cb, edit_cb)
    )


async def _show_bulk_preview(message: Message, state: FSMContext, items: list[dict], in_chat: bool):
    """Показывает список распознанных транзакций с кнопками."""
    await state.update_data(bulk_items=items, bulk_in_chat=in_chat)
    await state.set_state(AIState.confirming_bulk)

    confirm_cb = "bulk:confirm"
    cancel_cb = "bulk:cancel_chat" if in_chat else "bulk:cancel"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Записать всё", callback_data=confirm_cb),
            InlineKeyboardButton(text="Отмена", callback_data=cancel_cb),
        ]
    ])

    await message.answer(
        f"Разобрал {len(items)} записей:\n\n"
        f"{format_bulk_preview(items)}\n\n"
        f"Записываем всё?",
        parse_mode="HTML",
        reply_markup=kb
    )


@router.callback_query(F.data == "bulk:confirm")
async def bulk_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    items = data.get("bulk_items", [])
    in_chat = data.get("bulk_in_chat", False)

    saved = 0
    for item in items:
        try:
            await add_transaction(
                user_id=callback.from_user.id,
                type_=item["type"],
                amount=item["amount"],
                category=item["category"],
                description=item.get("description")
            )
            saved += 1
        except Exception as e:
            logging.error(f"bulk save error: {e}")

    total_exp = sum(i["amount"] for i in items if i["type"] == "expense")
    total_inc = sum(i["amount"] for i in items if i["type"] == "income")
    summary = []
    if total_exp > 0:
        summary.append(f"расходов {total_exp:,.0f} ₽")
    if total_inc > 0:
        summary.append(f"доходов {total_inc:,.0f} ₽")

    await callback.message.answer(
        f"<b>Записал {saved} транзакций</b> — {', '.join(summary)}.",
        parse_mode="HTML",
        reply_markup=main_menu() if not in_chat else None
    )
    if in_chat:
        await state.set_state(AIState.chatting)
    else:
        await state.clear()
    await callback.answer()


@router.callback_query(F.data.in_({"bulk:cancel", "bulk:cancel_chat"}))
async def bulk_cancel(callback: CallbackQuery, state: FSMContext):
    in_chat = callback.data == "bulk:cancel_chat"
    if in_chat:
        await state.set_state(AIState.chatting)
        await callback.message.answer("Не записал. Продолжай.")
    else:
        await state.clear()
        await callback.message.answer("Отменено.", reply_markup=main_menu())
    await callback.answer()


async def _answer_with_ai(message: Message, text: str):
    await message.answer("Щас подумаю...")
    try:
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
        await message.answer(f"Что-то сломалось: {str(e)}")


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
    in_chat = "chat" in source
    confirm_cb = "ai_tx:confirm_chat" if in_chat else "ai_tx:confirm_exit"
    cancel_cb = "ai_tx:cancel_chat" if in_chat else "ai_tx:cancel"
    edit_cb = "ai_tx:edit_chat" if in_chat else "ai_tx:edit_exit"

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


# ─── УМНЫЙ ВВОД (вне чата, вне других состояний) ─────────────────────────────

MENU_TEXTS = {
    "Статистика", "Платежи", "Бюджеты", "Настройки", "Доходы", "Цели", "История",
    "ИИ-чат", "/start", "/help", "/stop", "/skip", "/history", "/cancel"
}


def _looks_like_date_entry(text: str) -> bool:
    import re as _re
    return bool(_re.match(r'^\d{1,2}[./]\d{1,2}', text.strip()))


class PlannedEntryState(StatesGroup):
    waiting_description = State()



# ─── КОМАНДА /week — БЮДЖЕТ НА ТЕКУЩУЮ НЕДЕЛЮ ───────────────────────────────

@router.message(Command("week"))
async def week_budget_command(message: Message):
    """Показывает бюджет и совет на текущую неделю."""
    await message.answer("Считаю бюджет на эту неделю...")
    try:
        from weekly_advice import handle_weekly_advice_request
        text = await handle_weekly_advice_request(message.from_user.id)
        await message.answer(text, parse_mode="HTML")
    except Exception as e:
        logging.error(f"/week command error: {e}")
        await message.answer(f"Ошибка: {str(e)}")


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
                    f"<b>{found_date.strftime('%d.%m.%Y')}</b>  {amount:,.0f} ₽\n\nЧто это? Напиши или /skip:",
                    parse_mode="HTML",
                )
        else:
            from goals_income import PlannedIncomeState
            await state.set_state(PlannedIncomeState.waiting_input)
            await message.answer("Не нашёл сумму. Напиши так: <i>25.03 50000 зарплата</i>", parse_mode="HTML")
        return

    await _process_text_input(message, state, text, in_chat=False)
