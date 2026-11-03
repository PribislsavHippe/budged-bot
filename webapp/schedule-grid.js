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
    const m=/^(?:(\d{1,2})(?:-(\d{1,2}))?)?(\*)?$/.exec(String(value??'').trim());
    if(!m||!String(value??'').trim())return null;
    return {start:m[1]?+m[1]:null,end:m[2]?+m[2]:null,note:!!m[3]};
  }
  const works=v=>!!v&&v.start!=null;
  function shift(person,iso){
    const v=parse(person.cells?.[iso]),off=(person.off||[]).includes(iso);
    if(works(v)){
      // Выход в обязательный выходной — тоже «необычное», но цветом выходного.
      const cls=off?'off':v.note||v.end!=null?'note':'';
      return `<span class="sg-s ${cls}"><b>${v.start}${v.end!=null?'–'+v.end:''}</b></span>`;
    }
    if(off)return '<span class="sg-s off">вых</span>';
    if(v&&v.note)return '<span class="sg-s note"></span>';
    return '<span class="sg-e"></span>';
  }
  const onDay=(people,iso)=>people.filter(p=>works(parse(p.cells?.[iso]))).length;

  // o: days, people[{name,me,cells,off,wish_off}], events, today, near, scrolls, corner, edit, need(iso), flagged
  function table(o){
    const days=o.days,labels=['сегодня','завтра','послезавтра'];
    const gap=(iso,k)=>!o.near&&k>0&&at(iso).getDay()===1;
    const g=tag=>`<${tag} class="sg-gap" aria-hidden="true"></${tag}>`;
    const tail=tag=>o.scrolls?`<${tag} class="sg-tail" aria-hidden="true"></${tag}>`:'';
    const ti=o.today?days.indexOf(o.today):-1;
    let html=`<colgroup><col class="sg-n">${days.map((iso,k)=>(gap(iso,k)?'<col class="sg-g">':'')+'<col>').join('')}${o.scrolls?'<col class="sg-tail">':''}</colgroup>`;
    html+=`<thead><tr><th class="sg-name" scope="col">${o.corner??(o.near?'':NAMES[at(days[0]).getMonth()])}</th>`;
    days.forEach((iso,k)=>{const d=at(iso),rel=ti>=0?k-ti:-1;
      html+=(gap(iso,k)?g('th'):'')+`<th scope="col" data-day="${iso}" tabindex="0" class="sg-day ${iso===o.today?'sg-today':''}" title="${o.edit?'Настройки дня':'Поставить этот день рядом с именами'}">`
        +`${o.near?`<em>${rel>=0&&rel<3?labels[rel]:'&nbsp;'}</em>`:''}${WD[d.getDay()]}<b>${d.getDate()}</b>${o.events?.[iso]?`<i>${esc(o.events[iso])}</i>`:''}</th>`;});
    html+=tail('th')+'</tr></thead><tbody>';
    o.people.forEach((p,r)=>{
      html+=`<tr class="${p.me?'sg-me':''}"><th class="sg-name" scope="row" data-r="${r}" ${o.edit?'tabindex="0"':''}>${esc(p.name)}</th>`;
      days.forEach((iso,k)=>{const wish=o.edit&&(p.wish_off||[]).includes(iso);
        html+=(gap(iso,k)?g('td'):'')+`<td class="sg-c ${wish?'sg-wish':''}" data-r="${r}" data-day="${iso}" tabindex="0">${shift(p,iso)}</td>`;});
      html+=tail('td')+'</tr>';
    });
    html+=`</tbody><tfoot><tr><th class="sg-name" scope="row">В смене</th>`;
    days.forEach((iso,k)=>{const have=onDay(o.people,iso),need=o.need?.(iso);
      const bad=o.flagged?.[iso]||(need!=null&&have<need);
      html+=(gap(iso,k)?g('td'):'')+`<td class="${bad?'sg-bad':''}"><b>${have}</b>${need!=null&&need!==have?`<small>/${need}</small>`:''}</td>`;});
    return html+tail('td')+'</tr></tfoot>';
  }

  // Прокрутка: имена на месте, выбранный день встаёт к ним; хвост справа даёт довести даже последний.
  function scroller(t,sc,opts={}){
    const names=()=>t.querySelector('thead .sg-name')?.offsetWidth||0;
    function size(){
      t.style.setProperty('--tail','0px');
      if(opts.sheet)opts.sheet.style.width='';
      if(t.classList.contains('sg-near'))t.style.setProperty('--dw',Math.floor((sc.clientWidth-names())/3)+'px');
      else if(opts.sheet)opts.sheet.style.width=t.offsetWidth+'px';
      const heads=t.querySelectorAll('thead th.sg-day');if(!heads.length)return;
      t.style.setProperty('--tail',Math.max(0,sc.clientWidth-names()-heads[heads.length-1].offsetWidth)+'px');
      sc.style.scrollPaddingLeft=names()+'px';
    }
    function go(iso,smooth=true){
      t.querySelectorAll('.sg-pick').forEach(e=>e.classList.remove('sg-pick'));
      t.querySelectorAll(`[data-day="${iso}"]`).forEach(e=>e.classList.add('sg-pick'));
      const head=t.querySelector(`thead th[data-day="${iso}"]`);
      if(head)sc.scrollTo({left:head.offsetLeft-names(),behavior:smooth?'smooth':'auto'});
    }
    sc.addEventListener('scroll',()=>t.classList.toggle('sg-scrolled',sc.scrollLeft>2),{passive:true});
    return {size,go};
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
      const rows=options.people.length+2;let rh=Math.floor(room/rows);
      land.style.setProperty('--rh',rh+'px');
      // Шапка с событием дня выше остальных строк: отнимаем лишнее у всех строк поровну.
      for(let pass=0;pass<4&&full.offsetHeight>room;pass++){rh-=Math.max(1,Math.ceil((full.offsetHeight-room)/(rows-1)));land.style.setProperty('--rh',rh+'px');}
      full.style.setProperty('--tail','0px');full.style.setProperty('--tw',ls.clientWidth+'px');
      const names=full.querySelector('thead .sg-name').offsetWidth,heads=full.querySelectorAll('thead th.sg-day');
      const tail=Math.max(0,ls.clientWidth-names-heads[heads.length-1].offsetWidth);
      full.style.setProperty('--tail',tail+'px');full.style.setProperty('--tw',(ls.clientWidth+tail)+'px');ls.style.scrollPaddingLeft=names+'px';
    }
    function hide(){land.hidden=true;document.body.style.overflow='';land.classList.remove('rotated');land.style.width=land.style.height='';}
    function layout(){
      options=getOptions();
      if(!options){hide();return;}
      if(phoneLandscape()){manual=false;if(dismissed)return hide();}
      else{dismissed=false;if(!manual)return hide();}
      full.innerHTML=table({...options,near:false,scrolls:true,corner:'<button type="button" class="sg-fold">Свернуть</button>'});
      land.hidden=false;document.body.style.overflow='hidden';fit();
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

  window.ScheduleGrid={table,scroller,hover,landscape,parse,works,esc,dayName,period,at,WD,MONTHS};
})();
