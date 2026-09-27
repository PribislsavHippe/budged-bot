"""Strict chat commands: sales never fall through into personal expenses."""
import re
from datetime import date
from sales import number, month_key, COUNTS

LABELS = {'wine':'Вино','cocktails':'Коктейли','desserts':'Десерты','turnover':'Товарооборот','postcards':'Открытки','dvd':'ДВД'}
ALIASES = {'вино':'wine','коктейли':'cocktails','десерты':'desserts','оборот':'turnover','товарооборот':'turnover','открытки':'postcards','двд':'dvd'}
SALE_WORDS = {'бокал':'glass','бокалы':'glass','бутылка':'bottle','бутылки':'bottle','коктейль':'cocktails','коктейли':'cocktails','десерт':'desserts','десерты':'desserts','оборот':'turnover','товарооборот':'turnover','открытка':'postcards','открытки':'postcards','двд':'dvd'}


def _value(raw, count=False, zero=False):
    raw = re.sub(r'\s*(?:₽|руб\.?|шт\.?)$', '', raw.strip())
    return number(raw, count=count, zero=zero)


def _pairs(raw, allowed):
    result = {}
    for part in re.split(r'[;\n]+|,(?!\d)', raw):
        m = re.fullmatch(r'\s*([а-я]+)\s*[:=]?\s+(.+?)\s*', part)
        if not m or m[1] not in ALIASES or ALIASES[m[1]] not in allowed:
            raise ValueError('Перечисли показатели через точку с запятой: вино 143000; коктейли 110')
        key = ALIASES[m[1]]
        if key in result:
            raise ValueError('Один показатель указан дважды')
        result[key] = _value(m[2], key in COUNTS, zero=True)
    return result


def parse_sales_message(text, today):
    text = text.strip().lower().replace('ё','е')
    month = today.strftime('%Y-%m')
    if text.startswith('план продаж'):
        rest = text[len('план продаж'):].strip()
        m = re.match(r'^(\d{4}-\d{2})\s+',rest)
        if m: month=month_key(m[1]);rest=rest[m.end():]
        targets = _pairs(rest, ('wine','cocktails','desserts','turnover'))
        if any(v<=0 for v in targets.values()):raise ValueError('Планы должны быть больше нуля')
        return {'action':'settings','month':month,'targets':targets}
    if text.startswith('цена бокала'):
        return {'action':'settings','month':month,'glass_price':_value(text[len('цена бокала'):])}
    if text.startswith('отчет'):
        m=re.fullmatch(r'отчет\s+(\d{4}-\d{2}-\d{2})\s+(.+)',text,re.S)
        if not m:raise ValueError('Пример: отчет 2026-09-13 вино 73238; коктейли 57')
        cutoff=date.fromisoformat(m[1])
        if cutoff>today:raise ValueError('Отчет не может быть из будущего')
        return {'action':'report','month':cutoff.strftime('%Y-%m'),'cutoff':cutoff.isoformat(),'totals':_pairs(m[2],LABELS)}
    word=text.split(maxsplit=1)[0] if text else ''
    if word=='продажа':
        text=text[len(word):].strip();word=text.split(maxsplit=1)[0] if text else ''
        if word not in SALE_WORDS:raise ValueError('Пример: бокал, коктейль 2, бутылка 3500, оборот 25000')
    if word not in SALE_WORDS:
        if word=='вино':raise ValueError('Уточни: бокал 1 или бутылка 3500. Так оценка не смешается с точной суммой.')
        return None
    kind=SALE_WORDS[word];raw=text[len(word):].strip()
    value=_value(raw,kind in COUNTS) if raw else (1 if kind in COUNTS else None)
    if value is None:raise ValueError('Добавь сумму в рублях, например: бутылка 3500')
    return {'action':'save','month':month,'kind':kind,'value':value,'work_date':today.isoformat()}
