(() => {
  'use strict';
  const root=document.getElementById('tips-dashboard'),tg=window.Telegram?.WebApp,$=id=>document.getElementById(id);
  const n=v=>v===null?'—':Number(v).toLocaleString('ru-RU',{maximumFractionDigits:0});
  const money=v=>v===null?'—':n(v)+' ₽';
  const date=s=>new Date(s+'T12:00:00').toLocaleDateString('ru-RU',{day:'numeric',month:'short'});
  const range=p=>date(p.start)+(p.start.slice(0,4)!==p.end.slice(0,4)?' '+p.start.slice(0,4):'')+' — '+date(p.end)+' '+p.end.slice(0,4);
  const shiftsLabel=v=>v%100>=11&&v%100<=14?'смен':v%10===1?'смена':v%10>=2&&v%10<=4?'смены':'смен';
  const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let kind='month',current=null,request=0,custom=false,controller=null;
  const selections={month:null,week:null};
  root.innerHTML=`<div class="tips-top"><button id="tips-back" class="text-action" hidden>← Обзор</button><div class="tips-types" role="group" aria-label="Период"><button id="tips-month" aria-pressed="true">Месяц</button><button id="tips-week" aria-pressed="false">Неделя</button></div></div>
    <div id="tips-period-home"><div class="tips-period-nav" id="tips-period-nav"><button id="tips-prev" aria-label="Предыдущий период">‹</button><input id="tips-a" type="month" aria-label="Основной период"><button id="tips-next" aria-label="Следующий период">›</button></div></div>
    <div id="tips-error" class="tips-error" role="alert"></div><button id="tips-retry" class="text-action" hidden>Повторить загрузку</button>
    <div id="tips-overview"><div id="tips-result" aria-live="polite"></div><button id="tips-open-compare" class="tips-link-row">Сравнить периоды <span aria-hidden="true">→</span></button>
    <details class="tips-disclosure" id="tips-daily"><summary>По дням</summary><div id="tips-daily-result"></div></details></div>
    <section id="tips-comparison" hidden><div class="compare-periods"><label>Период<span id="tips-compare-primary"></span></label><label>Сравнить с<input id="tips-b" type="month" aria-label="Период сравнения"></label></div><div class="compare-options"><button id="tips-previous" class="text-action">Предыдущий месяц</button><label class="compare-align" id="tips-align-label"><input id="tips-align" type="checkbox" checked><span id="tips-align-text">Равное число дней</span></label></div><div id="tips-comparison-result" aria-live="polite"></div></section>`;
  function inputDate(id){const v=$(id).value;return v?(kind==='month'?v+'-01':v):undefined;}
  function mode(value){
    $('tips-overview').hidden=value;$('tips-comparison').hidden=!value;$('tips-back').hidden=!value;
    document.getElementById('earnings-panel').classList.toggle('is-comparing',value);
    $(value?'tips-compare-primary':'tips-period-home').appendChild($('tips-period-nav'));
    if(value){if(root.getAttribute('aria-busy')!=='true')renderComparison();$('tips-back').focus();}else $('tips-open-compare').focus();
  }
  function navigate(step){
    const d=new Date((inputDate('tips-a')||current?.today||new Date().toISOString().slice(0,10))+'T12:00:00');
    if(kind==='month')d.setMonth(d.getMonth()+step,1);else d.setDate(d.getDate()+step*7);
    const iso=[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
    $('tips-a').value=kind==='month'?iso.slice(0,7):iso;load();
  }
  async function load(){
    if(document.getElementById('earnings-panel').hidden)return;
    controller?.abort();controller=new AbortController();const activeController=controller,token=++request;
    const timeout=setTimeout(()=>activeController.abort(),20000);
    $('tips-error').textContent='';root.setAttribute('aria-busy','true');
    // Never leave old amounts displayed beneath newly selected dates.
    $('tips-result').hidden=true;$('tips-comparison-result').hidden=true;$('tips-daily-result').hidden=true;
    try{
      const r=await fetch('/api/tips_compare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({initData:tg?.initData||'',kind,anchor:inputDate('tips-a'),other:custom?inputDate('tips-b'):undefined,aligned:$('tips-align').checked}),signal:activeController.signal});
      const s=await r.json();if(!r.ok)throw new Error(r.status===401?'Открой бота и зайди в приложение ещё раз.':s.error);
      if(token!==request)return;current=s;
      for(const [id,p] of [['tips-a',s.overview],['tips-b',s.b]]){$(id).value=kind==='month'?p.start.slice(0,7):p.start;$(id).max=kind==='month'?s.today.slice(0,7):s.today;}
      $('tips-next').disabled=s.a_full_end>=s.today;$('tips-prev').disabled=s.overview.start<=(kind==='month'?'2000-01-01':'2000-01-03');
      render();$('tips-retry').hidden=true;
    }catch(e){if(token===request){$('tips-error').textContent=e.name==='AbortError'?'Не удалось загрузить данные':e.message;$('tips-retry').hidden=false;}}
    finally{clearTimeout(timeout);if(token===request)root.setAttribute('aria-busy','false');}
  }
  function renderComparison(){
    if(!current)return;const s=current,delta=s.delta.net;
    $('tips-align-label').hidden=!s.partial;
    const count=Math.min(Math.round((Date.parse(s.a.end)-Date.parse(s.a.start))/86400000)+1,Math.round((Date.parse(s.b.end)-Date.parse(s.b.start))/86400000)+1);
    $('tips-align-text').textContent=$('tips-align').checked?'По '+count+' дн.':'Равное число дней';
    const rows=[['net','Чистыми'],['gross','До расходов'],['expenses','Расходы'],['avg_gross','Средний чай / смена'],['avg_net','Чистыми / смена'],['shifts','Смен с чаевыми']];
    $('tips-comparison-result').innerHTML=`<p class="compare-delta">${delta.amount===null?'Нет данных для сравнения':(delta.amount===0?'Без изменений':(delta.amount>0?'+':'−')+money(Math.abs(delta.amount))+(delta.pct===null?'':' · '+(delta.pct>0?'+':'')+n(delta.pct)+'%'))}<span>чистыми</span></p><table class="tips-table compare-table"><thead><tr><th scope="col"></th><th scope="col">${range(s.a)}</th><th scope="col">${range(s.b)}</th></tr></thead><tbody>${rows.map(([key,label])=>`<tr${key==='net'?' class="compare-main"':''}><th scope="row">${label}</th><td>${s.a.has_data?(key==='shifts'?n(s.a[key]):money(s.a[key])):'—'}</td><td>${s.b.has_data?(key==='shifts'?n(s.b[key]):money(s.b[key])):'—'}</td></tr>`).join('')}</tbody></table>`;
    $('tips-comparison-result').hidden=false;
  }
  function daily(s){
    const days=s.days;if(!days.length)return '<p class="tips-empty">Нет записей за этот период.</p>';
    const max=Math.max(1,...days.flatMap(d=>[d.gross,d.expenses])),w=350,h=136,count=Math.round((Date.parse(s.end)-Date.parse(s.start))/86400000)+1,step=320/count,bw=Math.min(12,step/3);
    let svg=`<svg class="tips-chart" viewBox="0 0 ${w} 185" role="img" aria-label="Чаевые до расходов и расходы по дням с записями, общая шкала"><line x1="28" x2="348" y1="${h}" y2="${h}" stroke="#aeb6b9"/><text x="0" y="12" font-size="10">${n(max)} ₽</text>`;
    days.forEach((d,i)=>{const index=Math.round((Date.parse(d.date)-Date.parse(s.start))/86400000);const x=28+index*step+step/2;svg+=`<g><title>${d.date}: чай ${n(d.gross)}, расходы ${n(d.expenses)}, чистыми ${n(d.net)}</title><rect x="${x-bw}" y="${h-d.gross/max*112}" width="${bw}" height="${d.gross/max*112}" fill="#355e72"/><rect x="${x+1}" y="${h-d.expenses/max*112}" width="${bw}" height="${d.expenses/max*112}" fill="#b96b4c"/></g>`;});
    for(let i=0;i<count;i++)if(count<=7||i===0||(i+1)%5===0||i===count-1){const d=new Date(Date.parse(s.start)+i*86400000);svg+=`<text x="${28+i*step+step/2}" y="155" text-anchor="middle" font-size="9">${d.getUTCDate()}</text>`;}
    return svg+'</svg>';
  }
  function render(){const a=current.overview;
    $('tips-result').innerHTML=`<div class="tips-hero"><p class="tips-period-label">${range(a)}</p><h1>Чаевые чистыми</h1><div class="tips-total">${a.has_data?money(a.net):'—'}</div><div class="tips-breakdown"><div><span>До расходов</span><strong>${a.has_data?money(a.gross):'—'}</strong></div><div><span>Расходы</span><strong>${a.has_data?money(a.expenses):'—'}</strong></div></div></div><div class="tips-average"><div><span>Средний чай за смену</span><strong>${money(a.avg_gross)}</strong></div><div class="tips-shifts"><strong>${a.shifts}</strong><span>Смены с чаевыми</span></div></div>`;
    const exact=$('tips-daily-table')?.open;
    $('tips-daily-result').innerHTML=`<div class="tips-legend"><span>До расходов</span><span class="expense">Расходы</span></div>${daily(a)}<details id="tips-daily-table" ${exact?'open':''}><summary>Суммы по дням</summary><table class="tips-table"><thead><tr><th>Дата</th><th>Чай, ₽</th><th>Расходы, ₽</th><th>Чистыми, ₽</th></tr></thead><tbody>${a.days.map(d=>`<tr><td>${esc(date(d.date))}</td><td>${numExact(d.gross)}</td><td>${numExact(d.expenses)}</td><td>${numExact(d.net)}</td></tr>`).join('')}</tbody></table></details>`;
    $('tips-result').hidden=false;$('tips-daily-result').hidden=false;renderComparison();
  }
  function numExact(v){return v.toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2});}
  for(const type of ['month','week'])$('tips-'+type).onclick=()=>{
    if(kind===type)return;
    selections[kind]={a:inputDate('tips-a'),b:inputDate('tips-b'),custom,aligned:$('tips-align').checked};
    let a=inputDate('tips-a'),b=inputDate('tips-b');
    if(kind==='month'&&a?.slice(0,7)===current?.today.slice(0,7))a=current.today;
    const saved=selections[type];if(saved){a=saved.a;b=saved.b;custom=saved.custom;$('tips-align').checked=saved.aligned;}
    kind=type;$('tips-previous').textContent=kind==='month'?'Предыдущий месяц':'Предыдущая неделя';
    for(const [id,d] of [['tips-a',a],['tips-b',b]]){$(id).type=kind==='month'?'month':'date';$(id).value=d?(kind==='month'?d.slice(0,7):d):'';}
    $('tips-month').setAttribute('aria-pressed',String(kind==='month'));$('tips-week').setAttribute('aria-pressed',String(kind==='week'));load();};
  $('tips-a').onchange=load;$('tips-b').onchange=()=>{custom=true;load();};$('tips-align').onchange=load;
  $('tips-previous').onclick=()=>{custom=false;load();};$('tips-prev').onclick=()=>navigate(-1);$('tips-next').onclick=()=>navigate(1);
  $('tips-open-compare').onclick=()=>mode(true);$('tips-back').onclick=()=>mode(false);
  $('tips-retry').onclick=load;window.addEventListener('earnings-updated',load);load();
})();
