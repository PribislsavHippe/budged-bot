"""Обязательные выходные обычным текстом в личном чате с бота (без команд).

Бот просит написать числа и после этого ждёт их от сотрудника (`days_off.waiting`). Принимаются только
числа периода: у графика 16–30 число 5 бот не примет. Любое другое сообщение уходит в обычную обработку.
"""
import logging
from datetime import date

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter

import days_off
import roster

router = Router()
router.message.filter(F.chat.type == 'private', F.text, StateFilter(None))
router.callback_query.filter(F.message.chat.type == 'private', F.data.startswith(days_off.PREFIX + ':'))


async def expecting(message):
    """Фильтр: сообщение похоже на выходные и бот ждёт их от этого человека."""
    parsed = days_off.parse(message.text)
    if not parsed:
        return False
    try:
        wait = await days_off.waiting(message.from_user.id)
    except Exception as error:
        from diagnostics import failure
        failure(error, area='days_off', stage='waiting')
        return False
    return {'parsed': parsed, 'wait': wait} if wait else False


@router.message(expecting)
async def write(message, parsed, wait):
    link, start, end = wait['link'], wait['start'], wait['end']
    try:
        today = days_off.roster_today()
        limit = roster.off_limit((end - start).days + 1, await days_off.is_student(link['restaurant_id'], link['id']))
        chosen, problem = days_off.resolve(parsed, start, end, today, limit)
        if problem:
            await message.answer(problem)
            return
        await days_off.save(link, start, end, chosen)
        import research
        research.track(message.from_user.id, 'days_off_answered', source='bot', screen='schedule')
        await message.answer(days_off.summary_text(start, end, chosen), reply_markup=days_off.edit_markup(start, end))
    except Exception as error:
        from diagnostics import failure
        failure(error, area='days_off', stage='write')
        logging.warning('days off: text failed')
        await message.answer('Сейчас не получается сохранить. Попробуй чуть позже.')


@router.callback_query()
async def change(callback):
    """«Изменить»: бот снова ждёт числа. Кнопки-даты прежней версии больше не работают."""
    parts = callback.data.split(':')
    try:
        if parts[1] != 'e':
            raise ValueError
        start, end = date.fromisoformat(parts[2]), date.fromisoformat(parts[3])
        if not 0 <= (end - start).days < roster.MAX_DAYS:
            raise ValueError
    except (ValueError, IndexError):
        await callback.answer('Эта кнопка устарела. Напиши числа выходных сообщением.', show_alert=True)
        return
    try:
        link = await days_off.member(callback.from_user.id)
        if not link:
            await callback.answer('Выходные записывают сотрудники ресторана.', show_alert=True)
            return
        today = days_off.roster_today()
        if end < today or not await days_off.period_exists(link['restaurant_id'], start, end):
            await callback.answer('Этот график уже закрыт.', show_alert=True)
            return
        await days_off.reopen(link, start)
        limit = roster.off_limit((end - start).days + 1, await days_off.is_student(link['restaurant_id'], link['id']))
        try:
            await callback.message.edit_text(days_off.prompt_text(start, end, limit, today, again=True))
        except TelegramBadRequest:
            pass
        await callback.answer()
    except Exception as error:
        from diagnostics import failure
        failure(error, area='days_off', stage='change')
        logging.warning('days off: change failed')
        await callback.answer('Сейчас не получается. Попробуй чуть позже.', show_alert=True)
