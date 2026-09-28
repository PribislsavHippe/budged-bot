(() => {
  'use strict';
  const $=id=>document.getElementById(id),panel=$('research-panel'),tg=window.Telegram?.WebApp;
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pct=n=>n==null?'—':n.toLocaleString('ru-RU')+'%';
  const time=s=>new Date(s).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Moscow'});
  const labels={user_started:'Первый запуск',activity:'Действие в чате',onboarding_started:'Начато знакомство',onboarding_step:'Подсказка',onboarding_skipped:'Знакомство пропущено',onboarding_completed:'Знакомство завершено',tip_added:'Записаны чаевые',expense_added:'Записан расход',first_tip_added:'Первые чаевые',first_expense_added:'Первый расход',first_value_action:'Первая полезная запись',cabinet_opened:'Открыт кабинет',cabinet_loaded:'Кабинет загружен',cabinet_load_error:'Ошибка кабинета',tab_opened:'Открыт раздел',shift_closed:'Смена закрыта',first_shift_closed:'Первая закрытая смена',shift_planned:'Запланирована смена',sales_report_started:'Начат отчёт',sales_report_completed:'Отчёт сохранён',sales_report_error:'Ошибка отчёта',vision_started:'Начато распознавание',vision_completed:'Распознавание завершено',vision_failed:'Ошибка распознавания',help_opened:'Открыта помощь',problem_reported:'Отправлено обращение'};
  const screens={chat:'Чат',earnings:'Чаевые',sales:'План',restaurant:'Ресторан',research:'Исследования',history:'История',help:'Помощь',calendar:'Календарь'};
  const codes={schema:'Схема базы',permissions:'Права доступа',backend:'Сервер',network:'Связь',timeout:'Время ожидания',auth:'Вход',invalid:'Ответ сервера',vision:'Распознавание',save:'Сохранение'};
  let days=30,version=null,page=0,loading=false,serial=0;
  panel.innerHTML=`<header class="research-head"><h1>UX Research</h1><button id="research-refresh">Обновить</button></header>
    <div class="research-controls"><div class="research-period"><button data-days="7" aria-pressed="false">7 дней</button><button data-days="30" aria-pressed="true">30 дней</button></div><label>Знакомство <select id="research-version"><option value="">Все версии</option></select></label></div>
    <p id="research-error" role="alert"></p><p id="research-state" role="status"></p><div id="research-overview"></div>
    <section><h2>Путь новых пользователей</h2><div id="research-funnel"></div><details class="research-note"><summary>Как читать воронку</summary><p>Это накопленные достижения когорты, а не обязательный порядок действий. Пропустившие обучение видны отдельно. Для D1 и D7 считаются только завершившиеся дни по Москве. «Ждём» — ещё рано оценивать возврат. Сбой — сигнал для проверки, а не доказанная причина ухода.</p></details></section>
    <section><h2>Возвращаются ли</h2><div id="research-retention" class="research-cards"></div></section>
    <section><h2>Где возникают ошибки</h2><div id="research-errors"></div></section>
    <details><summary>Использование функций</summary><div id="research-features"></div></details>
    <section><h2>Пути пользователей</h2><div id="research-users"></div><div class="research-controls"><button id="research-prev">←</button><span id="research-page"></span><button id="research-next">→</button></div><div id="research-journey" hidden></div></section>
    <section><h2>Обратная связь</h2><button id="research-feedback-load">Последние обращения</button><div id="research-feedback"></div></section>`;
  async function api(action,body={}){
    const r=await fetch('/api/research/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,initData:tg?.initData||''}),signal:AbortSignal.timeout(20000)});
    let data;try{data=await r.json();}catch(_){throw new Error('Сервер не ответил. Попробуй ещё раз.');}
    if(!r.ok)throw new Error(r.status===401?'Открой приложение заново из бота.':data.error||'Раздел недоступен.');return data;
  }
  function error(e){$('research-error').textContent=e.name==='TimeoutError'?'Ответ задерживается. Попробуй ещё раз.':e.message;}
  function render(d){
    $('research-overview').innerHTML=`<div class="research-cards"><article><b>${d.new_users}</b>новых пользователей</article><article><b>${d.active_users}</b>активных</article><article><b>${d.first_value_users}</b>с первой записью</article></div><p class="research-muted">Начали знакомство: ${d.onboarding_started} · Пропустили: ${d.onboarding_skipped}</p>`;
    $('research-funnel').innerHTML=d.funnel.map(s=>`<article class="research-stage"><div><strong>${esc(s.label)}</strong><b>${s.users}</b></div><progress max="100" value="${s.overall||0}" aria-label="${esc(s.label)}"></progress><p>${pct(s.conversion)} от предыдущего · ${pct(s.overall)} от начала</p><small>Не достигли: ${s.drop_off}${s.with_errors?' · из них со сбоем: '+s.with_errors:''}${s.pending?' · ждём: '+s.pending:''}</small></article>`).join('');
    $('research-retention').innerHTML=Object.entries(d.retention).map(([k,v])=>`<article><b>${pct(v.rate)}</b>${k.toUpperCase()}<small>${v.returned} из ${v.eligible} · ждём ${v.pending}</small></article>`).join('');
    $('research-errors').innerHTML=d.errors.length?d.errors.map(e=>`<p class="research-line"><span>${esc(labels[e.event]||e.event)}<small>${esc(screens[e.screen]||e.screen)} · ${esc(codes[e.code]||e.code)}</small></span><b>${e.count}</b></p>`).join(''):'<p class="research-muted">Зарегистрированных ошибок за период нет.</p>';
    $('research-features').innerHTML=d.features.map(f=>`<p class="research-line"><span>${esc(labels[f.event]||f.event)}</span><span>${f.users} чел. · ${f.count}</span></p>`).join('')||'<p>Пока нет событий.</p>';
    $('research-users').innerHTML=d.users.map(u=>`<button class="research-user" data-subject="${esc(u.id)}" data-label="${esc(u.label)}"><span><strong>${esc(u.label)}</strong><small>${u.cohort==='new'?'Новый':'Ранее зарегистрирован'} · v${u.onboarding_version}${u.errors?' · ошибок '+u.errors:''}</small></span><span>${time(u.last_at)}<small>${esc(labels[u.last_event]||u.last_event)}</small></span></button>`).join('')||'<p class="research-muted">Пока нет наблюдений.</p>';
    $('research-prev').disabled=page===0;$('research-next').disabled=(page+1)*20>=d.user_count;$('research-page').textContent=d.user_count?`${page+1} / ${Math.ceil(d.user_count/20)}`:'0';
    const select=$('research-version');select.innerHTML='<option value="">Все версии</option>'+d.versions.map(v=>`<option value="${v}">v${v}${v===0?' · прежние пользователи':''}</option>`).join('');select.value=version??'';
  }
  async function load(){if(loading)return;loading=true;$('research-error').textContent='';$('research-state').textContent='Загружаю…';panel.querySelectorAll('[data-days],#research-version,#research-refresh').forEach(e=>e.disabled=true);
    try{render(await api('overview',{days,version,page}));$('research-state').textContent='Данные по московскому времени';}catch(e){error(e);$('research-state').textContent='Данные не обновились';}finally{loading=false;panel.querySelectorAll('[data-days],#research-version,#research-refresh').forEach(e=>e.disabled=false);}}
  async function journey(id,label){const n=++serial;$('research-journey').hidden=false;$('research-journey').textContent='Загружаю путь…';
    try{const d=await api('journey',{subject:id});if(n!==serial)return;
      const rows=d.rows.slice().reverse();$('research-journey').innerHTML=`<h3>${esc(label)}</h3><ol class="research-timeline">${rows.map(e=>`<li><time>${time(e.occurred_at)}</time><strong>${esc(labels[e.event]||e.event)}</strong><small>${esc(screens[e.screen]||e.screen)}${e.step?' · '+esc({tip:'Чаевые',expense:'Расход'}[e.step]||e.step):''}${e.error_code?' · '+esc(codes[e.error_code]||e.error_code):''} · v${e.onboarding_version}</small></li>`).join('')}</ol><p class="research-muted">${rows.length?'Последнее известное действие: '+time(rows.at(-1).occurred_at):'Событий пока нет.'}. Момент выхода неизвестен. Показаны последние ${d.limit} событий.</p>`;
    }catch(e){if(n===serial)$('research-journey').textContent='Не удалось загрузить путь.';error(e);}}
  panel.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
    if(b.dataset.days&&!loading){days=Number(b.dataset.days);page=0;panel.querySelectorAll('[data-days]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));load();}
    if(b.dataset.subject)journey(b.dataset.subject,b.dataset.label);
  });
  $('research-version').onchange=()=>{version=$('research-version').value===''?null:Number($('research-version').value);page=0;load();};
  $('research-prev').onclick=()=>{if(!loading){page=Math.max(0,page-1);load();}};$('research-next').onclick=()=>{if(!loading){page++;load();}};
  $('research-refresh').onclick=load;
  $('research-feedback-load').onclick=async()=>{const button=$('research-feedback-load');button.disabled=true;try{const d=await api('feedback');$('research-feedback').innerHTML=d.rows.map(r=>`<article class="research-feedback"><button data-subject="${esc(r.subject_id)}" data-label="${esc(r.label)}">${esc(r.label)}</button><small>${time(r.created_at)} · ${esc({broken:'Сбой',confusing:'Непонятно',idea:'Идея'}[r.category])}</small><p>${esc(r.body)}</p></article>`).join('')||'<p>Обращений пока нет.</p>';}catch(e){error(e);}finally{button.disabled=false;}};
  $('tab-research').onclick=()=>{for(const key of ['earnings','sales','restaurant','research']){$(key+'-panel').hidden=key!=='research';$('tab-'+key).setAttribute('aria-selected',String(key==='research'));}load();};
})();
