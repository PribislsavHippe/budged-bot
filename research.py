"""Content-free product telemetry. Bounded queue + isolated DB client.

Failure cannot block financial writes; loss is explicitly acceptable telemetry.
"""
import asyncio
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone

EVENTS = frozenset('user_started activity onboarding_started onboarding_step onboarding_skipped onboarding_completed tip_added expense_added first_tip_added first_expense_added first_value_action cabinet_opened cabinet_loaded cabinet_load_error tab_opened shift_closed first_shift_closed shift_planned hours_recorded sales_report_started sales_report_completed sales_report_error vision_started vision_completed vision_failed help_opened problem_reported schedule_import_started schedule_previewed schedule_imported roster_created roster_variant_picked roster_built roster_dates_changed roster_published roster_republished days_off_asked days_off_answered roster_viewed reminder_sent message_not_understood entry_corrected bot_blocked calendar_synced private_mode_enabled private_mode_disabled sale_saved sales_plan_saved shift_end_answer message_burst help_offered'.split())
SCREENS = frozenset('chat earnings sales schedule restaurant research history help calendar'.split())
ERRORS = frozenset('schema permissions backend network timeout auth invalid vision save'.split())
# Уточнение события без содержимого: шаг знакомства, какое напоминание, почему бот не понял,
# как исправили запись, каким путём записаны чаевые.
STEPS = frozenset('''tip expense shift_tomorrow shift_end days_off no_amount ambiguous_amount short_number unclear
bank_no_amount undo account amount delete message bank
button time_format small_amount understood no_shift not_started timeout edit cancel unknown'''.split())
# Серия: человек пишет снова и снова, а ничего не записывается. Три сообщения за две минуты без успешной
# записи — событие message_burst с последней причиной, по которой бот не справился (текст не хранится).
BURST_COUNT = 3
BURST_WINDOW = 120
SUCCESS = frozenset('tip_added expense_added hours_recorded days_off_answered shift_planned sale_saved '
                    'entry_corrected schedule_imported sales_report_completed'.split())
PROBLEMS = frozenset('message_not_understood shift_end_answer'.split())
_bursts = {}
_queue = deque(maxlen=500)
_worker = None
_client = None


def enabled():
    return os.getenv('UX_RESEARCH_ENABLED','0').strip().lower() in {'1','true','yes','on'}


def version():
    import re
    # Render сам передаёт коммит развёртывания: по нему видно, что было до правки и после.
    value=os.getenv('APP_VERSION') or os.getenv('RENDER_GIT_COMMIT','')[:12] or 'ux-1'
    return value if re.fullmatch(r'[A-Za-z0-9._-]{1,40}',value) else 'ux-1'


def daily(uid,name):
    """Ключ «один раз в рабочий день»: повтор в тот же день база не запишет второй раз."""
    from uuid import uuid5,NAMESPACE_URL
    from workday import op_today
    return str(uuid5(NAMESPACE_URL,f'ux-daily:{name}:{uid}:{op_today()}'))


def client():
    global _client
    if _client is None:
        from supabase import create_client
        from supabase.client import ClientOptions
        _client=create_client(os.environ['SUPABASE_URL'],os.environ['SUPABASE_KEY'],
                              options=ClientOptions(postgrest_client_timeout=3))
    return _client


async def execute(query):
    return await asyncio.wait_for(asyncio.to_thread(query.execute),timeout=4)


def payload(uid,event,*,source='bot',screen='chat',step=None,error_code=None,operation=None):
    if (event not in EVENTS or source not in {'bot','miniapp','server'} or
        screen not in SCREENS or (step is not None and step not in STEPS) or error_code not in ERRORS|{None}):
        raise ValueError('Invalid telemetry enum')
    # No arbitrary context, metadata, URLs, exceptions or user messages.
    if operation is not None:
        from uuid import UUID
        operation=str(UUID(str(operation)))
    return {'actor':uid,'kind':event,'origin':source,'screen_name':screen,'step_name':step,
            'failure_code':error_code,'release':version(),'operation':operation,
            'event_time':datetime.now(timezone.utc).isoformat()}


def burst(uid,event,step=None,now=None):
    """Причина серии или None. Считаются только сообщения (activity со step='message'); успешная запись
    обнуляет счёт; после паузы длиннее окна серия начинается заново. Одна серия — одно событие."""
    now=time.monotonic() if now is None else now
    if event in SUCCESS:
        _bursts.pop(uid,None);return None
    state=_bursts.setdefault(uid,{'times':deque(maxlen=BURST_COUNT*4),'reason':None,'sent':False})
    if event in PROBLEMS and step!='understood':
        state['reason']=step
        return None
    if event!='activity' or step!='message':return None
    times=state['times']
    if times and now-times[-1]>BURST_WINDOW:
        times.clear();state['reason']=None;state['sent']=False
    times.append(now)
    while times and now-times[0]>BURST_WINDOW:times.popleft()
    if len(times)>=BURST_COUNT and not state['sent']:
        state['sent']=True
        return state['reason'] or 'unknown'
    if len(_bursts)>2000:_bursts.clear()   # память процесса ограничена; потеря счёта допустима
    return None


def track(uid,event,**context):
    if not enabled():return
    from admin import is_admin
    if is_admin(uid):return
    global _worker
    try:
        records=[payload(uid,event,**context)]
        reason=burst(uid,event,context.get('step'))
        if reason:records.append(payload(uid,'message_burst',screen=context.get('screen','chat'),step=reason))
        for record in records:
            if len(_queue)==_queue.maxlen:
                logging.warning('ux_event_dropped reason=queue_full')
                return
            _queue.append(record)
        if _worker is None or _worker.done():_worker=asyncio.create_task(drain())
    except Exception:
        logging.warning('ux_event_dropped reason=invalid_event')


async def drain():
    while _queue:
        record=_queue.popleft()
        try:
            await execute(client().rpc('record_ux_event',record))
        except Exception as error:
            from diagnostics import failure
            failure(error,area='analytics',stage='record')


async def record(uid,event,**context):
    """For optional onboarding transitions only; never used before financial writes."""
    if not enabled():return {}
    from admin import is_admin
    if is_admin(uid):return {}
    try:
        return (await execute(client().rpc('record_ux_event',payload(uid,event,**context)))).data or {}
    except Exception as error:
        from diagnostics import failure
        failure(error,area='analytics',stage='transition')
        return {}


async def subject(uid):
    if not enabled():return None
    try:
        rows=(await execute(client().table('research_subjects').select('id,cohort,onboarding_version,onboarding_state').eq('user_id',uid))).data
        return rows[0] if rows else None
    except Exception as error:
        from diagnostics import failure
        failure(error,area='analytics',stage='subject')
        return None


async def owner_subject_id(uid):
    """Resolve only the signed owner's subject ID for exclusion from research views."""
    rows=(await execute(client().table('research_subjects').select('id').eq('user_id',uid).limit(1))).data
    return rows[0]['id'] if rows else None


async def pages(table,fields,configure=lambda q:q,max_rows=50000):
    rows=[]
    while True:
        batch=(await execute(configure(client().table(table).select(fields)).range(len(rows),len(rows)+499))).data
        if not batch:return rows
        rows.extend(batch)
        if len(rows)>=max_rows:raise ValueError('research_limit')


async def people(since,until):
    """Роль и даты смен участников (migration_v40). Telegram ID из базы не выходит."""
    return (await execute(client().rpc('research_people',
                                       {'since':since.isoformat(),'until':until.isoformat()}))).data or []


async def export(uid):
    s=await subject(uid)
    if not s:return None
    events=await pages('analytics_events','event,occurred_at,onboarding_version,app_version,source,screen,step,error_code',
                       lambda q:q.eq('subject_id',s['id']).order('occurred_at').order('id'))
    feedback=await pages('feedback','created_at,category,body,screen,app_version',
                         lambda q:q.eq('subject_id',s['id']).order('created_at').order('id'))
    return {'onboarding_version':s['onboarding_version'],'onboarding_state':s['onboarding_state'],
            'events':events,'feedback':feedback}


async def blocked_middleware(make_request,bot,method):
    """Человек заблокировал бота: Telegram отказывает в отправке. Отмечаем раз в день, без содержимого."""
    from aiogram.exceptions import TelegramForbiddenError
    try:
        return await make_request(bot,method)
    except TelegramForbiddenError:
        chat=getattr(method,'chat_id',None)
        if isinstance(chat,int) and chat>0:
            track(chat,'bot_blocked',source='server',operation=daily(chat,'bot_blocked'))
        raise
