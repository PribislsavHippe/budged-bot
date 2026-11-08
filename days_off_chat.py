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
        await message.answer(days_off.summary_text(start, end, chosen, limit), reply_markup=days_off.edit_markup(start, end))
    except Exception as error:
        from diagnostics import failure
        failure(error, area='days_off', stage='write')
        logging.warning('days off: text failed')
        await message.answer('Сейчас не получается сохранить. Попробуй чуть позже.')


async def off_request(message):
    """Фильтр: просьба о выходных обычной фразой — «выходной по вторникам», «нужно 17 и 19»."""
    found = days_off.parse_request(message.text)
    if not found:
        return False
    try:
        link = await days_off.member(message.from_user.id)
    except Exception as error:
        from diagnostics import failure
        failure(error, area='days_off', stage='request_member')
        return False
    return {'found': found, 'link': link}


@router.message(off_request)
async def write_request(message, found, link, state):
    from postgrest.exceptions import APIError
    from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
    # Такую фразу нельзя принимать за деньги («нужны выходные 17, 19» — не расход 17 ₽).
    if not link:
        await message.answer('Выходные записывают сотрудники ресторана. Когда менеджер подтвердит тебя, напиши ещё раз.')
        return
    saved, problems, buttons = [], [], []
    try:
        if found['weekdays'] or found['pattern']:
            try:
                # Люди пишут по частям («воскресенья», потом «и 2/2»): новое добавляется к прежнему пожеланию.
                before = await days_off.wish_of(link['id'])
                days = sorted(set(before['days']) | set(found['weekdays']))
                pattern = found['pattern'] or before['pattern']
                full = await days_off.save_weekdays(link, days, pattern)
                bits = ([f'выходные {days_off.weekdays_text(days)}'] if days else []) + \
                       ([f'график {pattern}'] if pattern and full else [])
                if bits:
                    saved.append('пожелание: ' + ', '.join(bits))
                    buttons.append([Button(text='Удалить пожелание', callback_data=f'{days_off.PREFIX}:w')])
                if found['pattern'] and not full:
                    problems.append(f"«{found['pattern']}» в боте пока не сохраняется — нажми «Передать Леше весь текст».")
            except APIError as error:
                if error.code not in days_off.MISSING:
                    raise
                problems.append('Пожелания по дням недели пока не подключены.')
        if found['dates']:
            start, end, chosen, extra = await days_off.place_dates(link, message.from_user.id, found['dates'])
            if chosen:
                await days_off.save(link, start, end, chosen)
                import research
                research.track(message.from_user.id, 'days_off_answered', source='bot', screen='schedule')
                saved.append(f'выходные {days_off.days_text(chosen)}')
                note = days_off.over_note(chosen, extra).strip()
                if note:
                    problems.append(note)
            else:
                problems.append(extra)
    except Exception as error:
        from diagnostics import failure
        failure(error, area='days_off', stage='request_save')
        await message.answer('Сейчас не получается сохранить. Попробуй чуть позже.')
        return
    lines = []
    if saved:
        lines.append('Записал ' + ' и '.join(saved) + '.')
    if saved and saved[0].startswith('пожелание'):
        lines.append('Это пожелание, а не обязательный выходной: программа постарается его учесть, менеджер увидит '
                     'его в графике. Чтобы дополнить, просто напиши ещё; чтобы начать заново — «Удалить пожелание».')
    lines += problems
    # Целиком сообщение можно передать Леше: в нём может быть что-то ещё.
    await state.update_data(wish_text=(message.text or '').strip()[:2000], wish_message=message.message_id)
    buttons.append([Button(text='Передать Леше весь текст', callback_data='ux:wishsend')])
    await message.answer('\n\n'.join(lines), reply_markup=Markup(inline_keyboard=buttons))


@router.callback_query(F.data == f'{days_off.PREFIX}:w')
async def clear_weekday_wish(callback):
    try:
        link = await days_off.member(callback.from_user.id)
        if link:
            await days_off.save_weekdays(link, [])
        await callback.answer()
        await callback.message.edit_text('Пожелание удалил. Напиши новое, когда захочешь.')
    except Exception as error:
        from diagnostics import failure
        failure(error, area='days_off', stage='weekday_clear')
        await callback.answer('Сейчас не получается. Попробуй чуть позже.', show_alert=True)


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
