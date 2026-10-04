"""Cohort metrics over behavioral events only. No product tables required."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ=ZoneInfo('Europe/Moscow')
ACTIVE={'activity','user_started','cabinet_opened','cabinet_loaded','tab_opened','help_opened','tip_added','expense_added',
        'shift_planned','shift_closed','sales_report_started','vision_started','problem_reported'}
ERROR_EVENTS={'cabinet_load_error','sales_report_error','vision_failed'}
STAGES=[('user_started','Пришли'),('onboarding_completed','Завершили знакомство'),
        ('first_value_action','Сделали первую запись'),('cabinet_opened','Открыли кабинет'),
        ('d1','Вернулись на следующий день'),('first_shift_closed','Закрыли первую смену'),('d7','Вернулись на 7-й день')]


def dt(value):return datetime.fromisoformat(value.replace('Z','+00:00'))
def pct(n,d):return round(n/d*100,1) if d else None

def summarize(subjects,events,days=30,now=None,ux_version=None):
    now=now or datetime.now(timezone.utc)
    today=now.astimezone(TZ).date(); start=today-timedelta(days=days-1)
    selected={s['id']:s for s in subjects if ux_version is None or s['onboarding_version']==ux_version}
    by=defaultdict(list)
    for e in events:
        if e['subject_id'] in selected and dt(e['occurred_at'])<=now:by[e['subject_id']].append(e)
    # Cohorts begin at first observed /start, never inferred from old financial data.
    cohort={}
    for sid,rows in by.items():
        starts=[dt(e['occurred_at']) for e in rows if e['event']=='user_started']
        if selected[sid]['cohort']=='new' and starts:
            first=min(starts)
            if start<=first.astimezone(TZ).date()<=today:cohort[sid]=first
    window=[e for rows in by.values() for e in rows if start<=dt(e['occurred_at']).astimezone(TZ).date()<=today]
    reached={sid:{e['event'] for e in by[sid] if dt(e['occurred_at'])>=first} for sid,first in cohort.items()}
    def retention_set(day):
        # Target day must have ended. Incomplete observation is pending, not a loss.
        eligible={sid for sid,t in cohort.items() if t.astimezone(TZ).date()+timedelta(days=day)<today}
        returned={sid for sid in eligible if any(e['event'] in ACTIVE and
            dt(e['occurred_at']).astimezone(TZ).date()==cohort[sid].astimezone(TZ).date()+timedelta(days=day) for e in by[sid])}
        return eligible,returned
    ret={d:retention_set(d) for d in (1,7)}
    remaining=set(cohort);funnel=[];base=len(remaining)
    for key,label in STAGES:
        previous=set(remaining);eligible=set(previous)
        if key in ('d1','d7'):
            maturity,passed=ret[int(key[1:])];eligible &= maturity;remaining=eligible & passed
        else:remaining={sid for sid in eligible if key in reached[sid]}
        missing=eligible-remaining
        errored={sid for sid in missing if reached[sid]&ERROR_EVENTS}
        funnel.append({'key':key,'label':label,'users':len(remaining),'previous':len(previous),
                       'eligible':len(eligible),'pending':len(previous-eligible),'conversion':pct(len(remaining),len(eligible)),
                       'overall':pct(len(remaining),base),'drop_off':len(missing),'with_errors':len(errored)})
    errors=Counter((e['event'],e.get('screen'),e.get('error_code')) for e in window if e['event'] in ERROR_EVENTS)
    active_window=[e for e in window if e['event'] in ACTIVE]
    day_events=Counter()
    day_active=defaultdict(set)
    for e in window:
        day=dt(e['occurred_at']).astimezone(TZ).date()
        day_events[day]+=1
        if e['event'] in ACTIVE:day_active[day].add(e['subject_id'])
    daily=[]
    for offset in range(days):
        day=today-timedelta(days=offset)
        daily.append({'date':day.isoformat(),'active_users':len(day_active[day]),'events':day_events[day]})
    users=[]
    for sid,rows in by.items():
        recent=[e for e in rows if start<=dt(e['occurred_at']).astimezone(TZ).date()<=today]
        if recent:
            last=max(recent,key=lambda e:(dt(e['occurred_at']),e.get('id',0)))
            users.append({'id':sid,'label':f"U-{selected[sid]['label']:04d}",'cohort':selected[sid]['cohort'],
                          'onboarding_version':selected[sid]['onboarding_version'],'last_at':last['occurred_at'],
                          'last_event':last['event'],'errors':sum(e['event'] in ERROR_EVENTS for e in recent)})
    # The list is a recent activity feed. Sorting by errors/label hid active
    # people on later pages even when their actions were counted above.
    users.sort(key=lambda u:(dt(u['last_at']),u['id']),reverse=True)
    return {'days':days,'from':start.isoformat(),'through':today.isoformat(),'new_users':base,
            'active_users':len({e['subject_id'] for e in active_window}),
            'active_today':len(day_active[today]),
            'event_count':len(window),
            'last_observed_at':max(window,key=lambda e:dt(e['occurred_at']))['occurred_at'] if window else None,
            'daily_activity':daily,
            'onboarding_started':len({e['subject_id'] for e in window if e['event']=='onboarding_started'}),
            'onboarding_skipped':len({e['subject_id'] for e in window if e['event']=='onboarding_skipped'}),
            'first_value_users':sum('first_value_action' in reached[s] for s in cohort),
            'funnel':funnel,'retention':{f'd{d}':{'eligible':len(a),'returned':len(b),'pending':base-len(a),'rate':pct(len(b),len(a))} for d,(a,b) in ret.items()},
            'errors':[{'event':k[0],'screen':k[1],'code':k[2],'count':n} for k,n in errors.most_common()],
            'features':[{'event':name,'users':len({e['subject_id'] for e in window if e['event']==name}),
                         'count':sum(e['event']==name for e in window)} for name in sorted({e['event'] for e in window})],
            'users':users}
