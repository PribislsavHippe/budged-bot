"""Все хендлеры бота. Трекер чаевых: регистрируем чай (и траты за смену),
считаем «чистыми за смену». Никакого баланса/остатка — это не бюджет.

Принцип: записываем сразу, отмена — одной кнопкой. Многошаговых диалогов нет.
"""
import csv
import html
import io
import logging
import os
import re

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
            text=f"Перенести на {db.ACCOUNT_LABELS[other].lower()}",
            callback_data=f"acc:{toggle_entry['id']}",
        )])
    ids = ",".join(str(i) for i in entry_ids)
    rows.append([InlineKeyboardButton(text="↩️ Отменить", callback_data=f"undo:{ids}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─── /start и знакомство ─────────────────────────────────────────────────────

def _welcome_text(name: str) -> str:
    return f"Привет, {html.escape(name)}! Спасибо, что тестируешь бота вместе с нами 💛"



def _name(message: Message) -> str:
    """Имя для приветствия — из самого сообщения Telegram, в базе мы его не держим."""
    return message.from_user.first_name or "друг"


async def _greet(message: Message, name: str):
    await db.set_onboarded(message.from_user.id)
    await message.answer(_welcome_text(name), reply_markup=main_menu())
    import research
    research.track(message.from_user.id,'user_started')
    from ux_chat import begin
    await begin(message)


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
    await message.answer('Помощь\n\nЕсли что-то не работает или есть идея — напиши нам.',reply_markup=help_buttons())
    await message.answer(
        "<b>Как я работаю</b>\n\n"
        "Чаевые: <i>500</i> или <i>чай 500</i>. Расход — с названием: <i>кофе 200</i>.\n"
        "Перешли сообщение банка о чаевых — разберу сумму и помогу сохранить.\n\n"
        "🧾 Записать расход — внести траты за смену (мойка, бар, еда…), "
        "покажу чистыми за смену\n"
        "📋 История — последние записи\n"
        "📊 Статистика — графики и календарь\n\n"
        "Продажи: <i>бокал</i> · <i>коктейль 2</i> · <i>открытка</i> · <i>двд</i>\n"
        "Суммы: <i>бутылка 3500</i> · <i>десерты 1200</i> · <i>оборот 25000</i>\n"
        "Личный сервисный сбор: <i>сс 2000</i> — начисление к зарплате, в чаевые не входит\n"
        "<i>план продаж вино 143000; коктейли 110; десерты 82000; оборот 1570000</i>\n"
        "<i>цена бокала 850</i> — оценка, не подтвержденная выручка\n"
        "<i>отчёт по 13 сентября, вино 73 238, коктейли 57</i> — итог с начала месяца\n"
        "/calendar — повторить отправку смен в Google\n\n"
        "<i>работаю 22 24 26</i> — поставить смены на эти дни; напомню о начале и завершении\n"
        "<i>план 2500</i> — цель по чаю на смену\n"
        "/undo — отменить последнюю запись\n"
        "/reset — очистить журнал\n\n"
        "🔒 /privacy — какие данные хранятся\n"
        "/export — забрать свои записи файлом\n"
        "/delete — удалить свои данные"
    )
    import schedule
    if schedule.enabled():
        await message.answer('Пришли фото графика → «График смен» или ссылку на Google Таблицу → выбери свою строку и месяц.\n'
                             '/sheet ссылка — прочитать открытую Google Таблицу с графиком\n'
                             '/hours — записать время ухода; /hours вчера — за прошлую смену\n'
                             '/rate 350 — твоя ставка за час\n/work — часы и заработок за месяц\n'
                             '/reminders — напоминания о смене\n'
                             '/learn — пройти знакомство ещё раз')
    from identity_chat import enabled
    if enabled():
        from identity_chat import buttons
        await message.answer('Твой ресторан, сотрудники и приглашения — по кнопкам ниже.',
                             reply_markup=buttons([[('Мой ресторан','ident:showprofile')],
                                                   [('Сотрудники и заявки','ident:showteam')],
                                                   [('Создать приглашение','ident:showinvite')]]))


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
            'Чаевые и расходы хранятся только на устройстве, где ты включил этот режим. '
            'Пересланное банковское сообщение видят Telegram и бот при обработке; '
            'чтобы запись попала на устройство, нажми кнопку в ответе бота. '
            'Суммы не сохраняются в Supabase.\n\n'
            'На сервере остаются твой Telegram ID, график и часы работы для напоминаний, '
            'открытый ключ устройства, отдельно созданные продажи, разрешения Google и события использования: время и тип действий, включая факт добавления чаевых или расхода, без суммы и содержимого записи. '
            'При смене телефона личный журнал не восстановится. Удалить его с этого устройства можно в «Статистике».')
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
    await callback.answer(f"Перенёс на {db.ACCOUNT_LABELS[other].lower()}")


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
                               delete_source: bool = True):
    """Encrypted one-shot handoff. No financial record is inserted into Supabase."""
    import private_payload
    from datetime import datetime, timezone
    user=await db.get_or_create_user(message.from_user.id)
    if not WEBHOOK_HOST or not user.get('private_money_public_key'):
        await message.answer('Открой «Статистику» на своём устройстве и настрой приватный журнал.')
        return False
    payload={**record,'id':f'telegram:{message.chat.id}:{message.message_id}:{index}',
             'work_date':op_today().isoformat(),
             'created_at':datetime.now(timezone.utc).isoformat()}
    try:
        sealed,signature=private_payload.seal(message.from_user.id,
            user['private_money_public_key'],payload,os.environ['BOT_TOKEN'])
    except Exception as error:
        from diagnostics import failure
        failure(error,area='private_money',stage='handoff')
        await message.answer('Не удалось подготовить запись. Открой приватный журнал и попробуй ещё раз.')
        return False
    url=f'{WEBHOOK_HOST}/app?private_entry={sealed}&private_sig={signature}'
    markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text='🔒 Сохранить на устройстве',web_app=WebAppInfo(url=url))]])
    intro = (f"Сервисный сбор {fmt(record['signed_amount'])} ₽ — начисление к зарплате. "
             if record['kind'] == 'accrual' else 'Разобрал запись. ')
    reply=await message.answer(intro+'Сохрани её на этом устройстве:',reply_markup=markup)
    if delete_source:
        try:
            await message.bot.delete_message(message.chat.id,message.message_id)
        except Exception as error:
            from diagnostics import failure
            failure(error,area='private_money',stage='delete_source')
            try:
                await reply.edit_text(intro+'Сохрани её на этом устройстве. '
                                      'Исходное сообщение осталось в чате — удали его вручную.',reply_markup=markup)
            except Exception:
                pass
    return True


async def _save_bank_tips(message: Message, notif: dict):
    """Чаевые из банковского уведомления → на карту, с чеком и процентом."""
    tips = notif["amount"]
    if (await db.get_or_create_user(message.from_user.id)).get('private_money_mode'):
        await _send_private_record(message,{'kind':'income','account':'card',
            'signed_amount':tips,'category':'Чаевые','note':'из банка',
            'order_amount':notif.get('order_amount'),'tip_percent':notif.get('tip_percent')},0)
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
        f"➕ Чаевые <b>{fmt(tips)} ₽</b>{details_str} → карта\n\n"
        + await today_block(message.from_user.id),
        reply_markup=undo_kb([entry["id"]], toggle_entry=entry),
    )
    from ux_chat import value_saved
    await value_saved(message,entry)


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
            "\n\n👌 Записал. Не тот счёт — кнопка «Перенести», "
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
