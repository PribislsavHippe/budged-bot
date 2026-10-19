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
from sales_chat import parse_sales_message, parse_report_edit
from chat_dates import parse_date, human_date
from sales_service import chat_write, save_report
from workday import op_today

router = Router()
router.message.filter(F.chat.type == 'private')
router.callback_query.filter(F.message.chat.type == 'private')
LABELS = {'wine': 'Вино', 'cocktails': 'Коктейли', 'desserts': 'Десерты',
          'turnover': 'Товарооборот', 'postcards': 'Открытки', 'dvd': 'ДВД'}
DRAFT_TTL = 15 * 60
MAX_IMAGE_MEMORY = 32 * 1024 * 1024


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
    image: bytes | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


drafts: dict[int, Draft] = {}
_recognition_slots = asyncio.Semaphore(2)


def expire(uid, draft):
    if drafts.get(uid) is draft:
        drafts.pop(uid, None)
        draft.image = None


def kb(draft, buttons):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f'ocr:{draft.nonce}:{action}')]
        for label, action in buttons])


def date_valid(value):
    day = parse_date(value, op_today())
    month_key(day.isoformat()[:7])
    if day > op_today():
        raise ValueError('Дата отчёта не может быть в будущем.')
    return day.isoformat()


def number(value):
    return f'{value:,.2f}'.rstrip('0').rstrip('.').replace(',', ' ').replace('.', ',')


def preview(draft):
    lines = [f"<b>Отчёт по {human_date(draft.cutoff) if draft.cutoff else '—'} включительно</b>"]
    if draft.report.get('selected_name'):
        lines.append(html.escape(draft.report['selected_name']))
    if draft.report.get('category_config'):
        cfg=draft.report['category_config']
        lines.append(html.escape(cfg['month']))
        for item in cfg['definitions']:
            value=draft.report.get('category_values',{}).get(item['id'])
            if value is None:continue
            unit={'count':'шт.','amount':'₽','number':''}[item['unit']]
            lines.append(html.escape(item['name'])+': '+number(value['value'])+' '+unit+
                         ' · по '+human_date(value['cutoff']))
        return '\n'.join(lines)
    for key, label in LABELS.items():
        value = draft.totals.get(key)
        unit = 'шт.' if key in ('cocktails', 'postcards', 'dvd') else '₽'
        mark = '⚠ ' if key in draft.report.get('warnings', []) else ''
        lines.append(mark + (f'{label}: {number(value)} {unit}' if value is not None else f'{label}: —'))
    if draft.report.get('row_mode'):
        lines.append('\nПроверка по плану: ' + draft.report.get('check_status','—'))
    if draft.report.get('targets'):
        lines.append('\n<b>Месячный план</b>')
        for key, value in draft.report['targets'].items():
            lines.append(f'{LABELS[key]}: {number(value)}')
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
        await message.answer('Хорошо, этот отчёт не сохраняю.')
        return
    return UNHANDLED


@router.message(F.photo | (F.document.mime_type.in_({'image/jpeg', 'image/png'})))
async def receive_photo(message):
    if not vision.configured():
        await message.answer('Читать фото пока не умею. Можно записать отчёт сообщением.')
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
        await message.answer('Сейчас читаю другие отчёты. Попробуй через минуту.')
        return
    oid = str(uuid5(NAMESPACE_URL, f'report-photo:{message.bot.id}:{message.chat.id}:{message.message_id}'))
    if old and old.oid == oid:
        return
    if old:
        expire(message.from_user.id, old)
    draft = Draft(uuid4().hex[:12], oid, image.file_id)
    drafts[message.from_user.id] = draft
    asyncio.get_running_loop().call_later(DRAFT_TTL, expire, message.from_user.id, draft)
    import schedule
    options=[('Отчёт продаж','read')]+([('График смен','schedule')] if schedule.enabled() else [])+[('Отмена','cancel')]
    await message.answer('Что на фото? Для распознавания отправлю изображение в Groq.',reply_markup=kb(draft,options))


async def ask_date_or_preview(message, draft):
    try:
        draft.cutoff = date_valid(draft.cutoff)
    except (ValueError, TypeError):
        draft.phase = 'date'
        await message.answer('По какой день этот отчёт? Например: «13 сентября», «13.09» или «вчера».',
                             reply_markup=kb(draft, [('Отмена', 'cancel')]))
        return
    if draft.report.get('category_config'):
        import categories
        cfg=draft.report['category_config']
        if cfg.get('discovered') and cfg['month']!=draft.cutoff[:7] and draft.file_id:
            stored=await categories.config(cfg['restaurant_id'],draft.cutoff[:7]) if cfg['restaurant_id'] else None
            base=stored or {**cfg,'month':draft.cutoff[:7],'definitions':[]}
            new=categories.discover(base,[{'name':d['name'],'unit':d['unit'] if d.get('unit_source')=='explicit' else None} for d in cfg['definitions']])
            old_ids={categories.key(d['name']):d['id'] for d in cfg['definitions']}
            values=draft.report['category_values']
            draft.report['category_values']={d['id']:{**values[old_ids[categories.key(d['name'])]],'cutoff':draft.cutoff} for d in new['definitions'] if old_ids[categories.key(d['name'])] in values}
            draft.report['category_config']=new
        try:
            for value in draft.report['category_values'].values():
                if value.get('cutoff') is None:value['cutoff']=draft.cutoff
            draft.report['category_values']=categories.validate_values(draft.report['category_config'],draft.report['category_values'])
        except ValueError:
            from report_sheet_chat import report_failure
            await report_failure(message,draft,ValueError('category_date_or_values'))
            draft.phase='date'
            return
    if draft.report.get('row_mode'):
        await check_plan(message.chat.id, draft)
    draft.phase = 'review'
    markup = kb(draft, [('Сохранить', 'save'), ('Исправить', 'edit'), ('Отмена', 'cancel')])
    if draft.report.get('sheet_source'):
        from report_sheet_chat import buttons
        markup.inline_keyboard.insert(-1, buttons(draft, [('Начать заново', 'restart')]).inline_keyboard[0])
    await message.answer(preview(draft), reply_markup=markup)


@router.callback_query(F.data.startswith('ocr:'))
async def photo_callback(callback):
    parts = callback.data.split(':')
    draft = drafts.get(callback.from_user.id)
    if len(parts) != 3 or not draft or draft.nonce != parts[1]:
        await callback.answer('Это фото уже закрылось. Пришли его ещё раз.', show_alert=True)
        return
    if draft.lock.locked():
        await callback.answer('Ещё читаю фото…')
        return
    await callback.answer()
    async with draft.lock:
        action = parts[2]
        if action.startswith('cr') and draft.phase=='category_restaurant':
            from admin import is_admin
            import identity,categories
            if not is_admin(callback.from_user.id):return
            try:
                index=int(action[2:]);choices=draft.report['category_choices']
                if not 0<=index<len(choices):return
                selected=choices[index]['config']
                if not await identity.owner_restaurant(callback.from_user.id,selected['restaurant_id']):return
                selected=await categories.config(selected['restaurant_id'],selected['month'])
                if selected is None:selected=categories.empty_context(choices[index]['config']['restaurant_id'],choices[index]['config']['month'],callback.from_user.id)
            except (ValueError,IndexError,KeyError):return
            draft.report={'category_config':selected};draft.phase='consent';action='read'
        if action=='restart' and draft.phase!='uncertain' and draft.file_id:
            draft.phase='consent';draft.report={};draft.image=None;action='read'
        if action == 'cancel':
            expire(callback.from_user.id, draft)
            await callback.message.edit_text('Хорошо, этот отчёт не сохраняю.')
        elif action == 'schedule' and draft.phase == 'consent':
            from schedule_chat import start_photo
            await start_photo(callback,draft)
        elif action == 'read' and draft.phase == 'consent':
            import research
            research.track(callback.from_user.id,'sales_report_started')
            research.track(callback.from_user.id,'vision_started')
            draft.phase = 'reading'
            await callback.message.edit_text('Распознаю…')
            cfg=None
            try:
                if _recognition_slots.locked():
                    raise vision.VisionError('Распознавание занято. Попробуй через минуту.')
                async with _recognition_slots:
                    file = await callback.bot.get_file(draft.file_id)
                    if (file.file_size or 0) > vision.MAX_BYTES:
                        raise vision.VisionError('Пришли фото размером до 8 МБ.')
                    image = LimitedImage()
                    await callback.bot.download_file(file.file_path, destination=image)
                    import categories
                    cfg=draft.report.get('category_config') or await categories.context(callback.from_user.id,op_today().strftime('%Y-%m'))
                    if cfg is None:
                        choices=await categories.options(callback.from_user.id,op_today().strftime('%Y-%m'))
                        if len(choices)==1:cfg=choices[0]['config']
                        elif len(choices)>1:
                            draft.phase='category_restaurant';draft.report={'category_choices':choices}
                            await callback.message.edit_text('Для какого ресторана эти результаты?',reply_markup=kb(draft,[(c['name'],f'cr{i}') for i,c in enumerate(choices)]+[('Отмена','cancel')]))
                            return
                    if cfg:
                        import category_groq
                        draft.report=await category_groq.recognize(image.getvalue(),cfg)
                    elif vision.row_mode():
                        data = image.getvalue()
                        if sum(len(item.image or b'') for item in drafts.values() if item is not draft) + len(data) > MAX_IMAGE_MEMORY:
                            raise vision.VisionError('Сейчас читаю другие отчёты. Попробуй через минуту.')
                        draft.image = data
                        draft.report = await vision.recognize_directory(draft.image)
                        draft.report['row_mode'] = True
                    else:
                        draft.report = await vision.recognize(image.getvalue())
                draft.cutoff = draft.report['cutoff']
            except Exception as error:
                research.track(callback.from_user.id,'vision_failed',error_code='vision')
                draft.image = None
                draft.phase = 'consent'
                if cfg:
                    from report_sheet_chat import report_failure
                    await report_failure(callback.message,draft,ValueError('category_photo_read'))
                    return
                text = str(error) if isinstance(error, vision.VisionError) else 'Не удалось скачать фото. Попробуй ещё раз.'
                await callback.message.edit_text(text, reply_markup=kb(draft, [('Повторить', 'read'), ('Отмена', 'cancel')]))
                return
            research.track(callback.from_user.id,'vision_completed')
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
                    if not await select_row(callback.message, draft, index):
                        return
                    await callback.message.edit_text('Нашёл твою строку. Проверь цифры.')
                    await ask_date_or_preview(callback.message,draft)
                    return
            await callback.message.edit_text('Выбери свою строку:', reply_markup=kb(draft,
                [(row['name'] or f'Строка {i+1}', f'row{i}') for i, row in enumerate(draft.report['rows'])] + [('Отмена', 'cancel')]))
        elif action.startswith('row') and draft.phase == 'row':
            try:
                index = int(action[3:])
                if not 0 <= index < len(draft.report['rows']):
                    return
                if not await select_row(callback.message, draft, index):
                    return
            except ValueError:
                return
            draft.report.pop('rows', None)  # Discard other employees immediately.
            await callback.message.edit_text('Нашёл. Проверь цифры перед сохранением.')
            await ask_date_or_preview(callback.message, draft)
        elif action == 'edit' and draft.phase == 'review':
            draft.phase = 'edit'
            if draft.report.get('category_config'):
                await callback.message.answer('Напиши, что исправить, обычным сообщением — например: «гарниров на 1500».',reply_markup=kb(draft,[('Отмена','cancel')]))
                return
            await callback.message.answer('Напиши, что исправить: «вино 73 238, коктейлей 57». Остальные числа оставлю. '
                                          'Чтобы поменять и дату: «отчёт за вчера, вино 73 238».',
                                          reply_markup=kb(draft, [('Отмена', 'cancel')]))
        elif action == 'save' and draft.phase in ('review', 'uncertain'):
            draft.phase = 'uncertain'
            try:
                await db.get_or_create_user(callback.from_user.id)
                month = draft.cutoff[:7]
                if not draft.report.get('category_config') and draft.report.get('targets'):
                    await chat_write(callback.from_user.id, draft.oid, {'action': 'settings', 'month': month,
                                                                      'targets': draft.report['targets']})
                if draft.report.get('category_config'):
                    import categories
                    await categories.save(callback.from_user.id,draft.oid,draft.report['category_config'],draft.report['category_values'])
                else:
                    await save_report(callback.from_user.id, draft.oid, month, draft.cutoff, draft.totals)
            except Exception:
                import research
                research.track(callback.from_user.id,'sales_report_error',error_code='save')
                logging.error('Photo report save failed')
                await callback.message.answer('Ответ о сохранении не пришёл. Нажми «Сохранить ещё раз» — второй такой записи не появится.',
                                             reply_markup=kb(draft, [('Сохранить ещё раз', 'save')]))
                return
            import research
            research.track(callback.from_user.id,'sales_report_completed',operation=draft.oid)
            expire(callback.from_user.id, draft)
            await callback.message.edit_text('Отчёт сохранён.' + (' План месяца обновлён.' if draft.report.get('targets') else ''))


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
            elif draft.report.get('category_config'):
                import categories
                corrected=categories.parse_correction(message.text,draft.report['category_config'])
                updated={cid:dict(v) for cid,v in draft.report['category_values'].items()}
                for cid,value in corrected.items():
                    updated[cid]={'value':value,'cutoff':updated.get(cid,{}).get('cutoff',draft.cutoff)}
                draft.report['category_values']=updated
            else:
                cutoff, totals = parse_report_edit(message.text, op_today(), draft.cutoff, draft.totals)
                draft.cutoff = date_valid(cutoff)
                draft.totals = totals
                draft.report['targets'] = {}
                draft.report['percent'] = {}
            await ask_date_or_preview(message, draft)
        except (ValueError, TypeError) as error:
            if draft.report.get('category_config'):
                from report_sheet_chat import report_failure
                await report_failure(message,draft,ValueError('category_correction'))
                return
            await message.answer(html.escape(str(error)) if isinstance(error, ValueError) else
                                 'Не совсем понял. Напиши дату словами или показатель и число: «вино 73 238».')



async def select_row(message, draft, index):
    row=draft.report['rows'][index]
    if draft.report.get('row_mode'):
        import research
        research.track(message.chat.id,'vision_started')
        try:
            if not draft.image:
                raise vision.VisionError('Фото больше недоступно. Пришли отчёт заново.')
            if _recognition_slots.locked():
                raise vision.VisionError('Сейчас читаю другие отчёты. Попробуй через минуту.')
            await message.answer('Читаю твою строку…')
            async with _recognition_slots:
                row=await vision.recognize_row(draft.image,row['index'],row['name'],relaxed_grid=True) if draft.report.get('relaxed_grid') else await vision.recognize_row(draft.image,row['index'],row['name'])
            draft.report['percent']=row['percent']
            research.track(message.chat.id,'vision_completed')
        except vision.VisionError as exc:
            research.track(message.chat.id,'vision_failed',error_code='vision')
            await message.answer(str(exc),reply_markup=kb(draft,[('Повторить',f'row{index}'),('Отмена','cancel')]))
            return False
    if draft.report.get('category_config'):
        draft.report['category_values']={cid:{'value':value,'cutoff':draft.cutoff} for cid,value in row['totals'].items()}
    draft.totals=row['totals']
    draft.report['selected_name']=row['name']
    draft.report.pop('rows',None)
    draft.image=None
    return True


async def check_plan(uid,draft):
    """Only existing personal monthly targets; OCR never changes them in row mode."""
    from decimal import Decimal
    draft.report['warnings']=[]
    draft.report['check_status']='—'
    try:
        response=await db._execute(db.supabase.table('sales_months').select('targets')
                                   .eq('user_id',uid).eq('month',draft.cutoff[:7]))
        targets=response.data[0]['targets'] if response.data else {}
        checked=[]
        for key,percent in draft.report.get('percent',{}).items():
            if key in draft.totals and targets.get(key,0)>0:
                actual=Decimal(str(draft.totals[key]))*100/Decimal(str(targets[key]))
                checked.append(key)
                if abs(actual-Decimal(str(percent)))>Decimal('0.011'):
                    draft.report['warnings'].append(key)
        if draft.report['warnings']:draft.report['check_status']='есть расхождения ⚠'
        elif len(checked)==len(set(draft.totals)&set(vision.TARGETS)) and checked:
            draft.report['check_status']='совпадает'
        elif checked:draft.report['check_status']='частично'
    except Exception:
        logging.error('Photo plan check unavailable')
