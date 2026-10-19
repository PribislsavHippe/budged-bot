"""Monthly named sales categories. Legacy earnings/sales remain untouched."""
import json
import re
import unicodedata
import time
from datetime import date
from uuid import UUID, uuid5, NAMESPACE_URL

from postgrest.exceptions import APIError
import db
from sales import month_key, number


class CategoryError(ValueError):
    pass


def key(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().replace('ё','е').split())


def clean(raw, restaurant=None):
    if not isinstance(raw, dict) or set(raw)-{'month','categories'}:
        raise CategoryError('invalid_schema')
    month = raw.get('month')
    if month is not None: month = month_key(month)
    items = raw.get('categories')
    if not isinstance(items,list) or not 1<=len(items)<=12:
        raise CategoryError('invalid_categories')
    names=set();result=[]
    for item in items:
        if not isinstance(item,dict) or set(item)-{'name','unit','target'}:raise CategoryError('invalid_category')
        name=item.get('name');unit=item.get('unit')
        if not isinstance(name,str):raise CategoryError('invalid_name')
        name=' '.join(name.split())
        if not 1<=len(name)<=60 or any(unicodedata.category(c).startswith('C') or c in '<>' for c in name):
            raise CategoryError('invalid_name')
        if key(name) in names:raise CategoryError('duplicate_names')
        names.add(key(name))
        if unit not in ('amount','count','number',None):raise CategoryError('invalid_unit')
        row={'name':name,'unit':unit}
        if item.get('target') is not None:
            row['target']=number(item['target'],count=unit=='count')
        if restaurant and month and unit:
            row['id']=str(uuid5(NAMESPACE_URL,f'category:{UUID(str(restaurant))}:{month}:{key(name)}'))
        result.append(row)
    return {'month':month,'categories':result}


async def configure(uid, restaurant, raw, expected, operation):
    from admin import is_admin
    import identity
    if not is_admin(uid):raise PermissionError('category_forbidden')
    restaurant=str(UUID(str(restaurant)))
    if not await identity.owner_restaurant(uid,restaurant):raise PermissionError('category_forbidden')
    data=clean(raw,restaurant)
    if data['month'] is None or any(not i['unit'] for i in data['categories']):
        raise CategoryError('missing_month_or_unit')
    return (await db._execute(db.supabase.rpc('configure_categories',{
        'actor':uid,'restaurant':restaurant,'period':data['month'],'expected':expected,
        'operation':str(UUID(operation)),'items':data['categories']}))).data


async def config(restaurant, month):
    try:
        rows=(await db._execute(db.supabase.table('category_months').select('*')
                               .eq('restaurant_id',str(UUID(str(restaurant)))).eq('month',month_key(month)))).data
        return rows[0] if rows else None
    except APIError as error:
        if error.code in ('42P01','PGRST205'):return None
        raise


_ready_until = 0.0
_ready = False


async def ready():
    """Keep legacy imports available until the owner applies the additive migration."""
    global _ready_until, _ready
    if time.monotonic() < _ready_until:return _ready
    try:
        await db._execute(db.supabase.table('category_alerts').select('id').limit(1))
        _ready = True
    except APIError as error:
        if error.code not in ('42P01','PGRST205'):raise
        _ready = False
    _ready_until = time.monotonic() + 30
    return _ready


async def context(uid, month):
    """Approved membership; multi-restaurant administrator chooses on import."""
    if not await ready():return None
    from admin import is_admin
    import identity
    owned=await identity.owner_restaurants(uid) if is_admin(uid) else []
    if len(owned)>1:return None
    rows=(await db._execute(db.supabase.table('employee_links').select('restaurant_id')
                          .eq('user_id',uid).eq('status','approved'))).data
    if rows:return await config(rows[0]['restaurant_id'],month) or empty_context(rows[0]['restaurant_id'],month,uid)
    if len(owned)==1:return await config(owned[0]['id'],month) or empty_context(owned[0]['id'],month,uid)
    return empty_context(None,month,uid)


async def save(uid, operation, cfg, values):
    values=validate_values(cfg,values)
    if cfg.get('discovered'):
        await db._execute(db.supabase.rpc('save_discovered_category_report',{
            'actor':uid,'restaurant':cfg['restaurant_id'],'period':cfg['month'],
            'operation':str(UUID(operation)),'items':cfg['definitions'],'metrics':values}))
        return
    await db._execute(db.supabase.rpc('save_category_report',{
        'actor':uid,'restaurant':cfg['restaurant_id'],'period':cfg['month'],
        'config_revision':cfg['revision'],'operation':str(UUID(operation)),'metrics':values}))


def validate_values(cfg, values):
    if not isinstance(values,dict) or not values:raise CategoryError('empty_result')
    definitions={i['id']:i for i in cfg['definitions']}
    result={}
    for cid,value in values.items():
        if cid not in definitions or not isinstance(value,dict) or set(value)-{'value','cutoff','target'}:
            raise CategoryError('unknown_category')
        if not isinstance(value.get('cutoff'),str):raise CategoryError('invalid_cutoff')
        cutoff=date.fromisoformat(value['cutoff'])
        from workday import op_today
        if cutoff.isoformat()[:7]!=cfg['month'] or cutoff>op_today():raise CategoryError('wrong_month')
        item={'cutoff':cutoff.isoformat(),'value':number(value.get('value'),count=definitions[cid]['unit']=='count',zero=True)}
        if value.get('target') is not None:item['target']=number(value['target'],count=definitions[cid]['unit']=='count')
        result[cid]=item
    return result


def summary(reports, cfg=None):
    """Snapshot definitions preserve old labels/units, including changed configurations."""
    latest={}
    for report in sorted(reports,key=lambda r:(r['created_at'],r['id'])):
        definitions={c['id']:c for c in report['definitions']}
        for cid,v in report['metrics'].items():
            old=latest.get(cid)
            if old and old['cutoff']>v['cutoff']:continue
            d=definitions[cid];target=v.get('target',d.get('target'))
            latest[cid]={'id':cid,'name':d['name'],'unit':d['unit'],'cutoff':v['cutoff'],
                         'total':v['value'],'official':v['value'],'recorded':0,'estimated':0,
                         'target':target,'pct':round(v['value']/target*100,2) if target else None,
                         'remaining':max(target-v['value'],0) if target else None}
    if cfg:
        for d in cfg['definitions']:
            latest.setdefault(d['id'],{'id':d['id'],'name':d['name'],'unit':d['unit'],
                'cutoff':None,'total':None,'official':None,'recorded':0,'estimated':0,
                'target':d.get('target'),'pct':None,'remaining':None})
    return list(latest.values())


async def view(uid, month):
    try:
        reports=await db._pages(lambda:db.supabase.table('category_reports').select('id,created_at,definitions,metrics')
                               .eq('user_id',uid).eq('month',month).order('created_at').order('id'))
        return summary(reports,await context(uid,month))
    except APIError as error:
        if error.code in ('42P01','PGRST205'):return []
        raise


async def options(uid,month):
    if not await ready():return []
    from admin import is_admin
    if not is_admin(uid):return []
    import identity
    result=[]
    for restaurant in (await identity.owner_restaurants(uid))[:70]:
        configured=await config(restaurant['id'],month) or empty_context(restaurant['id'],month,uid)
        result.append({'name':restaurant['name'],'config':configured})
    return result


BASE_UNITS={'вино':'amount','десерты':'amount','товарооборот':'amount','оборот':'amount',
            'коктейли':'count','открытки':'count','двд':'count','dvd':'count'}


def empty_context(restaurant,month,uid):
    return {'restaurant_id':restaurant,'month':month_key(month),'revision':0,
            'definitions':[],'personal_scope':str(uid) if restaurant is None else None}


def discover(cfg, detected):
    """Observed labels become report data, never administrator configuration."""
    known={key(d['name']):d for d in cfg['definitions']}
    raw={'month':cfg['month'],'categories':[]}
    for item in detected:
        name=item['name'];old=known.get(key(name))
        unit=item.get('unit') if item.get('unit') in ('amount','count') else (old['unit'] if old else (BASE_UNITS.get(key(name)) or 'number'))
        row={'name':name,'unit':unit}
        if old and old['unit']==unit and old.get('target') is not None:row['target']=old['target']
        raw['categories'].append(row)
    data=clean(raw)
    scope=cfg['restaurant_id'] or ('personal:'+cfg.get('personal_scope',''))
    for row,source in zip(data['categories'],detected):
        row['unit_source']='explicit' if source.get('unit') in ('amount','count') else 'known' if key(row['name']) in known or key(row['name']) in BASE_UNITS else 'unknown'
        old=known.get(key(row['name']))
        row['id']=old['id'] if old else str(uuid5(NAMESPACE_URL,f"category:{scope}:{cfg['month']}:{key(row['name'])}"))
    return {**cfg,'definitions':data['categories'],'discovered':True}


def parse_correction(text,cfg):
    """Local word-form matching for an unsaved result; no money sent to an LLM."""
    if not isinstance(text,str) or not 1<=len(text)<=2000:raise CategoryError('invalid_correction')
    def words(value):
        endings=('иями','ами','ями','ого','ему','ому','ией','ов','ев','ей','ам','ям','ах','ях','ом','ем','ою','ею','ы','и','а','я','у','ю','е','о')
        result=set()
        for word in re.findall(r'[а-яёa-z]+',key(value)):
            for ending in endings:
                if word.endswith(ending) and len(word)-len(ending)>=3:
                    word=word[:-len(ending)];break
            result.add(word)
        return result
    alternatives={c['id']:[words(part) for part in c['name'].split('/') if words(part)] for c in cfg['definitions']}
    definitions={c['id']:c for c in cfg['definitions']}
    pattern=r'(?<![\w.,])(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[.,]\d{1,2})?(?!\w|[.,]\d)'
    numbers=list(re.finditer(pattern,text))
    if not numbers:raise CategoryError('missing_value')
    results={};start=0
    for i,match in enumerate(numbers):
        prefix=text[start:match.start()]
        if len(numbers)==1 and not words(prefix):prefix=text
        tokens=words(prefix)
        found=[cid for cid,variants in alternatives.items() if any(v and v<=tokens for v in variants)]
        if len(found)!=1 or found[0] in results:raise CategoryError('ambiguous_correction')
        cid=found[0]
        results[cid]=number(match[0].replace('\u00a0',' '),count=definitions[cid]['unit']=='count',zero=True)
        start=match.end()
    tail=text[start:]
    if words(tail)-words('руб рублей рубля р шт штуки штук всего и'):
        # A reverse single-value form was already matched against the whole line.
        if not (len(numbers)==1 and not words(text[:numbers[0].start()])):
            raise CategoryError('unmatched_trailing_text')
    if re.search(r'\b(?:не|минус|плюс|умнож|вычти|прибавь|отними)\b',text,re.I) or re.search(r'[-+]\s*\d',text):
        raise CategoryError('arithmetic_not_supported')
    return results


def include_legacy_sales(rows,month,settings,events):
    """Known categories can include existing chat sales after the imported cutoff."""
    from sales import compute_sales
    aliases={'вино':'wine','десерты':'desserts','товарооборот':'turnover','оборот':'turnover',
             'коктейли':'cocktails','открытки':'postcards','двд':'dvd','dvd':'dvd'}
    result=[]
    for row in rows:
        metric=aliases.get(key(row['name']))
        expected='count' if metric in ('cocktails','postcards','dvd') else 'amount'
        if not metric or row['unit']!=expected:
            result.append(row);continue
        cfg={**(settings or {}),'targets':{metric:row['target']} if row.get('target') else {}}
        reports=[] if row['official'] is None else [{'id':row['id'],'created_at':'','cutoff':row['cutoff'],'totals':{metric:row['official']}}]
        calculated=compute_sales(month,cfg,events,reports)['metrics'][metric]
        result.append({**row,**calculated,'legacy_metric':metric})
    return result
