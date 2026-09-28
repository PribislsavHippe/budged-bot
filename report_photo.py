"""Private-chat report import with consent, ephemeral drafts and explicit confirmation."""
import asyncio
import html
import io
import logging
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid4, uuid5, NAMESPACE_URL

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import db
import report_vision as vision
from sales import month_key
from sales_chat import parse_sales_message
from sales_service import chat_write, save_report
from workday import op_today

router = Router()
router.message.filter(F.chat.type == 'private')
router.callback_query.filter(F.message.chat.type == 'private')
LABELS = {'wine': 'Вино', 'cocktails': 'Коктейли', 'desserts': 'Десерты',
          'turnover': 'Товарооборот', 'postcards': 'Открытки', 'dvd': 'ДВД'}
DRAFT_TTL = 15 * 60


class LimitedImage(io.BytesIO):
    def write(self, chunk):
        if self.tell() + len(chunk) > vision.MAX_BYTES:
            raise vision.VisionError('Пришли фото размером до 8 МБ.')
        return super().write(chunk)


@dataclass
class Draft:
    nonce: str
    oid: str
    file_id: str
    phase: str = 'consent'
    report: dict = field(default_factory=dict)
    totals: dict = field(default_factory=dict)
    cutoff: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


drafts: dict[int, Draft] = {}
_recognition_slots = asyncio.Semaphore(2)


def expire(uid, draft):
    if drafts.get(uid) is draft:
        drafts.pop(uid, None)


def kb(draft, buttons):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f'ocr:{draft.nonce}:{action}')]
        for label, action in buttons])


def date_valid(value):
    day = date.fromisoformat(value)
    month_key(day.isoformat()[:7])
    if day > op_today():
        raise ValueError('Дата отчёта не может быть в будущем.')
    return day.isoformat()


def preview(draft):
    lines = [f'<b>Отчёт по {draft.cutoff} включительно</b>']
    if draft.report.get('selected_name'):
        lines.append(html.escape(draft.report['selected_name']))
    for key, label in LABELS.items():
        value = draft.totals.get(key)
        unit = 'шт.' if key in ('cocktails', 'postcards', 'dvd') else '₽'
        lines.append(f'{label}: {value:g} {unit}' if value is not None else f'{label}: —')
    if draft.report.get('targets'):
        lines.append('\n<b>Месячный план</b>')
        for key, value in draft.report['targets'].items():
            lines.append(f'{LABELS[key]}: {value:g}')
    return '\n'.join(lines)


@router.message(Command('start', 'delete', 'reset', 'cancel'))
async def reset_draft(message):
    draft = drafts.get(message.from_user.id)
    if draft and draft.lock.locked():
        await message.answer('Дождись завершения обработки отчёта.')
        return
    if draft:
        expire(message.from_user.id, draft)
    if message.text.split()[0].split('@')[0] == '/cancel':
        await message.answer('Черновик закрыт.')
        return
    return UNHANDLED


@router.message(F.photo | (F.document.mime_type.in_({'image/jpeg', 'image/png'})))
async def receive_photo(message):
    if not vision.configured():
        await message.answer('Распознавание отчётов пока не подключено.')
        return
    old = drafts.get(message.from_user.id)
    if old and (old.lock.locked() or old.phase == 'uncertain'):
        await message.answer('Сначала заверши предыдущий отчёт.')
        return
    image = message.photo[-1] if message.photo else message.document
    if (image.file_size or 0) > vision.MAX_BYTES:
        await message.answer('Пришли фото размером до 8 МБ.')
        return
    if len(drafts) >= 100 and message.from_user.id not in drafts:
        await message.answer('Распознавание занято. Попробуй позже.')
        return
    oid = str(uuid5(NAMESPACE_URL, f'report-photo:{message.bot.id}:{message.chat.id}:{message.message_id}'))
    if old and old.oid == oid:
        return
    draft = Draft(uuid4().hex[:12], oid, image.file_id)
    drafts[message.from_user.id] = draft
    asyncio.get_running_loop().call_later(DRAFT_TTL, expire, message.from_user.id, draft)
    await message.answer('Распознать отчёт? Фото будет отправлено в Groq. Лучше оставить только свою строку и шапку.',
                         reply_markup=kb(draft, [('Распознать', 'read'), ('Отмена', 'cancel')]))


async def ask_date_or_preview(message, draft):
    try:
        draft.cutoff = date_valid(draft.cutoff)
    except (ValueError, TypeError):
        draft.phase = 'date'
        await message.answer('По какую дату отчёт включительно? Напиши ГГГГ-ММ-ДД.',
                             reply_markup=kb(draft, [('Отмена', 'cancel')]))
        return
    draft.phase = 'review'
    await message.answer(preview(draft), reply_markup=kb(draft, [
        ('Сохранить', 'save'), ('Исправить', 'edit'), ('Отмена', 'cancel')]))


@router.callback_query(F.data.startswith('ocr:'))
async def photo_callback(callback):
    parts = callback.data.split(':')
    draft = drafts.get(callback.from_user.id)
    if len(parts) != 3 or not draft or draft.nonce != parts[1]:
        await callback.answer('Черновик истёк. Пришли фото заново.', show_alert=True)
        return
    if draft.lock.locked():
        await callback.answer('Обрабатываю…')
        return
    await callback.answer()
    async with draft.lock:
        action = parts[2]
        if action == 'cancel':
            expire(callback.from_user.id, draft)
            await callback.message.edit_text('Черновик закрыт.')
        elif action == 'read' and draft.phase == 'consent':
            draft.phase = 'reading'
            await callback.message.edit_text('Распознаю…')
            try:
                if _recognition_slots.locked():
                    raise vision.VisionError('Распознавание занято. Попробуй через минуту.')
                async with _recognition_slots:
                    file = await callback.bot.get_file(draft.file_id)
                    if (file.file_size or 0) > vision.MAX_BYTES:
                        raise vision.VisionError('Пришли фото размером до 8 МБ.')
                    image = LimitedImage()
                    await callback.bot.download_file(file.file_path, destination=image)
                    draft.report = await vision.recognize(image.getvalue())
                draft.cutoff = draft.report['cutoff']
            except Exception as error:
                draft.phase = 'consent'
                text = str(error) if isinstance(error, vision.VisionError) else 'Не удалось скачать фото. Попробуй ещё раз.'
                await callback.message.edit_text(text, reply_markup=kb(draft, [('Повторить', 'read'), ('Отмена', 'cancel')]))
                return
            draft.phase = 'row'
            from identity_chat import enabled as identity_enabled
            if identity_enabled():
                import identity
                try:
                    index = await identity.report_row(callback.from_user.id,draft.report['rows'])
                except Exception:
                    logging.error('Identity lookup for photo failed')
                    index = None
                if index is not None:
                    row = draft.report.pop('rows')[index]
                    draft.totals = row['totals']
                    draft.report['selected_name'] = row['name']
                    await callback.message.edit_text('Твоя строка найдена.')
                    await ask_date_or_preview(callback.message,draft)
                    return
            await callback.message.edit_text('Выбери свою строку:', reply_markup=kb(draft,
                [(row['name'] or f'Строка {i+1}', f'row{i}') for i, row in enumerate(draft.report['rows'])] + [('Отмена', 'cancel')]))
        elif action.startswith('row') and draft.phase == 'row':
            try:
                index = int(action[3:])
                if not 0 <= index < len(draft.report['rows']):
                    return
                draft.totals = draft.report['rows'][index]['totals']
                draft.report['selected_name'] = draft.report['rows'][index]['name']
            except ValueError:
                return
            draft.report.pop('rows', None)  # Discard other employees immediately.
            await callback.message.edit_text('Строка выбрана.')
            await ask_date_or_preview(callback.message, draft)
        elif action == 'edit' and draft.phase == 'review':
            draft.phase = 'edit'
            await callback.message.answer('Пришли исправленный отчёт: отчет ГГГГ-ММ-ДД вино 73238; коктейли 57. '
                                          'Будут сохранены только перечисленные показатели; план из фото будет пропущен.',
                                          reply_markup=kb(draft, [('Отмена', 'cancel')]))
        elif action == 'save' and draft.phase in ('review', 'uncertain'):
            draft.phase = 'uncertain'
            try:
                await db.get_or_create_user(callback.from_user.id)
                month = draft.cutoff[:7]
                if draft.report.get('targets'):
                    await chat_write(callback.from_user.id, draft.oid, {'action': 'settings', 'month': month,
                                                                      'targets': draft.report['targets']})
                await save_report(callback.from_user.id, draft.oid, month, draft.cutoff, draft.totals)
            except Exception:
                logging.error('Photo report save failed')
                await callback.message.answer('Не удалось подтвердить сохранение. План мог уже обновиться. Повтори сохранение этого отчёта; повтор не создаст дубль.',
                                             reply_markup=kb(draft, [('Повторить сохранение', 'save')]))
                return
            expire(callback.from_user.id, draft)
            await callback.message.edit_text('Отчёт сохранён. План месяца обновлён.')


async def waiting_text(message):
    draft = drafts.get(message.from_user.id)
    return bool(draft and draft.phase in ('date', 'edit') and message.text and not message.text.startswith('/'))


@router.message(waiting_text, F.text)
async def photo_text(message):
    draft = drafts.get(message.from_user.id)
    if not draft or draft.lock.locked():
        return
    async with draft.lock:
        try:
            if draft.phase == 'date':
                draft.cutoff = date_valid(message.text.strip())
            else:
                command = parse_sales_message(message.text, op_today())
                if not command or command['action'] != 'report':
                    raise ValueError('Нужен текст вида: отчет ГГГГ-ММ-ДД вино 73238; коктейли 57')
                draft.cutoff = date_valid(command['cutoff'])
                draft.totals = command['totals']
                draft.report['targets'] = {}
            await ask_date_or_preview(message, draft)
        except (ValueError, TypeError):
            await message.answer('Проверь дату и числа. ' + ('Дата: ГГГГ-ММ-ДД.' if draft.phase == 'date' else
                                                          'Пример: отчет 2026-09-13 вино 73238; коктейли 57'))
