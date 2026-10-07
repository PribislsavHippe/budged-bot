(() => {
  'use strict';
  const $=id=>document.getElementById(id), panel=$('restaurant-panel'), tg=window.Telegram?.WebApp;
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=n=>n==null?'—':Number(n).toLocaleString('ru-RU',{maximumFractionDigits:2});
  const date=s=>s?new Date(s+'T12:00:00').toLocaleDateString('ru-RU',{day:'numeric',month:'short'}):'Нет отчёта';
  const names={wine:'Вино',cocktails:'Коктейли',desserts:'Десерты',turnover:'Оборот',postcards:'Открытки',dvd:'DVD'};
  let restaurant=null,selectedId=null,restaurants=[],canCreate=false, status='approved',page=0,busy=false,month=new Date().toLocaleDateString('sv-SE',{timeZone:'Europe/Moscow'}).slice(0,7);
  panel.innerHTML=`<header class="restaurant-head"><h1 id="restaurant-title">Ресторан</h1><input type="month" id="restaurant-month" aria-label="Месяц продаж"></header>
    <div class="restaurant-switch"><select id="restaurant-select" aria-label="Ресторан" hidden></select><button id="restaurant-add" hidden>+ Добавить ресторан</button></div>
    <p id="restaurant-error" role="alert"></p><p id="restaurant-notice" role="status"></p>
    <button id="restaurant-retry" class="btn btn-ghost" hidden>Попробовать ещё раз</button>
    <form id="restaurant-create" hidden><label>Название ресторана<input id="restaurant-name" maxlength="80" minlength="2" required autocomplete="organization"></label><button class="btn">Создать ресторан</button><button type="button" id="restaurant-create-cancel">Отмена</button></form>
    <div id="restaurant-content" hidden><div class="restaurant-filters" aria-label="Сотрудники">
      <button data-status="approved" aria-pressed="true">Сотрудники</button><button data-status="pending" aria-pressed="false">Заявки</button>
      <button data-status="rejected" aria-pressed="false">Отклонённые</button><button data-status="revoked" aria-pressed="false">Без доступа</button></div>
    <p class="restaurant-scope">Планы и продажи после присоединения к ресторану</p>
    <div id="restaurant-rows"></div><div class="restaurant-pages"><button id="restaurant-prev">← Назад</button><span id="restaurant-page"></span><button id="restaurant-next">Далее →</button></div>
    <details class="restaurant-invite"><summary>Пригласить сотрудника</summary><button id="restaurant-invite" class="btn btn-ghost">Создать ссылку</button><div id="restaurant-link" hidden><label>Приглашение на 7 дней<input readonly id="restaurant-url"></label><button id="restaurant-copy">Скопировать</button></div></details></div>`;
  $('restaurant-month').value=month;
  async function api(action,body={}){
    const r=await fetch('/api/restaurant/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({restaurant_id:selectedId,...body,initData:tg?.initData||''}),signal:AbortSignal.timeout(20000)});
    let data;try{data=await r.json();}catch(_){const e=new Error('Сервер не ответил. Попробуй ещё раз.');e.status=r.status;throw e;}if(!r.ok){const e=new Error(r.status===401?'Открой бота и зайди в приложение ещё раз.':data.error||'Кабинет пока недоступен.');e.status=r.status;throw e;}return data;
  }
  function lock(){panel.querySelectorAll('button,input,select').forEach(el=>el.disabled=busy);}
  function render(data){
    restaurant=data.restaurant;$('restaurant-title').textContent=restaurant.name;
    $('restaurant-create').hidden=true;$('restaurant-content').hidden=false;
    const labels={approved:'Сотрудники',pending:'Заявки',rejected:'Отклонённые',revoked:'Без доступа'};
    const states={pending:'Ждёт подтверждения',rejected:'Заявка отклонена',revoked:'Доступ закрыт'};
    panel.querySelectorAll('[data-status]').forEach(el=>{el.hidden=['rejected','revoked'].includes(el.dataset.status)&&!data.counts[el.dataset.status]&&status!==el.dataset.status;el.textContent=labels[el.dataset.status]+' '+data.counts[el.dataset.status];el.setAttribute('aria-pressed',String(el.dataset.status===status));});
    $('restaurant-rows').hidden=false;
    $('restaurant-rows').innerHTML=data.rows.length?data.rows.map(row=>{
      const sales=row.sales;const m=sales?.metrics,cutoffs=sales?.report_dates||[];
      const canTransfer=row.status==='approved'&&restaurants.some(r=>r.id!==selectedId);
      const controls=row.status==='pending'?`<button data-action="approve" data-id="${esc(row.id)}">Подтвердить</button><button data-action="reject" data-id="${esc(row.id)}">Отклонить</button>`:row.status==='approved'?`<button data-action="revoke" data-id="${esc(row.id)}">Отозвать доступ</button>${canTransfer?'<button data-action="transfer-open">Перенести</button>':''}`:'';
      const transfer=canTransfer?`<div class="restaurant-transfer" hidden><label>В какой ресторан<select aria-label="Новый ресторан для ${esc(row.report_name)}">${restaurants.filter(r=>r.id!==selectedId).map(r=>`<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('')}</select></label><p>После переноса прежний ресторан перестанет видеть сотрудника. Новый увидит планы и продажи, записанные после переноса; прежние личные записи останутся у сотрудника.</p><div class="restaurant-actions"><button data-action="transfer-confirm" data-id="${esc(row.id)}">Перенести сотрудника</button><button data-action="transfer-cancel">Отмена</button></div></div>`:'';
      return `<article class="restaurant-member"><details><summary><span><strong>${esc(row.report_name)}</strong><small>${sales?'Отчёт: '+date(cutoffs[0]):states[row.status]}</small></span>${m?`<span class="restaurant-turnover">${m.turnover.estimated?'≈ ':''}${num(m.turnover.total)}${m.turnover.total==null?'':' ₽'}<small>оборот</small></span>`:''}</summary>
        <p class="restaurant-id">Telegram ID: ${esc(row.user_id)}</p>${m?`<table><thead><tr><th>Показатель</th><th>Сейчас</th><th>План</th></tr></thead><tbody>${Object.entries(names).map(([k,label])=>`<tr><th>${label}, ${['cocktails','postcards','dvd'].includes(k)?'шт.':'₽'}${m[k].cutoff&&m[k].cutoff!==cutoffs[0]?`<small>Отчёт по ${date(m[k].cutoff)}</small>`:''}</th><td>${m[k].estimated?'≈ ':''}${num(m[k].total)}</td><td>${num(m[k].target)}</td></tr>`).join('')}</tbody></table>`:''}
        <div class="restaurant-actions">${row.status==='pending'?'':controls}</div>${transfer}</details>${row.status==='pending'?`<div class="restaurant-actions">${controls}</div>`:''}</article>`;
    }).join(''):`<p class="restaurant-empty">${status==='pending'?'Новых заявок нет.':status==='approved'?'Здесь появятся сотрудники, которых ты подтвердил.':'Список пока пуст.'}</p>`;
    $('restaurant-prev').hidden=page===0;$('restaurant-next').hidden=!data.more;
    $('restaurant-page').textContent=page||data.more?'Страница '+(page+1):'';
  }
  async function load(){
    if(busy)return;busy=true;lock();$('restaurant-error').textContent='';$('restaurant-retry').hidden=true;$('restaurant-rows').hidden=true;
    try{const access=await api('access');if(!access.available)throw new Error('Кабинет доступен администратору ресторана.');
      restaurants=access.restaurants||[];canCreate=!!access.can_create;
      if(canCreate)window.showResearchTab?.();
      if(!restaurants.some(r=>r.id===selectedId))selectedId=restaurants[0]?.id||null;
      restaurant=restaurants.find(r=>r.id===selectedId)||null;
      $('restaurant-add').hidden=!canCreate||!restaurant;
      $('restaurant-select').hidden=restaurants.length<2;
      $('restaurant-select').innerHTML=restaurants.map(r=>`<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('');
      $('restaurant-select').value=selectedId||'';
      if(!restaurant){$('restaurant-create').hidden=!canCreate;$('restaurant-create-cancel').hidden=true;$('restaurant-content').hidden=true;return;}
      $('restaurant-create-cancel').hidden=false;
      render(await api('view',{month,page,status}));
    }catch(e){if(!e.status)window.uxEvent?.('cabinet_load_error','restaurant',e.name==='TimeoutError'?'timeout':'network');$('restaurant-error').textContent=e.name==='TimeoutError'?'Ответ задерживается. Попробуй ещё раз.':e.message;$('restaurant-retry').hidden=false;}
    finally{busy=false;lock();}
  }
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
        $('restaurant-notice').textContent=action==='transfer'?'Сотрудник перенесён в «'+data.restaurant.name+'».':action==='approve'?'Сотрудник подтверждён.':action==='create'?'Кабинет готов.':'Готово.';
      }
    }catch(e){failure=e.name==='TimeoutError'?'Ответ задерживается. Обнови список, прежде чем повторять действие.':e.message;$('restaurant-error').textContent=failure;$('restaurant-retry').hidden=false;}
    finally{busy=false;lock();}
    if(action!=='invite'&&!failure)await load();
    if(failure){$('restaurant-error').textContent=failure;$('restaurant-retry').hidden=false;}
  }
  panel.addEventListener('click',e=>{const b=e.target.closest('button');if(!b||busy)return;
    if(b.dataset.status){status=b.dataset.status;page=0;load();}
    if(b.dataset.action==='transfer-open'){b.closest('.restaurant-member').querySelector('.restaurant-transfer').hidden=false;return;}
    if(b.dataset.action==='transfer-cancel'){b.closest('.restaurant-transfer').hidden=true;return;}
    if(b.dataset.action==='transfer-confirm'){
      const destination=b.closest('.restaurant-transfer').querySelector('select').value;
      change('transfer',{id:b.dataset.id,target_restaurant_id:destination});return;
    }
    if(b.dataset.action)change(b.dataset.action,{id:b.dataset.id});
  });
  $('restaurant-add').onclick=()=>{$('restaurant-create').hidden=false;$('restaurant-name').focus();};
  $('restaurant-create-cancel').onclick=()=>{$('restaurant-create').hidden=true;};
  $('restaurant-select').onchange=()=>{selectedId=$('restaurant-select').value;page=0;status='approved';$('restaurant-link').hidden=true;$('restaurant-url').value='';$('restaurant-notice').textContent='';load();};
  $('restaurant-month').onchange=()=>{month=$('restaurant-month').value;page=0;load();};
  $('restaurant-prev').onclick=()=>{page=Math.max(0,page-1);load();};$('restaurant-next').onclick=()=>{page++;load();};
  $('restaurant-retry').onclick=load;$('restaurant-invite').onclick=()=>change('invite',{});
  $('restaurant-copy').onclick=async()=>{try{await navigator.clipboard.writeText($('restaurant-url').value);$('restaurant-notice').textContent='Ссылка скопирована.';}catch(_){$('restaurant-url').select();$('restaurant-notice').textContent='Выделил ссылку — её можно скопировать.';}};
  $('restaurant-create').onsubmit=e=>{e.preventDefault();change('create',{name:$('restaurant-name').value});};
  $('tab-restaurant').onclick=()=>{for(const key of ['earnings','sales','restaurant','research']){$(key+'-panel').hidden=key!=='restaurant';$('tab-'+key).setAttribute('aria-selected',String(key==='restaurant'));}window.uxEvent?.('cabinet_opened','restaurant');load();};
  api('access').then(data=>{$('tab-restaurant').hidden=!data.available;if(data.can_create)window.showResearchTab?.();}).catch(e=>{if(e.status!==403&&e.status!==404&&e.status!==401){$('tab-restaurant').hidden=false;}});
})();
