"""Все хендлеры бота. Трекер чаевых: регистрируем чай (и траты за смену),
считаем «чистыми за смену». Никакого баланса/остатка — это не бюджет.

Принцип: записываем сразу, отмена — одной кнопкой. Многошаговых диалогов нет.
"""
import csv
import asyncio
import html
import io
import logging
import os
import re
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
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
from chat_dates import human_date, human_month
from workday import MSK, entry_op_date, op_today

router = Router()

WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")

SHIFT_SPEND_CATEGORIES = ["Мойка", "Бар", "Еда", "Такси"]

KIND_SIGN = {"income": 1, "expense": -1}
KIND_EMOJI = {"income": "➕", "expense": "➖", "accrual": "🕓"}


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
        return f"<b>Чистыми за сегодня: {fmt(net)} ₽</b>\nЧаевые до расходов {fmt(income)} − расходы {fmt(spent)}"
    return f"<b>Чистыми за сегодня: {fmt(income)} ₽</b>\nЧаевые до расходов {fmt(income)} − расходы 0"


async def today_totals(user_id: int):
    """Итоги текущей смены. Сутки операционные: ночь принадлежит вчерашнему дню."""
    entries = await db.get_entries_for_work_date(user_id, op_today().isoformat())
    income = sum(float(e["signed_amount"]) for e in entries if e["kind"] == "income" and e.get("category") == "Чаевые")
    spent = -sum(float(e["signed_amount"]) for e in entries if e["kind"] == "expense")
    return income, spent


async def today_block(user_id: int) -> str:
    income, spent = await today_totals(user_id)
    return today_line(income, spent)


def main_menu() -> ReplyKeyboardMarkup:
    row1 = [KeyboardButton(text="📋 История"), KeyboardButton(text="🧾 Записать расход")]
    rows = [row1]
    if WEBHOOK_HOST:
        rows.append([KeyboardButton(
            text="📊 Статистика", web_app=WebAppInfo(url=f"{WEBHOOK_HOST}/app")
        )])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def entry_line(e: dict) -> str:
    amount = float(e["signed_amount"])
    emoji = KIND_EMOJI.get(e["kind"], "•")
    if e["kind"] == "accrual":
        return f"{emoji} {fmt(amount)} ₽ · Сервисный сбор · начисление к зарплате"
    acc = db.ACCOUNT_LABELS[e["account"]]
    sign = "+" if amount > 0 else "−"
    note = f" ({html.escape(str(e['note']))})" if e.get("note") else ""
    return f"{emoji} {sign}{fmt(abs(amount))} ₽ · {html.escape(str(e['category']))} · {acc}{note}"


def undo_kb(entry_ids: list[int], toggle_entry: dict | None = None) -> InlineKeyboardMarkup:
    rows = []
    if toggle_entry is not None and toggle_entry["kind"] != "accrual":
        other = db.CASH if toggle_entry["account"] == db.CARD else db.CARD
        rows.append([InlineKeyboardButton(
            text=f"Изменить на {'наличные' if other==db.CASH else 'безналичные'}",
            callback_data=f"acc:{toggle_entry['id']}",
        )])
    ids = ",".join(str(i) for i in entry_ids)
    rows.append([InlineKeyboardButton(text="↩️ Отменить", callback_data=f"undo:{ids}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─── /start и знакомство ─────────────────────────────────────────────────────

def _welcome_text(name: str) -> str:
    return (f"Привет, {html.escape(name)}! 💛\n\n"
            "Можно сразу записать чаевые: пришли сумму, например <i>чай 1500</i>. "
            "Если хочешь начать с графика, пришли его фото.")



def _name(message: Message) -> str:
    """Имя для приветствия — из самого сообщения Telegram, в базе мы его не держим."""
    return message.from_user.first_name or "друг"


async def _greet(message: Message, name: str):
    await db.set_onboarded(message.from_user.id)
    await message.answer(_welcome_text(name), reply_markup=main_menu())
    import research
    research.track(message.from_user.id,'user_started')
    from ux_chat import begin
    await begin(message,prompt=False)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = await db.get_or_create_user(message.from_user.id)
    import research
    research.track(message.from_user.id,'user_started')
    if (message.text or '').strip().endswith('help'):
        await cmd_help(message)
        return
    # Deep-link из мини-апа: кнопка «Внести траты смены»
    if user.get("onboarded") and (message.text or "").strip().endswith("close_shift"):
        await send_shift_close_prompt(message)
        return
    if user.get("onboarded"):
        if user.get('private_money_mode'):
            await message.answer('Личные записи и итоги хранятся на твоём устройстве. Открой «Статистику».',reply_markup=main_menu())
        else:
            await message.answer(await today_block(message.from_user.id), reply_markup=main_menu())
        return
    await _greet(message, _name(message))


@router.message(Command("calendar"))
async def cmd_calendar(message: Message,user_id: int | None=None):
    import google_calendar as gcal
    user_id=user_id or message.from_user.id
    if not gcal.is_configured():
        await message.answer(gcal.ERROR_MESSAGES["not_configured"])
        return
    try:
        status = await gcal.connection_status(user_id)
        if not status["connected"]:
            await message.answer(status["message"] or "Подключи Google Календарь в Статистике.")
            return
        result = await gcal.sync_pending(user_id)
        await message.answer(f"📆 Добавил в Google Календарь {result['synced']} смен. Ещё не добавлены: {result['pending']}. " + result["message"])
    except Exception:
        logging.exception("Manual calendar sync failed")
        await message.answer("Смены сохранил, но пока не смог добавить их в Google Календарь. Попробуй ещё раз позже.",reply_markup=calendar_retry_kb())


def calendar_retry_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Повторить отправку в Google",callback_data="calendar:retry")]])


@router.callback_query(F.data=="calendar:retry")
async def calendar_retry(callback: CallbackQuery):
    await callback.answer()
    await cmd_calendar(callback.message,callback.from_user.id)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await send_help(message,message.from_user.id)


async def send_help(message: Message,user_id: int):
    import research
    from ux_chat import help_buttons
    research.track(user_id,'help_opened')
    await message.answer('Чем помочь? Можно написать Леше напрямую или найти короткий ответ здесь.',
                         reply_markup=help_buttons())


# ─── история ─────────────────────────────────────────────────────────────────

@router.message(F.text == "📋 История")
async def show_history(message: Message):
    import research
    research.track(message.from_user.id,"tab_opened",screen="history")
    if (await db.get_or_create_user(message.from_user.id)).get('private_money_mode'):
        await message.answer('История чаевых и расходов теперь в «Статистике» на твоём устройстве.',reply_markup=main_menu())
        return
    entries = await db.get_recent_entries(message.from_user.id, limit=15)
    if not entries:
        await message.answer("Пока пусто. Напиши первую: <i>чай 500</i>")
        return
    from datetime import date, datetime
    lines = []
    for e in entries:
        # В истории показываем настоящее время записи, а не операционное.
        dt = datetime.fromisoformat(e["created_at"].replace("Z", "+00:00")).astimezone(MSK)
        lines.append(f"<i>{dt.strftime('%d.%m %H:%M')}</i>  {entry_line(e)}")
    await message.answer(
        "<b>Последние записи</b>\n\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отменить последнюю запись",callback_data=f"history:undo:{entries[0]['id']}")]])
    )


@router.callback_query(F.data.startswith("history:undo:"))
async def history_undo(callback: CallbackQuery):
    await callback.answer()
    await cmd_undo(callback.message,user_id=callback.from_user.id,expected_id=callback.data.rsplit(":",1)[-1])


@router.message(Command("undo"))
async def cmd_undo(message: Message,user_id: int | None=None,expected_id: str | None=None):
    user_id=user_id or message.from_user.id
    if (await db.get_or_create_user(user_id)).get('private_money_mode'):
        await message.answer('Исправить или удалить личную запись можно в «Статистике» на этом устройстве.',reply_markup=main_menu())
        return
    entries = await db.get_recent_entries(user_id, limit=1)
    if not entries:
        await message.answer("Отменять нечего — журнал пуст.")
        return
    entry = entries[0]
    if expected_id is not None and str(entry['id'])!=expected_id:
        await message.answer('Последняя запись уже изменилась. Открой историю ещё раз.')
        return
    await db.delete_entry(entry["id"], user_id)
    await message.answer(
        "Отменил:\n" + entry_line(entry) + "\n\n" + await today_block(user_id)
    )


# ─── сброс ───────────────────────────────────────────────────────────────────

@router.message(Command("reset"))
async def cmd_reset(message: Message):
    if (await db.get_or_create_user(message.from_user.id)).get('private_money_mode'):
        await message.answer('Личный журнал хранится на твоём устройстве. Очистить его можно в «Статистике» → «Суперконфиденциальность».')
        return
    await message.answer(
        "Удалить <b>весь</b> журнал и начать заново?\n"
        "<i>Профиль останется. Чтобы стереть вообще всё — /delete</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Да, удалить всё", callback_data="reset:yes"),
            InlineKeyboardButton(text="Отмена", callback_data="reset:no"),
        ]]),
    )


@router.callback_query(F.data == "reset:yes")
async def reset_yes(callback: CallbackQuery, state: FSMContext):
    if (await db.get_or_create_user(callback.from_user.id)).get('private_money_mode'):
        await callback.answer('Личный журнал удаляется в приложении на устройстве.',show_alert=True)
        return
    await db.clear_entries(callback.from_user.id)
    await state.clear()
    await callback.message.edit_text("Журнал очищен.")
    await callback.message.answer("Начинаем заново. Запиши: <i>чай 500</i>", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "reset:no")
async def reset_no(callback: CallbackQuery):
    await callback.message.edit_text("Отмена — ничего не удалял.")
    await callback.answer()


# ─── приватность: что хранится, забрать своё, стереть себя ───────────────────

SOURCE_URL = os.getenv("SOURCE_URL")


@router.message(Command("privacy"))
async def cmd_privacy(message: Message):
    """Короткий и скучный список того, что лежит в базе. Скучность — это и есть аргумент."""
    if (await db.get_or_create_user(message.from_user.id)).get('private_money_mode'):
        await message.answer('<b>🔒 Суперконфиденциальность включена</b>\n\n'
            'Личный журнал находится на устройстве, где ты включил этот режим. '
            'Бот сразу сохраняет новые записи в зашифрованных копиях на сервере на 14 дней. '
            'При открытии приложения копии автоматически переносятся на телефон. '
            'Без ключа этого телефона прочитать суммы из копий нельзя. '
            'Пересланное банковское сообщение видят Telegram и бот при обработке.\n\n'
            'На сервере остаются твой Telegram ID, график и часы работы для напоминаний, '
            'открытый ключ устройства, зашифрованные временные копии, отдельно созданные продажи, разрешения Google и события использования: время и тип действий, включая факт добавления чаевых или расхода, без суммы и содержимого записи. '
            'При потере телефона или ключа личный журнал не восстановится. В «Статистике» можно удалить журнал вместе с временными копиями.')
        return
    shifts = await db.get_shift_dates(message.from_user.id)
    lines = [
        "<b>🔒 Что я о тебе знаю</b>",
        "",
        "В базе лежит ровно это:",
        f"• твой номер в Telegram — <code>{message.from_user.id}</code>",
        "• записи: сумма, категория, нал/карта, время",
        "• текст самой записи — он сохраняется как заметка",
        f"• даты и часы смен, которые ты поставил — сейчас {len(shifts)}",
        "• план смены, если задавал",
        "• фактическое время работы и часовая ставка, если записывал; они не видны администратору ресторана",
        "• личные планы продаж, записи продаж и версии официальных отчётов",
        "• разрешение на добавление смен в Google Календарь, если ты его подключил",
        "• фото отчёта или графика отправляется в Groq только после выбора типа распознавания; "
        "фото не сохраняется в базе, черновик распознавания хранится в памяти до 15 минут",
        "",
        "Чтобы находить твою строку в отчёте, сохраняются ресторан, имя или код "
        "из отчёта и статус подтверждения. Администратор видит твоё имя, Telegram ID, "
        "планы и продажи, записанные после присоединения к ресторану. "
        "Личные чаевые и расходы в его кабинете не показываются.",
        "Имя профиля Telegram, @username, телефон и номер карты не сохраняются.",
        "Для улучшения бота отдельно сохраняем действия: открытие раздела, успешную запись, "
        "шаг знакомства и код ошибки. Без сумм, сообщений и фотографий. "
        "Владелец бота видит эти пути под внутренними номерами, например U-0184. "
        "Обращения, которые ты отправляешь через /feedback, он читает отдельно. "
        "Свои события и обращения можно получить через /export и удалить через /delete.",
        "",
        "Я не считаю твой баланс и не знаю, сколько у тебя денег — "
        "чаевые и расходы смены; продажи ресторана хранятся отдельно.",
        "",
        "<b>Личные чаевые и расходы видишь только ты в своём кабинете.</b> "
        "Владелец серверного доступа к базе технически может прочитать хранимые данные — "
        "обещать абсолютную анонимность было бы неправильно.",
        "",
        "/export — забрать все свои записи файлом",
        "/delete — удалить свои данные",
    ]

    if SOURCE_URL:
        lines.append(f"\nКод открыт, можно проверить: {SOURCE_URL}")
    await message.answer("\n".join(lines))


@router.message(Command("export"))
async def cmd_export(message: Message):
    """Отдать человеку его собственные данные — сигнал «это твоё, а не моё»."""
    private=(await db.get_or_create_user(message.from_user.id)).get('private_money_mode')
    if private:
        await message.answer('Личные чаевые, расходы и начисления хранятся только на этом устройстве и не входят в серверную выгрузку. Остальные данные отправлю ниже.')
    entries = await db.get_all_entries(message.from_user.id)
    shifts = await db.get_shift_dates(message.from_user.id)
    import sales_db
    import json
    sales_data = await sales_db.export(message.from_user.id)
    has_sales = any(sales_data.values())
    from identity_chat import enabled as identity_enabled
    identity_data = None
    if identity_enabled():
        import identity
        identity_data = await identity.export(message.from_user.id)
    has_identity = bool(identity_data and any(identity_data.values()))
    import research
    if research.enabled():
        try:
            research_data=await research.export(message.from_user.id)
            if research_data:
                await message.answer_document(BufferedInputFile(
                    json.dumps(research_data,ensure_ascii=False,indent=2).encode('utf-8'),
                    filename='my-ux-data.json'),caption='Твои события использования и обращения.')
        except Exception as error:
            from diagnostics import failure
            failure(error,area='analytics',stage='export')
            await message.answer('События использования пока не удалось выгрузить. Остальные записи отправлю отдельно.')
    import schedule
    has_work=False
    if schedule.enabled():
        try:
            work_data=await schedule.export(message.from_user.id)
            has_work=bool(work_data['worked_shifts'] or work_data['planned_shifts'] or work_data['hourly_rate'])
            await message.answer_document(BufferedInputFile(
                json.dumps(work_data,ensure_ascii=False,indent=2).encode('utf-8'),
                filename='my-work-hours.json'),caption='Твой график, отработанное время и ставка.')
        except Exception as error:
            from diagnostics import failure
            failure(error,area='work_time',stage='export')
            await message.answer('Часы работы пока не удалось выгрузить. Остальные записи отправлю отдельно.')
    if not entries and not shifts and not has_sales and not has_identity and not has_work:
        await message.answer("Пока нет записей, которые можно скачать.")
        return

    from datetime import date, datetime

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow([
        "дата и время (МСК)", "смена от", "тип", "счёт",
        "категория", "сумма", "заметка", "чек заказа", "% чая",
    ])
    for e in entries:
        dt = datetime.fromisoformat(e["created_at"].replace("Z", "+00:00")).astimezone(MSK)
        writer.writerow([
            dt.strftime("%d.%m.%Y %H:%M"),
            (date.fromisoformat(e['work_date']) if e.get('work_date') else entry_op_date(e["created_at"])).strftime("%d.%m.%Y"),
            {"income": "доход", "expense": "расход", "adjustment": "сверка",
             "accrual": "начисление"}.get(e["kind"], e["kind"]),
            db.ACCOUNT_LABELS.get(e["account"], e["account"]),
            e["category"],
            f'{float(e["signed_amount"]):.2f}'.replace(".", ","),
            e.get("note") or "",
            e.get("order_amount") or "",
            e.get("tip_percent") or "",
        ])

    # utf-8-sig — чтобы Excel не показал кракозябры вместо русских букв
    doc = BufferedInputFile(
        buf.getvalue().encode("utf-8-sig"),
        filename=f"chaevye-{op_today().isoformat()}.csv",
    )
    caption = f"Твои записи целиком — {len(entries)} шт."
    if shifts:
        caption += f"\nЗапланированные смены: {', '.join(shifts[-10:])}"
        if len(shifts) > 10:
            caption += f" (и ещё {len(shifts) - 10})"
    await message.answer_document(doc, caption=caption)
    if has_sales:
        await message.answer_document(BufferedInputFile(
            json.dumps(sales_data, ensure_ascii=False, indent=2).encode("utf-8"),
            filename=f"sales-{op_today().isoformat()}.json",
        ), caption="Планы продаж, все записи (включая отменённые) и версии сверок.")
    if has_identity:
        await message.answer_document(BufferedInputFile(
            json.dumps(identity_data, ensure_ascii=False, indent=2).encode('utf-8'),
            filename=f'identity-{op_today().isoformat()}.json'), caption='Твоя привязка сотрудника и ресторан.')


@router.message(Command("delete"))
async def cmd_delete(message: Message):
    private=(await db.get_or_create_user(message.from_user.id)).get('private_money_mode')
    await message.answer(
        "Стереть <b>всё</b>: записи, смены, планы продаж, отчёты, профиль, привязку сотрудника, Google Календаря, события использования и обращения?\n"
        "Если ты администратор ресторана, ресторан останется без администратора.\n\n"
        "<i>Это навсегда. Восстановить не смогу — у меня не остаётся копии.\n"
        "Хочешь сначала забрать данные — /export</i>"+
        ("\n\nЛичный журнал на телефоне эта команда не затронет: очисти его отдельно в «Статистике»." if private else ""),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Да, удалить мои данные", callback_data="del:yes"),
            InlineKeyboardButton(text="Отмена", callback_data="del:no"),
        ]]),
    )


@router.callback_query(F.data == "del:yes")
async def delete_yes(callback: CallbackQuery, state: FSMContext):
    await db.delete_user(callback.from_user.id)
    await state.clear()
    await callback.message.edit_text(
        "Стёр. В базе тебя больше нет.\n\n"
        "Если вернёшься — начнём с чистого листа, /start."
    )
    await callback.answer()


@router.callback_query(F.data == "del:no")
async def delete_no(callback: CallbackQuery):
    await callback.message.edit_text("Отмена — всё на месте.")
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
    await callback.answer(f"Теперь {'наличные' if other==db.CASH else 'безналичные'}")


# ─── план смены ──────────────────────────────────────────────────────────────

PLAN_RE = re.compile(r"^\s*(?:план|цель)(?:\s+(?:на\s+)?смен[ыу])?\s*[:\-—]?\s*((?:\d[\d .,]*|ноль)(?:\s*(?:к|тыс\.?|тысяч[аи]?))?(?:\s*(?:₽|руб(?:лей|ля|ль|\.)?))?)?\s*$", re.IGNORECASE)


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
            await message.answer("Пока не знаем цель на смену. Напиши: <i>план 2000</i>")
        return
    from sales_chat import _value
    try:
        goal = _value(raw, zero=True)
    except ValueError as error:
        await message.answer(html.escape(str(error)))
        return
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


def shift_spend_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена",callback_data="ss:cancel")]])


async def send_shift_close_prompt(message: Message):
    await message.answer(
        "Какие были траты за смену?\n"
        "Выбери категорию и укажи сумму — или сразу «Готово».",
        reply_markup=shift_spend_kb(),
    )


@router.message(F.text.in_({"🧾 Записать расход", "🧾 Закрыть смену"}))
async def shift_close_button(message: Message):
    await send_shift_close_prompt(message)


@router.message(F.web_app_data)
async def web_app_data_handler(message: Message):
    if message.web_app_data.data == "close_shift":
        await send_shift_close_prompt(message)


@router.callback_query(F.data.startswith("ss:"))
async def shift_spend_chip(callback: CallbackQuery, state: FSMContext):
    choice = callback.data.split(":", 1)[1]
    if choice == "cancel":
        if await state.get_state()!=ShiftSpend.waiting_amount.state:
            await callback.answer('Этот вопрос уже закрыт.')
            return
        await state.clear()
        await callback.message.edit_text('Расход не записан.')
        await callback.answer()
        return
    if choice == "done":
        await state.clear()
        if (await db.get_or_create_user(callback.from_user.id)).get('private_money_mode'):
            await callback.message.answer('Итог личных записей — в приватном журнале на этом устройстве.')
        else:
            await _send_day_summary(callback.message, callback.from_user.id)
        await callback.answer()
        return
    await state.set_state(ShiftSpend.waiting_amount)
    await state.update_data(shift_category=choice)
    await callback.message.answer(f"Сколько ушло на «{choice}»? Например, 350 рублей.",reply_markup=shift_spend_cancel_kb())
    await callback.answer()


@router.message(ShiftSpend.waiting_amount)
async def shift_spend_amount(message: Message, state: FSMContext):
    amount = p.extract_amount(message.text or "")
    if amount is None:
        await message.answer("Напиши сумму числом, например <i>350</i>, или нажми «Отмена».",reply_markup=shift_spend_cancel_kb())
        return
    data = await state.get_data()
    category = data.get("shift_category", "Прочее")
    if (await db.get_or_create_user(message.from_user.id)).get('private_money_mode'):
        if await _send_private_record(message,{'kind':'expense','account':'cash',
            'signed_amount':-amount,'category':category,'note':'трата смены'},0):
            await state.clear()
        return
    entry = await db.add_entry(
        message.from_user.id, "expense", db.CASH, -amount,
        category=category, note="трата смены",
    )
    await state.clear()
    await message.answer(
        f"➖ {category} {fmt(amount)} ₽\n\nЕщё что-то?",
        reply_markup=shift_spend_kb(),
    )
    from ux_chat import value_saved
    await value_saved(message,entry)


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
        lines.append("Смена в минусе — посмотри в статистике, на что ушли деньги.")
    await message.answer("\n".join(lines))


# ─── кнопки старой версии бота ───────────────────────────────────────────────

LEGACY_BUTTONS = {"💰 Баланс", "Баланс", "Статистика", "Платежи", "Бюджеты",
                  "Настройки", "Доходы", "Цели", "ИИ-чат"}


@router.message(F.text == "История")
async def legacy_history(message: Message):
    await show_history(message)


@router.message(F.text.in_(LEGACY_BUTTONS))
async def legacy_button(message: Message):
    user = await db.get_or_create_user(message.from_user.id)
    if not user.get("onboarded"):
        await _greet(message, _name(message))
        return
    await message.answer(
        "Я теперь считаю только чаевые.\n"
        "📋 История и 🧾 Закрыть смену — на клавиатуре. Подсказки откроются по кнопке ниже.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Как пользоваться ботом",callback_data="ux:help")]]),
    )


@router.callback_query(F.data.startswith("saleundo:"))
async def undo_sale(callback: CallbackQuery):
    import sales_db
    saved = await sales_db.undo(callback.from_user.id, callback.data.split(":", 1)[1])
    await callback.answer("Продажа отменена" if saved else "Запись не найдена")
    if saved:
        await callback.message.edit_text("↩️ Продажа отменена. Официальный итог отчета не меняется.")


# ─── главный обработчик текста ───────────────────────────────────────────────

async def _send_private_record(message: Message, record: dict, index: int,
                               delete_source: bool = True, source_messages=None,
                               confirmation: str | None = None):
    """Store an encrypted 14-day copy; the phone imports it on next open."""
    import private_payload
    from datetime import datetime, timezone
    user=await db.get_or_create_user(message.from_user.id)
    if not user.get('private_money_public_key'):
        await message.answer('Открой «Статистику» на своём устройстве и настрой приватный журнал.')
        return False
    payload={**record,'id':f'telegram:{message.chat.id}:{message.message_id}:{index}',
             'work_date':op_today().isoformat(),
             'created_at':datetime.now(timezone.utc).isoformat()}
    try:
        sealed,_=private_payload.seal(message.from_user.id,
            user['private_money_public_key'],payload,os.environ['BOT_TOKEN'])
        await db.save_private_backup(message.from_user.id,str(payload['id']),sealed,
                                     private_payload.key_id(user['private_money_public_key']))
    except Exception as error:
        from diagnostics import failure
        failure(error,area='private_money',stage='backup_save')
        await message.answer('Не получил подтверждение сохранения. Открой личный журнал и проверь запись перед повтором.')
        return False
    amount=f"<b>{fmt(abs(float(record['signed_amount'])))} ₽</b>"
    label='наличные' if record['account']==db.CASH else 'безналичные'
    if record['kind']=='accrual':
        intro=f'Записал сервисный сбор: {amount} · к зарплате.'
    else:
        title='чаевые' if record['category']=='Чаевые' else (
            'расход' if record['kind']=='expense' else html.escape(str(record['category']).lower()))
        intro=(confirmation.rstrip('.') if confirmation else f'Записал {title}: {amount}')
        intro+=f' · {label}.'
    info=' В «Статистике» запись появится автоматически.'
    markup=None
    if record['kind'] in ('income','expense'):
        other=db.CASH if record['account']==db.CARD else db.CARD
        markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=f'Изменить на {"наличные" if other==db.CASH else "безналичные"}',
            callback_data=f'pacc:{message.message_id}:{index}:{other}')]])
    reply=await message.answer(intro+info,reply_markup=markup)
    if delete_source:
        failed=False
        for source in (source_messages or [message]):
            try:
                await message.bot.delete_message(source.chat.id,source.message_id)
            except Exception as error:
                from diagnostics import failure
                failure(error,area='private_money',stage='delete_source')
                failed=True
        if failed:
            try:
                warning=('Исходное сообщение осталось в чате — удали его вручную.'
                         if len(source_messages or [message])==1 else
                         'Некоторые исходные сообщения остались в чате — удали их вручную.')
                await reply.edit_text(intro+info+' '+warning,reply_markup=markup)
            except Exception:
                pass
    return True


@router.callback_query(F.data.startswith('pacc:'))
async def cb_private_account(callback: CallbackQuery):
    import private_payload
    from datetime import datetime, timezone
    try:
        _,message_id,index,account=callback.data.split(':')
        if not message_id.isdecimal() or not index.isdecimal() or account not in (db.CASH,db.CARD):
            raise ValueError('account')
        user=await db.get_or_create_user(callback.from_user.id)
        if not user.get('private_money_mode') or not user.get('private_money_public_key'):
            await callback.answer('Личный журнал сейчас недоступен.',show_alert=True)
            return
        target=f'telegram:{callback.message.chat.id}:{message_id}:{index}'
        change={'id':f'{target}:account:{callback.id}','kind':'account_change',
                'target_id':target,'account':account,'signed_amount':0,
                'created_at':datetime.now(timezone.utc).isoformat()}
        sealed,_=private_payload.seal(callback.from_user.id,
            user['private_money_public_key'],change,os.environ['BOT_TOKEN'])
        await db.save_private_backup(callback.from_user.id,change['id'],sealed,
                                     private_payload.key_id(user['private_money_public_key']))
    except Exception as error:
        from diagnostics import failure
        failure(error,area='private_money',stage='account_change')
        await callback.answer('Не получилось изменить способ получения. Попробуй ещё раз.',show_alert=True)
        return
    label='наличные' if account==db.CASH else 'безналичные'
    updated=re.sub(r'· (наличные|безналичные)',f'· {label}',callback.message.text or '',count=1)
    other=db.CASH if account==db.CARD else db.CARD
    markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=f'Изменить на {"наличные" if other==db.CASH else "безналичные"}',
        callback_data=f'pacc:{message_id}:{index}:{other}')]])
    try:await callback.message.edit_text(html.escape(updated),reply_markup=markup)
    except Exception:pass
    await callback.answer(f'Теперь {label}. В «Статистике» обновится автоматически.')


async def _save_bank_tips(message: Message, notif: dict, source_messages=None):
    """Чаевые из банковского уведомления → на карту, с чеком и процентом."""
    tips = notif["amount"]
    count=len(source_messages) if source_messages else 1
    if (await db.get_or_create_user(message.from_user.id)).get('private_money_mode'):
        await _send_private_record(message,{'kind':'income','account':'card',
            'signed_amount':tips,'category':'Чаевые','note':'из банка',
            'order_amount':notif.get('order_amount'),'tip_percent':notif.get('tip_percent')},0,
            source_messages=source_messages,
            confirmation=f'Записал чаевые: <b>{fmt(tips)} ₽</b>'+
                (f' из {count} уведомлений.' if count>1 else '.'))
        return
    entry = await db.add_entry(
        message.from_user.id, "income", db.CARD, tips,
        category="Чаевые", note="из банка",
        order_amount=notif.get("order_amount"),
        tip_percent=notif.get("tip_percent"),
        source_key=f"telegram:{message.chat.id}:{message.message_id}:0",
    )
    details = []
    if notif.get("order_amount"):
        details.append(f"чек {fmt(notif['order_amount'])}")
    if notif.get("tip_percent"):
        details.append(f"{notif['tip_percent']:g}%")
    details_str = f" ({', '.join(details)})" if details else ""
    await message.answer(
        f"➕ Чаевые <b>{fmt(tips)} ₽</b>{details_str} → карта"+
        (f" · {count} уведомлений" if count>1 else "")+"\n\n"
        + await today_block(message.from_user.id),
        reply_markup=undo_kb([entry["id"]], toggle_entry=entry),
    )
    from ux_chat import value_saved
    await value_saved(message,entry)


# Telegram delivers a selection of forwarded text messages as separate updates.
# Wait briefly for the rest of the selection before creating one entry.
_forward_batches = {}
_forward_quiet_seconds = 2.0


def _forward_source(origin):
    user=getattr(origin,'sender_user',None)
    chat=getattr(origin,'chat',None) or getattr(origin,'sender_chat',None)
    if user is not None:return ('user',user.id)
    if chat is not None:return ('chat',chat.id)
    return ('name',getattr(origin,'sender_user_name',None) or 'unknown')


def _queue_forwarded_tips(message: Message, notif: dict | None):
    key=(message.chat.id,message.from_user.id,_forward_source(message.forward_origin))
    loop=asyncio.get_running_loop()
    batch=_forward_batches.setdefault(key,{"messages":{},"timer":None})
    batch['messages'][message.message_id]=(message,notif)
    if batch['timer'] is not None:batch['timer'].cancel()
    batch['timer']=loop.call_later(_forward_quiet_seconds,
        lambda:asyncio.create_task(_flush_forwarded_tips(key)))


async def _flush_forwarded_tips(key):
    batch=_forward_batches.pop(key,None)
    if not batch:return
    items=[batch['messages'][mid] for mid in sorted(batch['messages'])]
    first=items[0][0]
    missing=sum(notif is None for _,notif in items)
    if missing:
        warning=('В пересланном сообщении не нашёл сумму чаевых. Ничего не записал. '
                 'Запиши вручную: <i>чай 500</i>.' if len(items)==1 else
                 f'Не нашёл сумму чаевых в {missing} из {len(items)} пересланных сообщений. '
                 'Ничего не записал. Перешли только уведомления о чаевых ещё раз.')
        await first.answer(warning)
        return
    total=sum((Decimal(str(notif['amount'])) for _,notif in items),Decimal('0'))
    notif=items[0][1].copy() if len(items)==1 else {
        'amount':float(total),'order_amount':None,'tip_percent':None}
    try:
        await _save_bank_tips(first,notif,source_messages=[message for message,_ in items])
    except Exception:
        logging.exception('Forwarded tips batch save failed')
        await first.answer('Не удалось подтвердить запись чаевых. Проверь историю перед повторной пересылкой.')


@router.shutdown()
async def flush_forwarded_tips_on_shutdown():
    """Do not discard a received batch during a graceful restart."""
    keys=list(_forward_batches)
    for key in keys:
        _forward_batches[key]['timer'].cancel()
    await asyncio.gather(*(_flush_forwarded_tips(key) for key in keys))


@router.message(F.text)
async def handle_text(message: Message, state: FSMContext):
    user = await db.get_or_create_user(message.from_user.id)
    text = message.text or ""

    # Новый пользователь (или без /start): знакомство
    if not user.get("onboarded"):
        await _greet(message, _name(message))

    # 1. Пересланное сообщение банка о чаевых → на карту
    if message.forward_origin is not None:
        notif = p.parse_bank_notification(text)
        _queue_forwarded_tips(message,notif)
        return

    # 2. Текст уведомления банка, скопированный без пересылки
    if p.looks_like_bank_tips(text):
        notif = p.parse_bank_notification(text)
        if notif is not None:
            await _save_bank_tips(message, notif)
            return

    # Sales use their own journal; malformed sales must never become expenses.
    from sales_chat import parse_sales_message
    from sales_service import chat_write
    from uuid import uuid5, NAMESPACE_URL
    try:
        sale = parse_sales_message(text, op_today())
    except ValueError as e:
        await message.answer(html.escape(str(e)))
        return
    if sale:
        import research
        if sale['action']=='report':research.track(message.from_user.id,'sales_report_started')
        oid = str(uuid5(NAMESPACE_URL, f"budget-sale:{message.bot.id}:{message.chat.id}:{message.message_id}"))
        try:
            saved = await chat_write(message.from_user.id, oid, sale)
        except Exception:
            logging.error('Chat sales write failed')
            if sale['action']=='report':research.track(message.from_user.id,'sales_report_error',error_code='save')
            await message.answer("Ответ о сохранении не пришёл. Загляни в историю в разделе «План», прежде чем отправлять сумму снова.")
            return
        if sale['action']=='report':research.track(message.from_user.id,'sales_report_completed',operation=oid)
        if sale["action"] == "save":
            names = {"glass":"Бокалы","bottle":"Бутылки","cocktails":"Коктейли","desserts":"Десерты","turnover":"Товарооборот","postcards":"Открытки","dvd":"ДВД"}
            unit = "шт." if sale["kind"] in ("glass","cocktails","postcards","dvd") else "₽"
            if saved.get("voided"):
                await message.answer("Эта продажа уже отменена.")
                return
            await message.answer(f"✓ {names[sale['kind']]}: {fmt(sale['value'])} {unit} · смена {human_date(sale['work_date'])}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отменить", callback_data=f"saleundo:{oid}")]]))
        else:
            await message.answer("✓ " + ("Записал отчёт по " + human_date(sale["cutoff"]) + " включительно." if sale["action"] == "report" else "План продаж обновлён на " + human_month(sale["month"]) + ".") + " Исправления — в разделе «План».")
        return

    from service_charge import parse as parse_service_charge
    try:
        service_amount = parse_service_charge(text)
    except ValueError as error:
        await message.answer(html.escape(str(error)))
        return
    if service_amount is not None:
        record = {"kind": "accrual", "account": db.PENDING,
                  "signed_amount": service_amount, "category": "Сервисный сбор", "note": None}
        if user.get("private_money_mode"):
            await _send_private_record(message, record, 0)
            return
        try:
            entry = await db.add_entry(message.from_user.id, **record,
                work_date=op_today().isoformat(),
                source_key=f"telegram:{message.chat.id}:{message.message_id}:0")
        except Exception:
            logging.exception("Service charge write failed")
            await message.answer("Не удалось подтвердить запись. Проверь личные начисления в «Плане» или историю, прежде чем отправлять сумму снова.")
            return
        await message.answer("✓ " + entry_line(entry), reply_markup=undo_kb([entry["id"]]))
        return

    # 3. Расписание смен: «работаю 22 24 26» → ставим смены
    shift_dates = p.parse_shift_days(text, op_today())
    if shift_dates is not None:
        iso = [d.isoformat() for d in shift_dates]
        await db.add_shifts(message.from_user.id, iso)
        import research
        await research.record(message.from_user.id,'shift_planned',screen='chat')
        from ux_chat import schedule_saved
        await schedule_saved(message.from_user.id)
        human = ", ".join(d.strftime("%d.%m") for d in shift_dates)
        word = "смену" if len(shift_dates) == 1 else "смены"
        extra = ""
        try:
            import google_calendar as gcal
            token = await db.get_google_token(message.from_user.id)
            if token and (token.get("google_refresh_token") or token.get("google_access_token")):
                result = await gcal.sync_shifts(message.from_user.id, iso)
                extra = f"\n📆 В Google Календаре: {result['synced']} из {len(iso)}."
                if result["message"]: extra += "\n" + result["message"]
        except Exception:
            logging.exception("Calendar sync failed after shift save")
            extra = "\nВ Google Календарь смены пока не добавлены. Попробуй ещё раз позже."
        await message.answer(
            f"📅 Поставил {word}: <b>{human}</b>.{extra}\n"
            "Напомню накануне и в конце смены. Чаевые записывай, когда удобно.",
            reply_markup=calendar_retry_kb() if extra.startswith("\nВ Google Календарь смены пока") else None,
        )
        return

    # 4. Обычные записи: «500» или «чай 500», «кофе 200, такси 350»
    items = p.parse_transactions(text)
    if not items:
        await message.answer(
            "Не нашёл сумму. Примеры:\n"
            "<i>чай 500</i> · <i>смена 2500</i>\n"
            "Нажми кнопку ниже, если нужна подсказка.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Как пользоваться ботом",callback_data="ux:help")]]),
        )
        return

    if user.get('private_money_mode'):
        all_sent=True
        for index,item in enumerate(items):
            sent=await _send_private_record(message,{'kind':item['kind'],'account':item['account'],
                'signed_amount':item['amount']*KIND_SIGN[item['kind']],
                'category':item['category'],'note':item['note']},index,
                delete_source=all_sent and index==len(items)-1)
            all_sent=all_sent and sent
        return
    is_first_tx = not await db.get_recent_entries(message.from_user.id, limit=1)
    saved = []
    for index, item in enumerate(items):
        signed = item["amount"] * KIND_SIGN[item["kind"]]
        entry = await db.add_entry(
            message.from_user.id, item["kind"], item["account"], signed,
            category=item["category"], note=item["note"],
            source_key=f"telegram:{message.chat.id}:{message.message_id}:{index}",
        )
        saved.append(entry)

    body = "\n".join(entry_line(e) for e in saved)
    if is_first_tx:
        body += (
            "\n\n👌 Записал. Не тот способ получения — кнопка под записью, "
            "нужно убрать — «Отменить»."
        )
    toggle = saved[0] if len(saved) == 1 else None
    await message.answer(
        body + "\n\n" + await today_block(message.from_user.id),
        reply_markup=undo_kb([e["id"] for e in saved], toggle_entry=toggle),
    )

    from ux_chat import value_saved
    for entry in saved:
        await value_saved(message,entry)
