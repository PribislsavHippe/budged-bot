// Таблица графика: одна разметка для вкладки «График» в мини-приложении и для редактора на сайте.
// Клетка: '9' — приход, '9-17' — необычный интервал, '*' — отметка «необычное», пусто — не работает.
(() => {
  'use strict';
  const WD=['вс','пн','вт','ср','чт','пт','сб'];
  const MONTHS=['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'];
  const NAMES=['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const at=iso=>new Date(iso+'T12:00:00');
  const dayName=iso=>{const d=at(iso);return `${WD[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]}`;};
  const period=(a,b)=>{const x=at(a),y=at(b);return x.getMonth()===y.getMonth()?`${x.getDate()}–${y.getDate()} ${MONTHS[y.getMonth()]}`:`${x.getDate()} ${MONTHS[x.getMonth()]} – ${y.getDate()} ${MONTHS[y.getMonth()]}`;};
  function parse(value){
    const m=/^(?:(\d{1,2}(?::[0-5]\d)?)(?:-(\d{1,2}(?::[0-5]\d)?))?)?(\*)?$/.exec(String(value??'').trim());
    if(!m||!String(value??'').trim())return null;
    const hour=s=>{const [h,min='0']=s.split(':');return Number(h)+Number(min)/60;};
    return {start:m[1]?hour(m[1]):null,end:m[2]?hour(m[2]):null,note:!!m[3]};
  }
  const clock=(value,short=false)=>{
    const minutes=Math.round(value*60), hour=Math.floor(minutes/60), minute=minutes%60;
    return short&&minute===0?String(hour):hour+':'+String(minute).padStart(2,'0');
  };
  const works=v=>!!v&&v.start!=null;
  function shift(person,iso){
    const v=parse(person.cells?.[iso]),off=(person.off||[]).includes(iso),asked=!off&&(person.asked||[]).includes(iso);
    if(works(v)){
      // Выход в обязательный выходной — тоже «необычное», но цветом выходного.
      // Смена в день, когда человек просит выходной (ещё не учтённый сборкой), обведена пунктиром.
      const cls=off?'off':v.note||v.end!=null?'note':'';
      return `<span class="sg-s ${cls}${v.end!=null?' rng':''}${asked?' clash':''}"${asked?' title="Просит выходной"':''}><b>${clock(v.start,true)}${v.end!=null?'–'+clock(v.end,true):''}</b></span>`;
    }
    if(off)return '<span class="sg-s off">вых</span>';
    if(asked)return '<span class="sg-s ask" title="Просит выходной">вых</span>';
    if(v&&v.note)return '<span class="sg-s note"></span>';
    return '<span class="sg-e"></span>';
  }
  const onDay=(people,iso)=>people.filter(p=>works(parse(p.cells?.[iso]))).length;

  // Правила таблицы (Бюро Горбунова, подборка «Таблицы»): все числа выровнены по разрядам вправо,
  // шапка прижата к верху и одной высоты у всех дней, события дня — отдельной строкой над датами,
  // повторяющееся (дни недели) бледное, отклонения (нехватка людей) заметные; группы разделены
  // промежутком, а не линиями; пустого и бессмысленного цвета нет. Месяц в таблице не пишется:
  // период стоит в заголовке страницы.
  // o: days, people[{name,me,cells,off}], events, holidays{iso:название}, today, near, scrolls, corner, edit,
  //    need(iso), flagged, weekGap (false — без промежутка между неделями)
  function table(o){
    const days=o.days,labels=['сегодня','завтра','послезавтра'];
    const gap=(iso,k)=>!o.near&&o.weekGap!==false&&k>0&&at(iso).getDay()===1;
    const g=tag=>`<${tag} class="sg-gap" aria-hidden="true"></${tag}>`;
    const tail=tag=>o.scrolls?`<${tag} class="sg-tail" aria-hidden="true"></${tag}>`:'';
    const ti=o.today?days.indexOf(o.today):-1;
    // На телефоне (три дня) события подписаны в строке «сегодня/завтра», отдельной строки нет.
    const withEvents=!o.near&&(!!o.edit||days.some(iso=>o.events?.[iso]||o.holidays?.[iso]));
    let html=`<colgroup><col class="sg-n">${days.map((iso,k)=>(gap(iso,k)?'<col class="sg-g">':'')+'<col>').join('')}${o.scrolls?'<col class="sg-tail">':''}</colgroup><thead>`;
    // Строка 1: события дня (генуборка, собрание) и праздники — над датами, чтобы даты не прыгали.
    // Подряд идущие дни с одним названием подписываются один раз.
    // Серия праздничных дней подряд подписывается один раз, по первому названию, кроме «Выходной».
    const runLabel={};
    days.forEach((iso,k)=>{
      if(!o.holidays?.[iso]||o.holidays?.[days[k-1]])return;
      let end=k;while(o.holidays?.[days[end+1]])end++;
      const names=days.slice(k,end+1).map(d=>o.holidays[d]);
      runLabel[iso]=names.find(n=>n!=='Выходной')||names[0];
    });
    if(withEvents)html+=`<tr class="sg-hv"><th class="sg-name" scope="col"></th>${days.map((iso,k)=>{
      const event=o.events?.[iso],holiday=event?'':runLabel[iso]||'';
      return (gap(iso,k)?g('th'):'')+`<th class="sg-ev" scope="col">${event?`<i>${esc(event)}</i>`:holiday?`<i class="sg-hol">${esc(holiday)}</i>`:''}</th>`;}).join('')}${tail('th')}</tr>`;
    // Строка 2: день недели бледным, число жирным.
    html+=`<tr class="sg-hd"><th class="sg-name" scope="col">${o.corner??''}</th>`;
    days.forEach((iso,k)=>{const d=at(iso),rel=ti>=0?k-ti:-1;
      html+=(gap(iso,k)?g('th'):'')+`<th scope="col" data-day="${iso}" tabindex="0" class="sg-day ${iso===o.today?'sg-today':''}" title="${o.edit?'Настройки дня':'Поставить этот день рядом с именами'}">`
        +`${o.near?(o.events?.[iso]?`<em class="ev">${esc(o.events[iso])}</em>`:`<em>${rel>=0&&rel<3?labels[rel]:runLabel[iso]?esc(runLabel[iso]):'&nbsp;'}</em>`):''}<span class="sg-wd">${WD[d.getDay()]}</span><b>${d.getDate()}</b></th>`;});
    html+=tail('th')+'</tr></thead><tbody>';
    o.people.forEach((p,r)=>{
      html+=`<tr class="${p.me?'sg-me':''}"><th class="sg-name" scope="row" data-r="${r}" ${o.edit?'tabindex="0"':''}>${esc(p.name)}${o.edit?`<button type="button" class="sg-x" data-remove="${r}" aria-label="Убрать из графика: ${esc(p.name)}" title="Убрать из графика">×</button>`:''}</th>`;
      days.forEach((iso,k)=>{
        // В редакторе смену можно взять и перетащить в другую клетку.
        const drag=o.edit&&works(parse(p.cells?.[iso]))?' draggable="true"':'';
        html+=(gap(iso,k)?g('td'):'')+`<td class="sg-c" data-r="${r}" data-day="${iso}" tabindex="0"${drag}>${shift(p,iso)}</td>`;});
      html+=tail('td')+'</tr>';
    });
    // Итог: число людей в смене. Когда людей столько, сколько нужно, число бледное;
    // отклонение — красное: заметно только то, что требует внимания.
    html+=`</tbody><tfoot><tr><th class="sg-name" scope="row">В смене</th>`;
    days.forEach((iso,k)=>{const have=onDay(o.people,iso),need=o.need?.(iso);
      const bad=o.flagged?.[iso]||(need!=null&&have!==need);
      html+=(gap(iso,k)?g('td'):'')+`<td class="${bad?'sg-bad':'sg-ok'}"${need!=null?` title="Нужно: ${need}"`:''}><b>${have}</b></td>`;});
    return html+tail('td')+'</tr></tfoot>';
  }

  // Прокрутка: имена на месте, выбранный день встаёт к ним; хвост справа даёт довести даже последний.
  function scroller(t,sc,opts={}){
    const names=()=>t.querySelector('thead .sg-name')?.offsetWidth||0;
    let fits=false;
    // Дни, ушедшие под закреплённую колонку имён, обрезаются по её краю: без заливки и без просвечивания.
    // x каждой ячейки считается при раскладке, край — при каждой прокрутке (одно свойство на таблицу).
    const cells=()=>t.querySelectorAll('thead th.sg-day,td.sg-c,tfoot td:not(.sg-tail)');
    // --sx — где сейчас колонка имён в координатах таблицы: по ней волнистая линия под своей строкой продолжается без шва.
    const setEdge=()=>{t.style.setProperty('--edge',(sc.scrollLeft+names())+'px');t.style.setProperty('--sx',sc.scrollLeft+'px');};
    // x — от левого края таблицы: offsetLeft у ячеек может считаться от страницы, и тогда обрезка опаздывает на отступ таблицы.
    function mark(){const l=t.getBoundingClientRect().left;[...cells(),...t.querySelectorAll('tbody tr.sg-me td')].forEach(c=>c.style.setProperty('--x',(c.getBoundingClientRect().left-l)+'px'));setEdge();}
    function size(){size_();mark();}
    function size_(){
      t.style.setProperty('--tail','0px');fits=false;
      if(opts.sheet)opts.sheet.style.width='';
      if(t.classList.contains('sg-near'))t.style.setProperty('--dw',Math.floor((sc.clientWidth-names())/3)+'px');
      else if(opts.sheet){
        // Весь период помещается — лист по ширине данных, без пустоты справа и без прокрутки.
        const natural=t.offsetWidth;
        if(natural<=opts.sheet.parentElement.clientWidth){fits=true;opts.sheet.style.width=natural+'px';sc.scrollLeft=0;t.style.setProperty('--names',names()+'px');return;}
        opts.sheet.style.width=natural+'px';
      }
      const heads=t.querySelectorAll('thead th.sg-day');if(!heads.length)return;
      t.style.setProperty('--tail',Math.max(0,sc.clientWidth-names()-heads[heads.length-1].offsetWidth)+'px');
      sc.style.scrollPaddingLeft=names()+'px';t.style.setProperty('--names',names()+'px');
    }
    // Колонка подсвечивается только после выбора человеком: без выбора заливка ничего не значит.
    function go(iso,smooth=true){
      t.querySelectorAll('.sg-pick').forEach(e=>e.classList.remove('sg-pick'));
      if(smooth)t.querySelectorAll(`[data-day="${iso}"]`).forEach(e=>e.classList.add('sg-pick'));
      const head=t.querySelector(`thead th[data-day="${iso}"]`);
      if(head&&!fits)sc.scrollTo({left:head.offsetLeft-names(),behavior:smooth?'smooth':'auto'});
    }
    sc.addEventListener('scroll',()=>{t.classList.toggle('sg-scrolled',sc.scrollLeft>2);setEdge();},{passive:true});
    return {size,go,mark};
  }

  // Наведение на компьютере подсвечивает строку и день.
  function hover(t){
    const clear=()=>t.querySelectorAll('.sg-hl,.sg-col').forEach(e=>e.classList.remove('sg-hl','sg-col'));
    t.addEventListener('mouseover',e=>{const c=e.target.closest('td.sg-c');clear();if(!c)return;
      c.parentElement.classList.add('sg-hl');t.querySelectorAll(`td.sg-c[data-day="${c.dataset.day}"]`).forEach(x=>x.classList.add('sg-col'));});
    t.addEventListener('mouseleave',clear);
  }

  // Весь график на телефоне: сам открывается при повороте, кнопка открывает его и вертикально.
  function landscape(getOptions){
    const land=document.createElement('div');
    land.className='sg sg-land';land.hidden=true;land.setAttribute('role','dialog');land.setAttribute('aria-modal','true');land.setAttribute('aria-label','Весь график');
    land.innerHTML='<div class="sg-fit"><div class="sg-lscroll"><table></table></div></div>';
    document.body.appendChild(land);
    const full=land.querySelector('table'),ls=land.querySelector('.sg-lscroll'),area=land.querySelector('.sg-fit');
    const sc=scroller(full,ls);
    full.addEventListener('click',e=>{if(e.target.closest('.sg-fold'))return collapse();const h=e.target.closest('thead th.sg-day');if(h)sc.go(h.dataset.day);});
    const tg=window.Telegram?.WebApp,touch=matchMedia('(pointer: coarse)'),iphone=/iPhone/.test(navigator.userAgent);
    let manual=false,dismissed=false,options=null;
    const phoneLandscape=()=>touch.matches&&innerWidth>innerHeight&&innerHeight<=540;
    // Если окно не сообщает вырез (env() = 0), на Айфоне отступаем сами со стороны Dynamic Island.
    function insets(portrait){
      land.style.removeProperty('--sl');land.style.removeProperty('--sr');
      if(!iphone||portrait)return;
      const probe=document.createElement('div');probe.style.cssText='position:fixed;padding-left:env(safe-area-inset-left,0px);visibility:hidden';
      document.body.append(probe);const known=parseFloat(getComputedStyle(probe).paddingLeft)>0;probe.remove();
      if(known)return;
      const angle=screen.orientation?.angle??window.orientation,left=angle===90,right=angle===270||angle===-90;
      land.style.setProperty('--sl',left||!right?'44px':'0px');land.style.setProperty('--sr',right||!left?'44px':'0px');
    }
    function fit(){
      const portrait=innerHeight>innerWidth,w=portrait?innerHeight:innerWidth,h=portrait?innerWidth:innerHeight;
      land.classList.toggle('rotated',portrait);insets(portrait);
      land.style.width=portrait?w+'px':'';land.style.height=portrait?h+'px':'';
      const style=getComputedStyle(area),room=area.clientHeight-parseFloat(style.paddingTop)-parseFloat(style.paddingBottom);
      const rows=options.people.length+2+(Object.keys(options.events||{}).length||Object.keys(options.holidays||{}).length?1:0);let rh=Math.floor(room/rows);
      land.style.setProperty('--rh',rh+'px');
      // Шапка с событием дня выше остальных строк: отнимаем лишнее у всех строк поровну.
      for(let pass=0;pass<4&&full.offsetHeight>room;pass++){rh-=Math.max(1,Math.ceil((full.offsetHeight-room)/(rows-1)));land.style.setProperty('--rh',rh+'px');}
      full.style.setProperty('--tail','0px');full.style.setProperty('--tw',ls.clientWidth+'px');
      widen();
      const names=full.querySelector('thead .sg-name').offsetWidth,heads=full.querySelectorAll('thead th.sg-day');
      const tail=Math.max(0,ls.clientWidth-names-heads[heads.length-1].offsetWidth);
      full.style.setProperty('--tail',tail+'px');full.style.setProperty('--tw',(ls.clientWidth+tail)+'px');ls.style.scrollPaddingLeft=names+'px';
      sc.mark();
    }
    // Правило внутреннего и внешнего: колонка с необычным временем (9–17) шире остальных, чтобы вокруг плашки
    // осталось поле больше её внутреннего отступа. Остальные колонки делят то, что осталось.
    function widen(){
      const cols=[...full.querySelectorAll('colgroup col:not(.sg-n):not(.sg-g):not(.sg-tail)')];
      cols.forEach(c=>{c.style.width='';});
      const names=full.querySelector('thead .sg-name').offsetWidth,room=ls.clientWidth-names;
      const need=options.days.map(day=>{let w=0;
        full.querySelectorAll(`td.sg-c[data-day="${day}"] .sg-s.rng b`).forEach(b=>{w=Math.max(w,b.offsetWidth+2*4+2*5+2);});return w;});
      const wide=need.filter(Boolean),plain=need.length-wide.length;
      if(!wide.length||cols.length!==need.length)return;
      const base=plain?Math.floor((room-wide.reduce((a,b)=>a+b,0))/plain):0;
      if(plain&&base<28)return;
      cols.forEach((c,i)=>{c.style.width=Math.max(need[i],base)+'px';});
    }
    function hide(){land.hidden=true;document.body.style.overflow='';document.documentElement.classList.remove('sg-land-open');land.classList.remove('rotated');land.style.width=land.style.height='';}
    function layout(){
      options=getOptions();
      if(!options){hide();return;}
      if(phoneLandscape()){manual=false;if(dismissed)return hide();}
      else{dismissed=false;if(!manual)return hide();}
      full.innerHTML=table({...options,near:false,scrolls:true,corner:'<button type="button" class="sg-fold">Свернуть</button>'});
      land.hidden=false;document.body.style.overflow='hidden';document.documentElement.classList.add('sg-land-open');fit();
      // В Telegram полный экран прячет и его шапку; в браузере — Fullscreen API, где он есть.
      try{if(tg?.requestFullscreen&&!tg.isFullscreen)tg.requestFullscreen();}catch(_){}
    }
    function expand(){
      manual=true;dismissed=false;layout();land.querySelector('.sg-fold')?.focus();
      try{if(!tg?.initData&&!document.fullscreenElement)document.documentElement.requestFullscreen?.().catch(()=>{});}catch(_){}
      try{if(tg?.lockOrientation)tg.lockOrientation();else screen.orientation?.lock?.('landscape').catch(()=>{});}catch(_){}
    }
    function collapse(){
      manual=false;dismissed=phoneLandscape();hide();
      try{if(tg?.unlockOrientation)tg.unlockOrientation();else screen.orientation?.unlock?.();}catch(_){}
      // Если мини-приложение и так всегда во весь экран, сворачивание графика его не выключает.
      try{if(tg?.isFullscreen&&!window.tgAlwaysFull)tg.exitFullscreen();}catch(_){}
      try{if(document.fullscreenElement)document.exitFullscreen();}catch(_){}
    }
    addEventListener('resize',layout);
    screen.orientation?.addEventListener?.('change',()=>setTimeout(layout,50));
    document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!land.hidden)collapse();});
    return {expand,collapse,refresh:layout};
  }

  window.ScheduleGrid={table,scroller,hover,landscape,parse,clock,works,esc,dayName,period,at,WD,MONTHS};
})();
