(() => {
  'use strict';
  const root=document.getElementById('tips-dashboard');
  const tg=window.Telegram?.WebApp;
  const $=id=>document.getElementById(id);
  const money=n=>Number(n).toLocaleString('ru-RU',{maximumFractionDigits:0});
  const MONTHS_GEN=['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'];
  const MONTHS=['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
  const WEEKDAYS=['понедельник','вторник','среда','четверг','пятница','суббота','воскресенье'];
  const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  /* ---------- даты: ISO-строки без часовых поясов ---------- */
  const DAY0='2000-01-01';
  const parse=s=>new Date(s+'T12:00:00Z');
  const iso=d=>d.toISOString().slice(0,10);
  const addDays=(s,n)=>{const d=parse(s);d.setUTCDate(d.getUTCDate()+n);return iso(d);};
  const dowOf=s=>(parse(s).getUTCDay()+6)%7;                       // понедельник = 0
  const monthEnd=s=>iso(new Date(Date.UTC(+s.slice(0,4),+s.slice(5,7),0,12)));
  const dayMonth=s=>Number(s.slice(8))+' '+MONTHS_GEN[Number(s.slice(5,7))-1];
  const date=dayMonth;
  // Операционный день начинается в 06:00 по Москве; точное «сегодня» приходит в ответе сервера.
  const guessToday=()=>(typeof lastStats!=='undefined'&&lastStats?.operational_today)||window.privateMoney?.state?.today
    ||new Date(Date.now()-6*36e5).toLocaleDateString('sv-SE',{timeZone:'Europe/Moscow'});

  /* ---------- выбранный период ---------- */
  // mode: day | week | half | month | all | custom. Неделя — календарная, с понедельника.
  const ui={mode:'week',day:null,a:null,b:null,picking:false};
  let today=guessToday(),corrected=false;
  function bounds(){
    const d=ui.day||today;
    if(ui.mode==='day')return [d,d];
    if(ui.mode==='week'){const s=addDays(d,-dowOf(d));return [s,addDays(s,6)];}
    if(ui.mode==='half'){const m=d.slice(0,8);return d.slice(8)<='15'?[m+'01',m+'15']:[m+'16',monthEnd(d)];}
    if(ui.mode==='month')return [d.slice(0,8)+'01',monthEnd(d)];
    if(ui.mode==='all')return [DAY0,today];
    return ui.a&&ui.b?[ui.a,ui.b].sort():[today,today];
  }
  function title(){
    if(ui.picking)return 'Выбери дни в календаре';
    const [x,y]=bounds(),sameMonth=x.slice(0,7)===y.slice(0,7);
    const span=sameMonth?'с '+Number(x.slice(8))+' по '+dayMonth(y):'с '+dayMonth(x)+' по '+dayMonth(y);
    if(ui.mode==='day')return (x===today?'Сегодня, ':'')+dayMonth(x)+', '+WEEKDAYS[dowOf(x)];
    if(ui.mode==='week')return 'Неделя '+span;
    if(ui.mode==='half')return 'Полмесяца '+span;
    if(ui.mode==='month')return MONTHS[Number(x.slice(5,7))-1]+(x.slice(0,4)===today.slice(0,4)?'':' '+x.slice(0,4));
    if(ui.mode==='all')return 'За всё время';
    return x===y?dayMonth(x):'С '+(sameMonth?Number(x.slice(8))+' по '+dayMonth(y):dayMonth(x)+' по '+dayMonth(y));
  }
  const hoursWord=h=>{const n=Math.floor(h),r=n%10,q=n%100;return r===1&&q!==11?'час':r>=2&&r<=4&&(q<12||q>14)?'часа':'часов';};
  const hoursText=h=>Number(h).toLocaleString('ru-RU',{maximumFractionDigits:1});
  // Правки и новые записи доступны, когда выбран один день: иначе непонятно, на какой день записывать.
  const single=()=>current&&current.period.start===current.period.end;

  let current=null,request=0,detailKind=null,editing=null,pendingTip=null,busy=false;

  const heroHtml=(hours,gross,net,expenses,placeholder)=>{
    const v=x=>placeholder?'<span class="tips-placeholder" aria-hidden="true">&nbsp;</span>':x;
    return `<div class="tips-hero" id="tips-hero">
      <button class="tips-cell tips-hours" id="tips-hours-btn" type="button"${placeholder?' disabled':''}><span>Часы${placeholder?'':' ›'}</span><strong id="tips-hours">${v(hours)}</strong></button>
      <div class="tips-money">
        <button id="tips-gross" class="tips-cell tips-lg" type="button"${placeholder?' disabled':''}><span>Грязные${placeholder?'':' ›'}</span><strong>${v(gross)}</strong></button>
        <div class="tips-sub">
          <div class="tips-cell plain"><span>Чистые</span><strong>${v(net)}</strong></div>
          <button id="tips-expense" class="tips-cell" type="button"${placeholder?' disabled':''}><span>Расходы${placeholder?'':' ›'}</span><strong>${v(expenses)}</strong></button>
        </div>
      </div></div>`;
  };
  root.innerHTML=`<div class="dashboard-controls"><button id="tips-period" class="period-link" aria-haspopup="dialog" aria-expanded="false" aria-controls="tips-range"></button><button id="tips-privacy" class="privacy-link">Приватность ›</button></div>
    <div id="tips-range" class="range-backdrop" hidden><section class="range-picker" role="dialog" aria-modal="true" aria-labelledby="tips-range-title"><div class="range-head"><h2 id="tips-range-title">Период</h2><button id="tips-cancel" type="button" aria-label="Закрыть выбор периода">✕</button></div><div id="tips-options"></div></section></div>
    <div id="tips-error" role="alert"></div><button id="tips-retry" class="text-action" hidden>Повторить</button>
    <div id="tips-result" aria-live="polite">${heroHtml('','','','',true)}</div>
    <div id="tips-detail" class="detail-backdrop" hidden><section class="detail-sheet" role="dialog" aria-modal="true" aria-labelledby="tips-detail-title"><div class="detail-head"><h2 id="tips-detail-title">—</h2><button id="tips-detail-close" type="button" aria-label="Закрыть">✕</button></div><p id="tips-detail-status" role="status"></p><div id="tips-detail-body"></div></section></div>`;
  $('tips-period').textContent=title()+' ›';

  /* ---------- размеры цифр: часы по высоте — весь денежный блок ---------- */
  // Кегли идут по золотой пропорции: 17 → 28 → 94 (×1,6 и ×3,4); длинные числа сами уменьшаются, чтобы влезть в колонку.
  const ctx=document.createElement('canvas').getContext('2d');
  function glyph(el){
    const cs=getComputedStyle(el),mark=document.createElement('i');
    mark.style.cssText='display:inline-block;width:0;height:0;vertical-align:baseline';
    el.appendChild(mark);const base=mark.getBoundingClientRect().top;mark.remove();
    ctx.font=`${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    const m=ctx.measureText(el.textContent.replace(/\s/g,''));
    return {top:base-m.actualBoundingBoxAscent,bottom:base+m.actualBoundingBoxDescent,size:parseFloat(cs.fontSize)};
  }
  function fit(hero,sel,max,prop){
    hero.style.setProperty(prop,'100px');
    let k=max;hero.querySelectorAll(sel).forEach(el=>{if(el.scrollWidth)k=Math.min(k,100*el.parentNode.clientWidth/el.scrollWidth);});
    hero.style.setProperty(prop,Math.floor(k)+'px');
  }
  function fitHero(){
    const hero=$('tips-hero');if(!hero||!hero.offsetWidth)return;
    const h=$('tips-hours'),g=hero.querySelector('.tips-lg strong'),n=hero.querySelector('.tips-sub .plain strong');
    if(!h||!g||!n||h.querySelector('.tips-placeholder'))return;
    hero.classList.toggle('wide',h.textContent.replace(/\D/g,'').length>=3);   // от трёх цифр часов деньги уходят к правому краю
    fit(hero,'.tips-lg strong',28,'--big');fit(hero,'.tips-sub .tips-cell strong',17,'--small');
    hero.style.setProperty('--huge-top','12px');
    const top=glyph(g).top,bot=glyph(n).bottom;
    let gh=glyph(h),size=.85*gh.size*(bot-top)/(gh.bottom-gh.top);
    hero.style.setProperty('--huge',size+'px');
    const w=(h.parentNode.clientWidth-12)/h.scrollWidth;if(w<1){size*=w;hero.style.setProperty('--huge',size+'px');}
    gh=glyph(h);hero.style.setProperty('--huge-top',(12+top-gh.top)+'px');
  }
  if('ResizeObserver' in window)new ResizeObserver(fitHero).observe(root);
  document.fonts?.ready?.then(fitHero);

  /* ---------- связь с календарём ---------- */
  const announce=()=>{const [start,end]=bounds();
    window.dispatchEvent(new CustomEvent('tips-period',{detail:{start,end,mode:ui.mode,picking:ui.picking,today}}));};
  window.addEventListener('tips-set-range',e=>{
    const {start,end}=e.detail;ui.mode='custom';ui.a=start;ui.b=end;ui.picking=false;
    $('tips-period').textContent=title()+' ›';announce();load();
  });

  /* ---------- выбор периода ---------- */
  const OPTIONS=[['day','День'],['week','Неделя'],['half','Полмесяца','1–15 или 16–конец'],['month','Месяц'],['all','За всё время'],['custom','Свои дни','выбрать в календаре']];
  const closeRange=()=>{$('tips-range').hidden=true;$('tips-period').setAttribute('aria-expanded','false');$('tips-period').focus();};
  $('tips-period').onclick=()=>{
    $('tips-options').innerHTML=OPTIONS.map(([k,t,n])=>`<button class="range-option${k===ui.mode&&!ui.picking?' on':''}" data-k="${k}" type="button"><span>${t}${n?` <small>${n}</small>`:''}</span></button>`).join('');
    $('tips-range').hidden=false;$('tips-period').setAttribute('aria-expanded','true');
  };
  $('tips-cancel').onclick=closeRange;
  $('tips-range').onclick=e=>{if(e.target===$('tips-range'))closeRange();};
  $('tips-range').onkeydown=e=>{if(e.key==='Escape')closeRange();};
  $('tips-options').onclick=e=>{
    const b=e.target.closest('.range-option');if(!b)return;
    const k=b.dataset.k;ui.mode=k;ui.picking=false;
    if(k==='custom'){
      ui.picking=true;
      const [x,y]=bounds();ui.a=x;ui.b=y;
      $('tips-period').textContent=title()+' ›';
      closeRange();announce();
      document.getElementById('cal-grid')?.scrollIntoView({behavior:'smooth',block:'center'});
      return;
    }
    $('tips-period').textContent=title()+' ›';closeRange();announce();load();
  };
  $('tips-privacy').onclick=()=>document.dispatchEvent(new Event('tips-open-privacy'));

  /* ---------- листы с записями ---------- */
  const closeDetail=()=>{$('tips-detail').hidden=true;editing=null;detailKind=null;};
  $('tips-detail-close').onclick=closeDetail;
  $('tips-detail').onclick=e=>{if(e.target===$('tips-detail'))closeDetail();};
  $('tips-detail').onkeydown=e=>{if(e.key==='Escape')closeDetail();};
  const entries=kind=>(current?.entries||[]).filter(e=>kind==='tips'?e.kind==='income'&&e.category==='Чаевые':e.kind==='expense');
  $('tips-detail-body').onclick=e=>{
    const button=e.target.closest('[data-edit]');
    if(button&&single())showEdit(entries('tips')[Number(button.dataset.edit)]);
  };
  async function post(path,body){
    const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},cache:'no-store',signal:AbortSignal.timeout(20000),body:JSON.stringify({initData:tg?.initData||'',...body})});
    const data=await r.json();
    if(!r.ok)throw new Error(r.status===401?'Открой бота и зайди в приложение ещё раз.':data.error||'Не получилось сохранить.');
    return window.PrivateFinance.view(path,body,data);
  }
  function changed(){closeDetail();document.dispatchEvent(new Event('tips-money-changed'));}
  function showHours(){
    const p=current.period,list=current.worked||[];
    detailKind='hours';editing=null;
    $('tips-detail-title').textContent='Часы · '+title();
    $('tips-detail-status').textContent='';
    const total=`<div class="detail-totals"><div>Всего <strong>${hoursText(current.hours||0)} ч</strong></div><div>Смен <strong>${list.length}</strong></div></div>`;
    const rows=list.length?list.slice().reverse().map(w=>`<div class="detail-row"><div><span>${esc(w.date===current.today?'Сегодня':dayMonth(w.date))} · ${esc(WEEKDAYS[dowOf(w.date)])}</span><strong>${hoursText(w.hours)} ${hoursWord(w.hours)}</strong></div></div>`).join('')
      :'<p class="detail-empty">За этот период записанных смен нет.</p>';
    $('tips-detail-body').innerHTML=total+rows+'<p class="detail-empty">Часы — это записанные отработанные смены. Если у смены время не записано, часов у неё нет.</p>';
    $('tips-detail').hidden=false;
  }
  function showDetail(kind){
    if(!current)return;
    if(kind==='hours')return showHours();
    detailKind=kind;editing=null;
    const p=current.period,list=entries(kind),isTips=kind==='tips',can=single();
    $('tips-detail-title').textContent=(isTips?'Грязные':'Расходы')+' · '+title();
    $('tips-detail-status').textContent='';
    const cash=list.filter(e=>e.account==='cash').reduce((sum,e)=>sum+Math.abs(e.amount),0);
    const card=list.filter(e=>e.account==='card').reduce((sum,e)=>sum+Math.abs(e.amount),0);
    const totals=isTips?`<div class="detail-totals"><div>Наличные <strong>${money(cash)} ₽</strong></div><div>Безналичные <strong>${money(card)} ₽</strong></div></div>`:'';
    const rows=list.length?list.map((e,i)=>`<div class="detail-row"><div><span>${esc(e.date===current.today?'Сегодня':date(e.date))} · ${isTips?(e.account==='cash'?'Наличные':'Безналичные'):esc(e.category)}</span><strong>${money(Math.abs(e.amount))} ₽</strong></div>${isTips&&can?`<button type="button" data-edit="${i}">Изменить ›</button>`:''}</div>`).join(''):'<p class="detail-empty">Записей за этот период нет.</p>';
    const actions=can?(isTips?`<form id="tips-add-form" class="detail-form"><label>Новые чаевые<input id="tips-new-amount" type="number" inputmode="decimal" step="0.01" min="0.01" max="10000000" placeholder="Сумма ₽" required></label><label>Куда поступили<select id="tips-new-account"><option value="card">Безналичные</option><option value="cash">Наличные</option></select></label><button type="submit">Добавить чаевые</button></form>`:`<button id="tips-add-spend" class="detail-primary" type="button">Записать расход за ${date(p.start)}</button>`):'';
    $('tips-detail-body').innerHTML=totals+rows+'<div id="tips-edit-form" hidden></div>'+actions;
    $('tips-detail').hidden=false;
    if(can&&isTips){
      $('tips-add-form').onsubmit=addTip;
    }
    if(can&&!isTips)$('tips-add-spend').onclick=()=>{
      const day=p.start;closeDetail();document.dispatchEvent(new CustomEvent('tips-open-spend',{detail:{date:day}}));
    };
  }
  function showEdit(entry){
    if(!entry)return;
    editing=entry;
    const box=$('tips-edit-form');box.hidden=false;
    box.innerHTML=`<form id="tips-edit-fields" class="detail-form"><label>Сумма чаевых<input id="tips-edit-amount" type="number" inputmode="decimal" step="0.01" min="0.01" max="10000000" required></label><label>Куда поступили<select id="tips-edit-account"><option value="card">Безналичные</option><option value="cash">Наличные</option></select></label><div class="detail-edit-actions"><button type="submit">Сохранить</button><button id="tips-delete" type="button">Удалить</button></div></form>`;
    $('tips-edit-amount').value=Math.abs(entry.amount);$('tips-edit-account').value=entry.account;
    $('tips-edit-fields').onsubmit=saveTip;
    $('tips-delete').onclick=deleteTip;
    box.scrollIntoView({block:'nearest',behavior:'smooth'});
  }
  const validAmount=raw=>{
    const normalized=String(raw).replace(',','.');
    if(!/^\d+(?:\.\d{1,2})?$/.test(normalized))return null;
    const value=Number(normalized);
    return Number.isFinite(value)&&value>0&&value<=10000000?value:null;
  };
  async function saveTip(e){
    e.preventDefault();if(busy||!editing)return;
    const amount=validAmount($('tips-edit-amount').value),account=$('tips-edit-account').value;
    if(amount===null){$('tips-detail-status').textContent='Проверь сумму чаевых.';return;}
    busy=true;$('tips-detail-status').textContent='Сохраняю…';
    try{
      if(window.privateMoney.active)await privateMoney.edit(editing.id,{signed_amount:amount,account});
      else await post('/api/entry_edit',{entry_id:editing.id,action:'tip_details',amount,account});
      changed();
    }catch(error){$('tips-detail-status').textContent=error.message;}
    finally{busy=false;}
  }
  async function deleteTip(){
    if(busy||!editing||!confirm('Удалить эту запись о чаевых?'))return;
    busy=true;$('tips-detail-status').textContent='Удаляю…';
    try{
      if(window.privateMoney.active)await privateMoney.remove(editing.id);
      else await post('/api/entry_edit',{entry_id:editing.id,action:'delete'});
      changed();
    }catch(error){$('tips-detail-status').textContent=error.message;}
    finally{busy=false;}
  }
  async function addTip(e){
    e.preventDefault();if(busy)return;
    const amount=validAmount($('tips-new-amount').value),account=$('tips-new-account').value,day=current.period.start;
    if(amount===null){$('tips-detail-status').textContent='Проверь сумму чаевых.';return;}
    if(!pendingTip)pendingTip={operation_id:crypto.randomUUID(),amount,account,day,mode:privateMoney.active?'device':'server'};
    if(pendingTip.amount!==amount||pendingTip.account!==account||pendingTip.day!==day){$('tips-detail-status').textContent='Сначала проверь предыдущую попытку сохранения.';return;}
    busy=true;$('tips-detail-status').textContent='Сохраняю…';
    try{
      if(window.privateMoney.active)await privateMoney.add({id:'local:'+pendingTip.operation_id,source_key:'calendar:'+pendingTip.operation_id,kind:'income',account,
        signed_amount:amount,category:'Чаевые',note:'из миниаппа',work_date:day,created_at:new Date().toISOString()},pendingTip.mode==='server');
      else await post('/api/calendar_edit',{date:day,action:'tip_add',amount,account,operation_id:pendingTip.operation_id});
      pendingTip=null;changed();
    }catch(error){$('tips-detail-status').textContent=error.message;}
    finally{busy=false;}
  }

  /* ---------- показ и загрузка ---------- */
  function render(data){
    const p=data.period,hours=data.hours||0;
    current=data;today=data.today;
    $('tips-period').textContent=title()+' ›';
    const known=p.has_data||data.has_shift||hours>0;
    $('tips-result').innerHTML=heroHtml(known?hoursText(hours):'—',known?money(p.gross):'—',known?money(p.net):'—',known?money(p.expenses):'—',false);
    $('tips-hours-btn').onclick=()=>showDetail('hours');
    $('tips-gross').onclick=()=>showDetail('tips');
    $('tips-expense').onclick=()=>showDetail('expenses');
    $('tips-result').hidden=false;$('tips-retry').hidden=true;
    fitHero();announce();
  }
  const emptyData=(start,end)=>({period:{start,end,has_data:false,gross:0,expenses:0,net:0,shifts:0,days:[]},today,custom:true,has_shift:false,hours:0,worked:[],entries:[]});
  async function load(){
    if($('earnings-panel').hidden||!window.privateMoney?.state?.ready||window.privateMoney.state.lost)return;
    const token=++request;
    const [start,fullEnd]=bounds(),end=fullEnd<today?fullEnd:today;
    $('tips-error').textContent='';root.setAttribute('aria-busy','true');
    try{
      // Период целиком в будущем — запрашивать нечего: сводка за него пустая.
      const data=start>today?emptyData(start,end):await post('/api/tips_range',{start,end,...(window.privateMoney?.payload||{})});
      if(token!==request)return;
      // Операционный день мог начаться позже, чем думает телефон: пересчитываем период один раз.
      if(data.today&&data.today!==today&&!corrected){today=data.today;corrected=true;return load();}
      render(data);
    }catch(e){if(token===request){$('tips-error').textContent=e.name==='TimeoutError'?'Не удалось загрузить данные.':e.message;$('tips-retry').hidden=false;}}
    finally{if(token===request)root.setAttribute('aria-busy','false');}
  }
  $('tips-retry').onclick=load;
  window.addEventListener('private-money-ready',load);
  window.addEventListener('earnings-updated',load);
  load();
})();
