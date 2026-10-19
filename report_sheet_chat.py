"""Short report-link flow; reuse report preview/edit/idempotent persistence."""
import asyncio
import html
import csv
import io
import time
from collections import OrderedDict
from uuid import uuid4, uuid5, NAMESPACE_URL

from aiogram import Router, F
import report_photo as photo
import report_sheet as sheet
import research
from workday import op_today

router = Router()
router.message.filter(F.chat.type == 'private')
router.callback_query.filter(F.message.chat.type == 'private')
_slots = asyncio.Semaphore(3)
_alerts = OrderedDict()


async def report_failure(message, draft, error):
    """Notify the owner without financial data, URLs, names or raw exceptions."""
    from admin import ADMIN_ID
    notified = False
    now = time.monotonic()
    uid = message.chat.id
    if ADMIN_ID and now - _alerts.get(uid, -1000) >= 600:
        # Reserve before await; repeated/concurrent failures must not spam owner.
        _alerts[uid] = now
        _alerts.move_to_end(uid)
        while len(_alerts) > 1000:
            _alerts.popitem(last=False)
        try:
            await asyncio.wait_for(message.bot.send_message(
                ADMIN_ID, 'Не удалось разобрать результаты.\n'
                f'Этап: {draft.phase}. Код: {error}. Проверка: {draft.nonce}.',
                disable_notification=True), timeout=4)
            notified = True
        except Exception:
            from diagnostics import failure
            # Log only the controlled failure, never a Telegram response body.
            failure(RuntimeError('owner_alert_failed'), area='report_sheet', stage='notify')
    if notified:
        text=('Для этого ресторана и месяца категории ещё не настроены. Уже пишу Лёше'
              if str(error)=='categories_not_configured' else
              'Не получилось сопоставить показатели с категориями. Уже пишу Лёше')
    else:
        text='Не получилось загрузить результаты. Попробуй начать заново'
    keyboard=buttons(draft,controls(draft)) if draft.report.get('sheet_source') else photo.kb(draft,[('Начать заново','restart'),('Отмена','cancel')])
    await message.answer(text, reply_markup=keyboard)


def buttons(draft, options):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=f'rs:{draft.nonce}:{action}')]
        for label, action in options])


def controls(draft):
    return [('Начать заново', 'restart'), ('Отмена', 'cancel')]


async def load_rows(message, draft, name, selected_config=None):
    import categories,report_auto
    if await report_auto.enabled():
        try:return await report_auto.load_sheet(message,draft,name)
        except sheet.SheetParseError as error:
            if str(error)=='no_matching_categories':raise
        except Exception as error:
            from diagnostics import failure
            failure(error,area='report_auto',stage='sheet')
        if draft.phase=='uncertain':
            await message.answer('Ответ о сохранении не пришёл. Нажми «Сохранить ещё раз» — второй такой записи не появится.',reply_markup=photo.kb(draft,[('Сохранить ещё раз','save')]))
        else:
            photo.expire(message.chat.id,draft)
            await report_auto.issue(message,draft.oid,'sheet')
        return True
    cfg = selected_config or (await categories.context(message.chat.id, sheet.sheet_month(name)) if sheet.sheet_month(name) else None)
    if cfg is None and sheet.sheet_month(name):
        choices=await categories.options(message.chat.id,sheet.sheet_month(name))
        if len(choices)==1:cfg=choices[0]['config']
        elif len(choices)>1:
            draft.report={'sheet_source':draft.report['sheet_source'],'sheet_name':name,'category_choices':choices}
            draft.phase='sheet_restaurant'
            await message.answer('Для какого ресторана эти результаты?',reply_markup=buttons(draft,[(c['name'],f'rest{i}') for i,c in enumerate(choices)]+controls(draft)))
            return True
    if cfg:
        source=await sheet.fetch(draft.report['sheet_source'],sheet=name)
        rows,cfg=sheet.discover_rows(list(csv.reader(io.StringIO(source))),cfg,op_today())
    else:
        rows = await sheet.read(draft.report['sheet_source'], name, op_today())
    if not rows:
        return False
    draft.report = {'sheet_source': draft.report['sheet_source'], 'sheet_name': name, 'rows': rows, **({'category_config':cfg} if cfg else {})}
    draft.phase = 'sheet_rows'
    research.track(message.chat.id, 'sales_report_started')
    await message.answer('Выбери свою строку', reply_markup=buttons(draft,
        [(r['name'], f'row{i}') for i, r in enumerate(rows)] + controls(draft)))
    return True


async def start(message, draft, *, fallback=False):
    names = await sheet.tabs(draft.report['sheet_source'])
    candidates = [n for n in names if sheet.sheet_month(n) == op_today().strftime('%Y-%m')]
    import report_auto
    automatic=await report_auto.enabled()
    if automatic and draft.report.get('explicit_gid'):
        ids=sheet.tab_ids(await sheet.fetch(draft.report['sheet_source'],directory=True))
        candidates=[n for n in names if ids.get(n)==draft.report['explicit_gid']]
        if len(candidates)!=1 or not sheet.sheet_month(candidates[0]):
            photo.expire(message.chat.id,draft)
            await report_auto.issue(message,draft.oid,'date');return
        draft.report.pop('explicit_gid',None)
    if len(candidates) == 1 and not draft.report.get('explicit_gid'):
        try:
            if await load_rows(message, draft, candidates[0]):
                return
        except sheet.SheetParseError as error:
            # A configured restaurant may still send a schedule link. Let the
            # positive schedule detector examine it before reporting a failure.
            if not fallback or str(error)!='no_matching_categories':raise
    if fallback:
        import schedule, schedule_chat, schedule_sheet
        if schedule.enabled():
            probe = candidates[0] if len(candidates) == 1 else names[0]
            text = await sheet.fetch(draft.report['sheet_source'], sheet=probe)
            if schedule_sheet.parse_rows(list(csv.reader(io.StringIO(text)))):
                photo.expire(message.chat.id, draft)
                await schedule_chat.sheet_link(message)
                return
    if len(candidates) == 1 and not draft.report.get('explicit_gid'):
        raise sheet.SheetParseError('unsupported_layout_or_no_results')
    if automatic:
        photo.expire(message.chat.id,draft)
        await report_auto.issue(message,draft.oid,'date');return
    draft.report = {'sheet_source': draft.report['sheet_source'], 'sheet_names': names}
    draft.phase = 'sheet_tabs'
    await message.answer('Выбери лист с результатами', reply_markup=buttons(draft,
        [(name, f'tab{i}') for i, name in enumerate(names)] + controls(draft)))


@router.message(F.text.regexp(r'https://docs\.google\.com/spreadsheets/d/'))
async def receive_link(message):
    uid = message.from_user.id
    old = photo.drafts.get(uid)
    if old and (old.lock.locked() or old.phase == 'uncertain'):
        await message.answer('Сначала заверши предыдущий отчёт.')
        return
    if len(photo.drafts) >= 100 and not old:
        await message.answer('Сейчас читаю другие отчёты. Попробуй через минуту.')
        return
    try:
        sheet_id, gid = sheet.link(message.text)
    except ValueError:
        await message.answer(sheet.ACCESS_ERROR)
        return
    if old:
        photo.expire(uid, old)
    oid = str(uuid5(NAMESPACE_URL, f'report-sheet:{message.bot.id}:{message.chat.id}:{message.message_id}'))
    draft = photo.Draft(uuid4().hex[:12], oid, '', phase='sheet_loading',
                        report={'sheet_source': sheet_id, 'explicit_gid': gid})
    photo.drafts[uid] = draft
    asyncio.get_running_loop().call_later(photo.DRAFT_TTL, photo.expire, uid, draft)
    async with draft.lock:
        try:
            if _slots.locked():
                raise ValueError('Сейчас читаю другие отчёты. Попробуй через минуту.')
            async with _slots:
                await start(message, draft, fallback=True)
        except sheet.SheetParseError as error:
            import report_auto
            if await report_auto.enabled():
                photo.expire(uid,draft)
                await report_auto.issue(message,draft.oid,'sheet');return
            await report_failure(message, draft, error)
            draft.phase = 'sheet_retry'
        except ValueError as error:
            import report_auto
            if await report_auto.enabled():
                photo.expire(uid,draft)
                await report_auto.issue(message,draft.oid,'sheet');return
            draft.phase = 'sheet_retry'
            await message.answer(html.escape(str(error)), reply_markup=buttons(draft, controls(draft)))


@router.callback_query(F.data.startswith('rs:'))
async def action(callback):
    parts = callback.data.split(':')
    draft = photo.drafts.get(callback.from_user.id)
    if len(parts) != 3 or not draft or draft.nonce != parts[1]:
        await callback.answer('Этот вопрос уже закрыт.')
        return
    if draft.lock.locked():
        await callback.answer('Ещё обрабатываю…')
        return
    await callback.answer()
    async with draft.lock:
        command = parts[2]
        if draft.phase == 'uncertain':
            return
        if command == 'cancel':
            photo.expire(callback.from_user.id, draft)
            await callback.message.edit_text('Хорошо, этот отчёт не сохраняю.')
            return
        try:
            if command == 'restart' and draft.report.get('sheet_source'):
                draft.totals = {}
                draft.cutoff = None
                source = draft.report['sheet_source']
                draft.report = {'sheet_source': source}
                draft.phase = 'sheet_loading'
                if _slots.locked():
                    raise ValueError('Сейчас читаю другие отчёты. Попробуй через минуту.')
                async with _slots:
                    await start(callback.message, draft)
            elif command.startswith('rest') and draft.phase=='sheet_restaurant':
                from admin import is_admin
                import identity,categories
                if not is_admin(callback.from_user.id):return
                index=int(command[4:])
                choices=draft.report['category_choices']
                if not 0<=index<len(choices):return
                selected=choices[index]['config']
                if not await identity.owner_restaurant(callback.from_user.id,selected['restaurant_id']):return
                current=await categories.config(selected['restaurant_id'],selected['month'])
                if current is None:current=categories.empty_context(selected['restaurant_id'],selected['month'],callback.from_user.id)
                async with _slots:await load_rows(callback.message,draft,draft.report['sheet_name'],current)
            elif command.startswith('tab') and draft.phase == 'sheet_tabs':
                i = int(command[3:])
                names = draft.report['sheet_names']
                if not 0 <= i < len(names):
                    return
                if _slots.locked():
                    raise ValueError('Сейчас читаю другие отчёты. Попробуй через минуту.')
                async with _slots:
                    if not await load_rows(callback.message, draft, names[i]):
                        raise sheet.SheetParseError('unsupported_layout_or_no_results')
            elif command.startswith('row') and draft.phase == 'sheet_rows':
                i = int(command[3:])
                rows = draft.report['rows']
                if not 0 <= i < len(rows):
                    return
                row = rows[i]
                cfg=draft.report.get('category_config')
                draft.totals = dict(row.get('totals',{}))
                draft.cutoff = row.get('cutoff') or max(v['cutoff'] for v in row['category_values'].values())
                draft.report = {'sheet_source': draft.report['sheet_source'],
                                'sheet_name': draft.report['sheet_name'], 'selected_name': row['name'],
                                **({'category_config':cfg,'category_values':row['category_values']} if cfg else {})}
                await photo.ask_date_or_preview(callback.message, draft)
        except sheet.SheetParseError as error:
            await report_failure(callback.message, draft, error)
        except (ValueError, IndexError, KeyError) as error:
            text = str(error) if isinstance(error, ValueError) and str(error) else sheet.ACCESS_ERROR
            await callback.message.answer(html.escape(text), reply_markup=buttons(draft, controls(draft)))
