/* Explicit enum-only telemetry. Never examines messages, request bodies or amounts. */
(() => {
  const tg=window.Telegram?.WebApp;
  let enabled=false;
  const pending=[];
  function operationId(){
    const random=window.crypto;
    if(random?.randomUUID)return random.randomUUID();
    if(!random?.getRandomValues)return undefined;
    const bytes=random.getRandomValues(new Uint8Array(16));
    bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
    const hex=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }
  function send(event,screen,error_code){
    if(!enabled){if(pending.length<15)pending.push([event,screen,error_code]);return;}
    try { fetch('/api/research/event',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({initData:tg?.initData||'',event,screen,error_code,operation:operationId()}),
      signal:AbortSignal.timeout(5000)}).catch(()=>{}); } catch (_) { /* Telemetry never blocks UI. */ }
  }
  window.uxEvent=send;
  let helpOrigin=null;
  function helpView(faq){
    document.getElementById('help-home').hidden=faq;
    document.getElementById('help-faq').hidden=!faq;
    document.getElementById('help-title').textContent=faq?'Частые вопросы':'Чем помочь?';
    document.getElementById('help-status').textContent='';
    document.getElementById('help-status').classList.remove('error');
    document.getElementById('help-manual').hidden=true;
  }
  window.uxHelp=()=>{
    send('help_opened','help');
    helpOrigin=document.activeElement;
    helpView(false);
    document.getElementById('help-panel').classList.add('on');
    document.getElementById('help-close').focus();
  };
  function closeHelp(){
    document.getElementById('help-panel').classList.remove('on');
    helpOrigin?.focus();
  }
  function contact(){
    const status=document.getElementById('help-status');
    const manual=document.getElementById('help-manual');
    const url='https://t.me/lechatsergeev';
    status.classList.remove('error');
    status.textContent='Открываю личный чат Леши. Если он не открылся, нажми ссылку ниже.';
    manual.href=url;manual.hidden=false;
    try{
      if(tg?.openTelegramLink)tg.openTelegramLink(url);
      else window.open(url,'_blank','noopener');
    }catch(_){status.textContent='Не получилось открыть чат. Нажми ссылку ниже.';status.classList.add('error');}
  }
  window.showResearchTab=()=>{const tab=document.getElementById('tab-research');if(tab)tab.hidden=false;};
  let checking=false;
  async function checkAccess(){
    if(checking)return;checking=true;
    const retry=document.getElementById('ux-access-retry');retry.hidden=true;
    try{
      const r=await fetch('/api/research/access',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({initData:tg?.initData||''}),signal:AbortSignal.timeout(20000)});
      if(!r.ok)throw new Error('access');
      const data=await r.json();const wasEnabled=enabled;enabled=!!data.enabled;
      if(data.available)window.showResearchTab();
      if(enabled&&!wasEnabled){send('cabinet_opened','earnings');for(const args of pending.splice(0))send(...args);}
      else if(!enabled)pending.length=0;
    }catch(_){retry.hidden=false;}
    finally{checking=false;}
  }
  window.addEventListener('DOMContentLoaded',()=>{
    checkAccess();
    document.getElementById('ux-access-retry').onclick=checkAccess;
    document.getElementById('ux-help').onclick=window.uxHelp;
    document.getElementById('help-close').onclick=closeHelp;
    document.getElementById('help-panel').addEventListener('click',e=>{if(e.target.id==='help-panel')closeHelp();});
    document.addEventListener('keydown',e=>{if(e.key==='Escape'&&document.getElementById('help-panel').classList.contains('on'))closeHelp();});
    document.getElementById('help-faq-open').onclick=()=>{helpView(true);document.getElementById('help-faq-back').focus();};
    document.getElementById('help-faq-back').onclick=()=>{helpView(false);document.getElementById('help-faq-open').focus();};
    document.getElementById('help-contact').onclick=contact;
    document.getElementById('help-faq-contact').onclick=contact;
    document.querySelector('.app-tabs')?.addEventListener('click',e=>{
      const b=e.target.closest('[role=tab]');if(!b)return;
      send('tab_opened',b.id.replace('tab-',''));
    });
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)checkAccess();});
  });
})();
