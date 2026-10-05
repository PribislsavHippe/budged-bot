/* Explicit enum-only telemetry. Never examines messages, request bodies or amounts. */
(() => {
  const tg=window.Telegram?.WebApp;
  let enabled=false,bot='';
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
  window.uxHelp=()=>{send('help_opened','help');if(bot){const url='https://t.me/'+bot+'?start=help';tg?.openTelegramLink?tg.openTelegramLink(url):window.open(url,'_blank','noopener');}else alert('Открой чат бота и отправь /help.');};
  window.showResearchTab=()=>{const tab=document.getElementById('tab-research');if(tab)tab.hidden=false;};
  let checking=false;
  async function checkAccess(){
    if(checking)return;checking=true;
    const retry=document.getElementById('ux-access-retry');retry.hidden=true;
    try{
      const r=await fetch('/api/research/access',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({initData:tg?.initData||''}),signal:AbortSignal.timeout(20000)});
      if(!r.ok)throw new Error('access');
      const data=await r.json();const wasEnabled=enabled;enabled=!!data.enabled;bot=data.bot_username||'';
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
    document.querySelector('.app-tabs')?.addEventListener('click',e=>{
      const b=e.target.closest('[role=tab]');if(!b)return;
      send('tab_opened',b.id.replace('tab-',''));
    });
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)checkAccess();});
  });
})();
