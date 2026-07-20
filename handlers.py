"""Все хендлеры бота. Принцип: записываем сразу, отмена — одной кнопкой.

Никаких многошаговых диалогов, кроме единственного вопроса при онбординге.
"""
import logging
import os
import re

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
    WebAppInfo,
)

import db
import parser as p

router = Router()

WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")

SHIFT_SPEND_CATEGORIES = ["Мойка", "Бар", "Еда", "Такси"]

KIND_SIGN = {"income": 1, "expense": -1}
KIND_EMOJI = {"income": "➕", "expense": "➖", "adjustment": "🔧"}


class Onboarding(StatesGroup):
    waiting_balances = State()
    waiting_account_choice = State()


class ShiftSpend(StatesGroup):
    waiting_amount = State()


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
    rows = [[KeyboardButton(text="💰 Баланс"), KeyboardButton(text="📋 История")]]
    row2 = [KeyboardButton(text="🧾 Закрыть смену")]
    if WEBHOOK_HOST:
        row2.append(KeyboardButton(
            text="📊 Статистика", web_app=WebAppInfo(url=f"{WEBHOOK_HOST}/app")
        ))
    rows.append(row2)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


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
    # Deep-link из мини-апа: кнопка «Внести траты смены»
    if user.get("onboarded") and (message.text or "").strip().endswith("close_shift"):
        await send_shift_close_prompt(message)
        return
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


# ─── план смены ──────────────────────────────────────────────────────────────

PLAN_RE = re.compile(r"^\s*план(?:\s+смены)?\s*[:\-—]?\s*(\d[\d ]*)?\s*$", re.IGNORECASE)


@router.message(F.text.regexp(PLAN_RE))
async def shift_plan(message: Message):
    m = PLAN_RE.match(message.text)
    raw = m.group(1)
    if raw is None:
        goal = await db.get_shift_goal(message.from_user.id)
        if goal:
            await message.answer(
                f"План смены: <b>{fmt(goal)} ₽</b>.\n"
                "Изменить: <i>план 2500</i> · Убрать: <i>план 0</i>"
            )
        else:
            await message.answer("План не задан. Задай: <i>план 2000</i>")
        return
    goal = float(raw.replace(" ", ""))
    if goal <= 0:
        await db.set_shift_goal(message.from_user.id, None)
        await message.answer("План смены убрал.")
        return
    await db.set_shift_goal(message.from_user.id, goal)
    await message.answer(f"План смены: <b>{fmt(goal)} ₽ чая</b>. Вечером посмотрим, как получилось.")


# ─── закрытие смены: траты кнопками ──────────────────────────────────────────

def shift_spend_kb() -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(text=c, callback_data=f"ss:{c}")
        for c in SHIFT_SPEND_CATEGORIES
    ]
    return InlineKeyboardMarkup(inline_keyboard=[
        row[:2], row[2:],
        [InlineKeyboardButton(text="✅ Готово, ничего больше", callback_data="ss:done")],
    ])


async def send_shift_close_prompt(message: Message):
    await message.answer(
        "Закрываем смену. Что потратил за день?\n"
        "Жми категорию и пиши сумму — или сразу «Готово».",
        reply_markup=shift_spend_kb(),
    )


@router.message(F.text == "🧾 Закрыть смену")
async def shift_close_button(message: Message):
    await send_shift_close_prompt(message)


@router.message(F.web_app_data)
async def web_app_data_handler(message: Message):
    if message.web_app_data.data == "close_shift":
        await send_shift_close_prompt(message)


@router.callback_query(F.data.startswith("ss:"))
async def shift_spend_chip(callback: CallbackQuery, state: FSMContext):
    choice = callback.data.split(":", 1)[1]
    if choice == "done":
        await state.clear()
        await _send_day_summary(callback.message, callback.from_user.id)
        await callback.answer()
        return
    await state.set_state(ShiftSpend.waiting_amount)
    await state.update_data(shift_category=choice)
    await callback.message.answer(f"Сколько ушло на «{choice}»? Просто число.")
    await callback.answer()


@router.message(ShiftSpend.waiting_amount)
async def shift_spend_amount(message: Message, state: FSMContext):
    amount = p.extract_amount(message.text or "")
    if amount is None:
        await message.answer("Нужно число, например: <i>350</i>. Или /cancel.")
        return
    data = await state.get_data()
    category = data.get("shift_category", "Прочее")
    entry = await db.add_entry(
        message.from_user.id, "expense", db.CASH, -amount,
        category=category, note="трата смены",
    )
    await state.clear()
    await message.answer(
        f"➖ {category} {fmt(amount)} ₽ (наличные)\n\nЕщё что-то?",
        reply_markup=shift_spend_kb(),
    )


async def _send_day_summary(message: Message, user_id: int):
    """Итог дня: чай − траты = чистыми, плюс план если задан."""
    from datetime import datetime, timedelta, timezone
    msk = timezone(timedelta(hours=3))
    day_start = datetime.now(msk).replace(hour=0, minute=0, second=0, microsecond=0)
    entries = await db.get_entries_since(user_id, day_start.astimezone(timezone.utc).isoformat())
    income = sum(float(e["signed_amount"]) for e in entries if e["kind"] == "income")
    spent = -sum(float(e["signed_amount"]) for e in entries if e["kind"] == "expense")
    net = income - spent

    lines = [
        f"<b>За сегодня: {fmt(net)} ₽ чистыми</b>",
        f"Заработал {fmt(income)} − потратил {fmt(spent)}",
    ]
    goal = await db.get_shift_goal(user_id)
    if goal:
        pct = round(min(income / goal, 1.0) * 100)
        mark = "✅ План сделан!" if income >= goal else f"{pct}% плана"
        lines.append(f"План {fmt(goal)}: {mark}")
    if income > 0 and net <= 0:
        lines.append("Смена ушла в минус — загляни в статистику, куда утекло.")
    await message.answer("\n".join(lines))


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

async def _save_bank_tips(message: Message, notif: dict):
    """Чаевые из банковского уведомления → доход на карту, с чеком и процентом."""
    tips = notif["amount"]
    entry = await db.add_entry(
        message.from_user.id, "income", db.CARD, tips,
        category="Чаевые", note="из банка",
        order_amount=notif.get("order_amount"),
        tip_percent=notif.get("tip_percent"),
    )
    details = []
    if notif.get("order_amount"):
        details.append(f"чек {fmt(notif['order_amount'])}")
    if notif.get("tip_percent"):
        details.append(f"{notif['tip_percent']:g}%")
    details_str = f" ({', '.join(details)})" if details else ""
    balances = await db.get_balances(message.from_user.id)
    await message.answer(
        f"➕ Чаевые <b>{fmt(tips)} ₽</b>{details_str} → карта\n\n" + fmt_balances(balances),
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
        notif = p.parse_bank_notification(text)
        if notif is not None:
            await _save_bank_tips(message, notif)
        else:
            await message.answer(
                "В пересланном сообщении не нашёл сумму чаевых.\n"
                "Запиши руками: <i>чай 500</i>"
            )
        return

    # 2. Текст уведомления банка, скопированный без пересылки
    if p.looks_like_bank_tips(text):
        notif = p.parse_bank_notification(text)
        if notif is not None:
            await _save_bank_tips(message, notif)
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
