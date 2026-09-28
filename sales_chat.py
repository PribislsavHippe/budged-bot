"""Strict chat commands: sales never fall through into personal expenses."""
import re
from datetime import date
from decimal import Decimal
from chat_dates import parse_date, MONTHS
from sales import number, month_key, COUNTS

LABELS = {'wine':'Вино','cocktails':'Коктейли','desserts':'Десерты','turnover':'Товарооборот','postcards':'Открытки','dvd':'ДВД'}
ALIASES = {'вино':'wine','коктейли':'cocktails','десерты':'desserts','оборот':'turnover','товарооборот':'turnover','открытки':'postcards','двд':'dvd'}
ALIASES.update({'вина':'wine','вину':'wine','коктейль':'cocktails','коктейлей':'cocktails','десерт':'desserts','десертов':'desserts','выручка':'turnover','открытка':'postcards','открыток':'postcards','dvd':'dvd'})
SALE_WORDS = {'бокал':'glass','бокалы':'glass','бутылка':'bottle','бутылки':'bottle','коктейль':'cocktails','коктейли':'cocktails','десерт':'desserts','десерты':'desserts','оборот':'turnover','товарооборот':'turnover','открытка':'postcards','открытки':'postcards','двд':'dvd'}


def _value(raw, count=False, zero=False):
    raw=raw.strip().lower().replace('\u00a0',' ').replace('\u202f',' ')
    raw=re.sub(r'^(?:было|получилось|продано|на|сумма)\s+','',raw)
    raw=re.sub(r'\s*(?:₽|руб(?:лей|ля|ль|\.)?|р\.|штук[аи]?|шт\.?)$','',raw).strip()
    words={'один':1,'одна':1,'два':2,'две':2,'три':3,'четыре':4,'пять':5,'шесть':6,'семь':7,'восемь':8,'девять':9,'десять':10,'ноль':0}
    if raw in words:return number(words[raw],count=count,zero=zero)
    m=re.fullmatch(r'([0-9]+(?:[.,][0-9]+)?)\s*(к|тыс\.?|тысяч[аи]?|млн\.?|миллион(?:а|ов)?)',raw)
    if m:
        raw=str(Decimal(m[1].replace(',','.'))*(1000000 if m[2].startswith(('млн','миллион')) else 1000))
    if not re.fullmatch(r'[0-9]+(?:[.,][0-9]+)?|[0-9]{1,3}(?: [0-9]{3})+(?:[.,][0-9]+)?',raw):
        raise ValueError('Не понял число. Можно написать «73 238», «250,50» или «2,5 тыс».')
    return number(raw,count=count,zero=zero)


def _pairs(raw, allowed):
    pattern=r'(?<![а-яa-z])('+ '|'.join(sorted(ALIASES,key=len,reverse=True))+r')(?![а-яa-z])'
    matches=list(re.finditer(pattern,raw))
    if not matches or raw[:matches[0].start()].strip(' ,;\n:—-') not in ('','по'):
        raise ValueError('Напиши показатель и число, например: «вино 73 238, коктейли 57».')
    result={}
    for i,m in enumerate(matches):
        key=ALIASES[m[1]]
        if key not in allowed:raise ValueError('Этот показатель сюда не подходит.')
        if key in result:raise ValueError('Здесь два значения одного показателя. Оставь одно, которое нужно записать.')
        tail=raw[m.end():matches[i+1].start() if i+1<len(matches) else len(raw)]
        tail=re.sub(r'\s+(?:и|по)\s*$','',tail)
        tail=tail.strip(' ,;\n:=—–')
        result[key]=_value(tail,key in COUNTS,zero=True)
    return result


def parse_report_edit(text,today,cutoff,totals):
    text=text.strip().lower().replace('ё','е')
    if text.startswith('отчет'):
        command=parse_sales_message(text,today)
        return command['cutoff'],{**totals,**command['totals']}
    if not any(re.search(r'\b'+word+r'\b',text) for word in ALIASES):
        return parse_date(text,today).isoformat(),dict(totals)
    return cutoff,{**totals,**_pairs(text,LABELS)}


def parse_sales_message(text, today):
    text = text.strip().lower().replace('ё','е')
    text=re.sub(r'^(?:запиши|добавь)\s+(?=отчет|план|цен[ау])','',text)
    text=re.sub(r'^месячный план\b','план продаж',text)
    if text.startswith('план ') and not text.startswith('план продаж') and any(re.search(r'\b'+word+r'\b',text) for word in ALIASES):
        text='план продаж '+text[len('план '):]
    month = today.strftime('%Y-%m')
    if text.startswith('план продаж'):
        rest = text[len('план продаж'):].strip()
        m = re.match(r'^(\d{4}-\d{2})\s+',rest)
        if m: month=month_key(m[1]);rest=rest[m.end():]
        else:
            named=re.match(r'^(?:на\s+)?([а-я]+)(?:\s+(\d{4}))?\s*[:;,]?\s+',rest)
            if named and named[1] not in ALIASES:
                matches=[i+1 for i,name in enumerate(MONTHS) if name[:3]==named[1][:3]]
                if len(matches)!=1:raise ValueError('Не понял месяц. Например: «план на октябрь, вино 143 тыс».')
                month=month_key(f'{int(named[2]) if named[2] else today.year:04}-{matches[0]:02}')
                rest=rest[named.end():]
        targets = _pairs(rest, ('wine','cocktails','desserts','turnover'))
        if any(v<=0 for v in targets.values()):raise ValueError('Планы должны быть больше нуля')
        return {'action':'settings','month':month,'targets':targets}
    if text.startswith('цена бокала'):
        return {'action':'settings','month':month,'glass_price':_value(text[len('цена бокала'):])}
    if text.startswith('отчет'):
        rest=text[len('отчет'):].strip()
        marker=re.search(r'(?<![а-яa-z])('+ '|'.join(sorted(ALIASES,key=len,reverse=True))+r')(?![а-яa-z])',rest)
        if not marker:raise ValueError('Добавь показатели: «отчёт за вчера, вино 73 238, коктейли 57».')
        cutoff=parse_date(rest[:marker.start()].strip(' ,;:\n'),today)
        return {'action':'report','month':cutoff.strftime('%Y-%m'),'cutoff':cutoff.isoformat(),'totals':_pairs(rest[marker.start():],LABELS)}
    explicit_sale=bool(re.match(r'^(?:продал[аи]?|продали|продажа)\b',text))
    text=re.sub(r'^(?:продал[аи]?|продали|запиши(?: продажу)?)\s+','',text)
    text=re.sub(r'^бутылку\b','бутылка',text)
    reverse=re.fullmatch(r'(.+?)\s+(бокала?|бокалов|коктейля|коктейлей|открытки|открыток|двд|dvd)',text)
    if reverse:
        kinds={'бокал':'бокал','бокала':'бокал','бокалов':'бокал','коктейля':'коктейль','коктейлей':'коктейль','открытки':'открытка','открыток':'открытка','двд':'двд','dvd':'двд'}
        text=kinds[reverse[2]]+' '+reverse[1]
    word=text.split(maxsplit=1)[0] if text else ''
    if word=='продажа':
        text=text[len(word):].strip();word=text.split(maxsplit=1)[0] if text else ''
        if word not in SALE_WORDS:raise ValueError('Пример: бокал, коктейль 2, бутылка 3500, оборот 25000')
    if word not in SALE_WORDS:
        if word in ('план','цель'):raise ValueError('Какой план записать? Например: «план 2,5 тыс» на смену или «план продаж вино 143 тыс».')
        if explicit_sale:raise ValueError('Не понял продажу. Например: «продал два коктейля» или «бутылка 3500».')
        if word=='вино':raise ValueError('Уточни: бокал 1 или бутылка 3500. Так оценка не смешается с точной суммой.')
        return None
    kind=SALE_WORDS[word];raw=text[len(word):].strip()
    value=_value(raw,kind in COUNTS) if raw else (1 if kind in COUNTS else None)
    if value is None:raise ValueError('Добавь сумму в рублях, например: бутылка 3500')
    return {'action':'save','month':month,'kind':kind,'value':value,'work_date':today.isoformat()}
