// Вкладка «График» в мини-приложении: опубликованный график всей команды.
// На телефоне — имена и три дня, остальные листаются; повернул телефон — весь график на экран.
(() => {
  'use strict';
  const $=id=>document.getElementById(id),tg=window.Telegram?.WebApp,G=window.ScheduleGrid;
  const panel=$('schedule-panel'),tab=$('tab-schedule');
  if(!panel||!tab||!G)return;
  let data=null,busy=false,picked=null;
  panel.classList.add('sg');
  panel.innerHTML=`<header class="sg-head"><div><h1 id="sg-title"></h1><p id="sg-sub"></p></div>
      <div class="sg-nav"><button type="button" id="sg-prev" aria-label="Предыдущий график">‹</button><button type="button" id="sg-next" aria-label="Следующий график">›</button></div></header>
    <p id="sg-error" class="sg-error" role="alert"></p>
    <section id="sg-mine" class="sg-mine" hidden aria-label="Мои смены"></section>
    <section class="sg-sheet" id="sg-sheet" hidden aria-label="График смен"><div class="sg-scroll"><table id="sg-grid"></table></div>
      <p class="sg-detail" id="sg-detail" role="status"></p>
      <button type="button" class="sg-expand" id="sg-expand"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2.5" y="6" width="19" height="12" rx="2.5"/><path d="M7 3.5h3M14 20.5h3"/></svg>Весь график</button></section>
    <p id="sg-empty" class="sg-empty" hidden></p>
    <button type="button" id="sg-site" class="sg-site" hidden>Составить график на сайте</button>`;
  const grid=$('sg-grid'),sc=grid.parentElement,sheet=$('sg-sheet');
  const scroll=G.scroller(grid,sc,{sheet});
  const phone=matchMedia('(max-width: 700px)');
  async function api(body={}){
    const r=await fetch('/api/schedule/view',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({...body,initData:tg?.initData||''}),signal:AbortSignal.timeout(20000)});
    let json;try{json=await r.json();}catch(_){throw new Error('Сервер не ответил. Попробуй ещё раз.');}
    if(!r.ok)throw new Error(json.error||'График пока недоступен.');
    return json;
  }
  const options=()=>({days:data.period.days,people:data.period.people,events:data.period.events,holidays:data.period.holidays,today:data.today,weekGap:false});
  function render(){
    const p=data.period,list=data.periods,index=p?list.findIndex(x=>x.start===p.start):-1;
    const place=data.restaurants.find(r=>r.id===data.restaurant_id);
    $('sg-title').textContent=p?G.period(p.start,p.end):'';
    $('sg-sub').textContent=place&&data.restaurants.length>1?place.name:'';
    $('sg-prev').hidden=$('sg-next').hidden=list.length<2;
    $('sg-prev').disabled=index<=0;$('sg-next').disabled=index<0||index>=list.length-1;
    $('sg-site').hidden=!(data.can_edit&&data.site_url);
    sheet.hidden=!p;$('sg-empty').hidden=!!p;
    renderMine(p);
    if(!p){$('sg-empty').textContent='График ещё не опубликован.';return;}
    const near=phone.matches;
    grid.className=near?'sg-near':'';
    grid.innerHTML=G.table({...options(),near,scrolls:true});
    $('sg-expand').hidden=!near;
    fitRows();
    scroll.size();
    picked=p.days.includes(data.today)?data.today:p.days[0];
    scroll.go(picked,false);
  }
  // Весь список людей на первом экране: высота строк подбирается под окно (36–48 px).
  function fitRows(){
    grid.style.setProperty('--rh','48px');
    if(!grid.classList.contains('sg-near'))return;
    const top=grid.getBoundingClientRect().top+scrollY,room=innerHeight-top-84,rows=Math.max(1,data.period.people.length);
    let rh=48;
    for(let pass=0;pass<4&&rh>36;pass++){
      grid.style.setProperty('--rh',rh+'px');
      const over=grid.offsetHeight-room;if(over<=0)break;
      rh=Math.max(36,rh-Math.ceil(over/(rows+1)));
    }
    grid.style.setProperty('--rh',rh+'px');
  }
  // Свои ближайшие смены над таблицей: искать себя глазами не нужно.
  // Свои обязательные выходные (записанные в боте) и пожелание по дням недели видны сразу, ещё до публикации.
  const WD=['понедельникам','вторникам','средам','четвергам','пятницам','субботам','воскресеньям'];
  function daysText(list){
    const by={};list.forEach(d=>{const x=G.at(d);(by[x.getMonth()]=by[x.getMonth()]||[]).push(x.getDate());});
    return Object.entries(by).map(([m,ds])=>`${ds.join(', ')} ${G.MONTHS[m]}`).join(', ');
  }
  function offsText(){
    const mine=data.mine||{},days=(mine.requests||[]).flatMap(r=>r.days),wd=mine.weekdays||[];
    const over=(mine.requests||[]).find(r=>r.limit&&r.days.length>r.limit);
    const wish=[wd.length?`выходные по ${wd.map(d=>WD[d]).join(', ')}`:'',mine.pattern?`график ${mine.pattern}`:''].filter(Boolean).join(', ');
    const parts=[days.length?`Твои выходные: ${daysText(days)}${over?` (больше ${over.limit} — по согласованию с менеджером)`:''}`:'',wish?`Пожелание: ${wish}`:''].filter(Boolean);
    return parts.length?`<p class="sg-mine-off">${parts.map(G.esc).join('<br>')}</p>`:'';
  }
  function renderMine(p){
    const box=$('sg-mine'),me=p?.people.find(x=>x.me),offs=offsText();
    if(!me){box.hidden=!offs;box.innerHTML=offs;return;}
    const today=data.today,rel=['Сегодня','Завтра'];
    const next=p.days.filter(d=>d>=today&&G.works(G.parse(me.cells[d]))).slice(0,3);
    const label=d=>{const k=Math.round((G.at(d)-G.at(today))/864e5);return rel[k]||G.dayName(d).replace(/^(\S+), (\d+).*/,'$1, $2');};
    box.hidden=false;
    box.innerHTML=(next.length?`<p>Твои смены</p><ul>${next.map(d=>{const v=G.parse(me.cells[d]);
        return `<li><span>${G.esc(label(d))}</span><b>${G.clock(v.start)}${v.end!=null?'–'+G.clock(v.end):''}</b></li>`;}).join('')}</ul>`
      :'<p>Смен больше нет</p>')+offs;
  }
  async function load(start){
    if(busy)return;busy=true;$('sg-error').textContent='';
    try{data=await api(start?{start,restaurant_id:data?.restaurant_id}:{});const mine=data.mine||{};tab.hidden=!data.available||(!data.periods.length&&!data.can_edit&&!(mine.requests||[]).length&&!(mine.weekdays||[]).length&&!mine.pattern);if(tab.hidden)return;render();window.dispatchEvent(new Event('schedule-tab-ready'));}
    catch(e){$('sg-error').textContent=e.name==='TimeoutError'?'Ответ задерживается. Попробуй ещё раз.':e.message;}
    finally{busy=false;}
  }
  const step=delta=>{const list=data.periods,i=list.findIndex(x=>x.start===data.period?.start);if(list[i+delta])load(list[i+delta].start);};
  $('sg-prev').onclick=()=>step(-1);$('sg-next').onclick=()=>step(1);
  grid.addEventListener('click',e=>{
    const h=e.target.closest('thead th.sg-day');if(h){picked=h.dataset.day;scroll.go(picked);return;}
    const c=e.target.closest('td.sg-c');if(!c)return;
    const person=data.period.people[+c.dataset.r],v=G.parse(person.cells[c.dataset.day]),off=person.off.includes(c.dataset.day);
    const end=v&&v.start!=null?(v.end??Math.min(v.start+12,23)):null;
    $('sg-detail').textContent=`${person.name} · ${G.dayName(c.dataset.day)} · ${v&&v.start!=null?`${G.clock(v.start)}–${G.clock(end)}${off?' вместо выходного':''}`:off?'обязательный выходной':'не работает'}`;
  });
  grid.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.closest('th.sg-day,td.sg-c')){e.preventDefault();e.target.click();}});
  G.hover(grid);
  addEventListener('resize',()=>{if(data?.period&&!panel.hidden){fitRows();scroll.size();scroll.go(picked,false);}});
  phone.addEventListener('change',()=>{if(data?.period)render();});
  const land=G.landscape(()=>!panel.hidden&&data?.period?options():null);
  $('sg-expand').onclick=()=>land.expand();
  $('sg-site').onclick=()=>{try{tg?.openLink?tg.openLink(data.site_url):window.open(data.site_url,'_blank','noopener');}catch(_){location.href=data.site_url;}};
  tab.onclick=()=>{tab.dataset.opened='1';window.selectTab('schedule');if(data?.period){render();land.refresh();}else load();};
  load();
})();
