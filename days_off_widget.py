"""Календарь выходных прямо в чате: HTML-виджет Telegram (Bot API 10.4).

Сотрудник нажимает дни: «вых» (обязательный), ещё раз — «жел» (желательный), ещё раз — часы (по умолчанию 9–17,
кнопками «−»/«+» по часу), ещё раз — пусто. Часы — правило «можешь работать только с … до …» на эту дату, то же,
что записывается сообщением (`shift_time_rules`, вид `limit`). «Сохранить» отправляет весь выбор боту одним
запросом; выходные записываются тем же путём, что ответ сообщением (`roster_wishes.save`), часы — `write_period_hours`
(миграция v45). Разметка одна на всех, данные — в `chat_data` сообщения.

Виджеты работают только в личном чате с ботом. Старые версии Telegram показывают подпись. Включается переменной
`DAYS_OFF_WIDGETS`: `1` — для всех, `admin` — только для владельца бота. По умолчанию выключено.
Дизайн временный: по принятой системе (градиент карточки смены, отметки таблицы графика), до макета владельца.
"""
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from aiogram.methods import TelegramMethod
from postgrest.exceptions import APIError

import shift_widget

MAX_DAYS = 31
MAX_HOURS = 12        # дней с часами: весь выбор должен поместиться в 64 байта запроса

HTML = '''<style>
*{box-sizing:border-box}
:root{--f:rgba(255,255,255,.26);--rim:inset 0 1px 1px rgba(255,255,255,.75),inset 0 0 0 1px rgba(255,255,255,.22);
 --sh:0 8px 20px -10px rgba(40,30,80,.38),0 1px 3px rgba(40,30,80,.08);
 --drop:radial-gradient(120% 140% at 30% 0%,rgba(255,255,255,.75),rgba(255,255,255,0) 55%);--b:cubic-bezier(.3,1.6,.5,1)}
.c{font:15px/1.3 -apple-system,system-ui,sans-serif;padding:16px 14px 14px;border-radius:18px;color:#172126;
 background:linear-gradient(100deg,#efc1da 0%,#efc1da 14%,#cec99f 40%,#dcd6f6 72%,#b5d0d9 100%)}
h1{font-size:19px;font-weight:700;margin:0 0 10px;letter-spacing:-.01em}
.g{display:grid;grid-template-columns:repeat(7,1fr);gap:5px}
.w{font-size:11px;text-align:center;opacity:.6}
button{border:0;color:#172126;padding:0;background:var(--f);box-shadow:var(--rim),var(--sh);
 -webkit-backdrop-filter:blur(10px) saturate(1.25) brightness(1.06);backdrop-filter:blur(10px) saturate(1.25) brightness(1.06);
 transition:transform .25s var(--b),background .25s,box-shadow .25s}
button:active{transform:scale(.94)}
.d{height:42px;border-radius:8px;background:rgba(255,255,255,.35);box-shadow:none;-webkit-backdrop-filter:none;backdrop-filter:none;
 font:400 15px/1.1 -apple-system,system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px}
.d:active{transform:none;background:rgba(255,255,252,.6)}
.d i{font:400 10px/1 -apple-system,system-ui,sans-serif;font-style:normal}
/* Как в «Графике»: обязательный — бледно-красное пятно, желательный — жёлтое, края размыты. */
.d.r{background:rgba(246,217,214,.92);color:#a1261c;box-shadow:0 0 10px 2px rgba(246,217,214,.7)}
.d.p{background:rgba(251,227,166,.92);color:#5a4300;box-shadow:0 0 10px 2px rgba(251,227,166,.7)}
.d.r:active{background:rgba(246,217,214,1)}.d.p:active{background:rgba(251,227,166,1)}
.d.h{background:rgba(255,255,255,.62)}
.d.k{box-shadow:inset 0 0 0 2px #172126}
.d.x{opacity:.55}
.e{display:flex;align-items:center;gap:4px;margin-top:10px;font-size:14px}
.e:empty{display:none}
.e span{flex:1;white-space:nowrap}.e b{min-width:40px;text-align:center}
.e button{width:30px;height:30px;border-radius:999px;font:600 17px/1 sans-serif}
.n{font-size:13px;margin:10px 0 8px;opacity:.8}
.b{display:flex;gap:8px}
.b button{flex:1;height:42px;border-radius:999px;font:600 15px -apple-system,system-ui,sans-serif}
.b button:active{transform:scale(.965)}
.s{color:#fff!important;background:rgba(24,22,23,.78)!important;
 box-shadow:inset 0 1px 1px rgba(255,255,255,.35),inset 0 0 0 1px rgba(255,255,255,.1),0 12px 24px -12px rgba(24,22,23,.7)!important}
.m{font-size:13px;margin-top:8px;min-height:17px}
</style>
<div class="c"><h1 id="t"></h1>
<div class="g" id="g"></div><div class="e" id="e"></div><div class="n" id="n"></div>
<div class="b"><button class="s" id="sv">Сохранить</button><button class="o" id="no">Пожеланий нет</button></div>
<div class="m" id="m"></div></div>
<script>
var W=window.TelegramWidget,S={},R=0,P=0,H={},K=-1,L=[6,22],MAXH=12,
M=['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'];
function $(i){return document.getElementById(i)}
function at(s){return new Date(s+'T00:00:00Z')}
function b(x){return parseInt(x,36)}
function load(){try{S=JSON.parse(W&&W.chatData||'{}')||{}}catch(e){S={}}R=b(S.r||'0');P=b(S.p||'0');H={};K=-1;
 var h=S.h||'';for(var j=0;j+2<h.length;j+=3)H[b(h[j])]=[b(h[j+1]),b(h[j+2])]}
function cnt(x){var k=0;while(x){k+=x&1;x>>>=1}return k}
function keys(){var a=[];for(var i in H)a.push(+i);return a.sort(function(x,y){return x-y})}
function tm(x,full){var m=360+x*30,h=Math.floor(m/60);return h+(m%60?':30':full?':00':'')}
function day(i){var d=new Date(at(S.s).getTime()+i*864e5);return d.getUTCDate()+' '+M[d.getUTCMonth()]}
function draw(){
 if(!S.s){$('t').textContent='График';return}
 var a=at(S.s),z=at(S.e),f=at(S.f||S.s),n=Math.round((z-a)/864e5)+1,h='';
 $('t').textContent='График на '+a.getUTCDate()+(a.getUTCMonth()==z.getUTCMonth()?'':' '+M[a.getUTCMonth()])+'–'+z.getUTCDate()+' '+M[z.getUTCMonth()];
 ['пн','вт','ср','чт','пт','сб','вс'].forEach(function(w){h+='<div class="w">'+w+'</div>'});
 for(var k=(a.getUTCDay()+6)%7;k>0;k--)h+='<div></div>';
 for(var i=0;i<n;i++){var d=new Date(a.getTime()+i*864e5),bit=1<<i,past=d<f,c=R&bit?'r':P&bit?'p':H[i]?'h':'';
  h+='<button class="d '+c+(K==i?' k':'')+(past?' x':'')+'" '+(past?'disabled':'onclick="tap('+i+')"')+'>'+d.getUTCDate()+'<i>'+
   (c=='r'?'вых':c=='p'?'жел':c=='h'?tm(H[i][0])+'–'+tm(H[i][1]):'')+'</i></button>'}
 $('g').innerHTML=h;
 $('e').innerHTML=H[K]?'<span>'+day(K)+'</span><button onclick="mv(0,-2)">−</button><b>'+tm(H[K][0],1)+'</b><button onclick="mv(0,2)">+</button>–<button onclick="mv(1,-2)">−</button><b>'+
  tm(H[K][1],1)+'</b><button onclick="mv(1,2)">+</button>':'';
 $('n').textContent='Обязательных: '+cnt(R)+' · Желательных: '+cnt(P)+' · С часами: '+keys().length;
 $('m').textContent=S.ok?'Записал '+S.ok+'.':''}
function tap(i){var bit=1<<i;S.ok=null;
 if(R&bit){R&=~bit;P|=bit}
 else if(P&bit){P&=~bit;if(H[i]||keys().length<MAXH){H[i]=H[i]||L.slice();K=i}else{draw();$('m').textContent='Часы можно указать не больше чем на '+MAXH+' дней.';return}}
 else if(H[i]){if(K==i){delete H[i];K=-1}else K=i}
 else R|=bit;
 if(!H[i])K=-1;draw()}
function mv(j,s){var r=H[K];if(!r)return;var v=r[j]+s;if(v<0||v>34||(j?v<=r[0]:v>=r[1]))return;r[j]=v;L=r.slice();S.ok=null;draw()}
function ask(q){var m=$('m');if(!W){return}m.textContent='Сохраняю…';
 W.request(q).then(function(a){m.textContent=a&&a.result&&a.result!='ok'?a.result:'Записал.'},
 function(e){m.textContent=e&&e.code=='RATE_LIMITED'?'Подожди пару секунд и нажми ещё раз.':'Не получилось отправить. Нажми ещё раз.'})}
$('sv').onclick=function(){var h='';keys().forEach(function(i){h+=i.toString(36)+H[i][0].toString(36)+H[i][1].toString(36)});
 ask('o:'+String(S.id).slice(0,8)+':'+R.toString(36)+':'+P.toString(36)+(h?':'+h:''))};
$('no').onclick=function(){R=P=0;H={};K=-1;draw();ask('n:'+String(S.id).slice(0,8))};
if(W){W.onEvent('chatDataChanged',function(){load();draw()})}
load();draw();if(W&&W.ready)W.ready();
</script>'''


class SendDaysOffWidget(shift_widget.SendWidget):
    """sendWidget с данными сообщения: chat_data видит только этот чат."""
    chat_data: str | None = None


class AnswerWidgetQuery(TelegramMethod[bool]):
    __returning__ = bool
    __api_method__ = 'answerWidgetQuery'

    widget_query_id: str
    data: str | None = None


class EditMessageWidgetData(TelegramMethod[bool]):
    __returning__ = bool
    __api_method__ = 'editMessageWidgetData'

    chat_id: int
    message_id: int
    chat_data: str | None = None


def enabled(uid):
    value = os.getenv('DAYS_OFF_WIDGETS', '0').strip().lower()
    if value in {'1', 'true', 'yes', 'on', 'all'}:
        return True
    if value == 'admin':
        from admin import is_admin
        return is_admin(uid)
    return False


def state(row, days, desired, today, saved_at=None, hours=None):
    """chat_data виджета: период, первый доступный день, выбор битами по дням от начала периода, часы тройками."""
    start, end = date.fromisoformat(row['start_date']), date.fromisoformat(row['end_date'])
    mask = lambda values: sum(1 << (date.fromisoformat(d) - start).days for d in values
                              if start <= date.fromisoformat(d) <= end)
    data = {'id': UUID(str(row['id'])).hex, 's': start.isoformat(), 'e': end.isoformat(),
            'f': max(start, today).isoformat(), 'r': _b36(mask(days)), 'p': _b36(mask(desired))}
    packed = _hours_text(max(start, today), end, hours or {}, period_start=start)[:3 * MAX_HOURS]
    if packed:
        data['h'] = packed
    if saved_at:
        data['ok'] = saved_at
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'))


def _b36(number):
    digits = '0123456789abcdefghijklmnopqrstuvwxyz'
    out = ''
    while number:
        number, rest = divmod(number, 36)
        out = digits[rest] + out
    return out or '0'


def _hours_text(start, end, hours, *, period_start=None):
    """{дата: [начало, конец] в минутах} → тройки «день, начало, конец» в 36-ричной записи, шаг 30 минут от 6:00."""
    out = ''
    for key in sorted(hours):
        day = date.fromisoformat(key)
        first, last = -(-(hours[key][0] - 360) // 30), (hours[key][1] - 360) // 30   # округляем внутрь окна
        if start <= day <= end and 0 <= first < last <= 34:
            out += _b36((day - (period_start or start)).days) + _b36(first) + _b36(last)
    return out


# Запрос виджета — не больше 64 байт: первые 8 знаков графика, выходные битами, часы тройками.
QUERY = re.compile(r'(?:o:([0-9a-f]{8}):([0-9a-z]{1,6}):([0-9a-z]{1,6})(?::((?:[0-9a-z]{3}){1,%d}))?|n:([0-9a-f]{8}))' % MAX_HOURS)


def parse(data):
    """'o:<график>:<обязательные>:<желательные>[:<часы>]' или 'n:<график>' (пожеланий нет). Иначе None."""
    found = QUERY.fullmatch(str(data or ''))
    if not found:
        return None
    if found[5]:
        return {'roster': found[5], 'none': True, 'required': 0, 'preferred': 0, 'hours': {}}
    required, preferred = int(found[2], 36), int(found[3], 36)
    hours, packed = {}, found[4] or ''
    for i in range(0, len(packed), 3):
        day, first, last = (int(c, 36) for c in packed[i:i + 3])
        if day in hours or not 0 <= first < last <= 34:
            return None
        hours[day] = (360 + first * 30, 360 + last * 30)
    if required & preferred:
        return None
    return {'roster': found[1], 'none': False, 'required': required, 'preferred': preferred, 'hours': hours}


def hours_for(row, query, today):
    """Часы на будущие дни периода: {дата: [начало, конец] в минутах}. Прошедшие дни не трогаем."""
    start = date.fromisoformat(row['start_date'])
    days = {start + timedelta(days=i): span for i, span in query['hours'].items()}
    return {day.isoformat(): list(span) for day, span in sorted(days.items()) if day >= today}


def preserve_exact_hours(row, today, hours, existing):
    """Keep precise calendar intervals when the widget sends their unchanged 30-minute display."""
    start, end = map(date.fromisoformat, (row['start_date'], row['end_date']))
    first = max(start, today)
    packed = _hours_text(first, end, existing, period_start=start)[:3 * MAX_HOURS]
    shown = {int(packed[i], 36): [360 + int(packed[i+1], 36) * 30, 360 + int(packed[i+2], 36) * 30]
             for i in range(0, len(packed), 3)}
    for key, span in existing.items():
        day = date.fromisoformat(key)
        if first <= day <= end:
            displayed = shown.get((day - start).days)
            if hours.get(key) == displayed:
                hours[key] = span


def changes(row, query, today):
    """Отметка на каждый будущий день периода: обязательный, желательный или нет — виджет шлёт выбор целиком."""
    start, end = date.fromisoformat(row['start_date']), date.fromisoformat(row['end_date'])
    size = (end - start).days + 1
    if query['required'] >> size or query['preferred'] >> size or any(day >= size for day in query['hours']):
        return None
    result = {}
    day = max(start, today)
    while day <= end:
        bit = 1 << (day - start).days
        result[day.isoformat()] = ('required' if query['required'] & bit else
                                   'preferred' if query['preferred'] & bit else 'none')
        day += timedelta(days=1)
    return result


def short_caption(row):
    """Подпись под виджетом — фраза владельца: «Собираем выходные с 16 по 30 ноября»."""
    import roster
    start, end = date.fromisoformat(row['start_date']), date.fromisoformat(row['end_date'])
    first = f'{start.day}' if start.month == end.month else f'{start.day} {roster.MONTHS[start.month - 1]}'
    return f'Собираем выходные с {first} по {end.day} {roster.MONTHS[end.month - 1]}.'


async def send(bot, uid, row, caption, days=(), desired=(), hours=None):
    """Виджет просьбы о выходных. None — не получилось (старый клиент, ошибка): тогда шлют обычный текст."""
    from workday import op_today
    start, end = date.fromisoformat(row['start_date']), date.fromisoformat(row['end_date'])
    # Дни виджет хранит битами 32-битного числа: период длиннее 31 дня получает обычный текст.
    if not enabled(uid) or not shift_widget.fits(HTML) or (end - start).days >= MAX_DAYS:
        return None
    try:
        return await bot(SendDaysOffWidget(chat_id=uid, html=HTML, caption=caption, allow_queries=True, height=0,
                                           protect_content=True, chat_data=state(row, days, desired, op_today(), hours=hours)))
    except Exception as error:
        logging.warning('days off widget refused: %s', type(error).__name__)
        return None


async def apply(uid, data, seed, source='bot'):
    """Записать выбор из календаря (виджет в чате или мини-приложение). → (ответ, новые данные календаря или None).
    seed делает запись идемпотентной: повтор того же нажатия не записывается дважды."""
    import roster_time_store as store
    import roster_wishes as wishes
    from workday import op_today
    parsed = parse(data)
    if parsed is None:
        return 'Обнови сообщение: эта кнопка устарела.', None
    link = await wishes.employee(uid)
    if not link:
        return wishes.NO_ACCESS, None
    rows = await wishes.period_rows(link, op_today())
    row = next((r for r in rows if UUID(str(r['id'])).hex.startswith(parsed['roster']) and wishes.migrated(r)), None)
    if row is None:
        return wishes.NO_PERIOD, None
    today = op_today()
    planned = {} if parsed['none'] else changes(row, parsed, today)
    if planned is None:
        return 'Обнови сообщение: эта кнопка устарела.', None
    operation = uuid5(NAMESPACE_URL, f'roster-wish-widget:{uid}:{seed}:{data}')
    result = await wishes.save(uid, link, row, operation, planned, clear=parsed['none'])
    if result.get('status') != 'ok':
        return wishes.problem(result, row), None
    hours = hours_for(row, parsed, today)
    try:
        if not parsed['none']:
            first, last = map(date.fromisoformat, (row['start_date'], row['end_date']))
            existing = await store.period_hours(link['id'], max(first, today), last)
            preserve_exact_hours(row, today, hours, existing)
        written = await store.write_period_hours(uid, uuid5(operation, 'hours'), row['id'], hours)
    except APIError as error:
        if error.code not in ('PGRST202', '42883', 'PGRST205', '42P01'):
            raise
        written = 'ok' if not hours else 'hours'
    if written == 'hours':
        return 'Выходные записал, а часы пока не получается. Напиши их сообщением.', None   # ЧЕРНОВИК
    if written != 'ok':
        return wishes.problem({'status': written}, row), None
    import research
    research.track(uid, 'days_off_answered', source=source, screen='schedule')
    saved_at = datetime.now(_moscow()).strftime('%d.%m в %H:%M')
    return 'ok', state(row, result.get('days', []), result.get('desired_days', []), today, saved_at, hours)


async def handle(bot, query):
    """Ответ из виджета: записать выбор, обновить виджет, ответить за 15 секунд. Без содержимого в журнале."""
    answer = 'Не получилось записать. Нажми ещё раз.'   # ЧЕРНОВИК: тексты виджета ждут согласования владельца
    try:
        uid = int(query['from']['id'])
        chat_id = int(query['chat']['id'])
        message_id = int(query['message_id'])
        if chat_id != uid:
            answer = 'Обнови сообщение: эта кнопка устарела.'
            return
        answer, chat_data = await apply(uid, query.get('data'), message_id)
        if chat_data is not None:
            try:
                await bot(EditMessageWidgetData(chat_id=chat_id, message_id=message_id, chat_data=chat_data))
            except Exception as error:
                logging.warning('days off widget data not updated: %s', type(error).__name__)
    except Exception as error:
        answer = 'Не получилось записать. Нажми ещё раз.'
        from diagnostics import failure
        failure(error, area='roster_wishes', stage='widget')
    finally:
        try:
            await bot(AnswerWidgetQuery(widget_query_id=str(query.get('id')), data=answer[:1024]))
        except Exception as error:
            logging.warning('days off widget answer failed: %s', type(error).__name__)


def _moscow():
    from workday import MSK
    return MSK
