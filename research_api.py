"""Signed first-party events; only ADMIN_ID may read journeys/feedback."""
from datetime import datetime,timedelta,timezone
from uuid import UUID
import time
from collections import OrderedDict
from aiohttp import web
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
            # Fetch subjects without the private Telegram mapping.
            subjects=await research.pages('research_subjects','id,label,cohort,onboarding_version',lambda q:q.order('label'))
            since=(datetime.now(timezone.utc)-timedelta(days=days+1)).isoformat()
            events=await research.pages('analytics_events','id,subject_id,event,occurred_at,onboarding_version,source,screen,step,error_code',
                                        lambda q:q.gte('occurred_at',since).order('occurred_at').order('id'))
            if action=='export':
                return respond(analysis_export(subjects,events,days,ux_version=version))
            data=summarize(subjects,events,days,ux_version=version)
            data['versions']=sorted({s['onboarding_version'] for s in subjects})
            data['user_count']=len(data['users']);page=body.get('page',0)
            if type(page) is not int or not 0<=page<=1000:raise ValueError()
            data['users']=data['users'][page*20:(page+1)*20];data['page']=page
            return respond(data)
        if action in ('journey','feedback'):
            sid=str(UUID(body['subject'])) if body.get('subject') else None
            if action=='journey' and not sid:raise ValueError()
            def query(q):
                if sid:q=q.eq('subject_id',sid)
                return q.order('occurred_at' if action=='journey' else 'created_at',desc=True).order('id',desc=True).limit(100)
            table='analytics_events' if action=='journey' else 'feedback'
            fields='id,event,occurred_at,onboarding_version,source,screen,step,error_code' if action=='journey' else 'id,subject_id,created_at,category,body,screen,app_version'
            rows=(await research.execute(query(research.client().table(table).select(fields)))).data
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
        return respond({'error':'Исследования пока не загрузились. Попробуй ещё раз.','code':code,'reference':reference},503)


def register(app):app.router.add_post('/api/research/{action}',handle)
