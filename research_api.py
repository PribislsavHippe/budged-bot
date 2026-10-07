"""Signed first-party events; only ADMIN_ID may read journeys/feedback."""
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from uuid import UUID
import time
from collections import OrderedDict
from aiohttp import web
from aiogram.types import BufferedInputFile
import json
import research
from research_stats import summarize,analysis_export

CLIENT_EVENTS={'cabinet_opened','cabinet_loaded','cabinet_load_error','tab_opened','help_opened'}
PRIVATE_MONEY_EVENTS={'tip_added','expense_added'}
_limits=OrderedDict()

def allowed(uid,channel,limit):
    now=time.monotonic();key=(uid,channel)
    count,until=_limits.get(key,(0,now+60))
    if until<now:count,until=0,now+60
    _limits[key]=(count+1,until);_limits.move_to_end(key)
    while len(_limits)>5000:_limits.popitem(last=False)
    return count<limit

async def handle(request):
    from webapp_api import _auth,NO_CACHE
    from admin import is_admin
    uid,body=await _auth(request)
    if uid is None:return body
    def respond(data,status=200):return web.json_response(data,status=status,headers=NO_CACHE)
    action=request.match_info['action']
    if action=='access':return respond({'available':is_admin(uid),'enabled':research.enabled(),'bot_username':request.app.get('bot_username')})
    if not research.enabled():return respond({'error':'Сбор действий сейчас выключен. Включи его в настройках сервиса и перезапусти бота.' if is_admin(uid) else 'Раздел пока не подключён.','code':'disabled'},404)
    if action=='event':
        if not allowed(uid,'event',60):return respond({'error':'Слишком много запросов.'},429)
        try:
            event=body.get('event');screen=body.get('screen');error=body.get('error_code')
            if set(body)-{'initData','event','screen','error_code','operation'}:raise ValueError()
            if event not in CLIENT_EVENTS|PRIVATE_MONEY_EVENTS:raise ValueError()
            if error is not None and (event!='cabinet_load_error' or error not in {'network','timeout','auth','invalid'}):raise ValueError()
            if event in PRIVATE_MONEY_EVENTS:
                import db
                if screen!='earnings' or body.get('operation') is None or \
                        not (await db.get_or_create_user(uid)).get('private_money_mode'):
                    raise ValueError()
            args=dict(source='miniapp',screen=screen,error_code=error,operation=body.get('operation'))
            research.payload(uid,event,**args)
        except (ValueError,TypeError):return respond({'error':'Неизвестное событие.'},400)
        research.track(uid,event,**args)
        return respond({'accepted':True},202)
    # Restaurant ownership grants NO access to product research.
    if not is_admin(uid):return respond({'error':'Раздел доступен владельцу бота.'},403)
    if not allowed(uid,'read',30):return respond({'error':'Подожди минуту и повтори.'},429)
    try:
        if action in ('overview','export'):
            days=body.get('days',30);version=body.get('version')
            if type(days) is not int or days not in (7,30):raise ValueError()
            if version is not None and (type(version) is not int or not 0<=version<=100):raise ValueError()
            owner_sid=await research.owner_subject_id(uid)
            # Fetch subjects without the private Telegram mapping.
            subjects=await research.pages('research_subjects','id,label,cohort,onboarding_version',lambda q:q.order('label'))
            since=(datetime.now(timezone.utc)-timedelta(days=days+1)).isoformat()
            def event_query(q):
                q=q.gte('occurred_at',since)
                if owner_sid:q=q.neq('subject_id',owner_sid)
                return q.order('occurred_at').order('id')
            events=await research.pages('analytics_events','id,subject_id,event,occurred_at,onboarding_version,source,screen,step,error_code',
                                        event_query)
            subjects=[s for s in subjects if s['id']!=owner_sid]
            events=[e for e in events if e['subject_id']!=owner_sid]
            if action=='export':
                data=analysis_export(subjects,events,days,ux_version=version)
                filename=f"ux-research-{data['summary']['from']}-{data['summary']['through']}.json"
                document=BufferedInputFile(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode('utf-8'),filename=filename)
                await request.app['bot'].send_document(chat_id=uid,document=document,
                    caption='Данные исследования за выбранный период. Файл можно переслать для разбора.')
                return respond({'sent':True})
            data=summarize(subjects,events,days,ux_version=version)
            data['versions']=sorted({s['onboarding_version'] for s in subjects})
            data['user_count']=len(data['users']);page=body.get('page',0)
            if type(page) is not int or not 0<=page<=1000:raise ValueError()
            data['users']=data['users'][page*20:(page+1)*20];data['page']=page
            return respond(data)
        if action in ('journey','feedback'):
            sid=str(UUID(body['subject'])) if body.get('subject') else None
            if action=='journey' and not sid:raise ValueError()
            if action=='journey' and sid==await research.owner_subject_id(uid):
                return respond({'error':'Этот путь не входит в исследование.'},404)
            if action=='journey':
                days=body.get('days',30)
                if type(days) is not int or days not in (7,30):raise ValueError()
                now=datetime.now(timezone.utc)
                start=now.astimezone(ZoneInfo('Europe/Moscow')).date()-timedelta(days=days-1)
                since=datetime.combine(start,datetime.min.time(),tzinfo=ZoneInfo('Europe/Moscow')).astimezone(timezone.utc).isoformat()
            def query(q):
                if sid:q=q.eq('subject_id',sid)
                if action=='journey':q=q.gte('occurred_at',since).lte('occurred_at',now.isoformat())
                return q.order('occurred_at' if action=='journey' else 'created_at',desc=True).order('id',desc=True).limit(101 if action=='journey' else 100)
            table='analytics_events' if action=='journey' else 'feedback'
            fields='id,event,occurred_at,onboarding_version,source,screen,step,error_code' if action=='journey' else 'id,subject_id,created_at,category,body,screen,app_version'
            rows=(await research.execute(query(research.client().table(table).select(fields)))).data
            if action=='journey':return respond({'rows':rows[:100],'limit':100,'truncated':len(rows)>100})
            if action=='feedback':
                labels=await research.pages('research_subjects','id,label',lambda q:q.order('label'))
                labelmap={s['id']:f"U-{s['label']:04d}" for s in labels}
                for row in rows:row['label']=labelmap.get(row['subject_id'],'Удалён')
            return respond({'rows':rows,'limit':100})
        return respond({'error':'Страница не найдена.'},404)
    except (ValueError,KeyError,TypeError):return respond({'error':'Проверь выбранный период или пользователя.'},400)
    except Exception as error:
        from diagnostics import failure
        code,reference=failure(error,area='research',stage=action if action in {'overview','export','journey','feedback'} else 'unknown')
        message='Не получилось отправить файл в чат. Попробуй ещё раз.' if action=='export' else 'Исследования пока не загрузились. Попробуй ещё раз.'
        return respond({'error':message,'code':code,'reference':reference},503)


def register(app):app.router.add_post('/api/research/{action}',handle)
