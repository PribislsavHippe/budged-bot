"""Tips-only periods: gross tips minus recorded expenses; salary is separate."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from workday import entry_op_date, op_today


def bounds(kind, anchor):
    if kind == 'week':
        start=anchor-timedelta(days=anchor.weekday());return start,start+timedelta(days=6)
    if kind == 'month':
        return anchor.replace(day=1),anchor.replace(day=monthrange(anchor.year,anchor.month)[1])
    raise ValueError('Выбери недели или месяцы')


def summarize(entries, start, end, today):
    days={}
    for e in entries:
        d=date.fromisoformat(e['work_date']) if e.get('work_date') else entry_op_date(e['created_at'])
        if not start<=d<=min(end,today):continue
        is_tip=e['kind']=='income' and e['category']=='Чаевые'
        if not is_tip and e['kind']!='expense':continue
        row=days.setdefault(d,{'gross':Decimal(0),'expenses':Decimal(0),'has_tip':False})
        if is_tip:
            row['gross']+=Decimal(str(e['signed_amount']));row['has_tip']=True
        else:row['expenses']-=Decimal(str(e['signed_amount']))
    gross=sum((r['gross'] for r in days.values()),Decimal(0))
    spent=sum((r['expenses'] for r in days.values()),Decimal(0))
    shifts=sum(r['has_tip'] for r in days.values());net=gross-spent
    return {'start':start.isoformat(),'end':end.isoformat(), 'has_data':bool(days),
            'gross':float(gross),'expenses':float(spent),'net':float(net),'shifts':shifts,
            'avg_gross':round(float(gross)/shifts,2) if shifts else None,
            'avg_net':round(float(net)/shifts,2) if shifts else None,
            'days':[{'date':d.isoformat(),'gross':float(r['gross']),'expenses':float(r['expenses']),
                     'net':float(r['gross']-r['expenses']),'has_tip':r['has_tip']} for d,r in sorted(days.items())]}


def compare_tips(entries, kind='month', anchor=None, other=None, aligned=True, today=None):
    today=today or op_today();anchor=anchor or today
    a_start,a_full=bounds(kind,anchor)
    b_start,b_full=bounds(kind,other or (a_start-timedelta(days=1)))
    if a_start>today or b_start>today:raise ValueError('Нельзя сравнить будущие периоды')
    if min(a_start.year,b_start.year)<2000 or max(a_start.year,b_start.year)>2100:
        raise ValueError('Недопустимый период')
    a_end=min(a_full,today);b_end=min(b_full,today)
    # The main result belongs to the selected period, independently of comparison.
    overview=summarize(entries,a_start,a_end,today)
    # Only shorten incomplete comparisons; completed months keep their full length.
    partial=a_full>today or b_full>today
    if aligned and partial:
        length=min((a_end-a_start).days,(b_end-b_start).days)
        a_end=a_start+timedelta(days=length);b_end=b_start+timedelta(days=length)
    a=summarize(entries,a_start,a_end,today);b=summarize(entries,b_start,b_end,today)
    deltas={}
    for key in ('gross','expenses','net','shifts','avg_gross','avg_net'):
        known=a['has_data'] and b['has_data'] and a[key] is not None and b[key] is not None
        delta=round(a[key]-b[key],2) if known else None
        # Negative/zero base: an absolute difference is more honest than a growth %.
        deltas[key]={'amount':delta,'pct':round(delta/b[key]*100,1) if known and b[key]>0 else None}
    return {'kind':kind,'overview':overview,'a':a,'b':b,'delta':deltas,'today':today.isoformat(),
            'partial':partial,'aligned':bool(aligned and partial),'a_full_end':a_full.isoformat(),'b_full_end':b_full.isoformat()}
