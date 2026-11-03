// Составление графика на сайте ресторана: период, люди, выходные и пожелания, настройки дня,
// сборка черновика программой, ручная правка с проверкой правил и публикация для команды.
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
  const daysOf=(a,b)=>{const out=[];for(let d=G.at(a);d<=G.at(b);d.setDate(d.getDate()+1))out.push(iso(d));return out;};

  function open(root,opts){
    // Повторное открытие раздела не пересоздаёт редактор: правки и слушатели остаются.
    if(root._roster)return root._roster.refresh(opts.places);
    let places=opts.places||[],placeId=places[0]?.id,periods=[],cur=null,team=[],sel=null,side=null;
    let edits=0,saved=0,saving=false,timer=null,busy=false;
    // Отмена: снимки черновика до каждого изменения. Режим: что ставит нажатие на клетку.
    let undo=[],redo=[],last=null,mode='shift',painting=null;
    root.innerHTML=`<div class="re sg">
      <div class="re-head">
        <div class="re-period">
          <button type="button" class="re-step" data-step="-1" aria-label="Предыдущий график">‹</button>
          <div class="re-heading"><strong class="re-title">График</strong><span class="re-state" role="status"></span></div>
          <button type="button" class="re-step" data-step="1" aria-label="Следующий график">›</button>
          <select class="re-jump" aria-label="Все графики" hidden></select>
          <select class="re-place" aria-label="Ресторан" hidden></select>
          <button type="button" class="re-new">+ Новый график</button>
          <button type="button" class="re-delete" hidden>Удалить черновик</button>
        </div>
        <div class="re-guide" role="status" hidden></div>
        <div class="re-actions"><button type="button" class="re-build" hidden>Собрать черновик</button><button type="button" class="btn re-publish" hidden>Опубликовать</button></div>
      </div>
      <form class="re-create" hidden><strong>Новый график</strong><label>с <input type="date" name="start" required></label><label>по <input type="date" name="end" required></label>
        <button class="btn">Создать</button><button type="button" class="re-cancel">Отмена</button></form>
      <p class="re-error" role="alert"></p>
      <p class="re-empty" hidden>Графиков пока нет.</p>
      <div class="re-editor" hidden>
        <div class="re-main">
          <div class="re-tools"><div class="re-modes" role="radiogroup" aria-label="Что ставить по нажатию">
              <button type="button" role="radio" data-mode="shift" aria-checked="true">Смены</button>
              <button type="button" role="radio" data-mode="off" aria-checked="false"><i class="sg-s off">вых</i>Выходные</button>
              <button type="button" role="radio" data-mode="wish" aria-checked="false"><i class="re-wish-mark"></i>Пожелания</button></div>
            <div class="re-history"><button type="button" class="re-undo" title="Отменить (Ctrl+Z)" disabled>↶ Отменить</button><button type="button" class="re-redo" title="Вернуть (Ctrl+Shift+Z)" disabled>↷</button></div></div>
          <section class="sg-sheet re-sheet"><div class="sg-scroll"><table class="sg-edit"></table></div></section></div>
        <aside class="re-aside">
          <section class="re-card re-cell" aria-live="polite"></section>
          <section class="re-card re-side" aria-label="Настройки"></section>
          <section class="re-card re-check" aria-label="Проверка правил"><h3>Проверка <span class="re-badge"></span></h3><div class="re-report"></div></section>
        </aside>
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
    function renderPeriods(){
      q('.re-place').hidden=places.length<2;
      q('.re-place').innerHTML=places.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');q('.re-place').value=placeId;
      const list=ordered(),i=list.findIndex(p=>p.id===cur?.id);
      q('.re-jump').hidden=list.length<3;
      q('.re-jump').innerHTML=list.map(p=>`<option value="${esc(p.id)}">${esc(G.period(p.start,p.end))} · ${stateOf(p)}</option>`).join('');
      if(cur)q('.re-jump').value=cur.id;
      root.querySelectorAll('.re-step').forEach(b=>{b.hidden=list.length<2;b.disabled=i<0||!list[i+(+b.dataset.step)];});
      q('.re-empty').hidden=!!periods.length;
      if(!cur){q('.re-title').textContent='';q('.re-state').textContent='';}
      q('.re-build').hidden=q('.re-publish').hidden=!cur;
      q('.re-delete').hidden=!cur||!!cur.published_at;
    }
    async function list(openId){
      error('');
      try{const data=await api('list',{restaurant_id:placeId});places=data.restaurants;placeId=data.restaurant_id;periods=data.periods;renderPeriods();
        const target=openId||periods[0]?.id;if(target)await load(target);else{cur=null;q('.re-editor').hidden=true;}}
      catch(e){failed(e);}
    }
    async function load(id){
      if(!(await flush()))return;
      try{const data=await api('open',{id});team=data.team||[];accept(data,true);sel=null;side=null;renderAll();
        const today=iso(new Date());scroll.go(cur.days.includes(today)?today:cur.days[0],false);}
      catch(e){failed(e);}
    }
    function accept(data,replace){
      const keep=cur&&cur.id===data.id&&!replace?cur.draft:null;
      if(cur?.id!==data.id){undo=[];redo=[];}
      cur={...data,days:daysOf(data.start,data.end),draft:keep||data.draft};
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
    const money=day=>[0,5,6].includes(G.at(day).getDay())||!!D().days[day]?.holiday;
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
      q('.re-state').textContent=saving?'Сохраняю…':pending?'Есть несохранённые правки':`Сохранено · ${stateOf(cur)}`;
      q('.re-publish').textContent=cur.published_at?(cur.changed||pending?'Опубликовать правки':'Опубликован'):'Опубликовать';
      q('.re-publish').disabled=busy||(!!cur.published_at&&!cur.changed&&!pending);q('.re-build').disabled=busy;
      q('.re-undo').disabled=!undo.length;q('.re-redo').disabled=!redo.length;
      renderGuide();
    }
    // Подсказка «что дальше»: менеджеру не нужно помнить порядок работы.
    function renderGuide(){
      const box=q('.re-guide');if(!cur){box.hidden=true;return;}
      const filled=Object.keys(D().cells).length,problems=cur.check?.problems||0,pending=edits!==saved;
      const offs=Object.keys(D().off).length+Object.keys(D().wish_off).length;
      let step,text;
      if(!D().people.length){step=1;text='Добавь людей';}
      else if(!filled){step=offs?2:1;text='';}
      else if(problems){step=3;text=`Нарушений: ${problems}`;}
      else if(!cur.published_at||cur.changed||pending){step=4;text=cur.published_at?'Есть неопубликованные правки':'';}
      else{step=5;text='Опубликован';}
      const steps=['Люди и выходные','Черновик','Правка','Публикация'];
      box.hidden=false;
      box.innerHTML=`<ol class="re-steps">${steps.map((name,i)=>`<li class="${i+1<step?'done':i+1===step?'now':''}">${name}</li>`).join('')}</ol><p>${esc(text)}</p>`;
    }
    function setMode(next){
      mode=next;root.querySelectorAll('[data-mode]').forEach(b=>b.setAttribute('aria-checked',String(b.dataset.mode===mode)));
      grid.classList.toggle('re-painting',mode!=='shift');
    }
    root.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>setMode(b.dataset.mode));
    function people(){
      const d=D();
      return d.people.map(p=>({name:p.name,cells:d.cells[p.name]||{},off:d.off[p.name]||[],wish_off:d.wish_off[p.name]||[]}));
    }
    function renderGrid(){
      const left=sc.scrollLeft;
      grid.innerHTML=G.table({days:cur.days,people:people(),events:D().events,today:iso(new Date()),scrolls:true,edit:true,need,flagged:cur.check?.days});
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
      q('.re-check').hidden=!Object.keys(D().cells).length;
      const items=cur.check?.items||[];
      const blocks=(cur.check?.text||'').split('\n\n').slice(1).filter(b=>!b.startsWith('Что не получилось:'));
      box.innerHTML=(items.length?`<ul class="re-problems">${items.map((it,i)=>`<li><button type="button" data-problem="${i}">${esc(it.text)}</button></li>`).join('')}</ul>`:'')+blocks.map(block=>{
        const lines=block.split('\n'),head=lines[0].endsWith(':')?lines.shift():null;
        const items=lines.filter(l=>l.startsWith('• ')),plain=lines.filter(l=>!l.startsWith('• '));
        return `${head?`<h4>${esc(head.slice(0,-1))}</h4>`:''}${plain.map(l=>`<p>${esc(l)}</p>`).join('')}${items.length?`<ul>${items.map(l=>`<li>${esc(l.slice(2))}</li>`).join('')}</ul>`:''}`;
      }).join('');
    }
    q('.re-report').addEventListener('click',e=>{
      const b=e.target.closest('[data-problem]');if(!b)return;
      const it=cur.check.items[+b.dataset.problem],r=it.name?D().people.findIndex(p=>p.name===it.name):-1;
      scroll.go(it.day);
      if(r>=0)select(r,it.day);else{side={kind:'day',day:it.day};renderSide();}
    });
    function renderAll(){
      q('.re-editor').hidden=false;q('.re-title').textContent=G.period(cur.start,cur.end);
      renderGrid();renderState();renderCell();renderSide();
    }

    // ─── клетка ───
    function renderCell(){
      const box=q('.re-cell');
      box.hidden=!sel||!person(sel.r);if(box.hidden)return;
      const p=person(sel.r),value=D().cells[p.name]?.[sel.day]||'',v=G.parse(value);
      const off=(D().off[p.name]||[]).includes(sel.day),wish=(D().wish_off[p.name]||[]).includes(sel.day);
      const start=v?.start;
      box.innerHTML=`<h3>${esc(p.name)}<small>${esc(G.dayName(sel.day))}</small></h3>
        <p class="re-label">Приход</p>
        <div class="re-hours">${STARTS.map(h=>`<button type="button" data-set="${h}" aria-pressed="${start===h&&v.end==null}">${h}</button>`).join('')}</div>
        <div class="re-line"><label class="re-range">Интервал <input value="${v&&v.end!=null?esc(start+'-'+v.end):''}" placeholder="9-17" inputmode="numeric"></label>
          <button type="button" data-set="" class="re-clear" aria-pressed="${!v||start==null}">Не работает</button></div>
        <div class="re-flags"><button type="button" data-off aria-pressed="${off}"><i class="sg-s off">вых</i>Обязательный выходной</button>
          <button type="button" data-note aria-pressed="${!!v?.note}"><i class="sg-s note">★</i>Необычное</button>
          <button type="button" data-wish aria-pressed="${wish}"><i class="re-wish-mark"></i>Просит выходной</button></div>`;
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
      // Обязательный выходной убирает смену, кроме отметки «необычное».
      if(on){const v=G.parse(D().cells[p.name]?.[sel.day]);setCell(p.name,sel.day,v?.note?'*':'');}
      changed();renderCell();
    }
    function toggleWish(){toggle('wish_off',person(sel.r).name,sel.day);changed();renderCell();}
    function select(r,day,focus=true){
      sel={r,day};side={kind:'day',day};
      grid.querySelectorAll('.sg-sel').forEach(e=>e.classList.remove('sg-sel'));
      const cell=grid.querySelector(`td.sg-c[data-r="${r}"][data-day="${day}"]`);cell?.classList.add('sg-sel');if(focus)cell?.focus();
      renderCell();renderSide();
    }
    q('.re-cell').addEventListener('click',e=>{
      const b=e.target.closest('button');if(!b||!sel)return;
      if('set' in b.dataset)applyCell(b.dataset.set);else if('note' in b.dataset)toggleNote();
      else if('off' in b.dataset)toggleOff();else if('wish' in b.dataset)toggleWish();
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
      if(key==='п'||key==='g'){e.preventDefault();return toggleWish();}
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
    // Режимы «Выходные» и «Пожелания»: нажатие ставит или снимает отметку, протяжка — на несколько дней.
    function paint(cell){
      const name=person(+cell.dataset.r).name,day=cell.dataset.day,key=painting.key;
      toggle(key,name,day,painting.on);
      if(key==='off'&&painting.on){const v=G.parse(D().cells[name]?.[day]);setCell(name,day,v?.note?'*':'');}
      if(key==='off')cell.innerHTML=painting.on?'<span class="sg-s off">вых</span>':'<span class="sg-e"></span>';
      else cell.classList.toggle('sg-wish',painting.on);
    }
    grid.addEventListener('pointerdown',e=>{
      const cell=e.target.closest('td.sg-c');if(!cell||mode==='shift'||e.button>0)return;
      e.preventDefault();cell.releasePointerCapture?.(e.pointerId);
      const key=mode==='off'?'off':'wish_off',name=person(+cell.dataset.r).name;
      painting={key,on:!(D()[key][name]||[]).includes(cell.dataset.day)};paint(cell);
    });
    grid.addEventListener('pointerover',e=>{const cell=e.target.closest('td.sg-c');if(painting&&cell)paint(cell);});
    addEventListener('pointerup',()=>{if(painting){painting=null;changed();}});
    grid.addEventListener('click',e=>{
      const cell=e.target.closest('td.sg-c');if(cell)return mode==='shift'?select(+cell.dataset.r,cell.dataset.day):undefined;
      const head=e.target.closest('thead th.sg-day');if(head){side={kind:'day',day:head.dataset.day};scroll.go(head.dataset.day);renderSide();return;}
      const name=e.target.closest('tbody th.sg-name');if(name){side={kind:'person',r:+name.dataset.r};renderSide();}
    });

    // ─── настройки дня и человека ───
    function renderSide(){
      const box=q('.re-side');
      const adder=`<form class="re-add"><label>Добавить человека<input list="re-team" maxlength="80" required></label>
        <datalist id="re-team">${team.filter(t=>!D().people.some(p=>p.name===t.report_name)).map(t=>`<option value="${esc(t.report_name)}">`).join('')}</datalist><button class="btn btn-ghost">Добавить</button></form>`;
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
        box.innerHTML=`<h3>${esc(G.dayName(day))}</h3>
          <div class="re-fields"><label>Людей<input type="number" min="0" max="60" data-day-field="count" value="${need(day)}"></label>
          <label>Открывают<input type="number" min="0" max="10" data-day-field="openers" value="${opening}"></label>
          <label>Закрывают<input type="number" min="0" max="10" data-day-field="closers" value="${s.closers??3}"></label></div>
          <label class="re-check-row"><input type="checkbox" data-holiday ${s.holiday?'checked':''}> Праздник</label>
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
        if('holiday' in t.dataset){if(t.checked)s.holiday=true;else delete s.holiday;}
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
      if('remove' in b.dataset){const p=list[r];if(!confirm(`Убрать ${p.name} из этого графика? Его смены в черновике удалятся.`))return;
        list.splice(r,1);for(const key of ['cells','off','wish_off','wish_starts'])delete D()[key][p.name];side=null;sel=null;changed();renderSide();renderCell();}
    });
    q('.re-side').addEventListener('submit',e=>{
      if(!e.target.matches('.re-add'))return;e.preventDefault();
      const name=e.target.querySelector('input').value.trim().replace(/\s+/g,' ');
      if(name.length<2)return;
      if(D().people.some(p=>p.name.toLowerCase()===name.toLowerCase())){error(`«${name}» уже в графике.`);return;}
      const link=team.find(t=>t.report_name===name);
      D().people.push({name,student:false,link:link?.id||null});error('');changed();renderSide();
    });

    // ─── сборка и публикация ───
    q('.re-build').onclick=async()=>{
      const filled=Object.keys(D().cells).length;
      if(filled&&!confirm('Пересобрать? Смены в таблице заменятся.'))return;
      if(!(await flush()))return;
      busy=true;renderState();error('');q('.re-state').textContent='Собираю…';
      try{const data=await api('build',{id:cur.id,revision:cur.revision,draft:D()},120000);accept(data,true);sel=null;renderAll();}
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
      try{await api('delete',{id:cur.id});periods=periods.filter(p=>p.id!==cur.id);cur=null;edits=saved=0;q('.re-editor').hidden=true;q('.re-guide').hidden=true;await list();}
      catch(e){failed(e);}
    };
    q('.re-new').onclick=()=>{const [a,b]=nextHalf();const f=q('.re-create');f.start.value=a;f.end.value=b;f.hidden=false;f.start.focus();};
    q('.re-cancel').onclick=()=>{q('.re-create').hidden=true;};
    q('.re-create').onsubmit=async e=>{
      e.preventDefault();const f=e.target;
      if(!(await flush()))return;
      try{const data=await api('create',{restaurant_id:placeId,start:f.start.value,end:f.end.value});f.hidden=true;team=[];
        await list(data.id);}
      catch(err){failed(err);}
    };
    q('.re-place').onchange=async()=>{if(!(await flush()))return;placeId=q('.re-place').value;cur=null;list();};
    addEventListener('beforeunload',e=>{if(cur&&edits!==saved){e.preventDefault();e.returnValue='';}});
    addEventListener('resize',()=>{if(cur)scroll.size();});
    list();
    root._roster={refresh:fresh=>{if(fresh?.length)places=fresh;if(!cur)list();}};
    return root._roster;
  }
  window.RosterEditor={open};
})();
