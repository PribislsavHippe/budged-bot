"""Подсказки по меню из PDF (выгрузка из Google Презентаций): одно блюдо или напиток на странице.

Вёрстка у файлов разная: «Состав» и «Аллергены» стоят то друг под другом, то рядом в колонках (у коктейлей —
три колонки «Состав / Украшение / Аллергены»), заголовки бывают набраны вразрядку («С О С Т А В :»). Простое
извлечение текста перемешивает колонки, поэтому страница разбирается по положению строк: каждая строка относится
к ближайшему заголовку раздела над ней в той же колонке.

Текст берётся из файла как есть, без додумывания: бот показывает то, что написано в подсказке. Страницы без
блока «Аллергены» блюдом не считаются (разделители, содержание, глоссарий) и попадают в список пропущенных.
"""
import io
import re
from statistics import median

HEADERS = {
    'состав': 'composition',
    'аллергены': 'allergens',
    'подача': 'serving',
    'тарелка': 'serving',
    'украшение': 'garnish',
}
HEADER = re.compile(r'^[•○·\-–]?\s*(состав|аллергены|подача|тарелка|украшение)\s*:?\s*(.*)$', re.I)
BULLET = re.compile(r'^\s*[•○·]\s*')
WEIGHT = re.compile(r'^/?\s*(\d+(?:[.,]\d+)?)\s*(г|гр|мл|шт)\.?\s*(.*)$', re.I)
STRENGTH = re.compile(r'^\d{1,2}(?:[.,]\d)?\s*[º°%]$')
GAP = 12            # больше этого (в пунктах) между словами одной строки — уже другая колонка
SAME_ROW = 12       # заголовки разделов, стоящие рядом, могут быть сдвинуты по высоте на несколько пунктов


def _row_pieces(chars):
    """Символы одной строки → куски по колонкам с расставленными пробелами.

    Пробел — это символ пробела в файле или разрыв шире обычного. В наборе вразрядку («С О С Т А В») буквы стоят
    далеко друг от друга, поэтому «обычный» разрыв считается по самой строке."""
    letters = [c for c in chars if c['text'].strip()]
    if not letters:
        return []
    gaps = [b['x0'] - a['x1'] for a, b in zip(letters, letters[1:])]
    size = median(c['size'] for c in letters)
    tracking = median(gaps) if gaps else 0
    spaced = tracking > .12 * size
    word_gap = max(tracking * 2.2, .3 * size) if spaced else .25 * size
    column_gap = max(GAP, word_gap * 2.5, size * 1.5)
    pieces, text, last, pending = [], '', None, False
    for c in chars:
        if not c['text'].strip():
            pending = last is not None
            continue
        if last is not None and c['x0'] - last['x1'] > column_gap:
            pieces.append((text, first, last))
            text, last, pending = '', None, False
        if last is None:
            first = c
        elif pending or c['x0'] - last['x1'] > word_gap:
            text += ' '
        text += c['text']
        last, pending = c, False
    pieces.append((text, first, last))
    return pieces


def segments(page):
    """Строки страницы, разрезанные на колонки: [{'x0', 'x1', 'top', 'bottom', 'size', 'text'}]."""
    rows = []
    for c in sorted(page.chars, key=lambda c: (round(c['top']), c['x0'])):
        row = next((r for r in rows if abs(r[0]['top'] - c['top']) < 3), None)
        if row is None:
            rows.append([c])
        else:
            row.append(c)
    found = []
    for row in rows:
        row.sort(key=lambda c: c['x0'])
        for text, first, last in _row_pieces(row):
            used = [c for c in row if first['x0'] <= c['x0'] <= last['x0'] and c['text'].strip()]
            found.append({'x0': first['x0'], 'x1': last['x1'], 'top': min(c['top'] for c in used),
                          'bottom': max(c['bottom'] for c in used), 'size': round(max(c['size'] for c in used), 1),
                          'text': text.strip()})
    # Отдельно стоящий маркер «•» приклеивается к тексту справа на той же строке.
    found.sort(key=lambda s: (s['top'], s['x0']))
    merged = []
    for seg in found:
        if seg['text'] in ('•', '○', '·'):
            right = next((s for s in found if s is not seg and abs(s['top'] - seg['top']) < 3 and s['x0'] > seg['x1']
                          and s['x0'] - seg['x1'] < 40), None)
            if right is not None:
                right['bullet'] = True
                continue
        merged.append(seg)
    for seg in merged:
        if seg.pop('bullet', False) and not BULLET.match(seg['text']):
            seg['text'] = '• ' + seg['text']
        seg['text'] = re.sub(r'\s*[•○·]$', '', seg['text']).strip()
    return [s for s in merged if s['text']]


def _join_letters(text):
    """«АЗИАТСКОМ С Т И Л Е» → «АЗИАТСКОМ СТИЛЕ»: хвост, набранный вразрядку."""
    return re.sub(r'(?<!\S)((?:[^\W\d_] ){2,}[^\W\d_])(?!\S)', lambda m: m[1].replace(' ', ''), text)


def _clean(text):
    return re.sub(r'\s+', ' ', text).strip()


def _items(lines):
    """Строки раздела → пункты: строка с маркером начинает пункт, без маркера продолжает предыдущий."""
    items = []
    for seg in lines:
        text = seg['text']
        if BULLET.match(text) or not items:
            items.append(BULLET.sub('', text))
        else:
            joiner = '' if items[-1].endswith('-') and not items[-1].endswith(' -') else ' '
            items[-1] = items[-1].rstrip('-') + joiner + text if joiner == '' else items[-1] + ' ' + text
    return [_clean(i) for i in items if _clean(i)]


def _paragraphs(lines):
    out, last = [], None
    for seg in lines:
        if last is not None and seg['top'] - last['bottom'] < max(6, seg['size'] * .9) and out:
            out[-1] += ' ' + seg['text']
        else:
            out.append(seg['text'])
        last = seg
    return [_clean(p) for p in out]


def page_card(segs, width):
    """Разбор одной страницы. None — на странице нет блока «Аллергены» (не блюдо)."""
    headers, header_ids = [], set()
    for seg in segs:
        found = HEADER.match(seg['text'])
        bulleted = bool(BULLET.match(seg['text']))
        # «• Аллергены:» бывает заголовком (в «Обеде дня» маркер стоит и перед ним), а «• Украшение: сухоцветы» —
        # пунктом состава. Заголовок с маркером — только пустой.
        if found and (':' in seg['text'] or found[1].isupper()) and not (bulleted and found[2]):
            headers.append({**seg, 'kind': HEADERS[found[1].lower()], 'value': found[2].strip()})
            header_ids.add(id(seg))
    if not any(h['kind'] == 'allergens' for h in headers):
        return None
    first = min(h['top'] for h in headers)
    sections = {h['kind']: [] for h in headers}
    owner = {}
    body = []
    for seg in segs:
        if id(seg) in header_ids:
            continue
        # Строка относится к ближайшей слева колонке заголовков, а в ней — к ближайшему заголовку сверху.
        above = [h for h in headers if h['top'] < seg['top'] - 1 and h['x0'] - 12 <= seg['x0']]
        if above:
            column = max(h['x0'] for h in above)
            h = max((h for h in above if h['x0'] >= column - 20), key=lambda h: h['top'])
            owner[id(seg)] = h
            sections[h['kind']].append(seg)
        elif seg['top'] < first:
            body.append(seg)
    # Ниже последнего пункта списка может стоять подпись к блюду (как в «Основном меню») — она не пункт состава.
    for kind in ('composition', 'allergens', 'garnish'):
        lines = sections.get(kind, [])
        kept = []
        for seg in lines:
            if kept and seg['top'] - kept[-1]['bottom'] > 3 * max(seg['size'], 8):
                break
            kept.append(seg)
        sections[kind] = kept
    sizes = [s['size'] for s in body] or [10]
    big = max(sizes)
    weight_by_size = {}
    for seg in body:
        weight_by_size[seg['size']] = weight_by_size.get(seg['size'], 0) + len(seg['text'])
    # Основной кегль — тот, которым набрано больше всего текста, не считая самого крупного (названия).
    rest = {k: v for k, v in weight_by_size.items() if k < big - 1.5} or weight_by_size
    common = max(rest, key=rest.get) if rest else 10
    title, weight, strength, subtitle, text, category, notes = [], None, None, [], [], None, []
    for seg in sorted(body, key=lambda s: (s['top'], s['x0'])):
        value = seg['text']
        found = WEIGHT.match(value)
        if found and len(value) < 25:
            weight = f'{found[1]} {found[2].lower()}' + (f' {found[3]}' if found[3] else '')
            continue
        if STRENGTH.match(value):
            strength = value.replace('º', '°')
            continue
        if seg['top'] < 55 and seg['size'] <= common and (value.isdigit() or value.isupper()):
            if value.lower().startswith(('кроме', 'только')):
                notes.append(value[:1] + value[1:].lower())     # «КРОМЕ СЧАСТЬЕ НА ИСААКИЕВСКОЙ» — где блюда нет
            elif not value.isdigit():
                category = value
            continue
        if seg['size'] >= big - 1.5 and big > common + 2:
            title.append(value)
        elif seg['size'] > common + 1.5 and not text:
            subtitle.append(value)
        else:
            text.append(seg)
    title = [_join_letters(t) for t in title]
    subtitle = [_join_letters(t) for t in subtitle]
    new = any(re.search(r'\bNEW\b', t) for t in title + subtitle)
    title = [re.sub(r'\s*\bNEW\b\s*', ' ', t).strip() for t in title]
    subtitle = [t for t in (re.sub(r'\s*\bNEW\b\s*', ' ', t).strip() for t in subtitle) if t]
    serving = [h['value'] for h in headers if h['kind'] == 'serving' and h['value']]
    serving += [s['text'] for s in sections.get('serving', [])]
    name = _clean(' '.join(title))
    english = None
    # У коктейлей под русским названием стоит английское тем же кеглем — это подпись, а не часть названия.
    latin = [t for t in title if re.fullmatch(r"[A-Z0-9 '&.,\-–]+", t)]
    if latin and len(latin) < len(title):
        english = _clean(' '.join(latin))
        name = _clean(' '.join(t for t in title if t not in latin))
    if not english and subtitle and re.fullmatch(r"[A-Z0-9 '&.,\-–]+", subtitle[0]) and re.search('[а-яё]', name, re.I):
        english = subtitle.pop(0)
    # Русское название бывает набрано мельче английского (у части коктейлей) — тогда оно попало в подзаголовок.
    if name and not re.search('[а-яё]', name, re.I) and subtitle and re.search('[а-яё]', subtitle[0], re.I):
        english, name = name, subtitle.pop(0)
    return {
        'name': name,
        'new': new,
        'english': english,
        'subtitle': _clean(' '.join(subtitle)) or None,
        'weight': weight,
        'strength': strength,
        'category': category,
        'description': _paragraphs(text),
        'composition': _items(sections.get('composition', [])),
        'garnish': _items(sections.get('garnish', [])),
        'allergens': _items(sections.get('allergens', [])),
        'serving': _clean(' '.join(serving)) or None,
        'notes': notes,
    }


def _photo(reader_page):
    """Самая крупная картинка страницы (фото блюда) в JPEG до 1000 точек по большей стороне. None — картинок нет."""
    best = None
    try:
        images = list(reader_page.images)
    except Exception:
        return None
    for image in images:
        try:
            picture = image.image
        except Exception:
            continue
        if picture is None:
            continue
        area = picture.width * picture.height
        if best is None or area > best[0]:
            best = (area, picture)
    if best is None or best[0] < 200 * 200:
        return None
    picture = best[1].convert('RGB')
    picture.thumbnail((1000, 1000))
    out = io.BytesIO()
    picture.save(out, 'JPEG', quality=82, optimize=True)
    return out.getvalue()


def section(text):
    """«СЕЗОННОЕ SEASONAL» → «Сезонное»; «ОСЕНЬ-ЗИМА,26/27 СЛАДКОЕ» → «Сладкое»."""
    text = re.sub(r'осень[\s-]*зима|весна[\s-]*лето|[\d/,.]+', ' ', str(text or ''), flags=re.I)
    words = [w for w in text.split() if re.search('[а-яё]', w, re.I) or w == '&']
    value = ' '.join(words).strip(' &')
    return value[:1].upper() + value[1:].lower() if value else None


def read(data, source, photos=True):
    """PDF → (блюда, пропущенные страницы). Раздел берётся со страницы-разделителя или из колонтитула."""
    import pdfplumber
    import pypdf
    dishes, skipped = [], []
    reader = pypdf.PdfReader(io.BytesIO(data)) if photos else None
    category = None
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            segs = segments(page)
            text = ' '.join(s['text'] for s in segs)
            card = page_card(segs, page.width)
            if card is None:
                page.close()
                if 0 < len(text) < 60 and not text.isdigit():
                    category = _clean(re.sub(r'\d+', '', text)).strip(' ,.') or category
                elif text:
                    skipped.append({'page': number, 'text': text[:80]})
                continue
            card['category'] = section(card['category'] or category)
            if not card['name']:
                skipped.append({'page': number, 'text': text[:80]})
                continue
            card.update(source=source, page=number)
            if photos:
                card['photo'] = _photo(reader.pages[number - 1])
            dishes.append(card)
            page.close()        # не держать в памяти разобранные страницы: на сервере её мало
    return dishes, skipped
