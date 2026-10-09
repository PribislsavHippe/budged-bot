/* Financial views of a private journal are calculated on the device.
   Integer kopecks keep money sums exact; calendar metadata comes from the API. */
(function () {
  const DAY=86400000,WEEKDAYS=['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
  const date=d=>new Date(d+'T12:00:00Z');
  const iso=d=>d.toISOString().slice(0,10);
  const add=(d,n)=>iso(new Date(date(d).getTime()+n*DAY));
  const weekday=d=>(date(d).getUTCDay()+6)%7;
  const monthStart=d=>d.slice(0,7)+'-01';
  const monthEnd=d=>iso(new Date(Date.UTC(+d.slice(0,4),+d.slice(5,7),0,12)));
  const cents=e=>Math.round(Number(e.signed_amount)*100);
  function rounded(value,places=0) {
    const scale=10**places,x=value*scale,low=Math.floor(x),part=x-low;
    const whole=Math.abs(part-.5)<1e-9 ? low+(low%2!==0?1:0):Math.round(x);
    return whole/scale || 0;
  }
  function entryDate(e) {
    if(e.work_date)return e.work_date;
    // Moscow operational days turn over at 06:00 (UTC+3, no DST).
    return iso(new Date(new Date(e.created_at).getTime()-3*3600000));
  }
  const isTip=e=>e.kind==='income'&&e.category==='Чаевые';
  const period=(rows,start,end='9999-12-31')=>rows.filter(e=>start<=entryDate(e)&&entryDate(e)<=end);
  const total=(rows,predicate)=>rows.filter(predicate).reduce((n,e)=>n+cents(e),0)/100;
  function daily(rows) {
    const days=new Map();
    for(const e of rows) {
      if(!isTip(e)&&e.kind!=='expense')continue;
      const d=entryDate(e),r=days.get(d)||{gross:0,expenses:0,cash:0,card:0,has_tip:false};
      if(isTip(e)){r.gross+=cents(e);r[e.account]+=cents(e);r.has_tip=true;}
      else r.expenses-=cents(e);
      days.set(d,r);
    }
    return days;
  }
  function summarize(rows,start,end,today) {
    const days=daily(period(rows,start,end<today?end:today));
    const gross=[...days.values()].reduce((n,r)=>n+r.gross,0)/100;
    const expenses=[...days.values()].reduce((n,r)=>n+r.expenses,0)/100;
    const shifts=[...days.values()].filter(r=>r.has_tip).length;
    return {start,end,has_data:days.size>0,gross,expenses,net:rounded(gross-expenses,2),shifts,
      avg_gross:shifts?rounded(gross/shifts,2):null,avg_net:shifts?rounded((gross-expenses)/shifts,2):null,
      days:[...days.entries()].sort(([a],[b])=>a.localeCompare(b)).map(([d,r])=>({date:d,
        gross:r.gross/100,expenses:r.expenses/100,net:(r.gross-r.expenses)/100,has_tip:r.has_tip}))};
  }
  function bounds(rows,shifts,year,month,today) {
    const months=[today.slice(0,7),...rows.map(e=>entryDate(e).slice(0,7)),...shifts.map(d=>d.slice(0,7))].sort();
    const first=months[0],floor=String(+first.slice(0,4)-2).padStart(4,'0')+first.slice(4);
    const next=add(monthEnd(today),1).slice(0,7),ceiling=[next,months.at(-1)].sort().at(-1);
    const shown=String(year).padStart(4,'0')+'-'+String(month).padStart(2,'0');
    return {has_prev:shown>floor,has_next:shown<ceiling};
  }
  function month(rows,shifts,year,number,today) {
    const start=String(year).padStart(4,'0')+'-'+String(number).padStart(2,'0')+'-01',end=monthEnd(start);
    const selected=period(rows,start,end),days=daily(selected),values=[...days.values()].filter(r=>r.has_tip);
    const sum=values.reduce((n,r)=>n+r.gross,0)/100;
    return {year,month:number,days_in_month:+end.slice(-2),scheduled_shifts:shifts.filter(d=>d.slice(0,7)===start.slice(0,7)),
      days:Array.from({length:+end.slice(-2)},(_,i)=>{const d=start.slice(0,8)+String(i+1).padStart(2,'0');
        const r=days.get(d)||{gross:0,cash:0,card:0,expenses:0};
        return {day:i+1,tips:rounded(r.gross/100),cash:rounded(r.cash/100),card:rounded(r.card/100),spent:rounded(r.expenses/100)};}),
      tips_total:rounded(sum),net:total(selected,e=>['income','expense'].includes(e.kind)),
      shifts_count:values.length,avg_shift_tips:values.length?rounded(sum/values.length):null,
      today_day:today.slice(0,7)===start.slice(0,7)?+today.slice(-2):null,...bounds(rows,shifts,year,number,today)};
  }
  function stats(rows,meta,today) {
    const current=period(rows,monthStart(today)),previousEnd=add(monthStart(today),-1);
    const previous=period(rows,monthStart(previousEnd),previousEnd),currentDay=period(rows,today);
    const tips=total(current,isTip),prevTips=total(previous,isTip),allDays=daily(rows);
    const shiftDays=[...daily(current).entries()].filter(([,r])=>r.has_tip);
    const pcts=current.filter(e=>e.tip_percent).map(e=>Number(e.tip_percent));
    const prevPcts=previous.filter(e=>e.tip_percent).map(e=>Number(e.tip_percent));
    const average=v=>v.length?rounded(v.reduce((a,b)=>a+b,0)/v.length):0;
    const byDay=Array.from({length:7},()=>[]);
    for(const [d,r] of allDays)if(r.has_tip)byDay[weekday(d)].push(r);
    const weekday_avg_tips=byDay.map(v=>average(v.map(r=>r.gross/100)));
    const todayIncome=total(currentDay,e=>e.kind==='income'),goal=meta.shift_goal||null;
    const split={cash:total(current,e=>isTip(e)&&e.account==='cash'),card:total(current,e=>isTip(e)&&e.account==='card')};
    const spend=total(current,e=>e.kind==='expense'&&e.note==='трата смены')*-1;
    const pctDays=new Map();
    for(const e of rows)if(e.tip_percent&&entryDate(e)<=today){const d=entryDate(e);pctDays.set(d,[...(pctDays.get(d)||[]),Number(e.tip_percent)]);}
    const best=shiftDays.reduce((best,item)=>!best||item[1].gross>best[1].gross?item:best,null);
    const cal=month(rows,meta.calendar_shift_dates||[],+today.slice(0,4),+today.slice(5,7),today);
    return {...meta,today_net:total(currentDay,e=>['income','expense'].includes(e.kind)),today_income:todayIncome,
      today_spent:-total(currentDay,e=>e.kind==='expense')||0,week_net:total(period(rows,add(today,-weekday(today))),e=>['income','expense'].includes(e.kind)),
      month_net:total(current,e=>['income','expense'].includes(e.kind)),total_net:total(rows,e=>['income','expense'].includes(e.kind)),
      shift_goal:goal,goal_pct:goal?rounded(Math.min(todayIncome/goal,1)*100):null,
      avg_tip_pct:pcts.length?rounded(pcts.reduce((a,b)=>a+b,0)/pcts.length,1):null,
      avg_tip_pct_delta:pcts.length&&prevPcts.length?rounded(rounded(pcts.reduce((a,b)=>a+b,0)/pcts.length,1)-prevPcts.reduce((a,b)=>a+b,0)/prevPcts.length,1):null,
      shifts_count:shiftDays.length,vs_prev_month_pct:prevTips>0?rounded((tips-prevTips)/prevTips*100):null,
      weekday_avg_tips,weekday_split:byDay.map(v=>({cash:average(v.map(r=>r.cash/100)),card:average(v.map(r=>r.card/100))})),
      month_days:cal.days,today_day:cal.today_day,days_in_month:cal.days_in_month,
      avg_shift_tips:shiftDays.length?rounded(tips/shiftDays.length):null,
      record:best?{day:+best[0].slice(-2),tips:rounded(best[1].gross/100),weekday:WEEKDAYS[weekday(best[0])]}:null,
      shift_spend_month:rounded(spend),month_tips:rounded(tips),shift_spend_pct:tips>0?rounded(spend/tips*100):null,
      best_weekday:Math.max(...weekday_avg_tips)>0?WEEKDAYS[weekday_avg_tips.indexOf(Math.max(...weekday_avg_tips))]:null,
      tips_split:{cash:rounded(split.cash),card:rounded(split.card),cash_pct:split.cash+split.card>0?rounded(split.cash/(split.cash+split.card)*100):null},
      tip_pct_daily:[...pctDays.entries()].sort(([a],[b])=>a.localeCompare(b)).slice(-14).map(([d,v])=>({date:d,pct:rounded(v.reduce((a,b)=>a+b,0)/v.length,1)})),
      heatmap:Array.from({length:28},(_,i)=>{const d=add(today,i-27);return {date:d,tips:rounded((allDays.get(d)?.gross||0)/100)};}),
      tip_month:summarize(rows,monthStart(today),today,today),...bounds(rows,meta.calendar_shift_dates||[],cal.year,cal.month,today),
      tip_entries:rows.filter(e=>isTip(e)&&monthStart(today)<=entryDate(e)&&entryDate(e)<=monthEnd(today)).map(e=>({id:e.id,date:entryDate(e),amount:Number(e.signed_amount),account:e.account}))};
  }
  function range(rows,args,meta,today) {
    const tipDays=rows.filter(e=>isTip(e)&&entryDate(e)<=today).map(entryDate).sort();
    const custom='start' in args||'end' in args;
    const chosen=meta.has_shift||!tipDays.length?today:tipDays.at(-1);
    const start=custom?args.start:chosen,end=custom?args.end:chosen;
    return {...meta,today,custom,period:summarize(rows,start,end,today),
      entries:period(rows,start,end).filter(e=>isTip(e)||e.kind==='expense').map(e=>({id:e.id,date:entryDate(e),
        kind:e.kind,account:e.account,category:e.category,amount:Number(e.signed_amount)}))
        .sort((a,b)=>b.date.localeCompare(a.date)||String(b.id).localeCompare(String(a.id)))};
  }
  function comparison(rows,args,today) {
    const kind=args.kind||'month';
    function span(anchor){if(kind==='week'){const start=add(anchor,-weekday(anchor));return [start,add(start,6)];}
      if(kind==='month')return [monthStart(anchor),monthEnd(anchor)];throw new Error('Выбери недели или месяцы.');}
    const [as,af]=span(args.anchor||today),[bs,bf]=span(args.other||add(as,-1));
    let ae=af<today?af:today,be=bf<today?bf:today;
    const overview=summarize(rows,as,ae,today),partial=af>today||bf>today;
    const aligned=(args.aligned??true)&&partial;
    if(aligned){const length=Math.min((date(ae)-date(as))/DAY,(date(be)-date(bs))/DAY);ae=add(as,length);be=add(bs,length);}
    const a=summarize(rows,as,ae,today),b=summarize(rows,bs,be,today),delta={};
    for(const key of ['gross','expenses','net','shifts','avg_gross','avg_net']){
      const known=a.has_data&&b.has_data&&a[key]!==null&&b[key]!==null;
      const amount=known?rounded(a[key]-b[key],2):null;
      delta[key]={amount,pct:known&&b[key]>0?rounded(amount/b[key]*100,1):null};
    }
    return {kind,overview,a,b,delta,today,partial,aligned,a_full_end:af,b_full_end:bf};
  }
  function view(path,args,data) {
    if(!window.privateMoney?.active)return data;
    const rows=window.privateMoney.entries,today=data.operational_today||data.today||window.privateMoney.state.today;
    path=path.split('?')[0];
    if(path==='/api/stats')return stats(rows,data,today);
    if(path==='/api/month'){
      const calculated=month(rows,data.calendar_shift_dates||[],data.year,data.month,today);
      const first=String(data.year).padStart(4,'0')+'-'+String(data.month).padStart(2,'0')+'-01';
      return {...data,...calculated,tip_entries:period(rows,first,monthEnd(first)).filter(isTip).map(e=>({
        id:e.id,date:entryDate(e),amount:Number(e.signed_amount),account:e.account}))};
    }
    if(path==='/api/tips_range')return range(rows,args,data,today);
    if(path==='/api/tips_compare')return comparison(rows,args,today);
    if(path==='/api/service_charge/view'){
      const selected=rows.filter(e=>e.kind==='accrual'&&e.category==='Сервисный сбор'&&entryDate(e).slice(0,7)===data.month);
      return {...data,total:total(selected,()=>true),count:selected.length};
    }
    return data;
  }
  window.PrivateFinance={view,stats,month,summarize,comparison,range,entryDate};
})();
