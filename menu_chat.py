"""Подсказки по меню в чате: «говяжьи щечки» → фото, аллергены и состав; «лук» → где он есть.

Поиск срабатывает, когда в сообщении нет суммы (`handlers` спрашивает сюда перед ответом «Не нашёл сумму»).
Меню загружает владелец бота: присылает боту PDF из папки «Подсказки». Новая загрузка того же файла заменяет
старую, старую можно вернуть кнопкой неделю. Карточки защищены от пересылки. Тексты — ЧЕРНОВИКИ.
"""
import html
import logging
import os

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton as Button, \
    InlineKeyboardMarkup as Markup, Message

import menu_store as store

router = Router()
CAPTION = 1024
LIST = 10

NO_ACCESS = 'Подсказки по меню пока доступны только владельцу бота.'
GONE = 'Этой позиции уже нет в меню: его обновили. Напиши название ещё раз.'
TOO_BIG = 'Файл больше 20 МБ — Telegram не отдаёт боту такие. Сожми PDF и пришли ещё раз.'
READING = 'Читаю «{source}»…'
EMPTY = 'В «{source}» не нашёл ни одной карточки с аллергенами — ничего не поменял.'
SAME = 'Этот файл уже загружен.'
UPLOAD_OFF = 'Меню сейчас загружается с компьютера, а не через чат. Файл не трогаю.'
FAILED = 'Не получилось загрузить файл. Попробуй ещё раз.'


def _admin(message):
    from admin import is_admin
    return bool(message.from_user) and is_admin(message.from_user.id)


def _name(card):
    name = card.get('name') or ''
    return name[:1] + name[1:].lower() if name.isupper() else name


def _short(card):
    weight = f" · {card['weight']}" if card.get('weight') else ''
    return f'{_name(card)}{weight}'[:60]


def text(card, full=True):
    """Карточка как в подсказке: название, аллергены, состав, подача, где блюда нет, откуда текст."""
    e = html.escape
    meta = ' · '.join(x for x in (card.get('weight'), card.get('strength')) if x)
    lines = [f"<b>{e(_name(card))}</b>" + (f' · {e(meta)}' if meta else '') + (' · NEW' if card.get('new') else '')]
    if card.get('english'):
        lines.append(e(card['english']))
    if card.get('subtitle'):
        lines.append(f"<i>{e(card['subtitle'].lower())}</i>")
    for note in card.get('notes') or []:
        lines.append(f'⚠️ {e(note)}')
    allergens = card.get('allergens') or []
    lines.append('')
    lines.append('<b>Аллергены:</b> ' + (e(', '.join(a[:1].lower() + a[1:] for a in allergens))
                                          if allergens else 'в подсказке не указаны'))
    if full:
        if card.get('composition'):
            lines += ['', '<b>Состав:</b>'] + [f'• {e(x)}' for x in card['composition']]
        if card.get('garnish'):
            lines.append('<b>Украшение:</b> ' + e(', '.join(card['garnish'])))
        if card.get('serving'):
            lines += ['', f"<b>Подача:</b> {e(card['serving'])}"]
    lines += ['', f"<i>{e(card.get('source') or '')}, стр. {card.get('page')}</i>"]
    return '\n'.join(lines)


def _buttons(item):
    if not (item['card'].get('description') or []):
        return None
    return Markup(inline_keyboard=[[Button(text='Описание', callback_data=f"menu:d:{item['id']}")]])


async def send_card(message, item):
    card = item['card']
    caption = text(card)
    rest = None
    if len(caption) > CAPTION:
        caption, rest = text(card, full=False), text(card)
    markup = _buttons(item)
    if item.get('photo_id'):
        try:
            file_id, data = await store.photo(item['photo_id'])
            if file_id or data:
                sent = await message.answer_photo(file_id or BufferedInputFile(data, 'dish.jpg'), caption=caption,
                                                  protect_content=True, reply_markup=None if rest else markup)
                if not file_id and sent.photo:
                    await store.remember_file_id(item['photo_id'], sent.photo[-1].file_id)
                if rest:
                    await message.answer(rest, protect_content=True, reply_markup=markup)
                return
        except Exception as error:
            logging.warning('menu photo not sent: %s', type(error).__name__)
    await message.answer(text(card), protect_content=True, reply_markup=markup)


async def answer(message, query):
    """Найти в меню. True — ответили карточкой или списком; False — не меню, пусть отвечают как раньше."""
    if not message.from_user or not await store.allowed(message.from_user.id):
        return False
    try:
        found = store.search(await store.items(), query)
    except Exception as error:
        from diagnostics import failure
        failure(error, area='menu', stage='search')
        return False
    if found is None:
        return False
    if found[0] == 'card':
        await send_card(message, found[1])
        return True
    _, matches, title, *key = found
    if key:
        await message.answer(html.escape(title), reply_markup=_page(matches, key[0], 0), protect_content=True)
        return True
    rows = [[Button(text=_short(i['card']), callback_data=f"menu:c:{i['id']}")] for i in matches[:LIST]]
    more = f'\nИ ещё {len(matches) - LIST} — уточни запрос.' if len(matches) > LIST else ''
    await message.answer(html.escape(title) + more, reply_markup=Markup(inline_keyboard=rows), protect_content=True)
    return True


def _page(matches, key, start):
    """Список по аллергену частями по LIST: кнопки позиций и «Ещё», пока не кончатся."""
    rows = [[Button(text=_short(i['card']), callback_data=f"menu:c:{i['id']}")] for i in matches[start:start + LIST]]
    left = len(matches) - start - LIST
    if left > 0:
        rows.append([Button(text=f'Ещё {min(left, LIST)} из {left}', callback_data=f'menu:g:{key}:{start + LIST}')])
    return Markup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('menu:g:'))
async def more_with_allergen(query: CallbackQuery):
    if not await store.allowed(query.from_user.id):
        await query.answer(NO_ACCESS, show_alert=True)
        return
    _, _, key, start = (query.data.split(':') + ['0'])[:4]
    await query.answer()
    if key not in store.ALLERGENS or not start.isdigit():
        return
    matches = store.with_allergen(await store.items(), key)
    start = int(start)
    if start >= len(matches):
        await query.message.answer(GONE)
        return
    await query.message.answer(f'Ещё, где есть {html.escape(store.ALLERGENS[key][1])}:',
                               reply_markup=_page(matches, key, start), protect_content=True)


@router.callback_query(F.data.startswith('menu:c:') | F.data.startswith('menu:d:'))
async def open_item(query: CallbackQuery):
    if not await store.allowed(query.from_user.id):
        await query.answer(NO_ACCESS, show_alert=True)
        return
    item = await store.item(query.data[7:])
    await query.answer()
    if item is None:
        await query.message.answer(GONE)
        return
    if query.data.startswith('menu:d:'):
        paragraphs = item['card'].get('description') or []
        body = html.escape('\n\n'.join(paragraphs))[:3800]
        await query.message.answer(f"<b>{html.escape(_name(item['card']))}</b>\n\n{body}", protect_content=True)
        return
    await send_card(query.message, item)


@router.message(F.chat.type == 'private', F.document.mime_type == 'application/pdf', _admin)
async def upload(message: Message):
    document = message.document
    source = store.source_name(document.file_name)
    if os.getenv('MENU_PDF_UPLOAD', '0').strip() != '1':
        # На бесплатном Render разбор PDF не помещается в память и роняет бота (18.11.2026): меню загружается
        # с компьютера (`menu_import.py`), а повторы этого сообщения из очереди проходят без разбора.
        await message.answer(UPLOAD_OFF)
        return
    if (document.file_size or 0) > store.MAX_BYTES:
        await message.answer(TOO_BIG)
        return
    await message.answer(READING.format(source=html.escape(source)))
    try:
        file = await message.bot.download(document)
        result = await store.import_pdf(message.from_user.id, document.file_unique_id, source, file.read())
    except Exception as error:
        from diagnostics import failure
        failure(error, area='menu', stage='import')
        await message.answer(FAILED)
        return
    if result['status'] == 'same':
        await message.answer(SAME)
        return
    if result['status'] == 'empty':
        await message.answer(EMPTY.format(source=html.escape(source)))
        return
    if result['status'] != 'ok':
        await message.answer(FAILED)
        return
    pages = ', '.join(str(s['page']) for s in result['skipped'][:15])
    lines = [f"Загрузил «{html.escape(source)}»: {result['count']} позиций, с фото — {result['photos']}."]
    if pages:
        lines.append(f'Не карточки (пропустил) — страницы {pages}.')
    lines.append('Проверь аллергены выборочно: напиши название блюда.')
    markup = None
    if result.get('previous'):
        markup = Markup(inline_keyboard=[[Button(text='Вернуть прежнюю версию',
                                                 callback_data=f"menu:u:{result['previous']}")]])
    await message.answer('\n'.join(lines), reply_markup=markup)


@router.callback_query(F.data.startswith('menu:u:'))
async def undo_import(query: CallbackQuery):
    from admin import is_admin
    if not is_admin(query.from_user.id):
        await query.answer(NO_ACCESS, show_alert=True)
        return
    status = await store.restore(query.data[7:])
    await query.answer()
    if status in ('ok', 'active'):
        await query.message.edit_reply_markup(reply_markup=None)
        await query.message.answer('Вернул прежнюю версию.')
    else:
        await query.message.answer('Прежней версии уже нет: её хранят неделю.')
