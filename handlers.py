"""Все хендлеры бота. Трекер чаевых: регистрируем чай (и траты за смену),
считаем «чистыми за смену». Никакого баланса/остатка — это не бюджет.

Принцип: записываем сразу, отмена — одной кнопкой. Многошаговых диалогов нет.
"""
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
KIND_EMOJI = {"income": "➕", "expense": "➖"}


class ShiftSpend(StatesGroup):
    waiting_amount = State()


# ─── форматирование ──────────────────────────────────────────────────────────

def fmt(v: float) -> str:
    """12345.0 → «12 345», 250.5 → «250,50»"""
    if v == int(v):
        return f"{int(v):,}".replace(",", " ")
    return f"{v:,.2f}".replace(",", " ").replace(".", ",")


def today_line(income: float, spent: float) -> str:
    """«За сегодня» = чай − траты смены."""
    net = income - spent
    if spent > 0:
        return f"<b>За сегодня: {fmt(net)} ₽</b>\nЧай {fmt(income)} − траты {fmt(spent)}"
    return f"<b>За сегодня: {fmt(income)} ₽</b>"


async def today_totals(user_id: int):
    from datetime import datetime, timedelta, timezone
    msk = timezone(timedelta(hours=3))
    day_start = datetime.now(msk).replace(hour=0, minute=0, second=0, microsecond=0)
    entries = await db.get_entries_since(user_id, day_start.astimezone(timezone.utc).isoformat())
    income = sum(float(e["signed_amount"]) for e in entries if e["kind"] == "income")
    spent = -sum(float(e["signed_amount"]) for e in entries if e["kind"] == "expense")
    return income, spent


async def today_block(user_id: int) -> str:
    income, spent = await today_totals(user_id)
    return today_line(income, spent)


def main_menu() -> ReplyKeyboardMarkup:
    row1 = [KeyboardButton(text="📋 История"), KeyboardButton(text="🧾 Закрыть смену")]
    rows = [row1]
    if WEBHOOK_HOST:
        rows.append([KeyboardButton(
            text="📊 Статистика", web_app=WebAppInfo(url=f"{WEBHOOK_HOST}/app")
        )])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def entry_line(e: dict) -> str:
    amount = float(e["signed_amount"])
    emoji = KIND_EMOJI.get(e["kind"], "•")
    acc = db.ACCOUNT_LABELS[e["account"]]
    sign = "+" if amount > 0 else "−"
    note = f" ({e['note']})" if e.get("note") else ""
    return f"{emoji} {sign}{fmt(abs(amount))} ₽ · {e['category']} · {acc}{note}"


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


# ─── /start и знакомство ─────────────────────────────────────────────────────

def _welcome_text(name: str) -> str:
    return (
        f"Привет, {name}! Я считаю твои чаевые.\n\n"
        "— пишешь <i>«чай 500»</i> — записываю сразу\n"
        "— пересылаешь сообщение банка о чаевых — записываю сам\n"
        "— в конце смены жмёшь «🧾 Закрыть смену» и вносишь траты — "
        "покажу, сколько подняла смена чистыми\n\n"
        "Запиши первую: <i>чай 500</i>"
    )


async def _greet(message: Message, name: str):
    await db.set_onboarded(message.from_user.id)
    await message.answer(_welcome_text(name), reply_markup=main_menu())


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
        await message.answer(await today_block(message.from_user.id), reply_markup=main_menu())
        return
    await _greet(message, user.get("first_name") or "друг")


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "<b>Как я работаю</b>\n\n"
        "Чаевые: <i>чай 500</i>, <i>смена 2500</i>\n"
        "Перешли сообщение банка о чаевых — запишу сам.\n\n"
        "🧾 Закрыть смену — внести траты за смену (мойка, бар, еда…), "
        "покажу чистыми за смену\n"
        "📋 История — последние записи\n"
        "📊 Статистика — графики за месяц\n\n"
        "<i>план 2500</i> — цель по чаю на смену\n"
        "/undo — отменить последнюю запись\n"
        "/reset — начать заново"
    )


# ─── история ─────────────────────────────────────────────────────────────────

@router.message(F.text == "📋 История")
async def show_history(message: Message):
    entries = await db.get_recent_entries(message.from_user.id, limit=15)
    if not entries:
        await message.answer("Пока пусто. Напиши первую: <i>чай 500</i>")
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
    await message.answer(
        "Отменил:\n" + entry_line(entry) + "\n\n" + await today_block(message.from_user.id)
    )


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
    await state.clear()
    await callback.message.edit_text("Журнал очищен.")
    await callback.message.answer("Погнали заново. Запиши: <i>чай 500</i>", reply_markup=main_menu())
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
    await callback.message.edit_text("↩️ Отменено.\n\n" + await today_block(callback.from_user.id))
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
    await callback.message.edit_text(
        entry_line(entry) + "\n\n" + await today_block(callback.from_user.id),
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
    await db.add_entry(
        message.from_user.id, "expense", db.CASH, -amount,
        category=category, note="трата смены",
    )
    await state.clear()
    await message.answer(
        f"➖ {category} {fmt(amount)} ₽\n\nЕщё что-то?",
        reply_markup=shift_spend_kb(),
    )


async def _send_day_summary(message: Message, user_id: int):
    """Итог дня: чай − траты = чистыми, плюс план если задан."""
    income, spent = await today_totals(user_id)
    net = income - spent
    lines = [today_line(income, spent)]
    goal = await db.get_shift_goal(user_id)
    if goal:
        pct = round(min(income / goal, 1.0) * 100)
        lines.append("✅ План сделан!" if income >= goal else f"План {fmt(goal)}: {pct}%")
    if income > 0 and net <= 0:
        lines.append("Смена ушла в минус — загляни в статистику, куда утекло.")
    await message.answer("\n".join(lines))


# ─── кнопки старой версии бота ───────────────────────────────────────────────

LEGACY_BUTTONS = {"💰 Баланс", "Баланс", "Статистика", "Платежи", "Бюджеты",
                  "Настройки", "Доходы", "Цели", "ИИ-чат"}


@router.message(F.text == "История")
async def legacy_history(message: Message):
    await show_history(message)


@router.message(F.text.in_(LEGACY_BUTTONS))
async def legacy_button(message: Message):
    user = await db.get_or_create_user(
        message.from_user.id, message.from_user.username, message.from_user.first_name
    )
    if not user.get("onboarded"):
        await _greet(message, user.get("first_name") or "друг")
        return
    await message.answer(
        "Я теперь считаю только чаевые.\n"
        "📋 История и 🧾 Закрыть смену — на клавиатуре, /help — что умею.",
        reply_markup=main_menu(),
    )


# ─── главный обработчик текста ───────────────────────────────────────────────

async def _save_bank_tips(message: Message, notif: dict):
    """Чаевые из банковского уведомления → на карту, с чеком и процентом."""
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
    await message.answer(
        f"➕ Чаевые <b>{fmt(tips)} ₽</b>{details_str} → карта\n\n"
        + await today_block(message.from_user.id),
        reply_markup=undo_kb([entry["id"]], toggle_entry=entry),
    )


@router.message(F.text)
async def handle_text(message: Message, state: FSMContext):
    user = await db.get_or_create_user(
        message.from_user.id, message.from_user.username, message.from_user.first_name
    )
    text = message.text or ""

    # Новый пользователь (или без /start): знакомство
    if not user.get("onboarded"):
        await _greet(message, user.get("first_name") or "друг")
        return

    # 1. Пересланное сообщение банка о чаевых → на карту
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

    # 3. Обычные записи: «чай 500», «кофе 200, такси 350»
    items = p.parse_transactions(text)
    if not items:
        await message.answer(
            "Не нашёл сумму. Примеры:\n"
            "<i>чай 500</i> · <i>смена 2500</i>\n"
            "/help — все команды",
            reply_markup=main_menu(),
        )
        return

    is_first_tx = not await db.get_recent_entries(message.from_user.id, limit=1)

    saved = []
    for item in items:
        signed = item["amount"] * KIND_SIGN[item["kind"]]
        entry = await db.add_entry(
            message.from_user.id, item["kind"], item["account"], signed,
            category=item["category"], note=item["note"],
        )
        saved.append(entry)

    body = "\n".join(entry_line(e) for e in saved)
    if is_first_tx:
        body += (
            "\n\n👌 Записал. Ошибся счётом — кнопка «Перенести», "
            "передумал — «Отменить»."
        )
    toggle = saved[0] if len(saved) == 1 else None
    await message.answer(
        body + "\n\n" + await today_block(message.from_user.id),
        reply_markup=undo_kb([e["id"] for e in saved], toggle_entry=toggle),
    )
