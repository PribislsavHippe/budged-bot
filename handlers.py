"""Все хендлеры бота. Принцип: записываем сразу, отмена — одной кнопкой.

Никаких многошаговых диалогов, кроме единственного вопроса при онбординге.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

import db
import parser as p

router = Router()

KIND_SIGN = {"income": 1, "expense": -1}
KIND_EMOJI = {"income": "➕", "expense": "➖", "adjustment": "🔧"}


class Onboarding(StatesGroup):
    waiting_balances = State()
    waiting_account_choice = State()


# ─── форматирование ──────────────────────────────────────────────────────────

def fmt(v: float) -> str:
    """12345.0 → «12 345», 250.5 → «250,50»"""
    if v == int(v):
        s = f"{int(v):,}".replace(",", " ")
    else:
        s = f"{v:,.2f}".replace(",", " ").replace(".", ",")
    return s


def fmt_balances(b: dict) -> str:
    return (
        f"💵 Наличные: <b>{fmt(b['cash'])} ₽</b>\n"
        f"💳 Карта: <b>{fmt(b['card'])} ₽</b>\n"
        f"Всего: <b>{fmt(b['total'])} ₽</b>"
    )


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="💰 Баланс"), KeyboardButton(text="📋 История")]],
        resize_keyboard=True,
    )


def entry_line(e: dict) -> str:
    amount = float(e["signed_amount"])
    emoji = KIND_EMOJI.get(e["kind"], "•")
    acc = db.ACCOUNT_LABELS[e["account"]]
    sign = "+" if amount > 0 else "−"
    label = e["category"] if e["kind"] != "adjustment" else "Сверка"
    note = f" ({e['note']})" if e.get("note") and e["kind"] != "adjustment" else ""
    return f"{emoji} {sign}{fmt(abs(amount))} ₽ · {label} · {acc}{note}"


def undo_kb(entry_ids: list[int], toggle_entry: dict | None = None) -> InlineKeyboardMarkup:
    rows = []
    if toggle_entry is not None:
        other = db.CASH if toggle_entry["account"] == db.CARD else db.CARD
        rows.append([InlineKeyboardButton(
            text=f"Перенести на {db.ACCOUNT_LABELS[other].lower()}",
            callback_data=f"acc:{toggle_entry['id']}",
        )])
    ids = ",".join(str(i) for i in entry_ids)
    rows.append([InlineKeyboardButton(text="↩️ Отменить", callback_data=f"undo:{ids}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─── /start и онбординг ──────────────────────────────────────────────────────

ONBOARD_PROMPT = (
    "<b>Шаг 1 из 2.</b> Сколько у тебя сейчас денег?\n\n"
    "Напиши, например:\n"
    "<i>наличными 5000, на карте 12000</i>\n\n"
    "Если всё в одном месте — просто <i>на карте 17000</i>.\n"
    "Если наличных нет — <i>наличными 0</i>. С этой точки считаю каждый рубль."
)


def _welcome_text(name: str) -> str:
    return (
        f"Привет, {name}! Я — точный учёт твоих денег.\n\n"
        "— пишешь <i>«чай 500»</i> или <i>«кофе 200»</i> — записываю сразу\n"
        "— пересылаешь сообщение банка о чаевых — записываю сам\n"
        "— в любой момент знаешь, сколько наличными и сколько на карте\n\n"
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = await db.get_or_create_user(
        message.from_user.id, message.from_user.username, message.from_user.first_name
    )
    if user.get("onboarded"):
        balances = await db.get_balances(message.from_user.id)
        await message.answer(fmt_balances(balances), reply_markup=main_menu())
        return
    name = user.get("first_name") or "друг"
    await message.answer(_welcome_text(name) + ONBOARD_PROMPT, reply_markup=main_menu())
    await state.set_state(Onboarding.waiting_balances)


async def _finish_onboarding_step1(message: Message, state: FSMContext, user_id: int, stated: dict):
    """Записывает стартовые остатки и показывает шаг 2."""
    for account in db.ACCOUNTS:
        await db.add_entry(
            user_id, "adjustment", account, stated.get(account, 0.0),
            category="Сверка", note="стартовый баланс",
        )
    await db.set_onboarded(user_id)
    await state.clear()

    balances = await db.get_balances(user_id)
    await message.answer(
        "Записал стартовую точку.\n\n" + fmt_balances(balances) + "\n\n"
        "<b>Шаг 2 из 2.</b> Проверим на деле — запиши первую операцию:\n"
        "<i>чай 500</i> — чаевые наличными\n"
        "<i>зп 30000</i> — зарплата на карту\n"
        "<i>кофе 200</i> — трата с карты\n\n"
        "Или перешли мне сообщение банка о чаевых — разберу сам.",
        reply_markup=main_menu(),
    )


@router.message(Onboarding.waiting_balances)
async def onboarding_balances(message: Message, state: FSMContext):
    text = message.text or ""
    stated = p.parse_reconciliation(text)
    if stated is not None:
        await _finish_onboarding_step1(message, state, message.from_user.id, stated)
        return

    amount = p.extract_amount(text)
    if amount is not None and p.detect_account(text) is None:
        # Написал просто число — не гадаем, спрашиваем кнопками.
        await state.update_data(pending_amount=amount)
        await state.set_state(Onboarding.waiting_account_choice)
        await message.answer(
            f"Понял, <b>{fmt(amount)} ₽</b>. Где эти деньги?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="💵 Наличные", callback_data="onb_acc:cash"),
                InlineKeyboardButton(text="💳 Карта", callback_data="onb_acc:card"),
            ]]),
        )
        return

    await message.answer("Не разобрал. Пример:\n<i>наличными 5000, на карте 12000</i>")


@router.callback_query(F.data.startswith("onb_acc:"), Onboarding.waiting_account_choice)
async def onboarding_account_chosen(callback: CallbackQuery, state: FSMContext):
    account = callback.data.split(":", 1)[1]
    data = await state.get_data()
    amount = data.get("pending_amount")
    if amount is None:
        await state.set_state(Onboarding.waiting_balances)
        await callback.message.answer(ONBOARD_PROMPT)
        await callback.answer()
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await _finish_onboarding_step1(callback.message, state, callback.from_user.id, {account: amount})
    await callback.answer()


@router.message(Onboarding.waiting_account_choice)
async def onboarding_account_text(message: Message, state: FSMContext):
    """Вместо кнопки написал текстом: «карта», «нал» — тоже понимаем."""
    account = p.detect_account(message.text or "")
    data = await state.get_data()
    amount = data.get("pending_amount")
    if account is not None and amount is not None:
        await _finish_onboarding_step1(message, state, message.from_user.id, {account: amount})
        return
    # Может, прислал полноценную сверку — разберём как на шаге 1
    await state.set_state(Onboarding.waiting_balances)
    await onboarding_balances(message, state)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "<b>Как я работаю</b>\n\n"
        "Доход: <i>чай 500</i>, <i>смена 2500</i>, <i>зп 30000</i>\n"
        "Расход: <i>кофе 200</i>, <i>такси 350 нал</i>\n"
        "Несколько сразу: <i>кофе 200, такси 350, обед 600</i>\n\n"
        "Сверка: <i>наличными 3200, на карте 8100</i> — скажу, сходится ли.\n"
        "Чаевые от банка: перешли мне сообщение банка — запишу сам.\n\n"
        "💰 Баланс — сколько денег сейчас\n"
        "📋 История — последние записи\n"
        "/undo — отменить последнюю запись\n"
        "/reset — начать учёт заново"
    )


# ─── баланс и история ────────────────────────────────────────────────────────

@router.message(F.text == "💰 Баланс")
async def show_balance(message: Message):
    balances = await db.get_balances(message.from_user.id)
    await message.answer(fmt_balances(balances), reply_markup=main_menu())


@router.message(F.text == "📋 История")
async def show_history(message: Message):
    entries = await db.get_recent_entries(message.from_user.id, limit=15)
    if not entries:
        await message.answer("Пока пусто. Напиши первую операцию: <i>чай 500</i>")
        return
    from datetime import datetime, timedelta, timezone
    msk = timezone(timedelta(hours=3))
    lines = []
    for e in entries:
        dt = datetime.fromisoformat(e["created_at"].replace("Z", "+00:00")).astimezone(msk)
        lines.append(f"<i>{dt.strftime('%d.%m %H:%M')}</i>  {entry_line(e)}")
    await message.answer(
        "<b>Последние записи</b>\n\n" + "\n".join(lines) + "\n\n/undo — отменить последнюю"
    )


@router.message(Command("undo"))
async def cmd_undo(message: Message):
    entries = await db.get_recent_entries(message.from_user.id, limit=1)
    if not entries:
        await message.answer("Отменять нечего — журнал пуст.")
        return
    entry = entries[0]
    await db.delete_entry(entry["id"], message.from_user.id)
    balances = await db.get_balances(message.from_user.id)
    await message.answer("Отменил:\n" + entry_line(entry) + "\n\n" + fmt_balances(balances))


# ─── сброс ───────────────────────────────────────────────────────────────────

@router.message(Command("reset"))
async def cmd_reset(message: Message):
    await message.answer(
        "Удалить <b>весь</b> журнал и начать заново?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Да, удалить всё", callback_data="reset:yes"),
            InlineKeyboardButton(text="Отмена", callback_data="reset:no"),
        ]]),
    )


@router.callback_query(F.data == "reset:yes")
async def reset_yes(callback: CallbackQuery, state: FSMContext):
    db.supabase.table("entries").delete().eq("user_id", callback.from_user.id).execute()
    await state.set_state(Onboarding.waiting_balances)
    await callback.message.edit_text("Журнал очищен.")
    await callback.message.answer(ONBOARD_PROMPT)
    await callback.answer()


@router.callback_query(F.data == "reset:no")
async def reset_no(callback: CallbackQuery):
    await callback.message.edit_text("Отмена — ничего не удалял.")
    await callback.answer()


# ─── кнопки под записями ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("undo:"))
async def cb_undo(callback: CallbackQuery):
    ids = [int(i) for i in callback.data.split(":", 1)[1].split(",") if i]
    deleted = 0
    for entry_id in ids:
        if await db.delete_entry(entry_id, callback.from_user.id):
            deleted += 1
    if not deleted:
        await callback.answer("Уже отменено", show_alert=True)
        return
    balances = await db.get_balances(callback.from_user.id)
    await callback.message.edit_text("↩️ Отменено.\n\n" + fmt_balances(balances))
    await callback.answer()


@router.callback_query(F.data.startswith("acc:"))
async def cb_toggle_account(callback: CallbackQuery):
    entry_id = int(callback.data.split(":", 1)[1])
    entry = await db.get_entry(entry_id, callback.from_user.id)
    if entry is None:
        await callback.answer("Запись уже удалена", show_alert=True)
        return
    other = db.CASH if entry["account"] == db.CARD else db.CARD
    entry = await db.update_entry_account(entry_id, callback.from_user.id, other)
    balances = await db.get_balances(callback.from_user.id)
    await callback.message.edit_text(
        entry_line(entry) + "\n\n" + fmt_balances(balances),
        reply_markup=undo_kb([entry_id], toggle_entry=entry),
    )
    await callback.answer(f"Перенёс на {db.ACCOUNT_LABELS[other].lower()}")


# ─── кнопки старой версии бота ───────────────────────────────────────────────

LEGACY_BUTTONS = {"Статистика", "Платежи", "Бюджеты", "Настройки", "Доходы", "Цели", "ИИ-чат"}


@router.message(F.text == "История")
async def legacy_history(message: Message):
    await show_history(message)


@router.message(F.text.in_(LEGACY_BUTTONS))
async def legacy_button(message: Message, state: FSMContext):
    user = await db.get_or_create_user(
        message.from_user.id, message.from_user.username, message.from_user.first_name
    )
    if not user.get("onboarded"):
        name = user.get("first_name") or "друг"
        await message.answer(
            "Я обновился и стал проще и надёжнее.\n\n" + _welcome_text(name) + ONBOARD_PROMPT,
            reply_markup=main_menu(),
        )
        await state.set_state(Onboarding.waiting_balances)
        return
    await message.answer(
        "Этого раздела больше нет — я теперь простой и надёжный учёт денег.\n"
        "💰 Баланс и 📋 История — на клавиатуре, /help — что я умею.",
        reply_markup=main_menu(),
    )


# ─── главный обработчик текста ───────────────────────────────────────────────

async def _save_bank_tips(message: Message, tips: float):
    """Чаевые из банковского уведомления → доход на карту."""
    entry = await db.add_entry(
        message.from_user.id, "income", db.CARD, tips,
        category="Чаевые", note="из банка",
    )
    balances = await db.get_balances(message.from_user.id)
    await message.answer(
        f"➕ Чаевые <b>{fmt(tips)} ₽</b> → карта\n\n" + fmt_balances(balances),
        reply_markup=undo_kb([entry["id"]], toggle_entry=entry),
    )


@router.message(F.text)
async def handle_text(message: Message, state: FSMContext):
    user = await db.get_or_create_user(
        message.from_user.id, message.from_user.username, message.from_user.first_name
    )
    text = message.text or ""

    # Старый пользователь (или без /start): сначала стартовая сверка балансов
    if not user.get("onboarded"):
        name = user.get("first_name") or "друг"
        await message.answer(_welcome_text(name) + ONBOARD_PROMPT, reply_markup=main_menu())
        await state.set_state(Onboarding.waiting_balances)
        return

    # 1. Пересланное сообщение банка о чаевых → сразу на карту
    if message.forward_origin is not None:
        tips = p.parse_bank_tips(text)
        if tips is not None:
            await _save_bank_tips(message, tips)
        else:
            await message.answer(
                "В пересланном сообщении не нашёл сумму чаевых.\n"
                "Запиши руками: <i>чай 500</i>"
            )
        return

    # 2. Текст уведомления банка, скопированный без пересылки
    if p.looks_like_bank_tips(text):
        tips = p.parse_bank_tips(text)
        if tips is not None:
            await _save_bank_tips(message, tips)
            return

    # 3. Сверка: «наличными 3200, на карте 8100»
    stated = p.parse_reconciliation(text)
    if stated is not None:
        balances = await db.get_balances(message.from_user.id)
        lines, ids = [], []
        for account, amount in stated.items():
            diff = round(amount - balances[account], 2)
            label = db.ACCOUNT_LABELS[account]
            if abs(diff) < 0.01:
                lines.append(f"✅ {label}: {fmt(amount)} ₽ — сходится копейка в копейку")
                continue
            entry = await db.add_entry(
                message.from_user.id, "adjustment", account, diff,
                category="Сверка", note=f"было по журналу {fmt(balances[account])}",
            )
            ids.append(entry["id"])
            sign = "+" if diff > 0 else "−"
            lines.append(
                f"🔧 {label}: по журналу было {fmt(balances[account])} ₽, "
                f"по факту {fmt(amount)} ₽ — расхождение {sign}{fmt(abs(diff))} ₽, учёл"
            )
        balances = await db.get_balances(message.from_user.id)
        await message.answer(
            "\n".join(lines) + "\n\n" + fmt_balances(balances),
            reply_markup=undo_kb(ids) if ids else None,
        )
        return

    # 4. Обычные операции
    items = p.parse_transactions(text)
    if not items:
        await message.answer(
            "Не нашёл сумму. Примеры:\n"
            "<i>чай 500</i> · <i>кофе 200</i> · <i>зп 30000</i>\n"
            "/help — все команды",
            reply_markup=main_menu(),
        )
        return

    # Первая ли это операция? (в журнале пока только стартовые сверки)
    prior = await db.get_recent_entries(message.from_user.id, limit=3)
    is_first_tx = all(e["kind"] == "adjustment" for e in prior)

    saved = []
    for item in items:
        signed = item["amount"] * KIND_SIGN[item["kind"]]
        entry = await db.add_entry(
            message.from_user.id, item["kind"], item["account"], signed,
            category=item["category"], note=item["note"],
        )
        saved.append(entry)

    balances = await db.get_balances(message.from_user.id)
    body = "\n".join(entry_line(e) for e in saved)
    if is_first_tx:
        body += (
            "\n\n👌 Готово, ты освоился. Ошибся счётом — кнопка «Перенести», "
            "передумал — «Отменить». /help — если забудешь примеры."
        )
    toggle = saved[0] if len(saved) == 1 else None
    await message.answer(
        body + "\n\n" + fmt_balances(balances),
        reply_markup=undo_kb([e["id"] for e in saved], toggle_entry=toggle),
    )
