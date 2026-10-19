"""Administrator-only forwarded manager message → restaurant/month → confirmation."""
import asyncio
import html
import re
from uuid import uuid4,uuid5,NAMESPACE_URL
from aiogram import Router,F
from aiogram.filters import Command
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import InlineKeyboardMarkup,InlineKeyboardButton
import categories
import category_groq
import identity

router=Router()
router.message.filter(F.chat.type=='private')
router.callback_query.filter(F.message.chat.type=='private')
_drafts={}


def permitted(uid):
    from admin import is_admin
    return is_admin(uid)


def markup(draft,options):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label,callback_data=f"cat:{draft['nonce']}:{action}")]
        for label,action in options])


def expire(uid,draft):
    if _drafts.get(uid) is draft:_drafts.pop(uid,None)


async def preview(message,draft):
    raw=draft['raw'];month=raw['month']
    current=await categories.config(draft['restaurant']['id'],month) if month else None
    draft['expected']=current['revision'] if current else 0
    lines=['<b>'+html.escape(draft['restaurant']['name'])+'</b>', 'Месяц: '+(month or 'не указан')]
    for row in raw['categories']:
        unit={'amount':'₽','count':'шт.','number':'число',None:'единица не указана'}[row['unit']]
        lines.append(html.escape(row['name'])+' · '+unit+(f" · цель {row['target']:g}" if 'target' in row else ' · без общей цели'))
    lines.append('\nИзменится только этот ресторан и месяц. Уже сохранённые результаты останутся с прежними подписями.')
    if not month or any(not r['unit'] for r in raw['categories']):
        lines.append('Нужны месяц с годом и единицы — нажми «Исправить».')
    options=[]
    if month and all(r['unit'] for r in raw['categories']):options.append(('Применить категории','save'))
    options += [('Исправить','edit'),('Начать заново','restart'),('Отмена','cancel')]
    draft['phase']='review'
    await message.answer('\n'.join(lines),reply_markup=markup(draft,options))


async def parse(message,draft,text):
    draft['phase']='reading'
    raw=await category_groq.parse_message(text)
    draft['raw']=raw
    await preview(message,draft)


async def begin(message,text=None):
    uid=message.from_user.id
    if not permitted(uid):return UNHANDLED
    old=_drafts.get(uid)
    if old and (old['lock'].locked() or old['phase']=='uncertain'):
        await message.answer('Сначала заверши сохранение категорий.');return
    restaurants=await identity.owner_restaurants(uid)
    if not restaurants:
        await message.answer('Сначала создай ресторан в миниаппе.');return
    draft={'nonce':uuid4().hex[:12],'lock':asyncio.Lock(),'restaurants':restaurants,
           'phase':'restaurant','text':text,
           'oid':str(uuid5(NAMESPACE_URL,f'categories:{message.bot.id}:{message.chat.id}:{message.message_id}'))}
    _drafts[uid]=draft
    asyncio.get_running_loop().call_later(900,expire,uid,draft)
    if len(restaurants)==1:
        draft['restaurant']=restaurants[0]
        await ask_or_parse(message,draft)
    else:
        await message.answer('Для какого ресторана эти категории?',reply_markup=markup(draft,
            [(r['name'],f'r{i}') for i,r in enumerate(restaurants)]+[('Отмена','cancel')]))


async def ask_or_parse(message,draft):
    text=draft.pop('text',None)
    if text:
        async with draft['lock']:
            try:await parse(message,draft,text)
            except Exception:
                draft['phase']='body'
                await message.answer('Не получилось разобрать сообщение. Перешли его заново или напиши категории своими словами, с месяцем и единицами.',reply_markup=markup(draft,[('Отмена','cancel')]))
    else:
        draft['phase']='body'
        await message.answer('Перешли сообщение управляющей. Отправлю его текст в Groq и покажу категории перед применением.',reply_markup=markup(draft,[('Отмена','cancel')]))


@router.message(Command('cancel','start','delete','reset'))
async def cancel_command(message):
    if not permitted(message.from_user.id):return UNHANDLED
    draft=_drafts.get(message.from_user.id)
    if not draft:return UNHANDLED
    if draft['lock'].locked() or draft['phase']=='uncertain':
        await message.answer('Сначала заверши сохранение категорий.');return
    expire(message.from_user.id,draft)
    if message.text.split()[0].split('@')[0]=='/cancel':
        await message.answer('Категории не меняю.');return
    return UNHANDLED


@router.message(Command('categories'))
async def command(message):
    return await begin(message)


@router.message(lambda m:permitted(m.from_user.id) and bool(getattr(m,'forward_origin',None)) and
                bool(m.text and re.search(r'категори|план|акцент',m.text,re.I)))
async def forwarded(message):
    draft=_drafts.get(message.from_user.id)
    if draft and draft['phase'] in ('body','edit'):return await body(message)
    return await begin(message,message.text)


def waiting(message):
    draft=_drafts.get(message.from_user.id)
    return permitted(message.from_user.id) and bool(draft and draft['phase'] in ('body','edit') and message.text and not message.text.startswith('/'))


def manual(text):
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    match=re.fullmatch(r'месяц:\s*(20\d{2}-\d{2})',lines[0],re.I) if lines else None
    if not match:return None
    items=[]
    for line in lines[1:]:
        bits=[b.strip() for b in line.split(';')]
        if len(bits) not in (2,3):raise categories.CategoryError('invalid_line')
        unit={'руб':'amount','₽':'amount','шт':'count','шт.':'count'}.get(bits[1].lower())
        if not unit:raise categories.CategoryError('missing_unit')
        row={'name':bits[0],'unit':unit}
        if len(bits)==3 and bits[2]:row['target']=bits[2]
        items.append(row)
    return categories.clean({'month':match[1],'categories':items})


@router.message(waiting,F.text)
async def body(message):
    if not permitted(message.from_user.id):return UNHANDLED
    draft=_drafts.get(message.from_user.id)
    if not draft or draft['lock'].locked():return
    async with draft['lock']:
        try:
            raw=manual(message.text)
            if raw is None:
                if draft['phase']=='edit' and draft.get('raw'):
                    draft['raw']=await category_groq.parse_setup_edit(message.text,draft['raw'])
                    await preview(message,draft)
                else:await parse(message,draft,message.text)
            else:
                draft['raw']=raw
                await preview(message,draft)
        except Exception:
            draft['phase']='edit'
            await message.answer('Не получилось разобрать категории. Напиши своими словами, что нужно изменить: месяц, название, единицу или цель.',reply_markup=markup(draft,[('Отмена','cancel')]))


@router.callback_query(F.data.startswith('cat:'))
async def callback(query):
    if not permitted(query.from_user.id):await query.answer();return
    parts=query.data.split(':');draft=_drafts.get(query.from_user.id)
    if len(parts)!=3 or not draft or parts[1]!=draft['nonce']:
        await query.answer('Настройка уже закрыта.');return
    if draft['lock'].locked():await query.answer('Ещё обрабатываю…');return
    await query.answer()
    action=parts[2]
    if draft['phase']=='uncertain' and action!='save':return
    if action=='cancel':
        expire(query.from_user.id,draft);await query.message.edit_text('Категории не меняю.');return
    if action.startswith('r') and action[1:].isdigit() and draft['phase']=='restaurant':
        index=int(action[1:])
        if not 0<=index<len(draft['restaurants']):return
        draft['restaurant']=draft['restaurants'][index]
        await ask_or_parse(query.message,draft);return
    async with draft['lock']:
        if action in ('edit','restart') and draft['phase'] in ('review','body','edit'):
            if action=='restart':
                draft.pop('raw',None);draft['phase']='body'
                await query.message.answer('Перешли новое сообщение управляющей или напиши категории своими словами.',reply_markup=markup(draft,[('Отмена','cancel')]))
            else:
                draft['phase']='edit'
                await query.message.answer('Напиши, что исправить, обычными словами. Например: «октябрь 2026, гарниры — в рублях, шоты — в штуках».',reply_markup=markup(draft,[('Отмена','cancel')]))
        elif action=='save' and draft['phase'] in ('review','uncertain'):
            draft['phase']='uncertain'
            try:
                await categories.configure(query.from_user.id,draft['restaurant']['id'],draft['raw'],draft['expected'],draft['oid'])
            except PermissionError:
                expire(query.from_user.id,draft)
                await query.message.answer('Доступ к настройке изменился. Открой её заново.')
                return
            except Exception as error:
                from postgrest.exceptions import APIError
                if isinstance(error,APIError) and 'category_stale' in str(error):
                    draft['phase']='edit'
                    await query.message.answer('Категории уже изменились. Перешли сообщение заново — сначала покажу актуальное сравнение.',reply_markup=markup(draft,[('Начать заново','restart'),('Отмена','cancel')]))
                else:
                    await query.message.answer('Ответ о сохранении не пришёл. Нажми ещё раз — повторного изменения не будет.',reply_markup=markup(draft,[('Сохранить ещё раз','save')]))
                return
            expire(query.from_user.id,draft)
            await query.message.edit_text('Категории сохранены: '+html.escape(draft['restaurant']['name'])+' · '+draft['raw']['month'])
