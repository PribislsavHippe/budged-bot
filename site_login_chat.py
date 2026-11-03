"""Вход на сайт ресторана через бота: ссылка с сайта приводит сюда, человек сверяет код и подтверждает.

Пускаем только тех, кому доступен кабинет ресторана; сам вход и cookie выдаёт site_auth.
"""
import logging

from aiogram import F, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup

import site_auth

router = Router()
router.message.filter(F.chat.type == 'private')
router.callback_query.filter(F.message.chat.type == 'private')

EXPIRED = 'Ссылка для входа устарела. Открой сайт и нажми «Войти через Telegram» ещё раз.'


async def has_cabinet(user_id):
    import identity
    from admin import is_admin
    from identity_chat import enabled
    if not enabled():
        return False
    if is_admin(user_id) or await identity.owner_restaurants(user_id):
        return True
    # Менеджер графика входит на сайт ради графика; до migration_v30 такого права нет.
    from postgrest.exceptions import APIError
    from roster_api import schedulable, MISSING
    try:
        return bool(await schedulable(user_id))
    except APIError as error:
        if error.code not in MISSING:
            raise
        return False


@router.message(CommandStart(deep_link=True))
async def start_site_login(message, command):
    if not (command.args or '').startswith(site_auth.START_PREFIX):
        return UNHANDLED
    token = command.args[len(site_auth.START_PREFIX):]
    try:
        allowed = await has_cabinet(message.from_user.id)
    except Exception:
        logging.warning('site login: access check failed')
        await message.answer('Сейчас не получается открыть данные ресторана. Попробуй чуть позже.')
        return
    if not allowed:
        await message.answer('Кабинет доступен администратору ресторана.')
        return
    code = site_auth.claim(token, message.from_user.id)
    if code is None:
        await message.answer(EXPIRED)
        return
    await message.answer(
        f'Вход в кабинет ресторана на компьютере. Код на сайте: {code}.\n'
        'Если сайт открывал не ты, нажми «Отмена».',
        reply_markup=Markup(inline_keyboard=[[Button(text='Войти', callback_data=f'site:ok:{token}'),
                                              Button(text='Отмена', callback_data=f'site:no:{token}')]]))


@router.callback_query(F.data.startswith('site:'))
async def decide_site_login(callback):
    _, action, token = (callback.data.split(':', 2) + ['', ''])[:3]
    approve = action == 'ok'
    done = site_auth.decide(token, callback.from_user.id, approve)
    await callback.answer()
    text = EXPIRED if not done else 'Готово, вернись на сайт.' if approve else 'Вход отменён.'
    try:
        await callback.message.edit_text(text)
    except Exception:
        # Старое или уже изменённое сообщение: ответим новым, вход от этого не зависит.
        await callback.message.answer(text)
