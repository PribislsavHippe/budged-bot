"""Раздел разработчика: агрегаты по боту и рассылка.

ГРАНИЦА, КОТОРУЮ НЕ ПЕРЕСЕКАЕМ. Здесь есть и должны быть только агрегаты:
сколько людей, сколько записей, сколько активных. Ни одного экрана с записями
или суммами конкретного человека — на этом стоит раздел «Приватность» в README
и весь разговор с коллегами, ради которого из базы убирали имена.

«Сколько людей писали сегодня» приватность не ломает. «Что записал вот этот» —
ломает, и добавлять такое сюда нельзя, даже если очень удобно для отладки.

Весь админский код живёт в одном файле намеренно: так обещание проверяется
одним взглядом, а не вычитыванием всего проекта.
"""
import asyncio
import logging
import os
from datetime import timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from workday import op_day_start_utc_iso, op_today

router = Router()

def _read_admin_id() -> int:
    """Кривой ADMIN_ID не должен ронять бота на старте — просто выключаем админку."""
    raw = (os.getenv("ADMIN_ID") or "").strip()
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        logging.warning(f"ADMIN_ID={raw!r} — не число, админские команды выключены")
        return 0


ADMIN_ID = _read_admin_id()

# Telegram ограничивает массовую отправку ~30 сообщениями в секунду.
# 25 в секунду — с запасом.
_SEND_DELAY = 0.04


def is_admin(user_id: int) -> bool:
    return bool(ADMIN_ID) and user_id == ADMIN_ID


class Broadcast(StatesGroup):
    waiting_confirm = State()


# ─── сводка ──────────────────────────────────────────────────────────────────

def _plural(n: int, one: str, few: str, many: str) -> str:
    m10, m100 = n % 10, n % 100
    if m10 == 1 and m100 != 11:
        return f"{n} {one}"
    if 2 <= m10 <= 4 and not 12 <= m100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    # Не отвечаем чужим вовсе: команда не должна выдавать своё существование.
    if not is_admin(message.from_user.id):
        return

    today = op_today()
    since_today = op_day_start_utc_iso(today)
    since_week = op_day_start_utc_iso(today - timedelta(days=6))
    since_month = op_day_start_utc_iso(today - timedelta(days=29))

    total = await db.count_users()
    onboarded = await db.count_onboarded_users()
    new_week = await db.count_users_since(since_week)
    active_today = len(await db.active_user_ids_since(since_today))
    active_week = len(await db.active_user_ids_since(since_week))
    active_month = len(await db.active_user_ids_since(since_month))
    entries_today = await db.count_entries_since(since_today)
    entries_week = await db.count_entries_since(since_week)
    shifts_today = await db.count_shifts_on(today.isoformat())

    retention = f"{round(active_week / onboarded * 100)}%" if onboarded else "—"

    await message.answer(
        f"<b>📊 Сводка на {today.strftime('%d.%m')}</b>\n"
        f"<i>сутки считаются с 6 утра</i>\n\n"
        f"<b>Люди</b>\n"
        f"Всего: {total}, дошли до конца знакомства: {onboarded}\n"
        f"Новых за неделю: {new_week}\n\n"
        f"<b>Активность</b>\n"
        f"Сегодня писали: {active_today}\n"
        f"За 7 дней: {active_week} ({retention} от дошедших)\n"
        f"За 30 дней: {active_month}\n\n"
        f"<b>Записи</b>\n"
        f"Сегодня: {entries_today}\n"
        f"За 7 дней: {entries_week}\n\n"
        f"<b>Смены</b>\n"
        f"Запланировано на сегодня: {shifts_today}\n\n"
        f"<i>Рассылка: /broadcast текст сообщения</i>"
    )


# ─── рассылка ────────────────────────────────────────────────────────────────

def _confirm_kb(count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"Отправить {count}", callback_data="bc:yes"),
        InlineKeyboardButton(text="Отмена", callback_data="bc:no"),
    ]])


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    text = (command.args or "").strip()
    if not text:
        await message.answer(
            "Что разослать?\n<code>/broadcast текст сообщения</code>\n\n"
            "Можно с разметкой: &lt;b&gt;жирный&lt;/b&gt;, &lt;i&gt;курсив&lt;/i&gt;."
        )
        return

    ids = await db.get_onboarded_user_ids()
    if not ids:
        await message.answer("Некому рассылать — пользователей нет.")
        return

    # Предпросмотр — ровно то сообщение, которое уйдёт людям. Если разметка
    # битая, Telegram откажется прямо здесь, до отправки кому-либо.
    try:
        await message.answer(text)
    except TelegramBadRequest as e:
        await message.answer(f"Разметка сломана, отправка отменена:\n<code>{e}</code>")
        return

    await state.set_state(Broadcast.waiting_confirm)
    await state.update_data(text=text)
    await message.answer(
        f"☝️ Вот так это увидят люди.\n\n"
        f"Разослать {_plural(len(ids), 'человеку', 'людям', 'людям')}?\n"
        f"<i>Отменить после отправки будет нельзя.</i>",
        reply_markup=_confirm_kb(len(ids)),
    )


@router.callback_query(F.data == "bc:no")
async def broadcast_no(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.message.edit_text("Рассылка отменена.")
    await callback.answer()


@router.callback_query(F.data == "bc:yes")
async def broadcast_yes(callback: CallbackQuery, state: FSMContext):
    """Без фильтра по состоянию: если бот перезапустился между /broadcast и
    нажатием кнопки, состояние потеряно — лучше сказать об этом, чем молчать."""
    if not is_admin(callback.from_user.id):
        return

    data = await state.get_data()
    text = data.get("text")
    await state.clear()
    if not text:
        await callback.message.edit_text(
            "Текст рассылки потерялся (бот перезапускался). Набери /broadcast заново."
        )
        await callback.answer()
        return

    ids = await db.get_onboarded_user_ids()
    await callback.message.edit_text(f"Рассылаю… 0 из {len(ids)}")
    await callback.answer()

    sent = blocked = failed = 0
    for i, user_id in enumerate(ids, 1):
        try:
            await callback.bot.send_message(user_id, text)
            sent += 1
        except TelegramForbiddenError:
            blocked += 1                      # заблокировал бота или удалился
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await callback.bot.send_message(user_id, text)
                sent += 1
            except Exception as ex:
                failed += 1
                logging.warning(f"broadcast to {user_id} failed after retry: {ex}")
        except Exception as e:
            failed += 1
            logging.warning(f"broadcast to {user_id} failed: {e}")

        if i % 25 == 0:
            try:
                await callback.message.edit_text(f"Рассылаю… {i} из {len(ids)}")
            except TelegramBadRequest:
                pass
        await asyncio.sleep(_SEND_DELAY)

    result = [f"<b>Разослано: {sent}</b>"]
    if blocked:
        result.append(f"Заблокировали бота: {blocked}")
    if failed:
        result.append(f"Не доставлено: {failed}")
    await callback.message.edit_text("\n".join(result))
