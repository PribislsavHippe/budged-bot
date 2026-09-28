(() => {
  'use strict';
  const $=id=>document.getElementById(id), tg=window.Telegram?.WebApp;
  const labels={wine:'Вино',cocktails:'Коктейли',desserts:'Десерты',turnover:'Товарооборот',postcards:'Открытки',dvd:'ДВД'};
  const kinds={glass:'Бокалы',bottle:'Бутылки',cocktails:'Коктейли',desserts:'Десерты',turnover:'Товарооборот',postcards:'Открытки',dvd:'ДВД'};
  const counts=new Set(['cocktails','postcards','dvd']);
  const num=n=>Number(n).toLocaleString('ru-RU',{maximumFractionDigits:2});
  const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const val=(k,n)=>num(n)+(counts.has(k)?' шт.':' ₽');
  const fields=(prefix,keys)=>keys.map(k=>`<label>${labels[k]} · ${counts.has(k)?'шт.':'₽'}<input id="${prefix}-${k}" inputmode="decimal" placeholder="Не указано"></label>`).join('');
  let data=null,busy=false,pending=null,editing=null;
  const storage='sales-correction-'+(tg?.initDataUnsafe?.user?.id||'local');
  try {pending=JSON.parse(sessionStorage.getItem(storage)||'null');} catch(_){}
  function persist(){try{pending?sessionStorage.setItem(storage,JSON.stringify(pending)):sessionStorage.removeItem(storage);}catch(_){}}
  $('sales-panel').innerHTML=`<div class="sales-toolbar"><h1>План месяца</h1><input id="sales-month" aria-label="Месяц плана" type="month" min="2000-01" max="2100-12"></div>
    <div id="sales-error" role="alert"></div><p id="sales-status" role="status"></p>
    <button class="btn btn-ghost" id="sales-reload" hidden>Загрузить ещё раз</button><button class="btn" id="sales-retry" hidden>Сохранить ещё раз</button>
    <div id="sales-content" hidden><p id="sales-freshness" class="sales-muted"></p><div id="sales-metrics"></div><div id="sales-counts" class="sales-counts"></div>
    <details class="sales-form" id="sales-settings"><summary>Исправить план и цену бокала</summary><form id="sales-settings-form">${fields('target',['wine','cocktails','desserts','turnover'])}<label>Оценка цены бокала · ₽<input id="glass-price" inputmode="decimal" required></label><button class="btn" data-change>Сохранить план</button></form></details>
    <div id="sales-reports"></div>
    <section class="sales-form" id="sales-report" hidden><h2>Исправить отчет</h2><form id="sales-report-form"><label>По какой день отчёт<input id="report-cutoff" type="date" readonly></label>${fields('report',Object.keys(labels))}<label><input id="report-complete" type="checkbox"> Я записал все продажи за этот период</label><button class="btn" data-change>Сохранить отчет</button></form></section>
    <section class="sales-form" id="sales-edit" hidden><h2 id="sales-edit-title">Исправить запись</h2><form id="sales-edit-form"><label>Сумма или количество<input id="sales-edit-value" inputmode="decimal" required></label><label>Дата смены<input id="sales-edit-date" type="date" required></label><button class="btn" data-change>Сохранить запись</button></form></section>
    <details class="sales-form"><summary>История продаж</summary><div id="sales-history"></div></details></div>`;
  function lock(){document.querySelectorAll('#sales-panel [data-change]').forEach(b=>b.disabled=busy||!!pending);$('sales-month').disabled=busy||!!pending;$('sales-retry').hidden=!pending;$('sales-retry').disabled=busy;}
  async function api(action,body){const r=await fetch('/api/sales/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,initData:tg?.initData||''}),signal:AbortSignal.timeout(20000)});const j=await r.json();if(!r.ok){const e=new Error(r.status===401?'Открой бота и зайди в приложение ещё раз.':j.error);e.status=r.status;throw e;}return j;}
  function render(s){
    const openMetrics=data?.month===s.month?[...document.querySelectorAll('.sales-goal[open]')].map(el=>el.dataset.metric):[];
    data=s;$('sales-month').value=s.month;$('sales-content').hidden=false;
    const shortDate=d=>new Date(d+'T12:00:00').toLocaleDateString('ru-RU',{day:'numeric',month:'short'});
    const cutoffs=Object.values(s.metrics).map(v=>v.cutoff).filter(Boolean).sort();
    $('sales-freshness').textContent=cutoffs.length?'Отчёт по '+shortDate(cutoffs.at(-1)):'Отчёта пока нет';
    function metric(k,v){
      const target=v.target,denom=target||1;
      let left=100;const widths=[v.official||0,v.recorded,v.estimated].map(n=>{const w=Math.max(0,Math.min(left,n/denom*100));left-=w;return w;});
      const amount=v.total===null?'Нет данных':(v.estimated?'≈ ':'')+val(k,v.total);
      const progress=v.pct===null?'—':Number(v.pct).toLocaleString('ru-RU',{maximumFractionDigits:1})+'%';
      const status=target?(v.remaining===null?'Нет записей':v.remaining===0?'План выполнен':'Осталось '+val(k,v.remaining)):'План не задан';
      const provenance=[v.official!==null?'По отчёту на '+shortDate(v.cutoff)+': '+val(k,v.official):'',v.recorded?(v.cutoff?'После отчёта: ':'Записано: ')+val(k,v.recorded):'',v.estimated?'Бокалы: оценка '+val(k,v.estimated)+' по '+num(s.glass_price)+' ₽':''].filter(Boolean).join('<br>')||'Записей за месяц пока нет.';
      return `<details class="sales-goal" data-metric="${k}" ${openMetrics.includes(k)?'open':''}><summary aria-label="${labels[k]}: ${esc(amount)}${target?'; план '+val(k,target)+'; выполнено '+progress:''}. ${status}. Показать расчёт"><div class="sales-goal-top"><span class="sales-goal-name">${labels[k]}</span><span class="sales-goal-percent">${target?progress:''}</span></div><div class="sales-goal-amount"><strong>${amount}</strong>${target?`<span> из ${val(k,target)}</span>`:''}</div>${target?`<div class="sales-track" aria-hidden="true"><i style="width:${widths[0]}%"></i><i class="recorded" style="width:${widths[1]}%"></i><i class="estimated" style="width:${widths[2]}%"></i></div>`:''}<span class="sales-expand" aria-hidden="true">⌄</span></summary><div class="sales-goal-detail"><strong>${status}</strong><div>${provenance}</div></div></details>`;
    }
    $('sales-metrics').innerHTML=['wine','cocktails','desserts','turnover'].map(k=>metric(k,s.metrics[k])).join('');
    $('sales-counts').innerHTML=['postcards','dvd'].map(k=>{const v=s.metrics[k];return `<div><span>${labels[k]}</span><strong>${v.total===null?'—':num(v.total)} <small>шт.</small></strong></div>`;}).join('');
    $('sales-settings').hidden=!s.has_settings;
    if(!$('sales-settings').open){for(const k of ['wine','cocktails','desserts','turnover'])$('target-'+k).value=s.targets[k]??'';$('glass-price').value=s.glass_price;}
    $('sales-reports').innerHTML='<details class="sales-form"><summary>Официальные отчеты</summary>'+ (s.reports.length?s.reports.map((r,i)=>`<div class="sales-record"><span>По ${esc(r.cutoff)}</span><button data-report="${i}" data-change>Исправить отчет</button></div>`).join(''):'<p class="sales-muted">Нет отчётов</p>')+'</details>';
    $('sales-history').innerHTML=s.events.length?s.events.map(e=>`<div class="sales-record"><div>${kinds[e.kind]} · ${num(e.value)}<p class="sales-muted">${esc(e.work_date)}${e.voided?' · отменено':''}</p></div>${e.voided?'':`<button data-edit="${esc(e.id)}" data-change>Исправить</button><button data-undo="${esc(e.id)}" data-change>Отменить</button>`}</div>`).join(''):'<p class="sales-muted">Нет записей</p>';lock();
  }
  async function load(){if(busy)return;busy=true;lock();$('sales-error').textContent='';try{render(await api('view',{month:$('sales-month').value||undefined}));$('sales-reload').hidden=true;}catch(e){$('sales-error').textContent=e.message;$('sales-reload').hidden=false;}finally{busy=false;lock();}}
  async function mutate(action,body){if(busy)return;if(!pending){pending={action,body:{month:data.month,...body}};persist();}busy=true;lock();$('sales-error').textContent='';try{render(await api(pending.action,pending.body));pending=null;persist();$('sales-status').textContent='Изменения сохранены';$('sales-report').hidden=true;$('sales-edit').hidden=true;$('sales-settings').open=false;}catch(e){$('sales-error').textContent=e.message;if(e.status>=400&&e.status<500&&e.status!==401){pending=null;persist();}}finally{busy=false;lock();}}
  const values=(prefix,keys)=>Object.fromEntries(keys.filter(k=>$(prefix+'-'+k).value.trim()!=='').map(k=>[k,$(prefix+'-'+k).value]));
  $('sales-panel').addEventListener('click',e=>{const b=e.target.closest('button');if(!b||busy||pending)return;
    if(b.dataset.undo)mutate('undo',{operation_id:b.dataset.undo});
    if(b.dataset.edit){editing=data.events.find(x=>x.id===b.dataset.edit);$('sales-edit-title').textContent=kinds[editing.kind];$('sales-edit-value').value=editing.value;$('sales-edit-date').value=editing.work_date;$('sales-edit-date').max=data.today;$('sales-edit').hidden=false;$('sales-edit').scrollIntoView({behavior:'smooth'});}
    if(b.dataset.report!==undefined){const r=data.reports[Number(b.dataset.report)];$('report-cutoff').value=r.cutoff;for(const k of Object.keys(labels))$('report-'+k).value=r.totals[k]??'';$('report-complete').checked=r.records_complete;$('sales-report').hidden=false;$('sales-report').scrollIntoView({behavior:'smooth'});}
  });
  $('sales-edit-form').onsubmit=e=>{e.preventDefault();if(!pending)mutate('edit',{operation_id:editing.id,value:$('sales-edit-value').value,work_date:$('sales-edit-date').value,month:$('sales-edit-date').value.slice(0,7)});};
  $('sales-settings-form').onsubmit=e=>{e.preventDefault();if(!pending)mutate('settings',{targets:values('target',['wine','cocktails','desserts','turnover']),glass_price:$('glass-price').value});};
  $('sales-report-form').onsubmit=e=>{e.preventDefault();if(!pending)mutate('report',{cutoff:$('report-cutoff').value,totals:values('report',Object.keys(labels)),records_complete:$('report-complete').checked,operation_id:crypto.randomUUID()});};
  $('sales-month').onchange=()=>{$('sales-content').hidden=true;$('sales-edit').hidden=true;$('sales-report').hidden=true;$('sales-settings').open=false;$('sales-settings-form').reset();load();};
  $('sales-reload').onclick=load;$('sales-retry').onclick=()=>{if(pending)mutate(pending.action,pending.body);};
  for(const tab of document.querySelectorAll('#tab-sales,#tab-earnings'))tab.onclick=()=>{document.getElementById('research-panel').hidden=true;document.getElementById('tab-research').setAttribute('aria-selected','false');document.getElementById('restaurant-panel').hidden=true;document.getElementById('tab-restaurant').setAttribute('aria-selected','false');const plan=tab.id==='tab-sales';$('sales-panel').hidden=!plan;$('earnings-panel').hidden=plan;$('tab-sales').setAttribute('aria-selected',String(plan));$('tab-earnings').setAttribute('aria-selected',String(!plan));if(plan)load();else window.dispatchEvent(new Event('earnings-updated'));};
  if(pending?.body?.month)$('sales-month').value=pending.body.month;lock();
})();
