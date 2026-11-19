"""Календарь выходных из виджета — в мини-приложении, пока Telegram не пускает обычных ботов к виджетам (13.11.2026).

Страница `/app/days-off` встраивает ту же разметку (`days_off_widget.HTML`) и подставляет вместо `TelegramWidget`
свою прослойку: данные календаря и запись идут через `/api/days_off/state` и `/api/days_off/save` с подписью Telegram
(`initData`). Записывается тем же путём, что из виджета (`days_off_widget.apply`). Если сейчас выходные не собирают,
показывается пример на ближайшие полмесяца, который ничего не записывает.

Кому видна ссылка в календаре мини-приложения — переменная `DAYS_OFF_MINIAPP`: `admin` (по умолчанию) — только
владельцу бота, `1` — всем сотрудникам, `0` — никому. Тексты — ЧЕРНОВИКИ.
"""
import calendar
import os
from datetime import date, timedelta

from aiohttp import web

DEMO_ID = '00000000-0000-4000-8000-000000000000'
DEMO_NOTE = 'Сейчас выходные не собирают — это пример. Нажимай, ничего не сохранится.'

PAGE = '''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Выходные</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
body{margin:0;padding:16px 12px 32px;font:15px/1.35 -apple-system,system-ui,sans-serif;color:#172126;
 background:linear-gradient(160deg,#e9e4f4,#dde9ee 55%,#efe6dc);min-height:100vh;box-sizing:border-box}
.cap{margin:4px 6px 12px;font-size:16px}.note{margin:0 6px 12px;color:#55575c;font-size:13px}
iframe{border:0;width:100%;display:block;min-height:320px;background:transparent}
</style></head><body>
<p class="cap" id="cap">Загружаю календарь…</p><p class="note" id="note" hidden></p><div id="box"></div>
<script>
(function(){
 var tg=window.Telegram&&Telegram.WebApp; if(tg){tg.ready();tg.expand();
  if(tg.BackButton){tg.BackButton.show();tg.BackButton.onClick(function(){history.length>1?history.back():location.href='/app'})}}
 var init=tg&&tg.initData||'';
 function post(path,body){body.initData=init;return fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify(body)}).then(function(r){return r.json().then(function(j){if(!r.ok)throw j;return j})})}
 function show(text){document.getElementById('cap').textContent=text}
 post('/api/days_off/state',{}).then(function(s){
  show(s.caption);var n=document.getElementById('note');if(s.note){n.textContent=s.note;n.hidden=false}
  var listeners=[];
  window.makeWidget=function(){return {chatData:s.chat_data,ready:function(){},
   onEvent:function(name,cb){if(name==='chatDataChanged')listeners.push(cb)},
   request:function(q){
    if(s.demo)return Promise.resolve({result:'Это пример — ничего не записал.'});
    return post('/api/days_off/save',{data:q,nonce:Date.now()+'-'+Math.random().toString(36).slice(2)}).then(function(a){
     if(a.chat_data){s.chat_data=a.chat_data;frame.contentWindow.TelegramWidget.chatData=a.chat_data;
      listeners.forEach(function(cb){cb()})}
     if(tg&&tg.HapticFeedback)tg.HapticFeedback.notificationOccurred(a.result==='ok'?'success':'error');
     return {result:a.result}},function(e){return {result:(e&&e.error)||'Не получилось записать. Нажми ещё раз.'}})}}};
  var frame=document.createElement('iframe');frame.title='Календарь выходных';
  frame.srcdoc='<!doctype html><meta charset="utf-8"><body style="margin:0;background:transparent">'+
   '<script>window.TelegramWidget=parent.makeWidget()<\\/script>'+s.html;
  frame.onload=function(){var d=frame.contentDocument;function fit(){frame.style.height=d.documentElement.scrollHeight+'px'}
   fit();if(window.ResizeObserver)new ResizeObserver(fit).observe(d.body)};
  document.getElementById('box').appendChild(frame);
 },function(e){show((e&&e.error)||'Не получилось открыть календарь. Открой приложение заново через бота.')});
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


async def current(uid):
    """Данные календаря на период, для которого собирают выходные; без такого периода — пример."""
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
    row = demo_period(today)
    return {'demo': True, 'caption': widget.short_caption(row), 'note': DEMO_NOTE,
            'chat_data': widget.state(row, [], [], today)}


async def page(request):
    from webapp_api import NO_CACHE
    return web.Response(text=PAGE, content_type='text/html', headers=NO_CACHE)


async def state_api(request):
    from webapp_api import NO_CACHE, _auth
    uid, body = await _auth(request)
    if uid is None:
        return body
    if not allowed(uid):
        return web.json_response({'error': 'Новый календарь выходных пока в проверке.'}, status=403, headers=NO_CACHE)
    import days_off_widget as widget
    try:
        result = await current(uid)
    except Exception as error:
        from diagnostics import failure
        failure(error, area='days_off_app', stage='state')
        return web.json_response({'error': 'Не получилось открыть календарь. Попробуй ещё раз.'}, status=503,
                                 headers=NO_CACHE)
    return web.json_response({**result, 'html': widget.HTML}, headers=NO_CACHE)


async def save_api(request):
    from webapp_api import NO_CACHE, _auth
    uid, body = await _auth(request)
    if uid is None:
        return body
    if not allowed(uid):
        return web.json_response({'error': 'Новый календарь выходных пока в проверке.'}, status=403, headers=NO_CACHE)
    import days_off_widget as widget
    data, nonce = str(body.get('data') or '')[:64], str(body.get('nonce') or '')[:40]
    if not data or not nonce:
        return web.json_response({'error': 'Обнови страницу и попробуй ещё раз.'}, status=400, headers=NO_CACHE)
    try:
        answer, chat_data = await widget.apply(uid, data, 'app:' + nonce, source='miniapp')
    except Exception as error:
        from diagnostics import failure
        failure(error, area='days_off_app', stage='save')
        answer, chat_data = 'Не получилось записать. Нажми ещё раз.', None
    return web.json_response({'result': answer, 'chat_data': chat_data}, headers=NO_CACHE)


def register(app):
    app.router.add_get('/app/days-off', page)
    app.router.add_post('/api/days_off/state', state_api)
    app.router.add_post('/api/days_off/save', save_api)
