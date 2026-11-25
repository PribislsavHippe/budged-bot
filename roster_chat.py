"""Прототип сборки графика в чате. Только для владельца; остальным команда не отвечает.

Экран и формат ввода временные. Ничего не сохраняется: график живёт в ответе бота.
"""
import asyncio
import html

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

import roster
from admin import is_admin
from workday import op_today

router = Router()
router.message.filter(F.chat.type == 'private', F.from_user.func(lambda user: is_admin(user.id)))

MESSAGE_LIMIT = 3800
HOLIDAY_OFF_CALLBACK = 'roster:holiday-off:2026'


def chunks(text):
    """Режет длинный разбор по строкам: у Telegram предел на длину сообщения."""
    part = ''
    for line in text.split('\n'):
        if part and len(part) + len(line) + 1 > MESSAGE_LIMIT:
            yield part
            part = ''
        part += ('\n' if part else '') + line
    if part:
        yield part


@router.message(Command('holidayoffs'))
async def holiday_off_prompt(message):
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='Запустить сбор на оба периода', callback_data=HOLIDAY_OFF_CALLBACK),
    ]])
    await message.answer('Сбор выходных на 16–31 декабря и 1–15 января.', reply_markup=markup)


@router.callback_query(F.message.chat.type == 'private', F.data == HOLIDAY_OFF_CALLBACK)
async def holiday_off_launch(callback):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await callback.answer()
    from holiday_offs import launch
    try:
        status = await launch(callback.bot, callback.from_user.id)
    except Exception as error:
        from diagnostics import failure
        failure(error, area='roster_wishes', stage='holiday_invitation')
        status = 'Не получил подтверждение рассылки. Открой кнопку ещё раз, чтобы продолжить.'
    await callback.message.answer(status)


@router.message(Command('roster'))
async def roster_command(message, command: CommandObject):
    try:
        request = roster.parse_request(command.args, op_today())
    except roster.RosterInputError as error:
        await message.answer(html.escape(str(error)))
        return
    try:
        table = await asyncio.to_thread(roster.build, request)
    except Exception as error:
        from diagnostics import failure
        failure(error, area='roster', stage='build')
        await message.answer('Не получилось собрать график. Проверь ввод и попробуй ещё раз.')
        return
    schedule = roster.as_schedule(request, table)
    findings = roster.audit(request, schedule)
    for part in chunks(roster.report(request, schedule, findings)):
        await message.answer(html.escape(part))
    document = roster.render_html(request, schedule, findings).encode('utf-8')
    name = f'grafik-{request.start.isoformat()}-{request.end.isoformat()}.html'
    await message.answer_document(BufferedInputFile(document, filename=name))
