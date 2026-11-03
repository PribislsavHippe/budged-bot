(() => {
  'use strict';
  const $=id=>document.getElementById(id), panel=$('restaurant-panel'), tg=window.Telegram?.WebApp;
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=n=>n==null?'—':Number(n).toLocaleString('ru-RU',{maximumFractionDigits:2});
  const date=s=>s?new Date(s+'T12:00:00').toLocaleDateString('ru-RU',{day:'numeric',month:'short'}):'Нет отчёта';
  const names={wine:'Вино',cocktails:'Коктейли',desserts:'Десерты',turnover:'Оборот',postcards:'Открытки',dvd:'DVD'};
  let restaurant=null,selectedId=null,restaurants=[],canCreate=false, status='approved',page=0,busy=false,month=new Date().toLocaleDateString('sv-SE',{timeZone:'Europe/Moscow'}).slice(0,7);
  let lastData=null,picked=null,schedulePlaces=[],siteUrl=null,section='team';
  async function api(action,body={}){
    const r=await fetch('/api/restaurant/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({restaurant_id:selectedId,...body,initData:tg?.initData||''}),signal:AbortSignal.timeout(20000)});
    let data;try{data=await r.json();}catch(_){const e=new Error('Сервер не ответил. Попробуй ещё раз.');e.status=r.status;throw e;}if(!r.ok){const e=new Error(r.status===401?'Открой бота и зайди в приложение ещё раз.':data.error||'Кабинет пока недоступен.');e.status=r.status;throw e;}return data;
  }
  // Та же вкладка живёт в мини-приложении и на отдельной странице для компьютера.
  const standalone=document.body.classList.contains('restaurant-page');
  // Без подписи мини-приложения это сайт в браузере: вход кнопкой Telegram, сессия в cookie.
  const site=standalone&&!tg?.initData;
  const wide=matchMedia('(min-width: 900px)');
  const parts={
    month:'<input type="month" id="restaurant-month" aria-label="Месяц продаж">',
    select:'<select id="restaurant-select" aria-label="Ресторан" hidden></select>',
    tools:'<button id="restaurant-add" hidden>+ Добавить ресторан</button><button id="restaurant-site" hidden>Сайт ресторана</button><button id="restaurant-logout" hidden>Выйти</button>',
    sections:'<nav class="restaurant-sections" id="restaurant-sections" hidden aria-label="Разделы кабинета"><button data-section="team" aria-pressed="true">Сотрудники</button><button data-section="schedule" aria-pressed="false">График</button></nav>',
    status:'<section id="restaurant-login" hidden><h2>Кабинет ресторана</h2><div id="restaurant-login-box"></div></section><p id="restaurant-error" role="alert"></p><p id="restaurant-notice" role="status"></p><button id="restaurant-retry" class="btn btn-ghost" hidden>Попробовать ещё раз</button><form id="restaurant-create" hidden><label>Название ресторана<input id="restaurant-name" maxlength="80" minlength="2" required autocomplete="organization"></label><button class="btn">Создать ресторан</button><button type="button" id="restaurant-create-cancel">Отмена</button></form>',
    filters:'<div class="restaurant-filters" aria-label="Сотрудники"><button data-status="approved" aria-pressed="true">Сотрудники</button><button data-status="pending" aria-pressed="false">Заявки</button><button data-status="rejected" aria-pressed="false">Отклонённые</button><button data-status="revoked" aria-pressed="false">Без доступа</button></div>',
    rows:'<div id="restaurant-rows"></div><div class="restaurant-pages"><button id="restaurant-prev">← Назад</button><span id="restaurant-page"></span><button id="restaurant-next">Далее →</button></div>',
    invite:'<details class="restaurant-invite"><summary>Пригласить сотрудника</summary><button id="restaurant-invite" class="btn btn-ghost">Создать ссылку</button><div id="restaurant-link" hidden><label>Приглашение на 7 дней<input readonly id="restaurant-url"></label><button id="restaurant-copy">Скопировать</button></div></details>',
  };
  // На сайте — шапка с рестораном и разделами-заголовками, как вкладки мини-приложения;
  // в мини-приложении — прежняя вкладка без изменений.
  panel.innerHTML=standalone?`<header class="site-bar"><div class="site-place"><strong id="restaurant-title">Ресторан</strong>${parts.select}</div><div class="site-tools">${parts.tools}</div></header>
    ${parts.sections}${parts.status}
    <div id="restaurant-content" hidden><div class="site-toolbar">${parts.filters}<div class="site-toolbar-end">${parts.month}${parts.invite}</div></div>
      ${parts.rows}</div>
    <section id="restaurant-schedule" hidden></section>`
  :`<header class="restaurant-head"><h1 id="restaurant-title">Ресторан</h1>${parts.month}</header>
    <div class="restaurant-switch">${parts.select}${parts.tools}</div>${parts.sections}${parts.status}
    <div id="restaurant-content" hidden>${parts.filters}<p class="restaurant-scope">Планы и продажи после присоединения к ресторану</p>${parts.rows}${parts.invite}</div>
    <section id="restaurant-schedule" hidden></section>`;
  $('restaurant-month').value=month;
  function lock(){panel.querySelectorAll('button,input,select').forEach(el=>el.disabled=busy);}
  function member(row){
    const states={pending:'Ждёт подтверждения',rejected:'Заявка отклонена',revoked:'Доступ закрыт'};
    const sales=row.sales;const m=sales?.metrics,cutoffs=sales?.report_dates||[];
    // Роли: управляющего назначает владелец, менеджеров — владелец и управляющий.
    // Менеджер не трогает руководителей; переносить между ресторанами может только владелец.
    const me=restaurant?.role||'owner',role=row.role||'staff';
    const canTransfer=me==='owner'&&row.status==='approved'&&restaurants.filter(r=>r.role==='owner').some(r=>r.id!==selectedId);
    const roleButton=(to,label)=>`<button data-action="role" data-id="${esc(row.id)}" data-role="${to}">${label}</button>`;
    const roleControls=!('role' in row)||row.status!=='approved'?'':me==='owner'
      ?(role!=='director'?roleButton('director','Сделать управляющим'):'')+(role==='manager'?roleButton('staff','Снять роль менеджера'):role==='director'?roleButton('manager','Сделать менеджером'):roleButton('manager','Сделать менеджером'))
      :me==='director'&&role!=='director'?(role==='manager'?roleButton('staff','Снять роль менеджера'):roleButton('manager','Сделать менеджером')):'';
    const canRevoke=role==='staff'||me==='owner'||(me==='director'&&role==='manager');
    const controls=row.status==='pending'?`<button data-action="approve" data-id="${esc(row.id)}">Подтвердить</button><button data-action="reject" data-id="${esc(row.id)}">Отклонить</button>`:row.status==='approved'?`${roleControls}${canRevoke?`<button data-action="revoke" data-id="${esc(row.id)}">Отозвать доступ</button>`:''}${canTransfer?'<button data-action="transfer-open">Перенести</button>':''}`:'';
    const transfer=canTransfer?`<div class="restaurant-transfer" hidden><label>В какой ресторан<select aria-label="Новый ресторан для ${esc(row.report_name)}">${restaurants.filter(r=>r.id!==selectedId).map(r=>`<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('')}</select></label><p>После переноса прежний ресторан перестанет видеть сотрудника. Новый увидит планы и продажи, записанные после переноса; прежние личные записи останутся у сотрудника.</p><div class="restaurant-actions"><button data-action="transfer-confirm" data-id="${esc(row.id)}">Перенести сотрудника</button><button data-action="transfer-cancel">Отмена</button></div></div>`:'';
    const label={manager:'менеджер',director:'управляющий'}[row.role]||'';
    const note=[label,sales?'Отчёт: '+date(cutoffs[0]):states[row.status]].filter(Boolean).join(' · ');
    const head=`<span><strong>${esc(row.report_name)}</strong>${note?`<small>${note}</small>`:''}</span>${m?`<span class="restaurant-turnover">${m.turnover.estimated?'≈ ':''}${num(m.turnover.total)}${m.turnover.total==null?'':' ₽'}<small>оборот</small></span>`:''}`;
    const body=`<p class="restaurant-id">Telegram ID: ${esc(row.user_id)}</p>${m?`<table><thead><tr><th>Показатель</th><th>Сейчас</th><th>План</th></tr></thead><tbody>${Object.entries(names).filter(([k])=>!(sales.category_metrics||[]).some(c=>c.legacy_metric===k)).map(([k,label])=>`<tr><th>${label}, ${['cocktails','postcards','dvd'].includes(k)?'шт.':'₽'}${m[k].cutoff&&m[k].cutoff!==cutoffs[0]?`<small>Отчёт по ${date(m[k].cutoff)}</small>`:''}</th><td>${m[k].estimated?'≈ ':''}${num(m[k].total)}</td><td>${num(m[k].target)}</td></tr>`).join('')}${(sales.category_metrics||[]).map(c=>`<tr><th>${esc(c.name)}, ${c.unit==='count'?'шт.':c.unit==='amount'?'₽':''}${c.cutoff?`<small>Отчёт по ${date(c.cutoff)}</small>`:''}</th><td>${num(c.total)}</td><td>${num(c.target)}</td></tr>`).join('')}</tbody></table>`:''}`;
    return {head,body,controls,transfer};
  }
  // На компьютере список слева, выбранный сотрудник справа; на телефоне прежние раскрывающиеся карточки.
  function rowsHtml(rows){
    if(standalone&&wide.matches){
      if(!rows.some(row=>row.id===picked))picked=rows[0].id;
      const parts=member(rows.find(row=>row.id===picked));
      return `<div class="restaurant-desk"><div class="restaurant-list">${rows.map(row=>`<button class="restaurant-pick" data-pick="${esc(row.id)}" aria-pressed="${row.id===picked}">${member(row).head}</button>`).join('')}</div>
        <article class="restaurant-member restaurant-detail"><div class="restaurant-detail-head">${parts.head}</div>${parts.body}<div class="restaurant-actions">${parts.controls}</div>${parts.transfer}</article></div>`;
    }
    return rows.map(row=>{const parts=member(row);
      return `<article class="restaurant-member"><details><summary>${parts.head}</summary>${parts.body}<div class="restaurant-actions">${row.status==='pending'?'':parts.controls}</div>${parts.transfer}</details>${row.status==='pending'?`<div class="restaurant-actions">${parts.controls}</div>`:''}</article>`;
    }).join('');
  }
  function render(data){
    lastData=data;
    restaurant=data.restaurant;$('restaurant-title').textContent=restaurant.name;
    // Месяц продаж нужен только тем, кто видит продажи: владельцу и управляющему.
    $('restaurant-month').hidden=!['owner','director'].includes(restaurant.role||'owner');
    $('restaurant-create').hidden=true;$('restaurant-content').hidden=false;
    const labels={approved:'Сотрудники',pending:'Заявки',rejected:'Отклонённые',revoked:'Без доступа'};
    panel.querySelectorAll('[data-status]').forEach(el=>{el.hidden=['rejected','revoked'].includes(el.dataset.status)&&!data.counts[el.dataset.status]&&status!==el.dataset.status;el.textContent=labels[el.dataset.status]+' '+data.counts[el.dataset.status];el.setAttribute('aria-pressed',String(el.dataset.status===status));});
    $('restaurant-rows').hidden=false;
    $('restaurant-rows').innerHTML=data.rows.length?rowsHtml(data.rows):`<p class="restaurant-empty">${status==='pending'?'Новых заявок нет.':status==='approved'?'Здесь появятся сотрудники, которых ты подтвердил.':'Список пока пуст.'}</p>`;
    $('restaurant-prev').hidden=page===0;$('restaurant-next').hidden=!data.more;
    $('restaurant-page').textContent=page||data.more?'Страница '+(page+1):'';
  }
  async function load(){
    if(busy)return;busy=true;lock();$('restaurant-error').textContent='';$('restaurant-retry').hidden=true;$('restaurant-rows').hidden=true;
    try{const access=await api('access');$('restaurant-login').hidden=true;$('restaurant-month').hidden=false;$('restaurant-logout').hidden=!site;
      if(!access.available)throw new Error('Кабинет доступен администратору ресторана.');
      restaurants=access.restaurants||[];canCreate=!!access.can_create;schedulePlaces=access.schedule||[];siteUrl=access.site_url||null;
      $('restaurant-site').hidden=standalone||!siteUrl;
      // На сайте график — второй раздел кабинета; у менеджера графика он единственный.
      $('restaurant-sections').hidden=!standalone||!schedulePlaces.length;
      panel.querySelector('[data-section=team]').hidden=!restaurants.length;
      if(standalone&&schedulePlaces.length&&(section==='schedule'||!restaurants.length)){showSchedule();return;}
      if(!restaurants.length&&schedulePlaces.length&&!canCreate){$('restaurant-title').textContent='График';$('restaurant-content').hidden=true;$('restaurant-create').hidden=true;
        $('restaurant-notice').textContent='График — на сайте ресторана.';return;}
      if(canCreate)window.showResearchTab?.();
      if(!restaurants.some(r=>r.id===selectedId))selectedId=restaurants[0]?.id||null;
      restaurant=restaurants.find(r=>r.id===selectedId)||null;
      $('restaurant-add').hidden=!canCreate||!restaurant;
      $('restaurant-select').hidden=restaurants.length<2;
      $('restaurant-select').innerHTML=restaurants.map(r=>`<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('');
      $('restaurant-select').value=selectedId||'';
      if(!restaurant){$('restaurant-create').hidden=!canCreate;$('restaurant-create-cancel').hidden=true;$('restaurant-content').hidden=true;return;}
      $('restaurant-create-cancel').hidden=false;$('restaurant-schedule').hidden=true;$('restaurant-month').hidden=false;
      render(await api('view',{month,page,status}));
    }catch(e){if(site&&e.status===401){showLogin();return;}
      if(!e.status)window.uxEvent?.('cabinet_load_error','restaurant',e.name==='TimeoutError'?'timeout':'network');$('restaurant-error').textContent=e.name==='TimeoutError'?'Ответ задерживается. Попробуй ещё раз.':e.message;$('restaurant-retry').hidden=false;}
    finally{busy=false;lock();}
  }
  function showSchedule(){
    section='schedule';
    panel.querySelectorAll('[data-section]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.section==='schedule')));
    $('restaurant-title').textContent=(restaurant||schedulePlaces[0])?.name||'График смен';$('restaurant-content').hidden=true;$('restaurant-create').hidden=true;
    $('restaurant-month').hidden=true;$('restaurant-select').hidden=true;$('restaurant-add').hidden=true;$('restaurant-schedule').hidden=false;
    window.RosterEditor?.open($('restaurant-schedule'),{places:schedulePlaces,initData:tg?.initData||'',onAuth:()=>{if(site)showLogin();}});
  }
  function showTeam(){
    section='team';$('restaurant-schedule').hidden=true;
    panel.querySelectorAll('[data-section]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.section==='team')));
    load();
  }
  function showLogin(){
    $('restaurant-title').textContent='Ресторан';$('restaurant-content').hidden=true;$('restaurant-create').hidden=true;
    $('restaurant-select').hidden=true;$('restaurant-add').hidden=true;$('restaurant-logout').hidden=true;$('restaurant-login').hidden=false;$('restaurant-month').hidden=true;
    $('restaurant-sections').hidden=true;$('restaurant-schedule').hidden=true;
    if(!loginWait)startLogin();
  }
  // Вход через бота: ссылка открывает Telegram на компьютере, QR-код — на телефоне; сайт ждёт подтверждения.
  let loginWait=null;
  const post=(path,body)=>fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(20000)});
  function loginAgain(text){
    clearTimeout(loginWait);loginWait=null;
    $('restaurant-login-box').innerHTML=`<p>${esc(text)}</p><button type="button" class="btn btn-ghost" id="restaurant-login-again">Получить новый код</button>`;
    $('restaurant-login-again').onclick=startLogin;
  }
  async function startLogin(){
    clearTimeout(loginWait);loginWait=-1;$('restaurant-error').textContent='';
    let data;
    try{const r=await post('/api/site/login/start',{});data=await r.json();if(!r.ok)throw new Error(data.error||'');}
    catch(e){loginWait=null;$('restaurant-error').textContent=e.message||'Вход сейчас недоступен. Попробуй через минуту.';
      $('restaurant-login-box').innerHTML='<button type="button" class="btn btn-ghost" id="restaurant-login-again">Получить новый код</button>';
      $('restaurant-login-again').onclick=startLogin;return;}
    $('restaurant-login-box').innerHTML=`<div class="restaurant-login-row"><div><a class="btn" href="${esc(data.link)}" target="_blank" rel="noopener">Войти через Telegram</a>
      <p class="restaurant-login-code">Откроется Telegram. Проверь, что код в боте совпадает: <b>${esc(data.code)}</b></p></div>
      <div class="restaurant-login-qr"><div id="restaurant-qr" aria-hidden="true"></div><p>Или наведи камеру телефона</p></div></div>`;
    drawQr(data.link);
    const until=Date.now()+data.expires_in*1000;
    const tick=async()=>{
      if(Date.now()>until)return loginAgain('Время вышло.');
      try{const state=(await (await post('/api/site/login/poll',{poll:data.poll})).json()).state;
        if(state==='approved'){loginWait=null;load();return;}
        if(state==='rejected')return loginAgain('Вход отменён в Telegram.');
        if(state==='expired')return loginAgain('Время вышло.');
      }catch(_){}
      loginWait=setTimeout(tick,2000);
    };
    loginWait=setTimeout(tick,2000);
  }
  function drawQr(link){
    const draw=()=>{try{$('restaurant-qr').innerHTML='';new window.QRCode($('restaurant-qr'),{text:link,width:148,height:148,colorDark:'#172126',colorLight:'#ffffff'});}catch(_){}};
    if(window.QRCode)return draw();
    const script=document.createElement('script');
    script.src='https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js';script.onload=draw;
    document.head.appendChild(script);
  }
  $('restaurant-logout').onclick=async()=>{
    try{await fetch('/api/site/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',signal:AbortSignal.timeout(20000)});}catch(_){}
    location.reload();
  };
  async function change(action,body){
    if(busy)return;
    if(action==='revoke'&&!confirm('Отозвать доступ сотрудника к ресторану? Его личные записи останутся.'))return;
    if(action==='invite'&&!confirm('Создать приглашение на 7 дней? Предыдущая ссылка перестанет работать.'))return;
    busy=true;lock();$('restaurant-error').textContent='';let failure='';
    try{const data=await api(action,body);
      if(action==='create'){selectedId=data.restaurant.id;page=0;status='approved';$('restaurant-name').value='';$('restaurant-link').hidden=true;}
      if(action==='invite'){$('restaurant-url').value=data.url;$('restaurant-link').hidden=false;}
      else{
        if(action==='transfer'){selectedId=data.restaurant.id;page=0;status='approved';}
        $('restaurant-notice').textContent=action==='transfer'?'Сотрудник перенесён в «'+data.restaurant.name+'».':action==='approve'?'Сотрудник подтверждён.':action==='role'?'Готово.':action==='create'?'Кабинет готов.':'Готово.';
      }
    }catch(e){if(site&&e.status===401){busy=false;lock();showLogin();return;}
      failure=e.name==='TimeoutError'?'Ответ задерживается. Обнови список, прежде чем повторять действие.':e.message;$('restaurant-error').textContent=failure;$('restaurant-retry').hidden=false;}
    finally{busy=false;lock();}
    if(action!=='invite'&&!failure)await load();
    if(failure){$('restaurant-error').textContent=failure;$('restaurant-retry').hidden=false;}
  }
  panel.addEventListener('click',e=>{const b=e.target.closest('button');if(!b||busy)return;
    if(b.dataset.pick){picked=b.dataset.pick;render(lastData);return;}
    if(b.dataset.section){if(b.dataset.section==='schedule')showSchedule();else showTeam();return;}
    if(b.dataset.action==='role'){
      if(b.dataset.role==='director'&&!confirm('Назначить управляющим? Он увидит продажи каждого и сможет ставить план.'))return;
      change('role',{id:b.dataset.id,role:b.dataset.role});return;}
    if(b.dataset.status){status=b.dataset.status;page=0;load();}
    if(b.dataset.action==='transfer-open'){b.closest('.restaurant-member').querySelector('.restaurant-transfer').hidden=false;return;}
    if(b.dataset.action==='transfer-cancel'){b.closest('.restaurant-transfer').hidden=true;return;}
    if(b.dataset.action==='transfer-confirm'){
      const destination=b.closest('.restaurant-transfer').querySelector('select').value;
      change('transfer',{id:b.dataset.id,target_restaurant_id:destination});return;
    }
    if(b.dataset.action)change(b.dataset.action,{id:b.dataset.id});
  });
  wide.addEventListener('change',()=>{if(lastData&&!busy)render(lastData);});
  $('restaurant-site').onclick=()=>{try{tg?.openLink?tg.openLink(siteUrl):window.open(siteUrl,'_blank','noopener');}catch(_){location.href=siteUrl;}};
  if(standalone&&location.hash==='#schedule')section='schedule';
  $('restaurant-add').onclick=()=>{$('restaurant-create').hidden=false;$('restaurant-name').focus();};
  $('restaurant-create-cancel').onclick=()=>{$('restaurant-create').hidden=true;};
  $('restaurant-select').onchange=()=>{selectedId=$('restaurant-select').value;page=0;status='approved';$('restaurant-link').hidden=true;$('restaurant-url').value='';$('restaurant-notice').textContent='';load();};
  $('restaurant-month').onchange=()=>{month=$('restaurant-month').value;page=0;load();};
  $('restaurant-prev').onclick=()=>{page=Math.max(0,page-1);load();};$('restaurant-next').onclick=()=>{page++;load();};
  $('restaurant-retry').onclick=load;$('restaurant-invite').onclick=()=>change('invite',{});
  $('restaurant-copy').onclick=async()=>{try{await navigator.clipboard.writeText($('restaurant-url').value);$('restaurant-notice').textContent='Ссылка скопирована.';}catch(_){$('restaurant-url').select();$('restaurant-notice').textContent='Выделил ссылку — её можно скопировать.';}};
  $('restaurant-create').onsubmit=e=>{e.preventDefault();change('create',{name:$('restaurant-name').value});};
  if(standalone){
    if(site){load();return;}
    const BACKGROUND='#f3f2ee';
    try{tg?.ready();tg?.expand();if(tg?.isVersionAtLeast?.('6.1')){tg.setHeaderColor(BACKGROUND);tg.setBackgroundColor(BACKGROUND);}}catch(_){}
    if(tg?.BackButton&&tg.isVersionAtLeast?.('6.1')){tg.BackButton.show();tg.BackButton.onClick(()=>{location.href='/app'+location.hash;});}
    load();return;
  }
  $('tab-restaurant').onclick=()=>{window.selectTab('restaurant');window.uxEvent?.('cabinet_opened','restaurant');load();};
  const showTabs=()=>{$('tab-restaurant').hidden=false;};
  api('access').then(data=>{if(data.restaurants?.length||data.can_create)showTabs();if(data.can_create)window.showResearchTab?.();}).catch(e=>{if(e.status!==403&&e.status!==404&&e.status!==401)showTabs();});
})();
