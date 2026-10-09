(() => {
  'use strict';
  const root=document.getElementById('tips-dashboard');
  const tg=window.Telegram?.WebApp;
  const $=id=>document.getElementById(id);
  const money=n=>Number(n).toLocaleString('ru-RU',{maximumFractionDigits:0});
  const date=iso=>new Date(iso+'T12:00:00').toLocaleDateString('ru-RU',{day:'numeric',month:'long'});
  const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const label=(start,end,custom,today)=>{
    if(!start||!end)return 'Сегодня';
    if(!custom&&start===today)return 'Сегодня';
    if(start===end)return date(start);
    if(start.slice(0,7)===end.slice(0,7))return 'С '+Number(start.slice(8))+' по '+date(end);
    return date(start)+' — '+date(end);
  };
  let current=null,rangeStart=null,rangeEnd=null,custom=false,request=0,detailKind=null,editing=null,pendingTip=null,busy=false;
  const loadingHero=()=>`<div class="tips-hero"><button class="tips-gross" type="button" disabled><span>Грязные ›</span><strong class="tips-placeholder" aria-hidden="true">&nbsp;</strong></button>
    <div class="tips-secondary"><div><span>Чистые</span><strong class="tips-placeholder" aria-hidden="true">&nbsp;</strong></div><button class="expense-link" type="button" disabled><span>Расходы ›</span><strong class="tips-placeholder" aria-hidden="true">&nbsp;</strong></button></div></div>`;
  root.innerHTML=`<div class="dashboard-controls"><button id="tips-period" class="period-link" aria-haspopup="dialog" aria-expanded="false" aria-controls="tips-range">Сегодня ›</button><button id="tips-privacy" class="privacy-link">Приватность ›</button></div>
    <div id="tips-range" class="range-backdrop" hidden><section class="range-picker" role="dialog" aria-modal="true" aria-labelledby="tips-range-title"><div class="range-head"><h2 id="tips-range-title">Выбрать период</h2><button id="tips-cancel" type="button" aria-label="Закрыть выбор периода">✕</button></div><label>Начало<input id="tips-start" type="date" min="2000-01-01"></label><label>Конец<input id="tips-end" type="date" min="2000-01-01"></label><p class="range-hint">Нажми на дату, чтобы открыть календарь.</p><p id="tips-range-error" role="alert"></p><button id="tips-apply" class="range-apply">Показать</button><button id="tips-home" class="range-home" type="button">Вернуться к последнему дню</button></section></div>
    <div id="tips-error" role="alert"></div><button id="tips-retry" class="text-action" hidden>Повторить</button>
    <div id="tips-result" aria-live="polite">${loadingHero()}</div>
    <div id="tips-detail" class="detail-backdrop" hidden><section class="detail-sheet" role="dialog" aria-modal="true" aria-labelledby="tips-detail-title"><div class="detail-head"><h2 id="tips-detail-title">—</h2><button id="tips-detail-close" type="button" aria-label="Закрыть">✕</button></div><p id="tips-detail-status" role="status"></p><div id="tips-detail-body"></div></section></div>`;
  const closeRange=()=>{$('tips-range').hidden=true;$('tips-period').setAttribute('aria-expanded','false');$('tips-period').focus();};
  $('tips-period').onclick=()=>{
    $('tips-start').value=rangeStart||'';$('tips-end').value=rangeEnd||'';
    $('tips-range-error').textContent='';$('tips-range').hidden=false;$('tips-period').setAttribute('aria-expanded','true');
  };
  $('tips-cancel').onclick=closeRange;
  $('tips-range').onclick=e=>{if(e.target===$('tips-range'))closeRange();};
  $('tips-range').onkeydown=e=>{if(e.key==='Escape')closeRange();};
  $('tips-privacy').onclick=()=>document.dispatchEvent(new Event('tips-open-privacy'));
  $('tips-apply').onclick=()=>{
    const start=$('tips-start').value,end=$('tips-end').value;
    if(!start||!end||start>end||end>current?.today||start<'2000-01-01'){
      $('tips-range-error').textContent='Проверь начало и конец периода.';return;
    }
    rangeStart=start;rangeEnd=end;custom=true;closeRange();load();
  };
  $('tips-home').onclick=()=>{custom=false;rangeStart=rangeEnd=null;closeRange();load();};
  const closeDetail=()=>{$('tips-detail').hidden=true;editing=null;detailKind=null;};
  $('tips-detail-close').onclick=closeDetail;
  $('tips-detail').onclick=e=>{if(e.target===$('tips-detail'))closeDetail();};
  $('tips-detail').onkeydown=e=>{if(e.key==='Escape')closeDetail();};
  const entries=kind=>(current?.entries||[]).filter(e=>kind==='tips'?e.kind==='income'&&e.category==='Чаевые':e.kind==='expense');
  $('tips-detail-body').onclick=e=>{
    const button=e.target.closest('[data-edit]');
    if(button&&!custom)showEdit(entries('tips')[Number(button.dataset.edit)]);
  };
  async function post(path,body){
    const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},cache:'no-store',signal:AbortSignal.timeout(20000),body:JSON.stringify({initData:tg?.initData||'',...body})});
    const data=await r.json();
    if(!r.ok)throw new Error(r.status===401?'Открой бота и зайди в приложение ещё раз.':data.error||'Не получилось сохранить.');
    return window.PrivateFinance.view(path,body,data);
  }
  function changed(){closeDetail();document.dispatchEvent(new Event('tips-money-changed'));}
  function showDetail(kind){
    if(!current)return;
    detailKind=kind;editing=null;
    const p=current.period,list=entries(kind),isTips=kind==='tips';
    $('tips-detail-title').textContent=(isTips?'Грязные':'Расходы')+' · '+label(p.start,p.end,custom,current.today);
    $('tips-detail-status').textContent='';
    const cash=list.filter(e=>e.account==='cash').reduce((sum,e)=>sum+Math.abs(e.amount),0);
    const card=list.filter(e=>e.account==='card').reduce((sum,e)=>sum+Math.abs(e.amount),0);
    const totals=isTips?`<div class="detail-totals"><div>Наличные <strong>${money(cash)} ₽</strong></div><div>Безналичные <strong>${money(card)} ₽</strong></div></div>`:'';
    const rows=list.length?list.map((e,i)=>`<div class="detail-row"><div><span>${esc(e.date===current.today?'Сегодня':date(e.date))} · ${isTips?(e.account==='cash'?'Наличные':'Безналичные'):esc(e.category)}</span><strong>${money(Math.abs(e.amount))} ₽</strong></div>${isTips&&!custom?`<button type="button" data-edit="${i}">Изменить ›</button>`:''}</div>`).join(''):'<p class="detail-empty">Записей за этот период нет.</p>';
    const actions=!custom?(isTips?`<form id="tips-add-form" class="detail-form"><label>Новые чаевые<input id="tips-new-amount" type="number" inputmode="decimal" step="0.01" min="0.01" max="10000000" placeholder="Сумма ₽" required></label><label>Куда поступили<select id="tips-new-account"><option value="card">Безналичные</option><option value="cash">Наличные</option></select></label><button type="submit">Добавить чаевые</button></form>`:`<button id="tips-add-spend" class="detail-primary" type="button">Записать расход за ${date(p.start)}</button>`):'';
    $('tips-detail-body').innerHTML=totals+rows+'<div id="tips-edit-form" hidden></div>'+actions;
    $('tips-detail').hidden=false;
    if(!custom&&isTips){
      $('tips-add-form').onsubmit=addTip;
    }
    if(!custom&&!isTips)$('tips-add-spend').onclick=()=>{
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
  function render(data){
    const p=data.period;current=data;custom=data.custom;
    rangeStart=p.start;rangeEnd=p.end;
    $('tips-period').textContent=label(p.start,p.end,custom,data.today)+' ›';
    $('tips-start').max=data.today;$('tips-end').max=data.today;
    const known=p.has_data||data.has_shift;
    $('tips-result').innerHTML=`<div class="tips-hero"><button id="tips-gross" class="tips-gross" type="button"><span>Грязные ›</span><strong>${known?money(p.gross):'—'}</strong></button>
      <div class="tips-secondary"><div><span>Чистые</span><strong>${known?money(p.net):'—'}</strong></div><button id="tips-expense" class="expense-link" type="button"><span>Расходы ›</span><strong>${known?money(p.expenses):'—'}</strong></button></div></div>`;
    $('tips-gross').onclick=()=>showDetail('tips');
    $('tips-expense').onclick=()=>showDetail('expenses');
    $('tips-result').hidden=false;$('tips-retry').hidden=true;
  }
  async function load(){
    if($('earnings-panel').hidden||!window.privateMoney?.state?.ready||window.privateMoney.state.lost)return;
    const token=++request;
    const periodChanged=current&&(custom!==current.custom||
      custom&&(rangeStart!==current.period.start||rangeEnd!==current.period.end));
    if(periodChanged)$('tips-result').innerHTML=loadingHero();
    $('tips-error').textContent='';root.setAttribute('aria-busy','true');
    try{
      const data=await post('/api/tips_range',{...(custom?{start:rangeStart,end:rangeEnd}:{}),...(window.privateMoney?.payload||{})});
      if(token===request)render(data);
    }catch(e){if(token===request){$('tips-error').textContent=e.name==='TimeoutError'?'Не удалось загрузить данные.':e.message;$('tips-retry').hidden=false;}}
    finally{if(token===request)root.setAttribute('aria-busy','false');}
  }
  $('tips-retry').onclick=load;
  window.addEventListener('private-money-ready',load);
  window.addEventListener('earnings-updated',load);
  load();
})();
