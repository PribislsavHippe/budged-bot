"""Strict schedule/time interpretation. Rate and worked hours are private product data."""
import os,re
from datetime import date,datetime,time,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import db

TZ=ZoneInfo('Europe/Moscow')

def enabled():return os.getenv('WORK_TIME_ENABLED','0').strip().lower() in {'1','true','yes','on'}

def clock(value):
    match=re.fullmatch(r'(\d{1,2})(?:[:.](\d{2}))?(?::00)?',str(value).strip())
    if not match:raise ValueError('Напиши время: 23:30.')
    h,m=int(match[1]),int(match[2] or 0)
    try:return time(h,m)
    except ValueError:raise ValueError('Напиши время от 00:00 до 23:59.') from None

def cell(value):
    text=str(value).strip().lower().replace('–','-').replace('—','-')
    if text in {'','-','в','вых','выходной','отпуск','off'}:return None
    parts=text.split('-')
    if len(parts)>2:raise ValueError('Не понял часы в ячейке.')
    start=clock(parts[0])
    if len(parts)==2:
        end=clock(parts[1])
    else:
        end_minutes=min(start.hour*60+start.minute+12*60,23*60+30)
        if end_minutes<=start.hour*60+start.minute:
            raise ValueError('Начало смены должно быть раньше 23:30.')
        end=time(end_minutes//60,end_minutes%60)
    if start==end:raise ValueError('Проверь плановое время смены.')
    return start.strftime('%H:%M'),end.strftime('%H:%M')

def month(text):
    names=['январь','февраль','март','апрель','май','июнь','июль','август','сентябрь','октябрь','ноябрь','декабрь']
    value=text.strip().lower()
    if re.fullmatch(r'20\d{2}-\d{2}',value):
        y,m=map(int,value.split('-'));date(y,m,1);return value
    match=re.fullmatch(r'([а-яё]+)\s+(20\d{2})',value)
    if match:
        for m,name in enumerate(names,1):
            if match[1][:3]==name[:3]:return f'{int(match[2]):04d}-{m:02d}'
    raise ValueError('Напиши месяц и год: октябрь 2026.')

def clean_cells(raw,month_key):
    if not isinstance(raw,list) or not 1<=len(raw)<=31:raise ValueError('Не нашёл смены в строке.')
    seen=set();result=[]
    for c in raw:
        if not isinstance(c,dict):raise ValueError('Не разобрал строку графика.')
        day=c.get('day')
        if type(day) is not int or day in seen:raise ValueError('Дни в строке прочитались неоднозначно.')
        seen.add(day)
        try:d=date.fromisoformat(f'{month_key}-{day:02d}')
        except ValueError:raise ValueError('В этом месяце нет такого дня. Проверь месяц и график.') from None
        times=cell(c.get('text',''))
        if times:result.append({'date':d.isoformat(),'start':times[0],'end':times[1]})
    if not result:raise ValueError('В выбранной строке не нашёл рабочих смен.')
    return sorted(result,key=lambda c:c['date'])

def actual(day,value,planned_start):
    parts=value.strip().replace('–','-').replace('—','-').split('-')
    if len(parts)>2:raise ValueError('Напиши время ухода или начало и конец: 10–23:30.')
    start=clock(parts[0]) if len(parts)==2 else clock(planned_start)
    end=clock(parts[-1]);d=date.fromisoformat(day)
    a=datetime.combine(d,start,TZ);b=datetime.combine(d,end,TZ)
    if b<=a:b+=timedelta(days=1)
    if not timedelta(0)<b-a<=timedelta(hours=24):raise ValueError('Проверь начало и конец смены.')
    return a,b

def earned(start,end,rate):
    minutes=Decimal(str((end-start).total_seconds()))/60
    return {'hours':float(minutes/60),'income':float((minutes/60*Decimal(str(rate))).quantize(Decimal('.01'))) if rate is not None else None}

async def load_shift(uid,day):
    rows=(await db._execute(db.supabase.table('shifts').select('shift_date,starts_at,ends_at').eq('user_id',uid).eq('shift_date',day))).data
    return rows[0] if rows else None

async def save(uid,cells):
    await db.get_or_create_user(uid)
    await db._execute(db.supabase.rpc('save_schedule',{'actor':uid,'cells':cells}))

async def save_work(uid,day,start,end):
    user=await db.get_or_create_user(uid)
    # Preserve the pay rate used for an already recorded shift when correcting time.
    rows=(await db._execute(db.supabase.table('worked_shifts').select('hourly_rate').eq('user_id',uid).eq('shift_date',day))).data
    rate=rows[0]['hourly_rate'] if rows else user.get('hourly_rate')
    await db._execute(db.supabase.table('worked_shifts').upsert({'user_id':uid,'shift_date':day,
        'actual_start':start.isoformat(),'actual_end':end.isoformat(),'hourly_rate':rate,'updated_at':datetime.now(TZ).isoformat()},on_conflict='user_id,shift_date'))
    return earned(start,end,rate)


async def export(uid):
    user=await db.get_or_create_user(uid)
    planned=await db._pages(lambda:db.supabase.table('shifts').select('shift_date,starts_at,ends_at').eq('user_id',uid).order('shift_date'))
    worked=await db._pages(lambda:db.supabase.table('worked_shifts').select('shift_date,actual_start,actual_end,hourly_rate,updated_at').eq('user_id',uid).order('shift_date'))
    return {'hourly_rate':user.get('hourly_rate'),'planned_shifts':planned,'worked_shifts':worked}
