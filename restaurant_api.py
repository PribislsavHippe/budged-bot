"""Restaurant-owner mini-app. Never queries personal earnings or calendar tokens."""
import asyncio
import logging
from uuid import UUID
from aiohttp import web
import db
import identity
from sales import compute_sales, month_key
from workday import op_today


async def member_sales(link, month):
    """Do not expose a user's records made before this restaurant membership."""
    uid=link['user_id'];since=link['requested_at']
    def scoped(table):
        fields={'sales_months':'targets,glass_price','sales_events':'id,created_at,kind,work_date,value,voided','sales_reports':'id,created_at,cutoff,totals'}
        return db.supabase.table(table).select(fields[table]).eq('user_id',uid).eq('month',month).gte('created_at',since)
    settings=(await db._execute(scoped('sales_months'))).data
    events=await db._pages(lambda:scoped('sales_events').order('created_at').order('id'))
    reports=await db._pages(lambda:scoped('sales_reports').order('created_at').order('id'))
    data=compute_sales(month,settings[0] if settings else None,events,reports)
    return {'metrics':data['metrics'],'report_dates':[r['cutoff'] for r in data['reports']],
            'records':len([e for e in events if not e.get('voided')])}


async def handle(request):
    from webapp_api import _auth,NO_CACHE
    from identity_chat import enabled
    from admin import is_admin
    uid,body=await _auth(request)
    if uid is None:return body
    def response(data,status=200):return web.json_response(data,status=status,headers=NO_CACHE)
    if not enabled():return response({'available':False},404)
    action=request.match_info['action']
    stage='identity'
    try:
        restaurant=await identity.owner_restaurant(uid)
        if action=='access':
            return response({'available':bool(restaurant or is_admin(uid)),'restaurant':restaurant})
        if action=='create':
            # identity.create checks ADMIN_ID server-side.
            return response({'restaurant':await identity.create(uid,body.get('name',''))})
        if not restaurant:return response({'error':'Этот раздел доступен администратору ресторана.'},403)
        if action=='invite':
            username=request.app.get('bot_username')
            if not username:return response({'error':'Приглашение пока можно получить в боте: /invite.'},503)
            _,token=await identity.invite(uid)
            return response({'url':f'https://t.me/{username}?start=team_{token}'})
        if action in ('approve','reject','revoke'):
            target=str(UUID(str(body.get('id',''))))
            await identity.action(uid,action,{'id':target})
            return response({'ok':True})
        if action!='view':return response({'error':'Такой страницы нет.'},404)
        month=month_key(body.get('month',op_today().strftime('%Y-%m')))
        page=body.get('page',0)
        if type(page) is not int or not 0<=page<=1000:raise ValueError('Не удалось открыть страницу. Обнови список.')
        status=body.get('status','approved')
        if status not in identity.STATUSES:raise ValueError('Выбери раздел со списком сотрудников.')
        stage='roster'
        roster=await db._pages(lambda:db.supabase.table('employee_links')
            .select('id,user_id,report_name,status,requested_at').eq('restaurant_id',restaurant['id'])
            .order('requested_at').order('id'))
        counts={key:sum(r['status']==key for r in roster) for key in identity.STATUSES}
        selected=[r for r in roster if r['status']==status]
        rows=selected[page*10:page*10+10]
        if status=='approved':
            stage='sales'
            results=await asyncio.gather(*(member_sales(row,month) for row in rows))
            for row,data in zip(rows,results):row['sales']=data
        # Recheck membership/ownership after reads so a concurrent revoke cannot
        # deliver newly fetched financial data to an owner who lost access.
        stage='access_recheck'
        if await identity.owner_restaurant(uid)!=restaurant:return response({'error':'Доступ изменился. Открой приложение заново.'},403)
        active=await db._pages(lambda:db.supabase.table('employee_links').select('id,status')
                .eq('restaurant_id',restaurant['id']).order('id'))
        allowed={(r['id'],r['status']) for r in active}
        rows=[r for r in rows if (r['id'],r['status']) in allowed]
        return response({'restaurant':restaurant,'month':month,'status':status,'page':page,
                         'more':len(selected)>(page+1)*10,'counts':counts,'rows':rows})
    except ValueError as error:
        text=str(error) if isinstance(error,ValueError) else 'Не получилось выполнить действие. Обнови страницу.'
        if 'UUID' in text or 'hexadecimal' in text:text='Заявка изменилась. Обнови список.'
        return response({'error':text},400)
    except Exception as error:
        from diagnostics import failure
        code,reference=failure(error,area='restaurant',stage=stage)
        from research import track
        track(uid,'cabinet_load_error',source='server',screen='restaurant',error_code=code)
        return response({'error':'Не получилось загрузить кабинет. Попробуй ещё раз чуть позже.',
                         'code':code,'reference':reference},503)


def register(app):
    app.router.add_post('/api/restaurant/{action}',handle)
