(() => {
  'use strict';
  const $=id=>document.getElementById(id),panel=$('research-panel'),tg=window.Telegram?.WebApp;
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pct=n=>n==null?'—':n.toLocaleString('ru-RU')+'%';
  const time=s=>new Date(s).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Moscow'});
  const labels={user_started:'Первый запуск',activity:'Действие в чате',onboarding_started:'Начато знакомство',onboarding_step:'Подсказка',onboarding_skipped:'Знакомство пропущено',onboarding_completed:'Знакомство завершено',tip_added:'Записаны чаевые',expense_added:'Записан расход',first_tip_added:'Первые чаевые',first_expense_added:'Первый расход',first_value_action:'Первое полезное действие',cabinet_opened:'Открыт кабинет',cabinet_loaded:'Кабинет загружен',cabinet_load_error:'Ошибка кабинета',tab_opened:'Открыт раздел',shift_closed:'Ранее: закрытие смены',first_shift_closed:'Ранее: первая закрытая смена',shift_planned:'Добавлена смена',hours_recorded:'Записаны часы',sales_report_started:'Начат отчёт',sales_report_completed:'Отчёт сохранён',sales_report_error:'Ошибка отчёта',vision_started:'Начато распознавание',vision_completed:'Распознавание завершено',vision_failed:'Ошибка распознавания',schedule_import_started:'Начали загрузку графика',schedule_previewed:'Показан результат',schedule_imported:'График сохранён',help_opened:'Открыта помощь',problem_reported:'Отправлено обращение'};
  const screens={chat:'Чат',earnings:'Чаевые',sales:'План',restaurant:'Ресторан',research:'Исследования',history:'История',help:'Помощь',calendar:'Календарь'};
  const codes={schema:'Схема базы',permissions:'Права доступа',backend:'Сервер',network:'Связь',timeout:'Время ожидания',auth:'Вход',invalid:'Ответ сервера',vision:'Распознавание',save:'Сохранение'};
  const sources={bot:'Бот',miniapp:'Миниапп',server:'Сервер'};
  const useful=new Set(['user_started','onboarding_started','onboarding_skipped','onboarding_completed','first_value_action','cabinet_opened','tab_opened','tip_added','expense_added','shift_planned','shift_closed','hours_recorded','sales_report_started','sales_report_completed','sales_report_error','vision_started','vision_completed','vision_failed','schedule_import_started','schedule_previewed','schedule_imported','help_opened','problem_reported','cabinet_load_error']);
  const day=s=>new Date(s).toLocaleDateString('ru-RU',{day:'numeric',month:'long',timeZone:'Europe/Moscow'});
  const clock=s=>new Date(s).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit',timeZone:'Europe/Moscow'});
  let days=30,version=null,page=0,loading=false,serial=0,task=null,latest=null;
  panel.innerHTML=`<header class="research-head"><h1>Исследование</h1><button id="research-refresh">Обновить</button></header>
    <div class="research-controls"><div class="research-period"><button data-days="7" aria-pressed="false">7 дней</button><button data-days="30" aria-pressed="true">30 дней</button></div><label>Знакомство <select id="research-version"><option value="">Все версии</option></select></label></div>
    <button id="research-export" class="research-export">Прислать данные в чат ↓</button><p class="research-muted research-export-note">Бот пришлёт файл с действиями и путями людей под кодами. Сумм и текстов обращений в нём нет.</p>
    <p id="research-error" role="alert"></p><p id="research-state" role="status"></p>
    <div id="research-home"><div id="research-overview"></div><div id="research-task-cards"></div><p class="research-muted">График и чаевые — две самостоятельные задачи. Человек может вести только одну из них.</p>
    <section class="research-people"><h2>Люди</h2><p class="research-muted">Выбери сотрудника, чтобы увидеть его действия по дням. Коды не раскрывают имя.</p><div id="research-users"></div><div class="research-controls research-pages"><button id="research-prev">← Назад</button><span id="research-page"></span><button id="research-next">Далее →</button></div></section></div>
    <div id="research-person" hidden><button id="research-person-back" class="research-back">← Все сотрудники</button><div id="research-person-content"></div></div>
    <div id="research-task" hidden><button id="research-back">← К двум задачам</button><div id="research-task-content"></div><div id="research-task-journey" hidden></div></div>
    <details class="research-more"><summary>Остальные данные исследования</summary>
    <section><h2>Активность по дням</h2><div id="research-daily"></div></section>
    <section><h2>Действия новых пользователей</h2><div id="research-funnel"></div><details class="research-note"><summary>Как читать показатели</summary><p>Каждое действие считается отдельно среди новых пользователей. Возврат на следующий день и через неделю считаем только после завершения соответствующего дня по Москве. «Ждём» — ещё рано оценивать возврат. Сбой — сигнал для проверки, а не доказанная причина ухода.</p></details></section>
    <section><h2>Возвращаются ли в приложение</h2><div id="research-retention" class="research-cards"></div></section>
    <section><h2>Где возникают ошибки</h2><div id="research-errors"></div></section>
    <details><summary>Использование функций</summary><div id="research-features"></div></details>
    <section><h2>Обратная связь</h2><button id="research-feedback-load">Последние обращения</button><div id="research-feedback"></div></section></details>`;
  async function api(action,body={}){
    const r=await fetch('/api/research/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,initData:tg?.initData||''}),signal:AbortSignal.timeout(action==='export'?60000:20000)});
    let data;try{data=await r.json();}catch(_){throw new Error('Сервер не ответил. Попробуй ещё раз.');}
    if(!r.ok)throw new Error(r.status===401?'Открой приложение заново из бота.':data.error||'Раздел недоступен.');return data;
  }
  function error(e){$('research-error').textContent=e.name==='TimeoutError'?'Ответ задерживается. Попробуй ещё раз.':e.message;}
  function row(label,value,detail=''){return `<p class="research-line"><span>${esc(label)}${detail?`<small>${esc(detail)}</small>`:''}</span><span>${esc(value)}</span></p>`;}
  function recent(items){return items.length?items.map(u=>`<button class="research-user" data-subject="${esc(u.id)}" data-label="${esc(u.label)}" data-task-journey="true"><span>${esc(u.label)}<small>${esc(labels[u.last_event]||u.last_event)}</small></span><span>${time(u.last_at)} ›</span></button>`).join(''):'<p class="research-muted">За этот период таких действий не наблюдалось.</p>';}
  function renderTask(d){
    if(!task)return;
    const s=d.tasks.schedule,t=d.tasks.tips;
    if(task==='schedule'){
      const eligible=s.repeat_eligible;
      $('research-task-content').innerHTML=`<h2>График</h2><div class="research-hero"><span>Повторно отправили график</span><div class="research-hero-number">${eligible?s.repeat:'—'}${eligible?` <small>из ${eligible}</small>`:''}</div><p>Подтвердили загрузку в разные дни периода. Повтор того же графика тоже считается.</p></div>
        <div class="research-secondary"><div>Подтвердили загрузку<div class="research-value">${s.imported}</div></div><div>Сохранили смену<div class="research-value">${s.saved}</div></div></div>
        <section><h3>Действия с графиком</h3>${row('Начали загрузку',s.import_started+' чел.')}${row('Показан результат',s.previewed+' чел.','Это не подтверждение правильных смен')}${row('Подтвердили загрузку',s.imported+' чел.')}${row('Сохранили смену',s.saved+' чел.','Любым способом, в том числе вручную')}</section>
        <section><h3>Трудности</h3>${row('Ошибка распознавания',s.recognition_failed+' чел.')}${row('Исправили результат','—','Это действие пока не записывается')}</section>
        <section><h3>Пути людей</h3>${recent(s.recent)}</section>
        <p class="research-muted">Правки результата и доставка напоминаний пока не измеряются. Последнее видимое действие не объясняет причину остановки.</p>`;
    }else{
      const eligible=t.repeat_eligible;
      $('research-task-content').innerHTML=`<h2>Чаевые</h2><div class="research-hero"><span>Записали чаевые в разные дни</span><div class="research-hero-number">${eligible?t.repeat:'—'}${eligible?` <small>из ${eligible}</small>`:''}</div><p>Среди записавших до сегодняшнего дня. Учитываются только дни выбранного периода.</p></div>
        <div class="research-secondary"><div>Записали чаевые<div class="research-value">${t.saved}</div></div><div>Открыли экран чаевых<div class="research-value">${t.miniapp_opened}</div></div></div>
        <section><h3>Каким путём записывают</h3>${row('Через бота',t.sources.bot+' чел.')}${row('В миниаппе',t.sources.miniapp+' чел.')}<p class="research-muted">Один человек может использовать оба способа. В суперприватности видны только факт и время записи.</p></section>
        <section><h3>Где останавливаются</h3>${row('Начали ввод, но не сохранили','—','Начало ввода пока не записывается')}${row('Ошибка сохранения','—','Такого события пока нет')}</section>
        <section><h3>Пути людей</h3>${recent(t.recent)}</section>
        <p class="research-muted">Открытие миниаппа и запись через чат не образуют одну воронку. Причину прекращения записи нужно спрашивать у сотрудника.</p>`;
    }
  }
  function render(d){
    latest=d;
    $('research-overview').innerHTML=`<p class="research-muted">Активных за период: ${d.active_users} · Последнее событие: ${d.last_observed_at?time(d.last_observed_at):'нет'}. Ваши действия не учитываются. Действия до включения исследования здесь не появятся.</p>`;
    $('research-task-cards').innerHTML=`<button class="research-topic" data-task="schedule"><span>График</span><strong>${d.tasks.schedule.saved}</strong><small>Сохранили смену за период</small><span class="research-arrow">Смотреть ›</span></button><button class="research-topic" data-task="tips"><span>Чаевые</span><strong>${d.tasks.tips.saved}</strong><small>Записали чаевые за период</small><span class="research-arrow">Смотреть ›</span></button>`;
    renderTask(d);
    const dailyMax=Math.max(1,...d.daily_activity.map(x=>x.active_users));
    $('research-daily').innerHTML=d.daily_activity.map(x=>`<div class="research-day"><span>${new Date(x.date+'T12:00:00+03:00').toLocaleDateString('ru-RU',{day:'numeric',month:'short',timeZone:'Europe/Moscow'})}</span><progress max="${dailyMax}" value="${x.active_users}" aria-label="${x.active_users} активных"></progress><span>${x.active_users} чел. · ${x.events} событий</span></div>`).join('');
    $('research-funnel').innerHTML=d.funnel.map(s=>`<article class="research-stage"><div><strong>${esc(s.label)}</strong><b>${s.users}</b></div><progress max="100" value="${s.overall||0}" aria-label="${esc(s.label)}"></progress><p>${pct(s.conversion)} из доступных · ${pct(s.overall)} от всех новых</p><small>Не достигли: ${s.drop_off}${s.with_errors?' · из них со сбоем: '+s.with_errors:''}${s.pending?' · ждём: '+s.pending:''}</small></article>`).join('');
    $('research-retention').innerHTML=Object.entries(d.retention).map(([k,v])=>`<article><b>${pct(v.rate)}</b>${k==='d1'?'На следующий день':k==='d7'?'Через неделю':esc(k)}<small>${v.returned} из ${v.eligible} · ждём ${v.pending}</small></article>`).join('');
    $('research-errors').innerHTML=d.errors.length?d.errors.map(e=>`<p class="research-line"><span>${esc(labels[e.event]||e.event)}<small>${esc(screens[e.screen]||e.screen)} · ${esc(codes[e.code]||e.code)}</small></span><b>${e.count}</b></p>`).join(''):'<p class="research-muted">Зарегистрированных ошибок за период нет.</p>';
    $('research-features').innerHTML=d.features.map(f=>`<p class="research-line"><span>${esc(labels[f.event]||f.event)}</span><span>${f.users} чел. · ${f.count}</span></p>`).join('')||'<p>Пока нет событий.</p>';
    $('research-users').innerHTML=d.users.map(u=>`<button class="research-user" data-subject="${esc(u.id)}" data-label="${esc(u.label)}"><span><strong>${esc(u.label)}</strong><small>${u.active_days} дн. с действиями · график ${u.shift_saves} · чаевые ${u.tip_saves}</small></span><span>${time(u.last_at)}<small>${esc(labels[u.last_event]||u.last_event)}</small></span></button>`).join('')||'<p class="research-muted">За выбранный период действий не видно. До начала сбора они могли быть.</p>';
    $('research-prev').disabled=page===0;$('research-next').disabled=(page+1)*20>=d.user_count;$('research-page').textContent=d.user_count?`${page+1} / ${Math.ceil(d.user_count/20)}`:'0';
    const select=$('research-version');select.innerHTML='<option value="">Все версии</option>'+d.versions.map(v=>`<option value="${v}">v${v}${v===0?' · прежние пользователи':''}</option>`).join('');select.value=version??'';
  }
  async function load(){if(loading)return;loading=true;serial++;$('research-person').hidden=true;$('research-home').hidden=false;$('research-task').hidden=true;panel.querySelector('.research-more').hidden=false;task=null;$('research-task-journey').hidden=true;$('research-error').textContent='';$('research-state').textContent='Загружаю…';panel.querySelectorAll('[data-days],#research-version,#research-refresh').forEach(e=>e.disabled=true);
    try{render(await api('overview',{days,version,page}));$('research-state').textContent='Данные по московскому времени';}catch(e){error(e);$('research-state').textContent='Данные не обновились';}finally{loading=false;panel.querySelectorAll('[data-days],#research-version,#research-refresh').forEach(e=>e.disabled=false);}}
  async function journey(id,label){const n=++serial,box=$('research-task-journey');box.hidden=false;box.textContent='Загружаю путь…';
    try{const d=await api('journey',{subject:id,days});if(n!==serial)return;
      const rows=d.rows.slice().reverse();box.innerHTML=`<h3>${esc(label)}</h3><ol class="research-timeline">${rows.map(e=>`<li><time>${time(e.occurred_at)}</time><span>${esc(labels[e.event]||e.event)}</span><small>${esc(screens[e.screen]||e.screen)}${e.step?' · '+esc({tip:'Чаевые',expense:'Расход'}[e.step]||e.step):''}${e.error_code?' · '+esc(codes[e.error_code]||e.error_code):''} · v${e.onboarding_version}</small></li>`).join('')}</ol><p class="research-muted">${rows.length?'Последнее известное действие: '+time(rows.at(-1).occurred_at)+'.':'Событий пока нет.'} Момент выхода неизвестен. Показаны последние ${d.limit} событий.</p>`;
      box.scrollIntoView({block:'nearest',behavior:'smooth'});
    }catch(e){if(n===serial)box.textContent='Не удалось загрузить путь.';error(e);}}
  async function person(id,label){
    const n=++serial,box=$('research-person-content'),user=latest?.users.find(u=>u.id===id);
    $('research-home').hidden=true;$('research-task').hidden=true;$('research-person').hidden=false;panel.querySelector('.research-more').hidden=true;
    box.textContent='Загружаю действия…';$('research-error').textContent='';
    try{
      const d=await api('journey',{subject:id,days});if(n!==serial)return;
      const facts=user?`<div class="research-person-facts"><div><b>${user.active_days}</b><span>дней с действиями</span></div><div><b>${user.shift_saves}</b><span>сохранений смен</span></div><div><b>${user.tip_saves}</b><span>записей чаевых</span></div></div><p class="research-muted">Время через чат записано ${user.hours_saves} раз. Одно сохранение графика может содержать несколько смен.</p>`:'';
      const events=d.rows.filter(e=>useful.has(e.event));let current='';
      const timeline=events.map(e=>{const date=day(e.occurred_at),heading=date!==current?`<h3 class="research-person-day">${esc(date)}</h3>`:'';current=date;
        return `${heading}<div class="research-person-event"><time>${clock(e.occurred_at)}</time><div><strong>${esc(labels[e.event]||e.event)}</strong><small>${esc(sources[e.source]||e.source||'Источник не указан')} · ${esc(screens[e.screen]||e.screen||'Раздел не указан')}${e.error_code?' · '+esc(codes[e.error_code]||e.error_code):''}</small></div></div>`;}).join('');
      box.innerHTML=`<h2>${esc(label)}</h2><p class="research-muted">Выбранные ${days} дней · время московское</p>${facts}<h3>Что видно</h3>${timeline||'<p class="research-muted">За этот период значимых действий не видно.</p>'}<p class="research-muted research-person-caveat">${d.truncated?'Показаны только последние 100 событий выбранного периода.':'Показаны события выбранного периода.'} Сохранение смены само по себе не подтверждает распознавание фото. Чтение напоминаний и причины действий здесь не видны.</p><details><summary>Все события, включая технические</summary><ol class="research-timeline">${d.rows.map(e=>`<li><time>${time(e.occurred_at)}</time><span>${esc(labels[e.event]||e.event)}</span><small>${esc(sources[e.source]||e.source||'—')} · ${esc(screens[e.screen]||e.screen||'—')}</small></li>`).join('')}</ol></details>`;
      $('research-person').scrollIntoView({block:'start'});
    }catch(e){if(n===serial)box.textContent='Не получилось загрузить действия. Вернись к списку и попробуй ещё раз.';error(e);}
  }
  panel.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
    if(b.dataset.days&&!loading){days=Number(b.dataset.days);page=0;panel.querySelectorAll('[data-days]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));load();}
    if(b.dataset.task){task=b.dataset.task;$('research-home').hidden=true;$('research-task').hidden=false;renderTask(latest);$('research-task').scrollIntoView({block:'start'});}
    if(b.dataset.subject){if(b.dataset.taskJourney==='true')journey(b.dataset.subject,b.dataset.label);else person(b.dataset.subject,b.dataset.label);}
  });
  $('research-person-back').onclick=()=>{serial++;$('research-person').hidden=true;$('research-home').hidden=false;panel.querySelector('.research-more').hidden=false;$('research-home').scrollIntoView({block:'start'});};
  $('research-back').onclick=()=>{task=null;$('research-task').hidden=true;$('research-home').hidden=false;$('research-task-journey').hidden=true;};
  $('research-version').onchange=()=>{version=$('research-version').value===''?null:Number($('research-version').value);page=0;load();};
  $('research-prev').onclick=()=>{if(!loading){page=Math.max(0,page-1);load();}};$('research-next').onclick=()=>{if(!loading){page++;load();}};
  $('research-refresh').onclick=load;
  $('research-export').onclick=async()=>{
    const button=$('research-export');button.disabled=true;$('research-error').textContent='';$('research-state').textContent='Готовлю файл…';
    try{
      await api('export',{days,version});
      $('research-state').textContent='Отправил файл в чат с ботом. Его можно переслать для разбора.';
    }catch(e){
      error(e);$('research-state').textContent='Файл не отправился.';
    }finally{button.disabled=false;}
  };
  $('research-feedback-load').onclick=async()=>{const button=$('research-feedback-load');button.disabled=true;try{const d=await api('feedback');$('research-feedback').innerHTML=d.rows.map(r=>`<article class="research-feedback"><button data-subject="${esc(r.subject_id)}" data-label="${esc(r.label)}">${esc(r.label)}</button><small>${time(r.created_at)} · ${esc({broken:'Сбой',confusing:'Непонятно',idea:'Идея'}[r.category])}</small><p>${esc(r.body)}</p></article>`).join('')||'<p>Обращений пока нет.</p>';}catch(e){error(e);}finally{button.disabled=false;}};
  $('tab-research').onclick=()=>{for(const key of ['earnings','sales','restaurant','research']){$(key+'-panel').hidden=key!=='research';$('tab-'+key).setAttribute('aria-selected',String(key==='research'));}load();};
})();
