"""Content-free product telemetry. Bounded queue + isolated DB client.

Failure cannot block financial writes; loss is explicitly acceptable telemetry.
"""
import asyncio
import logging
import os
from collections import deque
from datetime import datetime, timezone

EVENTS = frozenset('user_started activity onboarding_started onboarding_step onboarding_skipped onboarding_completed tip_added expense_added first_tip_added first_expense_added first_value_action cabinet_opened cabinet_loaded cabinet_load_error tab_opened shift_closed first_shift_closed shift_planned hours_recorded sales_report_started sales_report_completed sales_report_error vision_started vision_completed vision_failed help_opened problem_reported schedule_import_started schedule_previewed schedule_imported'.split())
SCREENS = frozenset('chat earnings sales restaurant research history help calendar'.split())
ERRORS = frozenset('schema permissions backend network timeout auth invalid vision save'.split())
_queue = deque(maxlen=500)
_worker = None
_client = None


def enabled():
    return os.getenv('UX_RESEARCH_ENABLED','0').strip().lower() in {'1','true','yes','on'}


def version():
    import re
    value=os.getenv('APP_VERSION','ux-1')
    return value if re.fullmatch(r'[A-Za-z0-9._-]{1,40}',value) else 'ux-1'


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
        screen not in SCREENS or step not in {None,'tip','expense'} or error_code not in ERRORS|{None}):
        raise ValueError('Invalid telemetry enum')
    # No arbitrary context, metadata, URLs, exceptions or user messages.
    if operation is not None:
        from uuid import UUID
        operation=str(UUID(str(operation)))
    return {'actor':uid,'kind':event,'origin':source,'screen_name':screen,'step_name':step,
            'failure_code':error_code,'release':version(),'operation':operation,
            'event_time':datetime.now(timezone.utc).isoformat()}


def track(uid,event,**context):
    if not enabled():return
    from admin import is_admin
    if is_admin(uid):return
    global _worker
    try:
        record=payload(uid,event,**context)
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


async def export(uid):
    s=await subject(uid)
    if not s:return None
    events=await pages('analytics_events','event,occurred_at,onboarding_version,app_version,source,screen,step,error_code',
                       lambda q:q.eq('subject_id',s['id']).order('occurred_at').order('id'))
    feedback=await pages('feedback','created_at,category,body,screen,app_version',
                         lambda q:q.eq('subject_id',s['id']).order('created_at').order('id'))
    return {'onboarding_version':s['onboarding_version'],'onboarding_state':s['onboarding_state'],
            'events':events,'feedback':feedback}
