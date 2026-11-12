// Составление графика на сайте ресторана: при создании программа сама ставит обязательные выходные и
// собирает несколько вариантов; менеджер выбирает вариант, правит клетки и публикует для команды.
// В график попадают только подтверждённые сотрудники ресторана.
(() => {
  'use strict';
  const G=window.ScheduleGrid;if(!G)return;
  const {esc}=G;
  const STARTS=[8,9,10,11,12,14];
  const EVENTS=['Генуборка','Собрание'];
  const iso=d=>d.toLocaleDateString('sv-SE');
  // Следующая половина месяца: с 1 по 15 или с 16 до конца.
  function nextHalf(){
    const now=new Date();let a,b;
    if(now.getDate()<=15){a=new Date(now.getFullYear(),now.getMonth(),16);b=new Date(now.getFullYear(),now.getMonth()+1,0);}
    else{a=new Date(now.getFullYear(),now.getMonth()+1,1);b=new Date(now.getFullYear(),now.getMonth()+1,15);}
    return [iso(a),iso(b)];
  }
  // Периоды всегда одни и те же: с 1 по 15 и с 16 до конца месяца. Новый — сразу после последнего.
  function halfAfter(endIso){
    const d=G.at(endIso),y=d.getFullYear(),m=d.getMonth();
    return d.getDate()<=15?[iso(new Date(y,m,16)),iso(new Date(y,m+1,0))]:[iso(new Date(y,m+1,1)),iso(new Date(y,m+1,15))];
  }
  const daysOf=(a,b)=>{const out=[];for(let d=G.at(a);d<=G.at(b);d.setDate(d.getDate()+1))out.push(iso(d));return out;};

  function open(root,opts){
    // Повторное открытие раздела не пересоздаёт редактор: правки и слушатели остаются.
    if(root._roster)return root._roster.refresh(opts.places,opts.placeId);
    let wishes={},places=opts.places||[],placeId=places.some(p=>p.id===opts.placeId)?opts.placeId:places[0]?.id,periods=[],cur=null,team=[],sel=null,side=null;
    let edits=0,saved=0,saving=false,timer=null,busy=false,conflict=false;
    // Отмена: снимки черновика до каждого изменения.
    let undo=[],redo=[],last=null,forcePick=false,offsInfo=null,toastTimer=null;
    root.innerHTML=`<div class="re sg">
      <div class="re-guide" role="status"></div>
      <div class="re-head">
        <div class="re-period">
          <button type="button" class="re-step" data-step="-1" aria-label="Предыдущий график">‹</button>
          <button type="button" class="re-title" title="Изменить даты"></button>
          <button type="button" class="re-step" data-step="1" aria-label="Следующий график">›</button>
          <select class="re-jump" aria-label="Все графики" hidden></select>
          <button type="button" class="re-new">+ График</button>
          <button type="button" class="re-all" hidden>Все варианты</button>
          <button type="button" class="re-delete is-off">Удалить черновик</button>
        </div>
        <div class="re-actions">
          <button type="button" class="re-ask" disabled>Запросить выходные</button>
          <button type="button" class="re-check-offs" disabled>Попросить проверить выходные</button>
          <button type="button" class="re-build" disabled>Собрать заново</button>
          <button type="button" class="btn re-publish" disabled>Опубликовать</button>
        </div>
      </div>
      <form class="re-create" hidden><strong>Даты графика</strong><label>с <input type="date" name="start" required></label><label>по <input type="date" name="end" required></label>
        <button class="btn">Сохранить</button><button type="button" class="re-cancel">Отмена</button></form>
      <p class="re-phone">Составлять и править график удобнее на компьютере. Здесь можно посмотреть его и запросить выходные.</p>
      <p class="re-error" role="alert"></p>
      <p class="re-empty" hidden>Графиков пока нет.</p>
      <div class="re-editor" hidden>
        <div class="re-pickview" hidden></div>
        <div class="re-editview">
          <div class="re-main">
            <section class="sg-sheet re-sheet"><div class="re-history" hidden><button type="button" class="re-undo" title="Отменить (Ctrl+Z)" aria-label="Отменить" disabled>↶</button><button type="button" class="re-redo" title="Вернуть (Ctrl+Shift+Z)" aria-label="Вернуть" disabled>↷</button></div><div class="sg-scroll"><table class="sg-edit"></table></div></section>
            <details class="re-adder" hidden><summary>+ Добавить сотрудника</summary><div class="re-chips"></div></details></div>
          <aside class="re-aside">
            <section class="re-card re-panel" aria-live="polite" hidden><header class="re-panel-head"><h3 class="re-who"></h3><button type="button" class="re-close" aria-label="Закрыть" title="Закрыть (Esc)">×</button></header>
              <div class="re-block re-person"></div><div class="re-block re-cell"></div><div class="re-block re-day"></div></section>
            <section class="re-card re-check" aria-label="Проверка правил"><h3>Проверка <span class="re-badge"></span></h3><div class="re-asks" hidden></div><div class="re-report"></div></section>
          </aside>
        </div>
      </div>
      <div class="re-toast" role="status" hidden></div></div>`;
    const q=s=>root.querySelector(s),grid=q('.sg-edit'),sc=grid.parentElement,sheet=q('.re-sheet');
    const scroll=G.scroller(grid,sc,{sheet});G.hover(grid);
    const error=t=>{q('.re-error').textContent=t||'';};
    // Короткое сообщение внизу экрана: место в вёрстке не занимает, ничего не сдвигает.
    function toast(text,sticky){
      const el=q('.re-toast');clearTimeout(toastTimer);el.textContent=text||'';el.hidden=!text;
      if(text&&!sticky)toastTimer=setTimeout(()=>{el.hidden=true;},4500);
    }

    async function api(action,body={},timeout=20000){
      const r=await fetch('/api/restaurant/roster/'+action,{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({...body,initData:opts.initData||''}),signal:AbortSignal.timeout(timeout)});
      let data;try{data=await r.json();}catch(_){throw new Error('Сервер не ответил. Попробуй ещё раз.');}
      if(r.status===401){opts.onAuth?.();throw new Error('Войди заново.');}
      if(!r.ok){const e=new Error(data.error||'График пока недоступен.');e.status=r.status;throw e;}
      return data;
    }
    root.querySelectorAll('.re-step').forEach(b=>b.onclick=()=>{const list=ordered(),i=list.findIndex(p=>p.id===cur?.id);const next=list[i+(+b.dataset.step)];if(next)load(next.id);});
    q('.re-jump').onchange=()=>{if(q('.re-jump').value!==cur?.id)load(q('.re-jump').value);};
    const failed=e=>error(e.name==='TimeoutError'?'Ответ задерживается. Обнови страницу и проверь, сохранилось ли.':e.message);

    // ─── список периодов ───
    function stateOf(p){return p.published_at?(p.changed?'опубликован, есть новые правки':'опубликован'):'черновик';}
    // Периоды листаются стрелками по датам, как в мини-приложении; список — для прыжка к давним.
    const ordered=()=>[...periods].sort((a,b)=>a.start<b.start?-1:1);
    const suggested=()=>{const all=ordered();return all.length?halfAfter(all[all.length-1].end):nextHalf();};
    function renderPeriods(){
      const list=ordered(),i=list.findIndex(p=>p.id===cur?.id);
      q('.re-jump').hidden=list.length<3;
      q('.re-jump').innerHTML=list.map(p=>`<option value="${esc(p.id)}">${esc(G.period(p.start,p.end))} · ${stateOf(p)}</option>`).join('');
      if(cur)q('.re-jump').value=cur.id;
      root.querySelectorAll('.re-step').forEach(b=>{b.hidden=list.length<2;b.disabled=i<0||!list[i+(+b.dataset.step)];});
      q('.re-empty').hidden=!!periods.length;
      const next=suggested();q('.re-new').title=G.period(next[0],next[1]);
      if(!cur){q('.re-title').textContent='';q('.re-all').hidden=true;}
      q('.re-title').disabled=!cur||!!cur.published_at;q('.re-title').title=cur?.published_at?'':'Изменить даты';
      q('.re-ask').disabled=q('.re-build').disabled=q('.re-publish').disabled=!cur;
      q('.re-check-offs').disabled=!cur||busy||conflict||!D().people.length;
      q('.re-delete').classList.toggle('is-off',!cur||!!cur.published_at);
    }
    async function list(openId){
      error('');
      try{const data=await api('list',{restaurant_id:placeId});places=data.restaurants;placeId=data.restaurant_id;periods=data.periods;renderPeriods();
        const target=openId||periods[0]?.id;if(target)await load(target);else{cur=null;q('.re-editor').hidden=true;}}
      catch(e){failed(e);}
    }
    async function load(id){
      if(!(await flush()))return;
      try{const data=await api('open',{id});team=data.team||[];wishes=data.wishes||{};toast('');accept(data,true);sel=null;side=null;renderAll();refreshOffs();
        const today=iso(new Date());scroll.go(cur.days.includes(today)?today:cur.days[0],false);}
      catch(e){failed(e);}
    }
    function accept(data,replace){
      const keep=cur&&cur.id===data.id&&!replace?cur.draft:null;
      if(cur?.id!==data.id){undo=[];redo=[];forcePick=false;}
      cur={...data,days:daysOf(data.start,data.end),draft:keep||data.draft};
      for(const key of ['off','off_auto','cells','wish_off','wish_starts','days','events'])cur.draft[key]=cur.draft[key]||{};
      cur.draft.variants=cur.draft.variants||[];
      if(!keep)last=JSON.stringify(cur.draft);
      const i=periods.findIndex(p=>p.id===data.id),summary={id:data.id,start:data.start,end:data.end,published_at:data.published_at,changed:data.changed};
      if(i>=0)periods[i]=summary;else periods.unshift(summary);
      renderPeriods();
    }

    // ─── черновик ───
    const D=()=>cur.draft;
    const person=r=>D().people[r];
    const listOf=(key,name)=>D()[key][name]||(D()[key][name]=[]);
    function toggle(key,name,day,on){
      const values=listOf(key,name),has=values.includes(day);
      if(on===undefined)on=!has;
      if(on&&!has)values.push(day);if(!on&&has)values.splice(values.indexOf(day),1);
      if(!values.length)delete D()[key][name];
    }
    function setCell(name,day,value){
      const row=D().cells[name]||(D().cells[name]={});
      if(value)row[day]=value;else delete row[day];
      if(!Object.keys(row).length)delete D().cells[name];
    }
    // Праздник: календарь России, если менеджер не решил иначе. Праздник — как пятница или суббота.
    const holiday=day=>D().days[day]?.holiday??!!cur.holidays?.[day];
    const holidayLabels=()=>Object.fromEntries(cur.days.filter(holiday).map(d=>[d,cur.holidays?.[d]||'Праздник']));
    const money=day=>[0,5,6].includes(G.at(day).getDay())||holiday(day);
    const need=day=>D().days[day]?.count??(money(day)?6:5);
    function changed(){
      const now=JSON.stringify(D());
      if(last!==null&&last!==now){undo.push(last);if(undo.length>100)undo.shift();redo=[];}
      last=now;edits++;renderGrid();renderState();clearTimeout(timer);timer=setTimeout(save,900);
    }
    function travel(from,to){
      if(!cur||!from.length)return;
      to.push(JSON.stringify(D()));cur.draft=JSON.parse(from.pop());last=JSON.stringify(cur.draft);
      if(sel&&!person(sel.r))sel=null;if(side?.kind==='person'&&!person(side.r))side=null;
      edits++;renderGrid();renderState();renderCell();renderSide();clearTimeout(timer);timer=setTimeout(save,900);
    }
    q('.re-undo').onclick=()=>travel(undo,redo);q('.re-redo').onclick=()=>travel(redo,undo);
    document.addEventListener('keydown',e=>{
      if(!cur||busy||root.closest('[hidden]')||!(e.ctrlKey||e.metaKey)||e.key.toLowerCase()!=='z'&&e.key.toLowerCase()!=='я')return;
      if(e.target.matches('input,textarea'))return;
      e.preventDefault();if(e.shiftKey)travel(redo,undo);else travel(undo,redo);
    });
    async function save(){
      clearTimeout(timer);
      if(saving||!cur||edits===saved)return;
      saving=true;const version=edits;renderState();
      try{const data=await api('save',{id:cur.id,revision:cur.revision,draft:D()});saved=version;
        accept(data,version===edits);if(version===edits)renderGrid();}
      catch(e){failed(e);
        // Конфликт: правка не записана. Она остаётся несохранённой, пока страницу не обновят; сохранять её
        // поверх чужой версии нельзя, поэтому автосохранение останавливается, а сменить период нельзя.
        if(e.status===409)conflict=true;}
      finally{saving=false;renderState();if(edits!==saved&&!conflict&&!q('.re-error').textContent)timer=setTimeout(save,900);}
    }
    // Перед сменой периода, сборкой и публикацией дописываем последнее изменение.
    async function flush(){
      if(!cur)return true;
      while(saving)await new Promise(r=>setTimeout(r,150));
      if(conflict)return false;
      if(edits!==saved)await save();
      return edits===saved&&!conflict;
    }

    // ─── отрисовка ───
    function renderState(){
      if(!cur)return;
      const pending=edits!==saved;
      q('.re-publish').textContent=cur.published_at?(cur.changed||pending?'Опубликовать правки':'Опубликован'):'Опубликовать';
      q('.re-publish').disabled=busy||conflict||showPick()||(!!cur.published_at&&!cur.changed&&!pending);q('.re-build').disabled=busy||conflict;q('.re-ask').disabled=busy;
      q('.re-check-offs').disabled=busy||conflict||!D().people.length;
      // Пока идёт публикация или сборка, таблица заперта: правка во время запроса пропала бы с ответом сервера.
      root.classList.toggle('re-working',busy);
      // Сколько человек уже записали выходные: подсказка к кнопке, а не строка под заголовком.
      q('.re-ask').title=offsInfo?.total?`Ответили: ${offsInfo.answered} из ${offsInfo.total}${offsInfo.fresh?', есть новые':''}`:'';
      q('.re-undo').disabled=!undo.length;q('.re-redo').disabled=!redo.length;
      q('.re-history').hidden=showPick()||(!undo.length&&!redo.length);
      renderGuide();
    }
    // Этапы во всю ширину над заголовком: вариант → правка → публикация.
    function renderGuide(){
      const box=q('.re-guide'),steps=['Вариант','Правка','Публикация'];
      let step=0;
      if(cur){
        const filled=Object.keys(D().cells).length,problems=cur.check?.problems||0,pending=edits!==saved;
        if(!D().people.length||!filled||showPick())step=1;else if(problems)step=2;else if(!cur.published_at||cur.changed||pending)step=3;else step=4;
      }
      box.innerHTML=`<ol class="re-steps">${steps.map((name,i)=>`<li class="${step&&i+1<step?'done':i+1===step?'now':''}">${name}</li>`).join('')}</ol>`;
    }
    function people(){
      const d=D();
      return d.people.map(p=>({name:p.name,cells:d.cells[p.name]||{},off:d.off[p.name]||[],asked:offsInfo?.requests?.[p.name]||[]}));
    }
    // Весь график помещается на экране: ширина колонок подбирается под место, а не наоборот.
    // Совсем узкое окно прокручивается, но без ползунка.
    // Колонка с необычным временем (9–17) шире остальных: вокруг плашки остаётся внешнее поле, которое
    // больше её внутреннего отступа. Остальные колонки делят то, что осталось.
    function sizeGrid(){
      const cols=grid.querySelectorAll('colgroup col'),head=grid.querySelector('thead .sg-name');
      if(!head||cols.length<cur.days.length+1)return;
      grid.style.tableLayout='';grid.style.width='';cols.forEach(c=>{c.style.width='';});
      const names=head.offsetWidth,room=sheet.parentElement.clientWidth-2,MIN=44,MAX=60,PR=3;
      const gap=parseFloat(getComputedStyle(grid.querySelector('.sg-s.rng')||grid).getPropertyValue('--m'))||10;
      const need=cur.days.map(day=>{let w=0;
        grid.querySelectorAll(`td.sg-c[data-day="${day}"] .sg-s.rng`).forEach(p=>{w=Math.max(w,p.offsetWidth+2*gap+PR);});return w;});
      const wide=need.filter(Boolean),plain=cur.days.length-wide.length;
      const base=plain?Math.min(MAX,Math.max(MIN,Math.floor((room-names-wide.reduce((a,b)=>a+b,0))/plain))):MAX;
      const widths=need.map(w=>Math.max(w,base));
      cols[0].style.width=names+'px';widths.forEach((w,i)=>{cols[i+1].style.width=w+'px';});
      grid.style.tableLayout='fixed';grid.style.width=names+widths.reduce((a,b)=>a+b,0)+'px';
      fitRows();
      scroll.size();
      // Не поместилось — таблица листается; хвост даёт довести до последнего дня.
      const tail=parseFloat(grid.style.getPropertyValue('--tail'))||0;
      if(tail)grid.style.width=names+widths.reduce((a,b)=>a+b,0)+tail+'px';
    }
    // График целиком на первом экране: высота строк подбирается под окно (30–40 px), а не наоборот.
    function fitRows(){
      grid.style.setProperty('--rh','40px');
      const top=grid.getBoundingClientRect().top+scrollY,room=innerHeight-top-20,rows=Math.max(1,D().people.length);
      let rh=40;
      for(let pass=0;pass<4;pass++){
        grid.style.setProperty('--rh',rh+'px');
        const over=grid.offsetHeight-room;
        if(over<=0||rh<=30)break;
        rh=Math.max(30,rh-Math.ceil(over/(rows+1)));
      }
    }
    function renderGrid(){
      const left=sc.scrollLeft;
      grid.innerHTML=G.table({days:cur.days,people:people(),events:D().events,holidays:holidayLabels(),today:iso(new Date()),scrolls:true,edit:true,weekGap:false,need,flagged:cur.check?.days});
      sizeGrid();sc.scrollLeft=left;
      if(sel)grid.querySelector(`td.sg-c[data-r="${sel.r}"][data-day="${sel.day}"]`)?.classList.add('sg-sel');
      renderReport();
    }
    // Разбор правил — тот же текст, что в боте, но списками: сначала нарушения, потом нагрузка.
    function renderReport(){
      const box=q('.re-report'),badge=q('.re-badge'),problems=cur.check?.problems||0;
      badge.textContent=problems?`${problems} ${problems%10===1&&problems%100!==11?'нарушение':problems%10>=2&&problems%10<=4&&(problems%100<12||problems%100>14)?'нарушения':'нарушений'}`:'правила соблюдены';
      badge.classList.toggle('bad',!!problems);
      // До первой сборки таблица пустая — «не хватает людей» во все дни ничего не говорит.
      const items=cur.check?.items||[];
      const blocks=(cur.check?.text||'').split('\n\n').slice(1).filter(b=>!b.startsWith('Что не получилось:'));
      box.innerHTML=(items.length?`<ul class="re-problems">${items.map((it,i)=>`<li><button type="button" data-problem="${i}">${esc(it.text)}</button></li>`).join('')}</ul>`:'')+blocks.map(block=>{
        const lines=block.split('\n'),head=lines[0].endsWith(':')?lines.shift():null;
        const items=lines.filter(l=>l.startsWith('• ')),plain=lines.filter(l=>!l.startsWith('• '));
        return `${head?`<h4>${esc(head.slice(0,-1))}</h4>`:''}${plain.map(l=>`<p>${esc(l)}</p>`).join('')}${items.length?`<ul>${items.map(l=>`<li>${esc(l.slice(2))}</li>`).join('')}</ul>`:''}`;
      }).join('');
    }
    // Все варианты сразу, от лучшего по расчёту к самому слабому: таблица, плюсы и минусы, кнопка выбора.
    const sameCells=(a,b)=>{const names=new Set([...Object.keys(a),...Object.keys(b)]);
      for(const n of names){const x=a[n]||{},y=b[n]||{},keys=new Set([...Object.keys(x),...Object.keys(y)]);for(const k of keys)if(x[k]!==y[k])return false;}return true;};
    const current=()=>(D().variants||[]).find(v=>v.id===D().variant&&D().picked&&sameCells(D().cells,v.cells));
    function renderPick(){
      const box=q('.re-pickview'),list=D().variants||[],auto=D().off_auto||{},manual={};
      for(const [n,days] of Object.entries(D().off)){const m=days.filter(d=>!(auto[n]||[]).includes(d));if(m.length)manual[n]=m;}
      const chosen=current();
      box.innerHTML=list.map((v,i)=>{
        const rank=i===0?'лучший по расчёту':i===list.length-1&&list.length>1?'слабее остальных':'';
        return `<section class="re-var" data-id="${esc(v.id)}"><div class="sg-sheet re-varsheet"><div class="sg-scroll"><table class="sg-var"></table></div></div>
          <article class="re-card re-var-info"><h3>${esc(v.title)}<small>${rank}</small></h3>
          <ul>${v.pros.map(t=>`<li class="pro">${esc(t)}</li>`).join('')}${v.cons.map(t=>`<li class="con">${esc(t)}</li>`).join('')}</ul>
          ${chosen?.id===v.id?'<button type="button" class="btn" data-edit>Править этот</button>':`<button type="button" class="btn btn-ghost" data-variant="${esc(v.id)}">Выбрать</button>`}</article></section>`;}).join('');
      list.forEach(v=>{
        const people=D().people.map(p=>({name:p.name,cells:v.cells[p.name]||{},off:[...new Set([...(manual[p.name]||[]),...(v.off[p.name]||[])])]}));
        box.querySelector(`[data-id="${v.id}"] table`).innerHTML=G.table({days:cur.days,people,events:D().events,holidays:holidayLabels(),weekGap:false,need});
      });
    }
    function pick(id){
      const v=(D().variants||[]).find(x=>x.id===id);if(!v)return;
      // Если таблицу уже правили руками, замена вариантом их сотрёт.
      const was=(D().variants||[]).find(x=>x.id===D().variant);
      if(D().picked&&was&&!sameCells(D().cells,was.cells)&&!confirm('Заменить таблицу этим вариантом? Правки смен пропадут.'))return;
      // Выходные, поставленные вручную (отпуск) или запрошенные людьми, остаются; автоматические берутся из варианта.
      const auto=D().off_auto||{},manual={};
      for(const [n,days] of Object.entries(D().off)){const m=days.filter(d=>!(auto[n]||[]).includes(d));if(m.length)manual[n]=m;}
      D().cells=JSON.parse(JSON.stringify(v.cells));D().off_auto=JSON.parse(JSON.stringify(v.off));D().off={};
      for(const n of new Set([...Object.keys(manual),...Object.keys(v.off)]))D().off[n]=[...new Set([...(manual[n]||[]),...(v.off[n]||[])])].sort();
      D().variant=id;D().picked=true;forcePick=false;sel=null;side=null;
      applyView();changed();
    }
    q('.re-pickview').addEventListener('click',e=>{
      const b=e.target.closest('button');if(!b)return;
      if(b.dataset.variant)pick(b.dataset.variant);
      else if('edit' in b.dataset){forcePick=false;applyView();}
    });
    q('.re-report').addEventListener('click',e=>{
      const b=e.target.closest('[data-problem]');if(!b)return;
      const it=cur.check.items[+b.dataset.problem],r=it.name?D().people.findIndex(p=>p.name===it.name):-1;
      scroll.go(it.day);
      if(r>=0)select(r,it.day);else{sel=null;clearSel();side={kind:'day',day:it.day};renderPanel();}
    });
    // Два вида одного графика: сначала все варианты сразу, после выбора — правка выбранного.
    // На телефоне график только смотрят: всегда таблица, без выбора вариантов.
    const phone=matchMedia('(max-width: 700px)');
    phone.addEventListener?.('change',()=>{if(cur)applyView();});
    const showPick=()=>!phone.matches&&!!cur&&(cur.draft.variants||[]).length>0&&(forcePick||!cur.draft.picked);
    function applyView(){
      const pickMode=showPick();
      q('.re-pickview').hidden=!pickMode;q('.re-editview').hidden=pickMode;
      q('.re-all').hidden=!(D().variants||[]).length||pickMode;
      if(pickMode)renderPick();else{renderGrid();renderCell();renderSide();}
      renderState();
    }
    function renderAll(){
      q('.re-editor').hidden=false;q('.re-title').textContent=G.period(cur.start,cur.end);
      applyView();
    }
    q('.re-all').onclick=()=>{forcePick=true;applyView();};

    // ─── карточка справа: один человек и один день, ниже блоки «Смена», «День», «Сотрудник» ───
    // Блоки стоят на месте всегда; чего не выбрано, то неактивно.
    function focusOf(){
      if(sel&&person(sel.r))return {r:sel.r,day:sel.day,cell:true};
      if(side?.kind==='person'&&person(side.r))return {r:side.r,day:null,cell:false};
      return {r:null,day:side?.kind==='day'?side.day:null,cell:false};
    }
    function renderPanel(){
      if(!cur)return;
      // Пока ничего не выбрано, вместо пустой карточки справа стоит проверка правил.
      q('.re-panel').hidden=!sel&&!side;
      if(!sel&&!side)return;
      const f=focusOf(),p=f.r!=null?person(f.r):null;
      q('.re-who').innerHTML=`${p?esc(p.name):f.day?esc(G.dayName(f.day)):'Смена'}<small>${p&&f.day?esc(G.dayName(f.day)):''}</small>`;
      blockPerson(p);blockCell(f,p);blockDay(f);blockAdd();
    }
    function renderCell(){renderPanel();}
    function renderSide(){renderPanel();}
    function blockCell(f,p){
      const box=q('.re-cell'),has=f.cell,dis=has?'':' disabled';
      const v=has?G.parse(D().cells[p.name]?.[f.day]||''):null;
      const off=has&&(D().off[p.name]||[]).includes(f.day);
      const start=v?.start;
      box.innerHTML=`<h4 class="re-sub">Смена</h4>
        <p class="re-label">Приход</p>
        <div class="re-hours">${STARTS.map(h=>`<button type="button" data-set="${h}"${dis} aria-pressed="${start===h&&v.end==null}">${h}</button>`).join('')}</div>
        <div class="re-line"><label class="re-range">Интервал <input${dis} value="${v&&v.end!=null?esc(G.clock(start,true)+'-'+G.clock(v.end,true)):''}" placeholder="9-17" inputmode="numeric"></label>
          <button type="button" data-set=""${dis} class="re-clear" aria-pressed="${has&&(!v||start==null)}">Не работает</button></div>
        <div class="re-flags"><button type="button" data-off${dis} aria-pressed="${off}"><i class="sg-s off">вых</i>Обязательный выходной</button>
          <button type="button" data-note${dis} aria-pressed="${!!v?.note}"><i class="sg-s note">★</i>Необычное</button></div>`;
    }
    function blockDay(f){
      const day=f.day,dis=day?'':' disabled',s=day?D().days[day]||{}:{},event=day?D().events[day]||'':'';
      const opening=day?(s.openers??(money(day)?2:1)):'';
      q('.re-day').innerHTML=`<h4 class="re-sub">День<small>${day?esc(cur.holidays?.[day]||''):''}</small></h4>
        <div class="re-fields"><label>Людей<input type="number" min="0" max="60" data-day-field="count"${dis} value="${day?need(day):''}"></label>
        <label>Открывают<input type="number" min="0" max="10" data-day-field="openers"${dis} value="${opening}"></label>
        <label>Закрывают<input type="number" min="0" max="10" data-day-field="closers"${dis} value="${day?s.closers??3:''}"></label></div>
        <label class="re-check-row"><input type="checkbox" data-holiday${dis} ${day&&holiday(day)?'checked':''}> Праздник</label>
        <label>Событие дня<input list="re-events" data-event maxlength="40"${dis} value="${esc(event)}" placeholder="Генуборка, собрание…"></label>
        <datalist id="re-events">${EVENTS.map(x=>`<option value="${x}">`).join('')}</datalist>`;
    }
    function blockPerson(p){
      const dis=p?'':' disabled',wish=p?(D().wish_starts[p.name]||[]).join(', '):'';
      const WD=['пн','вт','ср','чт','пт','сб','вс'],w=p?.link?wishes[p.link]||{}:{},asks=w.days||[];
      const wishText=[asks.length?`выходные по ${asks.map(d=>WD[d]).join(', ')}`:'',w.pattern?`график ${w.pattern}`:''].filter(Boolean).join(', ');
      q('.re-person').innerHTML=`<h4 class="re-sub">Сотрудник</h4>
        ${wishText?`<p class="re-wish">Пожелание: ${esc(wishText)}</p>`:''}
        <label class="re-check-row"><input type="checkbox" data-student${dis} ${p?.student?'checked':''}> Учёба</label>
        <label>Удобный приход<input data-starts${dis} value="${esc(wish)}" placeholder="например, 9, 10"></label>
        <div class="re-row"><button type="button" data-move="-1"${dis}>Выше</button><button type="button" data-move="1"${dis}>Ниже</button><button type="button" data-remove${dis}>Убрать из графика</button></div>`;
    }
    // Добавить можно только подтверждённого официанта и только из списка, не по имени.
    function blockAdd(){
      const addable=team.filter(t=>!D().people.some(p=>p.link===t.id)),box=q('.re-adder');
      box.hidden=!addable.length;
      q('.re-chips').innerHTML=addable.map(t=>`<button type="button" data-add="${esc(t.id)}">${esc(t.report_name)}</button>`).join('');
    }
    q('.re-close').onclick=()=>{sel=null;side=null;clearSel();renderPanel();};
    q('.re-chips').addEventListener('click',e=>{
      const b=e.target.closest('[data-add]');if(!b)return;
      const t=team.find(x=>x.id===b.dataset.add);
      if(!t||D().people.some(p=>p.link===t.id))return;
      D().people.push({name:t.report_name,student:false,link:t.id});error('');changed();renderPanel();
    });
    // target — клетка, в которую пишем: отложенный ввод с клавиатуры привязан к клетке, где его начали,
    // а не к той, что выделена к моменту срабатывания таймера.
    function applyCell(raw,target=sel){
      if(!target||!person(target.r))return;
      const p=person(target.r),day=target.day,old=G.parse(D().cells[p.name]?.[day]);
      if(raw===''){setCell(p.name,day,old?.note?'*':'');}
      else{
        // Ресторан закрывается в 23: смена не может кончаться позже.
        const v=G.parse(raw);if(!v||v.start==null||v.start<6||v.start>=23||(v.end!=null&&!(v.end>v.start&&v.end<=23))){error(`Не понял смену «${raw}». Пиши час прихода (9) или интервал (9-17), конец не позже 23.`);return;}
        error('');setCell(p.name,day,G.clock(v.start,true)+(v.end!=null?'-'+G.clock(v.end,true):'')+(old?.note||v.note?'*':''));
      }
      changed();renderCell();
    }
    function toggleNote(){
      const p=person(sel.r),raw=D().cells[p.name]?.[sel.day]||'',v=G.parse(raw);
      setCell(p.name,sel.day,v?.note?raw.replace('*',''):raw+'*');changed();renderCell();
    }
    function toggleOff(){
      const p=person(sel.r),on=!(D().off[p.name]||[]).includes(sel.day);
      toggle('off',p.name,sel.day,on);
      // Снятый выходной больше не автоматический.
      if(!on){const auto=D().off_auto[p.name]||[],i=auto.indexOf(sel.day);if(i>=0){auto.splice(i,1);if(!auto.length)delete D().off_auto[p.name];}}
      // Обязательный выходной убирает смену, кроме отметки «необычное».
      if(on){const v=G.parse(D().cells[p.name]?.[sel.day]);setCell(p.name,sel.day,v?.note?'*':'');}
      changed();renderCell();
    }
    const clearSel=()=>grid.querySelectorAll('.sg-sel').forEach(e=>e.classList.remove('sg-sel'));
    function select(r,day,focus=true){
      commitTyped();
      sel={r,day};side={kind:'day',day};
      grid.querySelectorAll('.sg-sel').forEach(e=>e.classList.remove('sg-sel'));
      const cell=grid.querySelector(`td.sg-c[data-r="${r}"][data-day="${day}"]`);cell?.classList.add('sg-sel');if(focus)cell?.focus();
      renderCell();renderSide();
    }
    q('.re-cell').addEventListener('click',e=>{
      const b=e.target.closest('button');if(!b||!sel)return;
      if('set' in b.dataset)applyCell(b.dataset.set);else if('note' in b.dataset)toggleNote();
      else if('off' in b.dataset)toggleOff();
    });
    q('.re-cell').addEventListener('change',e=>{if(e.target.matches('.re-range input'))applyCell(e.target.value.trim());});
    let typed='',typedAt=0,pend=null;
    // Набранное с клавиатуры дописывается в свою клетку при переходе, Enter и по таймеру; Esc его отменяет.
    function commitTyped(){clearTimeout(grid._commit);if(!pend)return;const p=pend;pend=null;typed='';applyCell(p.value,{r:p.r,day:p.day});}
    function dropTyped(){clearTimeout(grid._commit);pend=null;typed='';}
    grid.addEventListener('keydown',e=>{
      if(busy)return;
      const cell=e.target.closest('td.sg-c');
      if(!cell){if((e.key==='Enter'||e.key===' ')&&e.target.closest('th')){e.preventDefault();e.target.click();}return;}
      const r=+cell.dataset.r,i=cur.days.indexOf(cell.dataset.day);
      const move=(dr,di)=>{const nr=Math.min(Math.max(r+dr,0),D().people.length-1),ni=Math.min(Math.max(i+di,0),cur.days.length-1);e.preventDefault();typed='';select(nr,cur.days[ni]);};
      if(e.key==='ArrowRight')return move(0,1);if(e.key==='ArrowLeft')return move(0,-1);
      if(e.key==='ArrowDown')return move(1,0);if(e.key==='ArrowUp')return move(-1,0);
      if(!sel||sel.r!==r||sel.day!==cell.dataset.day)select(r,cell.dataset.day,false);
      const key=e.key.toLowerCase();
      if(key==='delete'||key==='backspace'){e.preventDefault();dropTyped();return applyCell('');}
      if(key==='в'||key==='d'){e.preventDefault();return toggleOff();}
      if(key==='*'){e.preventDefault();return toggleNote();}
      if(/^[0-9-]$/.test(key)){
        e.preventDefault();typed=Date.now()-typedAt<1200?typed+key:key;typedAt=Date.now();
        // «1» ждёт вторую цифру; одна цифра больше 2 или полный интервал — сразу в клетку.
        clearTimeout(grid._commit);pend=null;
        if(/^(?:[3-9]|1\d|2[0-2])(?:-(?:1\d|2[0-3]|[3-9]))?$/.test(typed)&&!/-$/.test(typed)){
          pend={r,day:cell.dataset.day,value:typed};
          grid._commit=setTimeout(commitTyped,typed.includes('-')||typed.length>=2?250:700);
        }
        return;
      }
      if(e.key==='Enter'){e.preventDefault();if(typed){pend={r,day:cell.dataset.day,value:typed};commitTyped();}else q('.re-cell button')?.focus();}
      if(e.key==='Escape'){dropTyped();sel=null;side=null;clearSel();renderPanel();}
    });
    // Смену можно взять и перетащить на другую клетку: в пустую — переезжает, в занятую — меняются местами.
    // В обязательный выходной человека смена не переносится.
    let drag=null;
    const mark=cell=>{grid.querySelectorAll('.sg-drop').forEach(x=>x.classList.remove('sg-drop'));cell?.classList.add('sg-drop');};
    grid.addEventListener('dragstart',e=>{
      const c=e.target.closest('td.sg-c');if(!c)return;
      drag={r:+c.dataset.r,day:c.dataset.day};e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain','Смена');
      requestAnimationFrame(()=>c.classList.add('sg-drag'));
    });
    grid.addEventListener('dragover',e=>{
      const c=e.target.closest('td.sg-c');if(!drag||!c)return;
      e.preventDefault();e.dataTransfer.dropEffect='move';mark(c);
    });
    grid.addEventListener('dragend',()=>{drag=null;grid.querySelectorAll('.sg-drag,.sg-drop').forEach(x=>x.classList.remove('sg-drag','sg-drop'));});
    grid.addEventListener('drop',e=>{
      const c=e.target.closest('td.sg-c');if(!drag||!c)return;
      e.preventDefault();const from=drag,to={r:+c.dataset.r,day:c.dataset.day};drag=null;mark(null);moveShift(from,to);
    });
    function moveShift(from,to){
      const a=person(from.r),b=person(to.r);
      if(!a||!b||(from.r===to.r&&from.day===to.day))return;
      const at=(p,day)=>D().cells[p.name]?.[day]||'',off=(p,day)=>(D().off[p.name]||[]).includes(day);
      const mine=at(a,from.day),theirs=at(b,to.day);
      if(off(b,to.day))return toast(`${b.name}: в этот день обязательный выходной`);
      if(theirs&&off(a,from.day))return toast(`${a.name}: в этот день обязательный выходной`);
      setCell(b.name,to.day,mine);setCell(a.name,from.day,theirs);
      changed();select(to.r,to.day,false);
      toast(theirs?`${a.name} и ${b.name} поменялись сменами`:from.r===to.r?`Смена ${a.name} перенесена`:`Смена ${a.name} → ${b.name}`);
    }
    grid.addEventListener('click',e=>{
      const x=e.target.closest('.sg-x');if(x){e.stopPropagation();return removePerson(+x.dataset.remove);}
      const cell=e.target.closest('td.sg-c');if(cell)return select(+cell.dataset.r,cell.dataset.day);
      const head=e.target.closest('thead th.sg-day');if(head){sel=null;clearSel();side={kind:'day',day:head.dataset.day};scroll.go(head.dataset.day);renderPanel();return;}
      const name=e.target.closest('tbody th.sg-name');if(name){sel=null;clearSel();side={kind:'person',r:+name.dataset.r};renderPanel();}
    });

    // ─── настройки дня и человека ───
    // Убрать человека из графика (уволился): его смены и выходные удаляются из черновика.
    function removePerson(r){
      const p=person(r);if(!p)return;
      if(!confirm(`Убрать ${p.name} из графика?`))return;
      D().people.splice(r,1);
      for(const key of ['cells','off','off_auto','wish_off','wish_starts'])delete D()[key][p.name];
      side=null;sel=null;clearSel();changed();renderPanel();
    }
    q('.re-day').addEventListener('change',e=>{
      const t=e.target,day=focusOf().day;if(!day)return;
      const s=D().days[day]||(D().days[day]={});
      if(t.dataset.dayField){const n=parseInt(t.value,10);if(Number.isFinite(n)&&n>=0)s[t.dataset.dayField]=n;else delete s[t.dataset.dayField];}
      // Явная отметка хранится, только если она расходится с календарём России.
      if('holiday' in t.dataset){if(t.checked===!!cur.holidays?.[day])delete s.holiday;else s.holiday=t.checked;}
      if('event' in t.dataset){const v=t.value.trim();if(v)D().events[day]=v;else delete D().events[day];}
      if(!Object.keys(s).length)delete D().days[day];
      changed();renderPanel();
    });
    q('.re-person').addEventListener('change',e=>{
      const t=e.target,f=focusOf(),p=f.r!=null?person(f.r):null;if(!p)return;
      if('student' in t.dataset)p.student=t.checked;
      if('starts' in t.dataset){const hours=[...new Set(t.value.split(/[^0-9]+/).filter(Boolean).map(Number).filter(h=>h>=6&&h<=22))];
        if(hours.length)D().wish_starts[p.name]=hours;else delete D().wish_starts[p.name];}
      changed();renderPanel();
    });
    q('.re-person').addEventListener('click',e=>{
      const b=e.target.closest('button'),f=focusOf();if(!b||f.r==null)return;
      const list=D().people,r=f.r;
      if(b.dataset.move){const to=r+(+b.dataset.move);if(to<0||to>=list.length)return;[list[r],list[to]]=[list[to],list[r]];side={kind:'person',r:to};sel=null;clearSel();changed();renderPanel();}
      if('remove' in b.dataset)removePerson(r);
    });

    // Сколько сотрудников уже записали обязательные выходные в Telegram.
    async function refreshOffs(){
      if(!cur)return;
      try{offsInfo=await api('offs',{id:cur.id});}catch(e){offsInfo=null;}
      renderState();renderAsks();
      if(!showPick())renderGrid();
    }
    // Что люди записали в Telegram: видно до сборки, в таблице — пунктиром, здесь — списком.
    const WD=['пн','вт','ср','чт','пт','сб','вс'];
    function daysText(list){
      const by={};list.forEach(d=>{const x=G.at(d);(by[x.getMonth()]=by[x.getMonth()]||[]).push(x.getDate());});
      return Object.entries(by).map(([m,ds])=>`${ds.join(', ')} ${G.MONTHS[m]}`).join(', ');
    }
    function renderAsks(){
      const box=q('.re-asks'),req=offsInfo?.requests||{},desired=offsInfo?.desired||{},wd=offsInfo?.weekdays||{},pt=offsInfo?.patterns||{},times=offsInfo?.time||{};
      const names=D().people.map(p=>p.name).filter(n=>req[n]?.length||desired[n]?.length||wd[n]?.length||pt[n]||times[n]?.length);
      box.hidden=!names.length;
      box.innerHTML=names.length?`<h4>Пожелания к графику</h4>${names.map(n=>`<p>${esc(n)} — ${[
        req[n]?.length?`Обязательные: ${esc(daysText(req[n]))}`:'',
        desired[n]?.length?`Желательные: ${esc(daysText(desired[n]))}`:'',wd[n]?.length?`<small>по ${wd[n].map(d=>WD[d]).join(', ')}</small>`:'',
        pt[n]?`<small>график ${esc(pt[n])}</small>`:'',offsInfo?.over?.[n]?`<small class="re-over">больше ${offsInfo.over[n]} — согласовать</small>`:'',
        ...(times[n]||[]).map(t=>`<small>${esc(t)}</small>`)].filter(Boolean).join(' · ')}</p>`).join('')}
        ${offsInfo?.fresh?'<p><small>Есть новые: «Собрать заново» учтёт их.</small></p>':''}`:'';
    }
    document.addEventListener('visibilitychange',()=>{if(!document.hidden&&cur&&!root.closest('[hidden]'))refreshOffs();});
    q('.re-ask').onclick=async()=>{
      if(!cur||!confirm('Отправить сотрудникам просьбу о пожеланиях к графику? Получат те, кто ещё не ответил.'))return;
      busy=true;renderState();error('');
      try{offsInfo=await api('ask_offs',{id:cur.id});toast(`Отправлено: ${offsInfo.sent}`);}
      catch(e){failed(e);}finally{busy=false;renderState();}
    };
    q('.re-check-offs').onclick=async()=>{
      if(!cur||busy||conflict||!D().people.length)return;
      const {id,start,end}=cur;
      if(!confirm(`Отправить сотрудникам графика на ${G.period(start,end)} их пожелания по выходным для проверки?`))return;
      busy=true;renderState();error('');
      try{
        if(!(await flush())||cur?.id!==id)return;
        toast('Отправляю сообщение…',true);
        const {sent,total}=await api('check_offs',{id,revision:cur.revision},90000);
        toast(total?`Отправлено: ${sent} из ${total}`:'В графике нет сотрудников, которым можно отправить сообщение.');
      }catch(e){toast('');error(e.status?e.message:'Не удалось подтвердить отправку.');}
      finally{busy=false;renderState();}
    };

    // ─── сборка и публикация ───
    q('.re-build').onclick=async()=>{
      const filled=Object.keys(D().cells).length;
      if(filled&&!confirm('Собрать заново? Варианты и смены в таблице заменятся, выходные, поставленные вручную, останутся.'))return;
      if(!(await flush()))return;
      busy=true;renderState();error('');toast('Собираю варианты…',true);
      try{const data=await api('build',{id:cur.id,revision:cur.revision,draft:D()},120000);accept(data,true);sel=null;forcePick=false;renderAll();refreshOffs();toast('');}
      catch(e){failed(e);toast('');}finally{busy=false;renderState();}
    };
    q('.re-publish').onclick=async()=>{
      if(!(await flush()))return;
      const problems=cur.check?.problems||0;
      if(!confirm(problems?`Нарушений: ${problems}. Всё равно опубликовать?`:'Опубликовать?'))return;
      busy=true;renderState();error('');const version=edits;
      try{const data=await api('publish',{id:cur.id,revision:cur.revision});
        // Если всё же что-то изменилось за время запроса, локальный черновик остаётся и уйдёт следующим сохранением.
        accept(data,version===edits);renderAll();
        toast(data.notified?`Опубликовано. Сообщение отправлено: ${data.notified}`:'Опубликовано');}
      catch(e){failed(e);}finally{busy=false;renderState();}
    };

    // ─── создание периода ───
    q('.re-delete').onclick=async()=>{
      if(!cur||cur.published_at||!confirm('Удалить черновик?'))return;
      clearTimeout(timer);
      try{await api('delete',{id:cur.id});periods=periods.filter(p=>p.id!==cur.id);cur=null;edits=saved=0;q('.re-editor').hidden=true;await list();}
      catch(e){failed(e);}
    };
    async function createPeriod(start,end,done){
      if(!(await flush()))return;
      const buttons=[q('.re-new')];buttons.forEach(b=>b.disabled=true);
      toast('Собираю варианты…',true);
      try{const data=await api('create',{restaurant_id:placeId,start,end},120000);team=[];done?.();await list(data.id);toast('');}
      catch(err){failed(err);toast('');}finally{buttons.forEach(b=>b.disabled=false);}
    }
    // Один клик: следующий период по порядку, даты вводить не нужно. «Другие даты» — на всякий случай.
    q('.re-new').onclick=()=>{const [a,b]=suggested();createPeriod(a,b);};
    // Даты графика — сама дата в заголовке: нажал, поменял, сохранил. Черновик остаётся, лишние дни отбрасываются.
    q('.re-title').onclick=()=>{
      if(!cur||cur.published_at)return;
      const f=q('.re-create');f.start.value=cur.start;f.end.value=cur.end;f.hidden=!f.hidden;if(!f.hidden)f.start.focus();
    };
    q('.re-cancel').onclick=()=>{q('.re-create').hidden=true;};
    q('.re-create').onsubmit=async e=>{
      e.preventDefault();const f=e.target;
      if(!cur||(f.start.value===cur.start&&f.end.value===cur.end)){f.hidden=true;return;}
      const lost=cur.days.filter(d=>d<f.start.value||d>f.end.value).length;
      if(lost&&Object.keys(D().cells).length&&!confirm(`Дней вне новых дат: ${lost}. Их смены пропадут, варианты придётся собрать заново. Продолжить?`))return;
      if(!(await flush()))return;
      busy=true;renderState();error('');
      try{const data=await api('dates',{id:cur.id,revision:cur.revision,start:f.start.value,end:f.end.value});
        accept(data,true);sel=null;side=null;forcePick=false;f.hidden=true;renderAll();refreshOffs();}
      catch(err){failed(err);}finally{busy=false;renderState();}
    };
    // Ресторан выбирает заголовок страницы (только у владельца нескольких ресторанов).
    async function setPlace(id){
      if(!id||id===placeId||!(await flush()))return;
      placeId=id;cur=null;periods=[];q('.re-editor').hidden=true;list();
    }
    addEventListener('beforeunload',e=>{if(cur&&edits!==saved){e.preventDefault();e.returnValue='';}});
    addEventListener('resize',()=>{if(cur&&!q('.re-editview').hidden)sizeGrid();});
    list();
    root._roster={refresh:(fresh,id)=>{if(fresh?.length)places=fresh;if(id&&id!==placeId)setPlace(id);else if(!cur)list();},setPlace};
    return root._roster;
  }
  window.RosterEditor={open};
})();
