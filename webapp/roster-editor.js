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
    if(root._roster)return root._roster.refresh(opts.places);
    let places=opts.places||[],placeId=places[0]?.id,periods=[],cur=null,team=[],sel=null,side=null;
    let edits=0,saved=0,saving=false,timer=null,busy=false;
    // Отмена: снимки черновика до каждого изменения.
    let undo=[],redo=[],last=null,forcePick=false,offsInfo=null,note='';
    root.innerHTML=`<div class="re sg">
      <div class="re-guide" role="status"></div>
      <div class="re-head">
        <div class="re-period">
          <button type="button" class="re-step" data-step="-1" aria-label="Предыдущий график">‹</button>
          <div class="re-heading"><strong class="re-title"></strong><span class="re-state" role="status"></span></div>
          <button type="button" class="re-step" data-step="1" aria-label="Следующий график">›</button>
          <select class="re-jump" aria-label="Все графики" hidden></select>
          <select class="re-place" aria-label="Ресторан" hidden></select>
          <button type="button" class="re-new"></button>
          <button type="button" class="re-dates">Другие даты</button>
          <button type="button" class="re-delete is-off">Удалить черновик</button>
        </div>
        <div class="re-actions"><button type="button" class="re-ask" disabled>Запросить выходные</button><button type="button" class="re-build" disabled>Собрать заново</button><button type="button" class="btn re-publish" disabled>Опубликовать</button></div>
      </div>
      <form class="re-create" hidden><strong>Другие даты</strong><label>с <input type="date" name="start" required></label><label>по <input type="date" name="end" required></label>
        <button class="btn">Создать</button><button type="button" class="re-cancel">Отмена</button></form>
      <p class="re-error" role="alert"></p>
      <p class="re-empty" hidden>Графиков пока нет.</p>
      <div class="re-editor" hidden>
        <div class="re-pickview" hidden></div>
        <div class="re-editview">
          <div class="re-main">
            <div class="re-tools"><button type="button" class="re-all">Все варианты</button><div class="re-history"><button type="button" class="re-undo" title="Отменить (Ctrl+Z)" disabled>↶ Отменить</button><button type="button" class="re-redo" title="Вернуть (Ctrl+Shift+Z)" disabled>↷</button></div></div>
            <section class="sg-sheet re-sheet"><div class="sg-scroll"><table class="sg-edit"></table></div></section></div>
          <aside class="re-aside">
            <section class="re-card re-cell" aria-live="polite"></section>
            <section class="re-card re-side" aria-label="Настройки"></section>
            <section class="re-card re-check" aria-label="Проверка правил"><h3>Проверка <span class="re-badge"></span></h3><div class="re-report"></div></section>
          </aside>
        </div>
      </div></div>`;
    const q=s=>root.querySelector(s),grid=q('.sg-edit'),sc=grid.parentElement,sheet=q('.re-sheet');
    const scroll=G.scroller(grid,sc,{sheet});G.hover(grid);
    const error=t=>{q('.re-error').textContent=t||'';};

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
      q('.re-place').hidden=places.length<2;
      q('.re-place').innerHTML=places.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');q('.re-place').value=placeId;
      const list=ordered(),i=list.findIndex(p=>p.id===cur?.id);
      q('.re-jump').hidden=list.length<3;
      q('.re-jump').innerHTML=list.map(p=>`<option value="${esc(p.id)}">${esc(G.period(p.start,p.end))} · ${stateOf(p)}</option>`).join('');
      if(cur)q('.re-jump').value=cur.id;
      root.querySelectorAll('.re-step').forEach(b=>{b.hidden=list.length<2;b.disabled=i<0||!list[i+(+b.dataset.step)];});
      q('.re-empty').hidden=!!periods.length;
      const next=suggested();q('.re-new').textContent=`+ График ${G.period(next[0],next[1])}`;
      if(!cur){q('.re-title').textContent='';q('.re-state').textContent='';}
      q('.re-ask').disabled=q('.re-build').disabled=q('.re-publish').disabled=!cur;
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
      try{const data=await api('open',{id});team=data.team||[];note='';accept(data,true);sel=null;side=null;renderAll();refreshOffs();
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
      if(!cur||root.closest('[hidden]')||!(e.ctrlKey||e.metaKey)||e.key.toLowerCase()!=='z'&&e.key.toLowerCase()!=='я')return;
      if(e.target.matches('input,textarea'))return;
      e.preventDefault();if(e.shiftKey)travel(redo,undo);else travel(undo,redo);
    });
    async function save(){
      clearTimeout(timer);
      if(saving||!cur||edits===saved)return;
      saving=true;const version=edits;renderState();
      try{const data=await api('save',{id:cur.id,revision:cur.revision,draft:D()});saved=version;
        accept(data,version===edits);if(version===edits)renderGrid();}
      catch(e){failed(e);if(e.status===409)saved=edits;}
      finally{saving=false;renderState();if(edits!==saved&&!q('.re-error').textContent)timer=setTimeout(save,900);}
    }
    // Перед сменой периода, сборкой и публикацией дописываем последнее изменение.
    async function flush(){
      if(!cur)return true;
      while(saving)await new Promise(r=>setTimeout(r,150));
      if(edits!==saved)await save();
      return edits===saved;
    }

    // ─── отрисовка ───
    function renderState(){
      if(!cur)return;
      const pending=edits!==saved;
      const offs=offsInfo?.total?` · выходные: ${offsInfo.answered} из ${offsInfo.total}${offsInfo.fresh?', есть новые':''}`:'';
      q('.re-state').textContent=saving?'Сохраняю…':pending?'Есть несохранённые правки':`Сохранено · ${stateOf(cur)}${offs}${note?' · '+note:''}`;
      q('.re-publish').textContent=cur.published_at?(cur.changed||pending?'Опубликовать правки':'Опубликован'):'Опубликовать';
      q('.re-publish').disabled=busy||showPick()||(!!cur.published_at&&!cur.changed&&!pending);q('.re-build').disabled=busy;q('.re-ask').disabled=busy;
      q('.re-undo').disabled=!undo.length;q('.re-redo').disabled=!redo.length;
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
      return d.people.map(p=>({name:p.name,cells:d.cells[p.name]||{},off:d.off[p.name]||[]}));
    }
    function renderGrid(){
      const left=sc.scrollLeft;
      grid.innerHTML=G.table({days:cur.days,people:people(),events:D().events,holidays:holidayLabels(),today:iso(new Date()),scrolls:true,edit:true,need,flagged:cur.check?.days});
      scroll.size();sc.scrollLeft=left;
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
        box.querySelector(`[data-id="${v.id}"] table`).innerHTML=G.table({days:cur.days,people,events:D().events,holidays:holidayLabels(),need});
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
      if(r>=0)select(r,it.day);else{side={kind:'day',day:it.day};renderSide();}
    });
    // Два вида одного графика: сначала все варианты сразу, после выбора — правка выбранного.
    const showPick=()=>!!cur&&(cur.draft.variants||[]).length>0&&(forcePick||!cur.draft.picked);
    function applyView(){
      const pickMode=showPick();
      q('.re-pickview').hidden=!pickMode;q('.re-editview').hidden=pickMode;
      q('.re-all').hidden=!(D().variants||[]).length;
      if(pickMode)renderPick();else{renderGrid();renderCell();renderSide();}
      renderState();
    }
    function renderAll(){
      q('.re-editor').hidden=false;q('.re-title').textContent=G.period(cur.start,cur.end);
      applyView();
    }
    q('.re-all').onclick=()=>{forcePick=true;applyView();};

    // ─── клетка ───
    function renderCell(){
      // Карточка стоит на месте всегда; без выбранной клетки её кнопки неактивны.
      const box=q('.re-cell'),has=!!sel&&!!person(sel.r),dis=has?'':' disabled';
      const p=has?person(sel.r):null,v=has?G.parse(D().cells[p.name]?.[sel.day]||''):null;
      const off=has&&(D().off[p.name]||[]).includes(sel.day);
      const start=v?.start;
      box.innerHTML=`<h3>${has?esc(p.name):'Смена'}<small>${has?esc(G.dayName(sel.day)):''}</small></h3>
        <p class="re-label">Приход</p>
        <div class="re-hours">${STARTS.map(h=>`<button type="button" data-set="${h}"${dis} aria-pressed="${start===h&&v.end==null}">${h}</button>`).join('')}</div>
        <div class="re-line"><label class="re-range">Интервал <input${dis} value="${v&&v.end!=null?esc(start+'-'+v.end):''}" placeholder="9-17" inputmode="numeric"></label>
          <button type="button" data-set=""${dis} class="re-clear" aria-pressed="${has&&(!v||start==null)}">Не работает</button></div>
        <div class="re-flags"><button type="button" data-off${dis} aria-pressed="${off}"><i class="sg-s off">вых</i>Обязательный выходной</button>
          <button type="button" data-note${dis} aria-pressed="${!!v?.note}"><i class="sg-s note">★</i>Необычное</button></div>`;
    }
    function applyCell(raw){
      const p=person(sel.r),old=G.parse(D().cells[p.name]?.[sel.day]);
      if(raw===''){setCell(p.name,sel.day,old?.note?'*':'');}
      else{
        const v=G.parse(raw);if(!v||v.start==null||v.start<6||v.start>22||(v.end!=null&&!(v.end>v.start&&v.end<=24))){error(`Не понял смену «${raw}». Пиши час прихода (9) или интервал (9-17).`);return;}
        error('');setCell(p.name,sel.day,v.start+(v.end!=null?'-'+v.end:'')+(old?.note||v.note?'*':''));
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
    function select(r,day,focus=true){
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
    let typed='',typedAt=0;
    grid.addEventListener('keydown',e=>{
      const cell=e.target.closest('td.sg-c');
      if(!cell){if((e.key==='Enter'||e.key===' ')&&e.target.closest('th')){e.preventDefault();e.target.click();}return;}
      const r=+cell.dataset.r,i=cur.days.indexOf(cell.dataset.day);
      const move=(dr,di)=>{const nr=Math.min(Math.max(r+dr,0),D().people.length-1),ni=Math.min(Math.max(i+di,0),cur.days.length-1);e.preventDefault();select(nr,cur.days[ni]);};
      if(e.key==='ArrowRight')return move(0,1);if(e.key==='ArrowLeft')return move(0,-1);
      if(e.key==='ArrowDown')return move(1,0);if(e.key==='ArrowUp')return move(-1,0);
      if(!sel||sel.r!==r||sel.day!==cell.dataset.day)select(r,cell.dataset.day,false);
      const key=e.key.toLowerCase();
      if(key==='delete'||key==='backspace'){e.preventDefault();typed='';return applyCell('');}
      if(key==='в'||key==='d'){e.preventDefault();return toggleOff();}
      if(key==='*'){e.preventDefault();return toggleNote();}
      if(/^[0-9-]$/.test(key)){
        e.preventDefault();typed=Date.now()-typedAt<1200?typed+key:key;typedAt=Date.now();
        // «1» ждёт вторую цифру; одна цифра больше 2 или полный интервал — сразу в клетку.
        if(/^(?:[3-9]|1\d|2[0-2])(?:-(?:1\d|2[0-4]|[3-9]))?$/.test(typed)&&!/-$/.test(typed)){
          const v=typed;clearTimeout(grid._commit);grid._commit=setTimeout(()=>{applyCell(v);typed='';},typed.includes('-')||typed.length>=2?250:700);
        }
        return;
      }
      if(e.key==='Enter'){e.preventDefault();if(typed){applyCell(typed);typed='';}else q('.re-cell button')?.focus();}
      if(e.key==='Escape'){sel=null;grid.querySelectorAll('.sg-sel').forEach(x=>x.classList.remove('sg-sel'));renderCell();}
    });
    grid.addEventListener('click',e=>{
      const x=e.target.closest('.sg-x');if(x){e.stopPropagation();return removePerson(+x.dataset.remove);}
      const cell=e.target.closest('td.sg-c');if(cell)return select(+cell.dataset.r,cell.dataset.day);
      const head=e.target.closest('thead th.sg-day');if(head){side={kind:'day',day:head.dataset.day};scroll.go(head.dataset.day);renderSide();return;}
      const name=e.target.closest('tbody th.sg-name');if(name){side={kind:'person',r:+name.dataset.r};renderSide();}
    });

    // ─── настройки дня и человека ───
    // Убрать человека из графика (уволился): его смены и выходные удаляются из черновика.
    function removePerson(r){
      const p=person(r);if(!p)return;
      if(!confirm(`Убрать ${p.name} из графика?`))return;
      D().people.splice(r,1);
      for(const key of ['cells','off','off_auto','wish_off','wish_starts'])delete D()[key][p.name];
      side=null;sel=null;changed();renderSide();renderCell();
    }
    function renderSide(){
      const box=q('.re-side');
      if(!side){const today=iso(new Date());side={kind:'day',day:cur.days.includes(today)?today:cur.days[0]};}
      // Добавить можно только подтверждённого сотрудника: по списку, а не по имени.
      const addable=team.filter(t=>!D().people.some(p=>p.link===t.id));
      const adder=addable.length?`<form class="re-add"><label>Сотрудник не в графике<select>${addable.map(t=>`<option value="${esc(t.id)}">${esc(t.report_name)}</option>`).join('')}</select></label><button class="btn btn-ghost">Добавить</button></form>`:'';
      if(side?.kind==='person'&&person(side.r)){
        const p=person(side.r),wish=(D().wish_starts[p.name]||[]).join(', ');
        box.innerHTML=`<h3>${esc(p.name)}</h3>
          <label class="re-check-row"><input type="checkbox" data-student ${p.student?'checked':''}> Учёба</label>
          <label>Удобный приход<input data-starts value="${esc(wish)}" placeholder="например, 9, 10"></label>
          <div class="re-row"><button type="button" data-move="-1">Выше</button><button type="button" data-move="1">Ниже</button><button type="button" data-remove>Убрать из графика</button></div>${adder}`;
        return;
      }
      if(side?.kind==='day'){
        const day=side.day,s=D().days[day]||{},event=D().events[day]||'';
        const opening=s.openers??(money(day)?2:1);
        box.innerHTML=`<h3>${esc(G.dayName(day))}<small>${esc(cur.holidays?.[day]||'')}</small></h3>
          <div class="re-fields"><label>Людей<input type="number" min="0" max="60" data-day-field="count" value="${need(day)}"></label>
          <label>Открывают<input type="number" min="0" max="10" data-day-field="openers" value="${opening}"></label>
          <label>Закрывают<input type="number" min="0" max="10" data-day-field="closers" value="${s.closers??3}"></label></div>
          <label class="re-check-row"><input type="checkbox" data-holiday ${holiday(day)?'checked':''}> Праздник</label>
          <label>Событие дня<input list="re-events" data-event maxlength="40" value="${esc(event)}" placeholder="Генуборка, собрание…"></label>
          <datalist id="re-events">${EVENTS.map(x=>`<option value="${x}">`).join('')}</datalist>${adder}`;
        return;
      }
      box.innerHTML=adder;
    }
    q('.re-side').addEventListener('change',e=>{
      const t=e.target;
      if(side?.kind==='day'){
        const day=side.day,s=D().days[day]||(D().days[day]={});
        if(t.dataset.dayField){const n=parseInt(t.value,10);if(Number.isFinite(n)&&n>=0)s[t.dataset.dayField]=n;else delete s[t.dataset.dayField];}
        // Явная отметка хранится, только если она расходится с календарём России.
        if('holiday' in t.dataset){if(t.checked===!!cur.holidays?.[day])delete s.holiday;else s.holiday=t.checked;}
        if('event' in t.dataset){const v=t.value.trim();if(v)D().events[day]=v;else delete D().events[day];}
        if(!Object.keys(s).length)delete D().days[day];
        changed();renderSide();
      }
      if(side?.kind==='person'){
        const p=person(side.r);
        if('student' in t.dataset)p.student=t.checked;
        if('starts' in t.dataset){const hours=[...new Set(t.value.split(/[^0-9]+/).filter(Boolean).map(Number).filter(h=>h>=6&&h<=22))];
          if(hours.length)D().wish_starts[p.name]=hours;else delete D().wish_starts[p.name];}
        changed();renderSide();
      }
    });
    q('.re-side').addEventListener('click',e=>{
      const b=e.target.closest('button');if(!b||side?.kind!=='person')return;
      const list=D().people,r=side.r;
      if(b.dataset.move){const to=r+(+b.dataset.move);if(to<0||to>=list.length)return;[list[r],list[to]]=[list[to],list[r]];side.r=to;sel=null;changed();renderSide();renderCell();}
      if('remove' in b.dataset)removePerson(r);
    });
    q('.re-side').addEventListener('submit',e=>{
      if(!e.target.matches('.re-add'))return;e.preventDefault();
      const t=team.find(x=>x.id===e.target.querySelector('select').value);
      if(!t||D().people.some(p=>p.link===t.id))return;
      D().people.push({name:t.report_name,student:false,link:t.id});error('');changed();renderSide();
    });

    // Сколько сотрудников уже записали обязательные выходные в Telegram.
    async function refreshOffs(){
      if(!cur)return;
      try{offsInfo=await api('offs',{id:cur.id});}catch(e){offsInfo=null;}
      renderState();
    }
    document.addEventListener('visibilitychange',()=>{if(!document.hidden&&cur&&!root.closest('[hidden]'))refreshOffs();});
    q('.re-ask').onclick=async()=>{
      if(!cur||!confirm('Отправить сотрудникам просьбу выбрать обязательные выходные? Получат те, кто ещё не ответил.'))return;
      busy=true;renderState();error('');
      try{offsInfo=await api('ask_offs',{id:cur.id});note=`отправлено: ${offsInfo.sent}`;}
      catch(e){failed(e);}finally{busy=false;renderState();}
    };

    // ─── сборка и публикация ───
    q('.re-build').onclick=async()=>{
      const filled=Object.keys(D().cells).length;
      if(filled&&!confirm('Собрать заново? Варианты и смены в таблице заменятся, выходные, поставленные вручную, останутся.'))return;
      if(!(await flush()))return;
      busy=true;renderState();error('');q('.re-state').textContent='Собираю варианты…';
      try{const data=await api('build',{id:cur.id,revision:cur.revision,draft:D()},120000);accept(data,true);sel=null;note='';forcePick=false;renderAll();refreshOffs();}
      catch(e){failed(e);}finally{busy=false;renderState();}
    };
    q('.re-publish').onclick=async()=>{
      if(!(await flush()))return;
      const problems=cur.check?.problems||0;
      if(!confirm(problems?`Нарушений: ${problems}. Всё равно опубликовать?`:'Опубликовать?'))return;
      busy=true;renderState();error('');
      try{const data=await api('publish',{id:cur.id,revision:cur.revision});accept(data,true);renderAll();
        q('.re-state').textContent=data.notified?`Опубликован · отправлено: ${data.notified}`:'Опубликован';}
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
      const buttons=[q('.re-new'),q('.re-dates')];buttons.forEach(b=>b.disabled=true);
      if(cur)q('.re-state').textContent='Собираю варианты…';else q('.re-title').textContent='Собираю варианты…';
      try{const data=await api('create',{restaurant_id:placeId,start,end},120000);team=[];done?.();await list(data.id);}
      catch(err){failed(err);if(cur)renderState();else q('.re-title').textContent='';}finally{buttons.forEach(b=>b.disabled=false);}
    }
    // Один клик: следующий период по порядку, даты вводить не нужно. «Другие даты» — на всякий случай.
    q('.re-new').onclick=()=>{const [a,b]=suggested();createPeriod(a,b);};
    q('.re-dates').onclick=()=>{const [a,b]=suggested();const f=q('.re-create');f.start.value=a;f.end.value=b;f.hidden=false;f.start.focus();};
    q('.re-cancel').onclick=()=>{q('.re-create').hidden=true;};
    q('.re-create').onsubmit=e=>{e.preventDefault();const f=e.target;createPeriod(f.start.value,f.end.value,()=>{f.hidden=true;});};
    q('.re-place').onchange=async()=>{if(!(await flush()))return;placeId=q('.re-place').value;cur=null;list();};
    addEventListener('beforeunload',e=>{if(cur&&edits!==saved){e.preventDefault();e.returnValue='';}});
    addEventListener('resize',()=>{if(cur)scroll.size();});
    list();
    root._roster={refresh:fresh=>{if(fresh?.length)places=fresh;if(!cur)list();}};
    return root._roster;
  }
  window.RosterEditor={open};
})();
