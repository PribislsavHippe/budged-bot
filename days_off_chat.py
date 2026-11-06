"""Кнопки выбора обязательных выходных в личном чате с ботом (без команд)."""
import logging
from datetime import date

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest

import days_off
import roster

router = Router()
router.callback_query.filter(F.message.chat.type == 'private', F.data.startswith(days_off.PREFIX + ':'))


async def _show(callback, text, markup):
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass  # то же самое сообщение: ничего менять не нужно


@router.callback_query()
async def choose(callback):
    parts = callback.data.split(':')
    try:
        kind, start, end = parts[1], date.fromisoformat(parts[2]), date.fromisoformat(parts[3])
        day = date.fromisoformat(parts[4]) if kind == 't' else None
        if kind not in ('t', 'd', 'e') or not 0 <= (end - start).days < roster.MAX_DAYS or (day and not start <= day <= end):
            raise ValueError
    except (ValueError, IndexError):
        await callback.answer('Эта кнопка устарела.', show_alert=True)
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
        chosen = await days_off.get(link['id'], start) or set()
        limit = roster.off_limit((end - start).days + 1, await days_off.is_student(link['restaurant_id'], link['id']))
        if kind == 't':
            if day < today:
                await callback.answer('Этот день уже прошёл.')
                return
            if day in chosen:
                chosen.discard(day)
            elif len(chosen) >= limit:
                await callback.answer(f'Можно выбрать до {limit}.', show_alert=True)
                return
            else:
                chosen.add(day)
            await days_off.save(link, start, end, chosen)
            await _show(callback, days_off.prompt_text(start, end, chosen, limit), days_off.keyboard(start, end, chosen, today))
        elif kind == 'd':
            await days_off.save(link, start, end, chosen)
            await _show(callback, days_off.summary_text(start, end, chosen), days_off.keyboard(start, end, chosen, today, finished=True))
        else:
            await _show(callback, days_off.prompt_text(start, end, chosen, limit), days_off.keyboard(start, end, chosen, today))
        await callback.answer()
    except Exception as error:
        from diagnostics import failure
        failure(error, area='days_off', stage='choose')
        logging.warning('days off: callback failed')
        await callback.answer('Сейчас не получается сохранить. Попробуй чуть позже.', show_alert=True)
