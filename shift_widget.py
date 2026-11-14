"""Карточка смены прямо в сообщении: HTML-виджет Telegram (Bot API 10.4).

Виджет работает пока только в личном чате с ботом, а у официанта вечернее напоминание именно там.
Карточка статична: без скриптов, запросов к боту и внешних файлов. Если Telegram виджет не принял, у человека
не слишком новое приложение или карточка велика, отправляется обычный текст: напоминание не теряется.
Включается переменной `SHIFT_WIDGETS=1`; по умолчанию выключено, пока карточку не увидят на настоящих телефонах.
"""
import html as htmllib
import logging
import os
import zlib
from datetime import date

from aiogram.methods import TelegramMethod
from aiogram.types import Message

WEEK = ('понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье')
MONTHS = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа',
          'сентября', 'октября', 'ноября', 'декабря')
MAX_HTML, MAX_PACKED = 32768, 4096


class SendWidget(TelegramMethod[Message]):
    """Метод Bot API `sendWidget`; в aiogram 3.31 его ещё нет."""
    __returning__ = Message
    __api_method__ = 'sendWidget'

    chat_id: int
    html: str
    caption: str | None = None
    allow_queries: bool | None = None
    width: int | None = None
    height: int | None = None
    protect_content: bool | None = None


def enabled():
    return os.getenv('SHIFT_WIDGETS', '0').strip().lower() in {'1', 'true', 'yes', 'on'}


def clock(value):
    return str(value)[:5] if value else ''


def card(day, start=None, end=None, lead='Завтра'):
    """HTML карточки смены. Цвета берутся из темы чата, поэтому карточка подходит и светлой, и тёмной."""
    when = date.fromisoformat(str(day)[:10])
    title = f'{lead}, {WEEK[when.weekday()]} {when.day} {MONTHS[when.month - 1]}'
    start, end = clock(start), clock(end)
    hours = ''
    if start and end:
        a, b = int(start[:2]) * 60 + int(start[3:5]), int(end[:2]) * 60 + int(end[3:5])
        length = ((b - a) % 1440) / 60
        hours = f'до {end} · {length:g} ч'
    elif not start:
        hours = 'время уточни у менеджера'
    big = htmllib.escape(start or 'Смена')
    return f'''<style>
.c{{box-sizing:border-box;font:16px/1.3 -apple-system,system-ui,sans-serif;padding:18px 20px;border-radius:18px;
 color:var(--tg-theme-text-color,#172126);
 background:linear-gradient(100deg,#efc1da 0%,#efc1da 14%,#cec99f 40%,#dcd6f6 72%,#b5d0d9 100%)}}
.k{{font-size:14px;opacity:.7}}
.t{{font-size:44px;font-weight:700;letter-spacing:-.02em;line-height:1.05;margin:6px 0 4px}}
.s{{font-size:15px;opacity:.75}}
@media (prefers-color-scheme:dark){{.c{{color:#172126}}}}
</style>
<div class="c"><div class="k">{htmllib.escape(title)}</div><div class="t">{big}</div><div class="s">{htmllib.escape(hours)}</div></div>'''


def fits(markup):
    raw = markup.encode('utf-8')
    return b'\x00' not in raw and len(raw) <= MAX_HTML and len(zlib.compress(raw, 9)) <= MAX_PACKED


async def send_or_text(bot, chat_id, text, day, start=None, end=None, lead='Завтра'):
    """Карточка смены, а при любой неудаче — тот же текст обычным сообщением."""
    if enabled():
        markup = card(day, start, end, lead)
        if fits(markup):
            try:
                return await bot(SendWidget(chat_id=chat_id, html=markup, caption=text, height=0, protect_content=True))
            except Exception as error:
                logging.warning('shift widget refused: %s', type(error).__name__)
    return await bot.send_message(chat_id, text)
