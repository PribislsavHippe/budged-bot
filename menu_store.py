"""Подсказки по меню: хранение, поиск по названию и составу, загрузка из PDF (`menu_pdf`).

Блюда хранятся так, как написаны в файле: бот ничего не додумывает, а показывает текст подсказки и откуда он.
Фото лежат в базе один раз; после первой отправки бот запоминает file_id Telegram и дальше шлёт по нему.

Доступ — переменная `MENU_ACCESS`: `admin` (по умолчанию) — только владелец бота, `staff` — ещё и подтверждённые
сотрудники ресторанов. Тексты — ЧЕРНОВИКИ.
"""
import asyncio
import base64
import os
import re
import time
from difflib import SequenceMatcher
from uuid import NAMESPACE_URL, uuid4, uuid5

import db

MAX_BYTES = 20 * 1024 * 1024        # больше Telegram не отдаёт боту файл
CACHE_SECONDS = 300
STOP = {'есть', 'ли', 'в', 'во', 'у', 'с', 'со', 'и', 'а', 'на', 'что', 'как', 'где', 'из', 'к', 'по', 'для', 'без',
        'это', 'там', 'какой', 'какая', 'какие', 'какое', 'блюдо', 'блюде', 'блюда', 'напиток', 'напитке', 'состав',
        'аллергены', 'аллерген', 'мне', 'про', 'расскажи', 'покажи', 'нужен', 'нужна', 'нужно'}
ENDINGS = {'а', 'у', 'е', 'ы', 'и', 'ом', 'ой', 'ей', 'ов', 'ам', 'ах', 'ами'}
_cache = {'at': 0.0, 'items': None}


async def allowed(uid):
    from admin import is_admin
    if is_admin(uid):
        return True
    if os.getenv('MENU_ACCESS', 'admin').strip().lower() != 'staff':
        return False
    rows = (await db._execute(db.supabase.table('employee_links').select('id')
                              .eq('user_id', uid).eq('status', 'approved').limit(1))).data
    return bool(rows)


def norm(text):
    return re.sub(r'\s+', ' ', re.sub(r'[^0-9a-zа-я]+', ' ', str(text or '').lower().replace('ё', 'е'))).strip()


def words(text):
    return norm(text).split()


def _prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def same_word(query, word, typos=True):
    """«говядины» ~ «говядина», «глютена» ~ «глютен», «щеки» ~ «щечки» (окончания и опечатки). Короткие слова
    в составе — только точно, и опечатки там не прощаются: «привет» не должен найти «пиве», «дела» — «делаем»."""
    if query == word:
        return True
    # «лука», «луком» → «лук», «сыра» → «сыр»: короткое слово с падежным окончанием.
    if len(word) >= 3 and query.startswith(word) and query[len(word):] in ENDINGS:
        return True
    if len(query) >= 5 and len(word) >= 4 and _prefix(query, word) >= max(4, min(len(query), len(word)) - 2):
        return True
    return typos and len(query) >= 4 and len(word) >= 4 and SequenceMatcher(None, query, word).ratio() >= .8


def _covered(query, text_words, typos=True):
    return all(any(same_word(q, w, typos) for w in text_words) for q in query)


def search(items, text):
    """→ ('card', item) | ('list', [items], заголовок) | None. Сначала название, потом состав и аллергены."""
    query = [w for w in words(text) if w not in STOP]
    if not query or len(query) > 8 or len(norm(text)) > 80:
        return None
    named, partial = [], []
    for item in items:
        card = item['card']
        title = words(f"{card.get('name')} {card.get('english') or ''}")
        if _covered(query, title):
            named.append(item)
            continue
        hits = sum(any(same_word(q, w) for w in title) for q in query)
        if hits:
            partial.append((hits, item))
    if len(named) == 1:
        return 'card', named[0]
    if named:
        return 'list', named, 'Нашёл несколько:'
    inside = [i for i in items if _covered(query, words(' '.join(
        (i['card'].get('composition') or []) + (i['card'].get('allergens') or []) + (i['card'].get('garnish') or []))),
        typos=False)]
    if len(inside) == 1:
        return 'card', inside[0]
    if inside:
        # «без глютена» — всё равно список, где он ЕСТЬ: бот не утверждает, что в остальных его нет.
        title = f'Где есть «{" ".join(query)}» (по подсказкам):'
        if 'без' in words(text):
            title += '\nПро остальные блюда спроси по названию — я покажу их аллергены.'
        return 'list', inside, title
    # Совпало одно слово из нескольких («спасибо большое» → «Большой сэндвич») — это не вопрос про меню.
    partial = [(h, i) for h, i in partial if h >= 2]
    if partial:
        best = max(h for h, _ in partial)
        top = [i for h, i in partial if h == best]
        return ('card', top[0]) if len(top) == 1 else ('list', top, 'Похожие названия:')
    return None


async def items(force=False):
    """Карточки включённых загрузок (без фото), из памяти процесса не дольше 5 минут."""
    if not force and _cache['items'] is not None and time.monotonic() - _cache['at'] < CACHE_SECONDS:
        return _cache['items']
    imports = (await db._execute(db.supabase.table('menu_imports').select('id,source')
                                 .not_.is_('activated_at', 'null').is_('replaced_at', 'null'))).data
    found = []
    for row in imports:
        found += (await db._execute(db.supabase.table('menu_items').select('id,card,photo_id,import_id,position')
                                    .eq('import_id', row['id']).order('position'))).data
    _cache.update(at=time.monotonic(), items=found)
    return found


def forget():
    _cache.update(at=0.0, items=None)


async def item(item_id):
    return next((i for i in await items() if str(i['id']) == item_id), None)


async def photo(photo_id):
    """(file_id, None) или (None, байты JPEG)."""
    rows = (await db._execute(db.supabase.table('menu_photos').select('data,file_id').eq('id', photo_id).limit(1))).data
    if not rows:
        return None, None
    if rows[0].get('file_id'):
        return rows[0]['file_id'], None
    return None, base64.b64decode(rows[0]['data'])


async def remember_file_id(photo_id, file_id):
    await db._execute(db.supabase.table('menu_photos').update({'file_id': file_id}).eq('id', photo_id), retries=1)


def source_name(file_name):
    name = re.sub(r'\.pdf$', '', str(file_name or '').strip(), flags=re.I).strip()
    return name[:120] or 'Меню'


async def import_pdf(uid, file_key, source, data):
    """Загрузка файла целиком: сначала всё записывается невидимым, потом одной операцией включается вместо
    прежней версии того же файла. → {'status', 'count', 'photos', 'skipped', 'previous', 'import_id'}."""
    import menu_pdf
    existing = (await db._execute(db.supabase.table('menu_imports').select('id,activated_at,replaced_at')
                                  .eq('file_key', file_key).limit(1))).data
    if existing and existing[0]['activated_at'] and not existing[0]['replaced_at']:
        return {'status': 'same'}
    if existing and not existing[0]['activated_at']:
        # Прошлая попытка оборвалась на середине: начинаем заново.
        await db._execute(db.supabase.table('menu_imports').delete().eq('id', existing[0]['id']), retries=1)
    dishes, skipped = await asyncio.to_thread(menu_pdf.read, data, source)
    if not dishes:
        return {'status': 'empty', 'skipped': skipped}
    photo_count = sum(1 for d in dishes if d.get('photo'))
    if existing and existing[0]['replaced_at']:
        import_id = existing[0]['id']          # тот же файл уже был: просто вернуть его
    else:
        import_id = str(uuid5(NAMESPACE_URL, f'menu-import:{file_key}'))
        await db._execute(db.supabase.table('menu_imports').upsert({
            'id': import_id, 'source': source, 'file_key': file_key, 'created_by': uid,
            'item_count': len(dishes)}, on_conflict='id'), retries=1)
        rows, photos = [], []
        for position, dish in enumerate(dishes[:500]):
            picture = dish.pop('photo', None)
            photo_id = None
            if picture:
                photo_id = str(uuid5(NAMESPACE_URL, f'menu-photo:{import_id}:{position}'))
                photos.append({'id': photo_id, 'import_id': import_id, 'data': base64.b64encode(picture).decode()})
            rows.append({'id': str(uuid5(NAMESPACE_URL, f'menu-item:{import_id}:{position}')), 'import_id': import_id,
                         'position': position, 'card': dish, 'photo_id': photo_id})
        for start in range(0, len(photos), 6):
            await db._execute(db.supabase.table('menu_photos').upsert(photos[start:start + 6], on_conflict='id'),
                              retries=1)
        for start in range(0, len(rows), 50):
            await db._execute(db.supabase.table('menu_items').upsert(rows[start:start + 50], on_conflict='id'),
                              retries=1)
    previous = (await db._execute(db.supabase.table('menu_imports').select('id').ilike('source', source)
                                  .not_.is_('activated_at', 'null').is_('replaced_at', 'null')
                                  .neq('id', import_id).limit(1))).data
    status = (await db._execute(db.supabase.rpc('activate_menu_import', {'p_import': import_id}), retries=1)).data
    forget()
    return {'status': 'ok' if status in ('ok', 'active') else status, 'count': len(dishes), 'import_id': import_id,
            'photos': photo_count,
            'skipped': skipped, 'previous': previous[0]['id'] if previous else None}


async def restore(import_id):
    status = (await db._execute(db.supabase.rpc('activate_menu_import', {'p_import': import_id}), retries=1)).data
    forget()
    return status
