"""Календарь выходных из виджета — в мини-приложении, пока Telegram не пускает обычных ботов к виджетам (13.11.2026).

Страница `/app/days-off` встраивает ту же разметку (`days_off_widget.HTML`) и подставляет вместо `TelegramWidget`
свою прослойку: данные календаря и запись идут через `/api/days_off/state` и `/api/days_off/save` с подписью Telegram
(`initData`). Записывается тем же путём, что из виджета (`days_off_widget.apply`). Если сейчас выходные не собирают,
показывается пример на ближайшие полмесяца, который ничего не записывает.

Кому видна ссылка в календаре мини-приложения — переменная `DAYS_OFF_MINIAPP`: `admin` (по умолчанию) — только
владельцу бота, `1` — всем сотрудникам, `0` — никому. Приглашение на конкретный период проверяет участие
сотрудника в этом графике. Тексты — ЧЕРНОВИКИ.
"""
import asyncio
import calendar
import os
from datetime import date, timedelta
from functools import partial
from uuid import UUID, uuid5

from aiohttp import web

DEMO_ID = '00000000-0000-4000-8000-000000000000'
DEMO_NOTE = 'Сейчас выходные не собирают — это пример. Нажимай, ничего не сохранится.'
ROSTER_UNAVAILABLE = 'График не найден или доступ изменился.'

PAGE = '''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Выходные</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
body{padding:calc(16px + var(--tg-safe-area-inset-top,0px) + var(--tg-content-safe-area-inset-top,0px)) 12px
 calc(32px + var(--tg-safe-area-inset-bottom,0px));max-width:480px;margin:0 auto;font:15px/1.35 -apple-system,system-ui,sans-serif;color:#172126;
 background:linear-gradient(160deg,#e9e4f4,#dde9ee 55%,#efe6dc);min-height:100vh;box-sizing:border-box}
.cap{margin:4px 6px 12px;font-size:16px}.cap[hidden]{display:none}.note{margin:0 6px 12px;color:#55575c;font-size:13px}
iframe{border:0;width:100%;display:block;min-height:320px;background:transparent}
</style></head><body>
<p class="cap" id="cap">Загружаю календарь…</p><p class="note" id="note" hidden></p><div id="box"></div>
<script>
(function(){
 var tg=window.Telegram&&Telegram.WebApp; if(tg){tg.ready();tg.expand();
  if(tg.BackButton){tg.BackButton.show();tg.BackButton.onClick(function(){history.length>1?history.back():location.href='/app'})}}
 var init=tg&&tg.initData||'';
 var rosters=Array.from(new Set(new URLSearchParams(location.search).getAll('roster')));
 if(!rosters.length)rosters=[null];
 function post(path,body,roster){body.initData=init;if(roster!==null)body.roster=roster;return fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify(body)}).then(function(r){return r.json().then(function(j){if(!r.ok)throw {status:r.status,error:j.error};return j})})}
 var cap=document.getElementById('cap'),loadError='Не получилось открыть календарь. Открой приложение заново через бота.';
 if(rosters.length>2){cap.textContent=loadError;return}
 function nonce(){return Date.now()+'-'+Math.random().toString(36).slice(2)}
 function save(roster,data,key){return post('/api/days_off/save',{data:data,nonce:key},roster)}
 function dayNumber(day){return Date.parse(day+'T00:00:00Z')/86400000}
 function joinedPeriod(items){
  var parts=items.map(function(item){return {roster:item.roster,state:JSON.parse(item.data.chat_data)}});
  parts.sort(function(a,b){return a.state.s.localeCompare(b.state.s)});
  var first=parts[0].state,last=parts[1].state,split=dayNumber(last.s)-dayNumber(first.s);
  var size=dayNumber(last.e)-dayNumber(first.s)+1;
  if(split<1||dayNumber(first.e)+1!==dayNumber(last.s)||size>31)return null;
  function merge(){
   var combined={...first,e:last.e,f:last.f>last.s?last.f:first.f,split:split};
   ['r','p'].forEach(function(key){
    combined[key]=(parseInt(first[key],36)|(parseInt(last[key],36)<<split)).toString(36);
   });
   combined.h=(first.h||'')+(last.h||'').replace(/(.)(..)/g,function(_,day,hours){
    return (parseInt(day,36)+split).toString(36)+hours;
   });
   combined.ok=first.ok&&last.ok?last.ok:null;
   return JSON.stringify(combined);
  }
  var previous=[];
  async function saveBoth(query){
   var fields=query.split(':'),none=fields[0]==='n';
   var results=await Promise.all(parts.map(async function(part,i){
    var offset=i?split:0,length=i?size-split:split,mask=(1<<length)-1;
    var id=part.state.id.slice(0,8),data='n:'+id;
    if(!none){
     var required=((parseInt(fields[2],36)>>>offset)&mask).toString(36);
     var desired=((parseInt(fields[3],36)>>>offset)&mask).toString(36);
     var hours=(fields[4]||'').replace(/(.)(..)/g,function(_,day,span){
      var index=parseInt(day,36)-offset;
      return index>=0&&index<length?index.toString(36)+span:'';
     });
     data='o:'+id+':'+required+':'+desired+(hours?':'+hours:'');
    }
    var entry=previous[i];
    if(!entry||entry.data!==data)entry=previous[i]={data:data,key:nonce()};
    if(entry.result)return entry.result;
    var result=await save(part.roster,data,entry.key).catch(function(error){return {result:error?.error}});
    if(result.result==='ok'&&result.chat_data){entry.result=result}
    return result;
   }));
   var failed=results.findIndex(function(result){return result.result!=='ok'||!result.chat_data});
   if(failed!==-1){
    var detail=results[failed].result||'Не получилось записать. Нажми ещё раз.';
    return {result:items.find(function(item){return item.roster===parts[failed].roster}).data.caption+' '+detail};
   }
   Object.assign(first,JSON.parse(results[0].chat_data));
   Object.assign(last,JSON.parse(results[1].chat_data));
   previous=[];
   return {result:'ok',chat_data:merge()};
  }
  return {data:{chat_data:merge(),html:items[0].data.html},save:saveBoth};
 }
 function render(s,saveSelection){
  var n=document.getElementById('note');if(s.note){n.textContent=s.note;n.hidden=false}
  var listeners=[],pending=false;
  var frame=document.createElement('iframe');frame.title=s.caption||'Календарь выходных';
  frame.makeWidget=function(){return {chatData:s.chat_data,ready:function(){},
   onEvent:function(name,cb){if(name==='chatDataChanged')listeners.push(cb)},
   request:function(q){
    if(s.demo)return Promise.resolve({result:'Это пример — ничего не записал.'});
    if(pending)return pending;
    pending=saveSelection(q).then(function(a){
     if(a.chat_data){s.chat_data=a.chat_data;frame.contentWindow.TelegramWidget.chatData=a.chat_data;
      listeners.forEach(function(cb){cb()})}
     if(tg&&tg.HapticFeedback)tg.HapticFeedback.notificationOccurred(a.result==='ok'?'success':'error');
     return {result:a.result}},function(e){return {result:(e&&e.error)||'Не получилось записать. Нажми ещё раз.'}})
     .finally(function(){pending=false});return pending}}};
  frame.srcdoc='<!doctype html><meta charset="utf-8"><body style="margin:0;background:transparent">'+
   '<script>window.TelegramWidget=window.frameElement.makeWidget()<\\/script>'+s.html;
  frame.onload=function(){var d=frame.contentDocument;function fit(){frame.style.height=Math.ceil(d.body.getBoundingClientRect().height)+'px'}
   fit();if(window.ResizeObserver)new ResizeObserver(fit).observe(d.body)};
  document.getElementById('box').appendChild(frame);
 }
 Promise.allSettled(rosters.map(function(roster){return post('/api/days_off/state',{},roster)})).then(function(results){
  var items=results.flatMap(function(result,i){return result.status==='fulfilled'?[{data:result.value,roster:rosters[i]}]:[]});
  var failed=results.find(function(result){return result.status==='rejected'&&(!items.length||result.reason?.status!==404)});
  if(failed){cap.textContent=failed.reason?.error||loadError;return}
  if(items.length===2){
   var joined=joinedPeriod(items);
   if(!joined){cap.textContent=loadError;return}
   render(joined.data,joined.save);
  }else{
   var item=items[0];
   render(item.data,function(data){return save(item.roster,data,nonce())});
  }
  cap.hidden=true;
 });
})();
</script></body></html>'''


def allowed(uid):
    value = os.getenv('DAYS_OFF_MINIAPP', 'admin').strip().lower()
    if value in {'1', 'true', 'yes', 'on', 'all'}:
        return True
    if value == 'admin':
        # Без импорта admin (он тянет aiogram): этот флаг считается в каждом /api/stats.
        owner = os.getenv('ADMIN_ID', '').strip()
        return owner.isdigit() and int(owner) == uid
    return False


def demo_period(today):
    """Ближайшие полмесяца: 1–15 или 16–последнее число."""
    if today.day <= 15:
        start, end = today.replace(day=16), today.replace(day=calendar.monthrange(today.year, today.month)[1])
    else:
        first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        start, end = first, first.replace(day=15)
    return {'id': DEMO_ID, 'start_date': start.isoformat(), 'end_date': end.isoformat()}


async def current(uid, roster_id=None):
    """Приглашение открывает указанный период. Без приглашения можно посмотреть ближайший период или пример."""
    import db
    import days_off_widget as widget
    import roster_time_store
    import roster_wishes as wishes
    from workday import op_today
    today = op_today()
    link = await wishes.employee(uid)
    if link:
        rows = [r for r in await wishes.period_rows(link, today)
                if wishes.migrated(r) and wishes.in_roster(r, link['id'])
                and (roster_id is None or r['id'] == roster_id)
                and (date.fromisoformat(r['end_date']) - date.fromisoformat(r['start_date'])).days < widget.MAX_DAYS]
        # Сначала тот, что ещё не опубликован: выходные собирают для него.
        rows.sort(key=lambda r: (r.get('published') is not None, r['start_date']))
        if rows:
            row = rows[0]
            saved = (await db._execute(db.supabase.table('day_off_requests').select('days,desired_days')
                                       .eq('roster_id', row['id']).eq('link_id', link['id']).limit(1))).data
            answer = saved[0] if saved else {}
            try:
                hours = await roster_time_store.period_hours(link['id'], date.fromisoformat(row['start_date']),
                                                             date.fromisoformat(row['end_date']))
            except Exception:
                hours = {}
            return {'demo': False, 'caption': widget.short_caption(row), 'note': None,
                    'chat_data': widget.state(row, answer.get('days') or [], answer.get('desired_days') or [],
                                              today, hours=hours)}
    if roster_id is not None:
        return None
    row = demo_period(today)
    return {'demo': True, 'caption': widget.short_caption(row), 'note': DEMO_NOTE,
            'chat_data': widget.state(row, [], [], today)}


async def page(request):
    from webapp_api import NO_CACHE
    return web.Response(text=PAGE, content_type='text/html', headers=NO_CACHE)


def requested_roster(body):
    if 'roster' not in body:
        return None
    value = body['roster']
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError
    return str(UUID(value))


async def state_api(request):
    from webapp_api import NO_CACHE, _auth
    uid, body = await _auth(request)
    if uid is None:
        return body
    try:
        roster_id = requested_roster(body)
    except ValueError:
        return web.json_response({'error': ROSTER_UNAVAILABLE}, status=404, headers=NO_CACHE)
    if roster_id is None and not allowed(uid):
        return web.json_response({'error': 'Новый календарь выходных пока в проверке.'}, status=403, headers=NO_CACHE)
    import days_off_widget as widget
    try:
        result = await current(uid, roster_id)
    except Exception as error:
        from diagnostics import failure
        failure(error, area='days_off_app', stage='state')
        return web.json_response({'error': 'Не получилось открыть календарь. Попробуй ещё раз.'}, status=503,
                                 headers=NO_CACHE)
    if result is None:
        return web.json_response({'error': ROSTER_UNAVAILABLE}, status=404, headers=NO_CACHE)
    return web.json_response({**result, 'html': widget.HTML}, headers=NO_CACHE)


async def send_confirmation(bot, uid, link, row, result, hours, operation):
    import days_off
    import db
    from roster_time import clock

    try:
        days = days_off.days_text(map(date.fromisoformat, result.get('days') or []))
        desired = days_off.days_text(map(date.fromisoformat, result.get('desired_days') or []))
        if not (days or desired or hours):
            return
        markup = days_off.app_markup(uid, roster_id=row['id'], label='Изменить')
        if not markup:
            return
        lines = ['Записал пожелания.']
        if days:
            lines.append(f'Обязательные выходные: {days}.')
        if desired:
            lines.append(f'Желательные: {desired}.')
        if hours:
            lines.append('Необычные часы:')
            for day, (start, end) in sorted(hours.items()):
                day_text = days_off.days_text([date.fromisoformat(day)])
                lines.append(f'{day_text} — с {clock(start / 60)} до {clock(end / 60)}.')
        # A replay of the same save can return success again. Claim this receipt once.
        claimed = (await db._execute(db.supabase.table('roster_wish_operations').upsert(
            {'id': str(uuid5(operation, 'chat-confirmation')), 'link_id': link['id'],
             'roster_id': row['id'], 'result': {}}, on_conflict='id', ignore_duplicates=True), retries=1)).data
        if claimed:
            await asyncio.wait_for(bot.send_message(uid, '\n'.join(lines), reply_markup=markup), timeout=5)
    except Exception as error:
        from diagnostics import failure
        failure(error, area='days_off_app', stage='confirmation')


async def save_api(request):
    from webapp_api import NO_CACHE, _auth
    uid, body = await _auth(request)
    if uid is None:
        return body
    try:
        roster_id = requested_roster(body)
    except ValueError:
        return web.json_response({'error': ROSTER_UNAVAILABLE}, status=404, headers=NO_CACHE)
    if roster_id is None and not allowed(uid):
        return web.json_response({'error': 'Новый календарь выходных пока в проверке.'}, status=403, headers=NO_CACHE)
    import days_off_widget as widget
    data, nonce = str(body.get('data') or '')[:64], str(body.get('nonce') or '')[:40]
    if not data or not nonce:
        return web.json_response({'error': 'Обнови страницу и попробуй ещё раз.'}, status=400, headers=NO_CACHE)
    bot = request.app.get('bot')
    try:
        answer, chat_data = await widget.apply(uid, data, 'app:' + nonce, source='miniapp', roster_id=roster_id,
                                               on_saved=partial(send_confirmation, bot) if bot else None)
    except Exception as error:
        from diagnostics import failure
        failure(error, area='days_off_app', stage='save')
        answer, chat_data = 'Не получилось записать. Нажми ещё раз.', None
    return web.json_response({'result': answer, 'chat_data': chat_data}, headers=NO_CACHE)


def register(app):
    app.router.add_get('/app/days-off', page)
    app.router.add_post('/api/days_off/state', state_api)
    app.router.add_post('/api/days_off/save', save_api)
