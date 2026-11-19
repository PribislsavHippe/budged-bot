"""Собирает docs/prototypes/miniapp-preview.html из текущих webapp/index.html, glass.css и background.js.

Страница для проверки фона, вкладок и выделения периода в календаре без Telegram и сервера; данные вымышленные.
Запуск: .venv/bin/python docs/prototypes/build_miniapp_preview.py
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
read = lambda p: open(os.path.join(ROOT, p), encoding='utf-8').read()


def between(s, start, end, include_end=False):
    a = s.index(start)
    b = s.index(end, a)
    return s[a:b + (len(end) if include_end else 0)]


index = read('webapp/index.html')
css = index[index.index('<style>') + 7:index.index('</style>')]
nav = between(index, '<div class="tabs-scroll">', '</nav>', True) + '</div>'
tabs_js = between(index, '  // Вкладки: тёмная плашка', "})(document.querySelector('.app-tabs'));", True)
period_js = between(index, 'function drawPeriod()', "window.addEventListener('tips-period'")

PAGE = r'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Мини-приложение: проверка вкладок и календаря</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Onest:wght@400;500;600;700&family=Prata&display=swap">
<!-- Собрано скриптом build_miniapp_preview.py из webapp/. Данные вымышленные. -->
<style>%CSS%
%GLASS%
.demo-note{font-size:13px;color:var(--ink-soft);margin:6px 0 14px}
.demo-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.panel-title{font-size:22px;margin:22px 0 14px}
/* Варианты выделения для сравнения */
html.sel-dark{--sel-fill:rgba(24,22,23,.45);--sel-ink:#fff;--sel-shift:#9ecbe8;--sel-req:#f19a9a;--sel-pref:#e7c45c}
html.sel-solid{--sel-fill:rgba(24,22,23,.78);--sel-ink:#fff;--sel-shift:#9ecbe8;--sel-req:#f19a9a;--sel-pref:#e7c45c}
.sel-bar{position:fixed;left:50%;bottom:calc(16px + env(safe-area-inset-bottom));transform:translateX(-50%);z-index:50;display:flex;gap:4px;padding:5px;border-radius:999px;
  background:rgba(255,255,255,.7);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);box-shadow:0 8px 20px -10px rgba(40,30,80,.38)}
.sel-bar button{border:0;border-radius:999px;padding:8px 11px;font:600 13px Onest,sans-serif;background:transparent;color:#111;white-space:nowrap}
.sel-bar button[aria-pressed=true]{background:#111;color:#fff}
body{padding-bottom:90px}
</style></head><body>
<div id="bg" aria-hidden="true"><canvas id="gradient-canvas"></canvas></div>
%NAV%
<section id="earnings-panel" role="tabpanel">
  <div class="hero-label">Чаевые за месяц</div>
  <div class="hero num">32 400</div>
  <h2 class="panel-title">Календарь смен</h2>
  <div class="cal-wrap" id="cal-wrap"><div class="cal-grid" id="cal-grid"></div></div>
  <p class="demo-note" style="margin-top:14px">Выбери период кнопками ниже или проведи пальцем по дням.</p>
  <div class="demo-row">
    <button class="chip" data-p="2026-11-16,2026-11-22">Эта неделя</button>
    <button class="chip" data-p="2026-11-01,2026-11-15">1–15</button>
    <button class="chip" data-p="2026-11-16,2026-11-30">16–30</button>
    <button class="chip" data-p="2026-11-11,2026-11-26">11–26</button>
    <button class="chip" data-p="">Весь месяц</button>
  </div>
  <button class="btn" type="button">Сохранить смену</button>
  <button class="btn btn-ghost" type="button">Пожеланий нет</button>
</section>
<section id="schedule-panel" role="tabpanel" hidden><h2 class="panel-title">График</h2><p class="demo-note">Здесь был бы график.</p></section>
<section id="sales-panel" role="tabpanel" hidden><h2 class="panel-title">План</h2><p class="demo-note">Здесь был бы план продаж.</p></section>
<div class="sel-bar" role="group" aria-label="Цвет выделения"><button data-c="" aria-pressed="true">Светлое стекло</button><button data-c="sel-dark" aria-pressed="false">Тёмное 45 %</button><button data-c="sel-solid" aria-pressed="false">Тёмное, было</button></div>
<script>%BG%</script>
<script>
window.selectTab=key=>{
  document.querySelectorAll('.app-tabs [role=tab]').forEach(t=>t.setAttribute('aria-selected',String(t.id==='tab-'+key)));
  document.querySelectorAll('body>[role=tabpanel]').forEach(p=>{p.hidden=p.id!==key+'-panel';});
};
document.querySelectorAll('.app-tabs [role=tab]').forEach(t=>t.onclick=()=>selectTab(t.id.slice(4)));
document.getElementById('tab-schedule').hidden=false;
%TABS%

let tipsPeriod={mode:'range',start:'2026-11-16',end:'2026-11-22'},calPreview=null;
const grid=document.getElementById('cal-grid');
for(let i=0;i<6;i++)grid.appendChild(Object.assign(document.createElement('div'),{className:'cal-cell empty'}));
const shifts={2:'12,5 ч',3:'11,5 ч',4:'10,5 ч',8:'12,1 ч',9:'9,3 ч',12:'10,8 ч',13:'8,2 ч',15:'12,3 ч',16:'10,7 ч',19:'10–22',20:'12–23',23:'10–22',24:'11–23',27:'9–21',28:'11–23',29:'12–23'};
for(let d=1;d<=30;d++){
  const c=document.createElement('button');c.type='button';c.className='cal-cell';c.dataset.iso='2026-11-'+String(d).padStart(2,'0');
  c.append(String(d));
  if(shifts[d]){c.classList.add('shift');const t=document.createElement('span');t.className='cal-time';t.textContent=shifts[d];c.appendChild(t);}
  if(d===19)c.classList.add('today');
  if(d===25)c.dataset.wish='required';if(d===26)c.dataset.wish='preferred';
  grid.appendChild(c);
}
%PERIOD%
window.addEventListener('resize',()=>drawPeriod());
if('ResizeObserver' in window)new ResizeObserver(()=>drawPeriod()).observe(document.getElementById('cal-wrap'));
document.querySelectorAll('[data-p]').forEach(b=>b.onclick=()=>{const p=b.dataset.p.split(',');
  tipsPeriod=p[0]?{mode:'range',start:p[0],end:p[1]}:null;calPreview=null;drawPeriod();});
let from=null;
grid.addEventListener('pointerdown',e=>{const c=e.target.closest('.cal-cell[data-iso]');if(!c)return;from=c.dataset.iso;calPreview={start:from,end:from};drawPeriod();grid.setPointerCapture(e.pointerId);});
grid.addEventListener('pointermove',e=>{if(!from)return;const el=document.elementFromPoint(e.clientX,e.clientY);const c=el&&el.closest('.cal-cell[data-iso]');if(!c)return;
  const iso=c.dataset.iso;calPreview={start:from<iso?from:iso,end:from<iso?iso:from};drawPeriod();});
grid.addEventListener('pointerup',()=>{if(!from)return;tipsPeriod={mode:'range',...calPreview};calPreview=null;from=null;drawPeriod();});
document.querySelectorAll('.sel-bar button').forEach(b=>b.onclick=()=>{document.documentElement.className=b.dataset.c;
  document.querySelectorAll('.sel-bar button').forEach(x=>x.setAttribute('aria-pressed',x===b));drawPeriod();});
drawPeriod();
</script></body></html>
'''

page = (PAGE.replace('%CSS%', css).replace('%GLASS%', read('webapp/glass.css')).replace('%NAV%', nav)
        .replace('%BG%', read('webapp/background.js')).replace('%TABS%', tabs_js).replace('%PERIOD%', period_js))
with open(os.path.join(ROOT, 'docs/prototypes/miniapp-preview.html'), 'w', encoding='utf-8') as f:
    f.write(page)
print('docs/prototypes/miniapp-preview.html')
