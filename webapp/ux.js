/* Explicit enum-only telemetry. Never examines messages, request bodies or amounts. */
(() => {
  const tg=window.Telegram?.WebApp;
  let enabled=false,bot='';
  const pending=[];
  function send(event,screen,error_code){
    if(!enabled){if(pending.length<15)pending.push([event,screen,error_code]);return;}
    try { fetch('/api/research/event',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({initData:tg?.initData||'',event,screen,error_code,operation:window.crypto?.randomUUID?.()}),
      signal:AbortSignal.timeout(5000)}).catch(()=>{}); } catch (_) { /* Telemetry never blocks UI. */ }
  }
  window.uxEvent=send;
  window.uxHelp=()=>{send('help_opened','help');if(bot){const url='https://t.me/'+bot+'?start=help';tg?.openTelegramLink?tg.openTelegramLink(url):window.open(url,'_blank','noopener');}else alert('Открой чат бота и отправь /help.');};
  window.addEventListener('DOMContentLoaded',()=>{
    fetch('/api/research/access',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({initData:tg?.initData||''}),signal:AbortSignal.timeout(5000)})
      .then(r=>r.ok?r.json():{}).then(data=>{enabled=!!data.enabled;bot=data.bot_username||'';
        if(enabled){send('cabinet_opened','earnings');for(const args of pending.splice(0))send(...args);}
        else pending.length=0;
        document.getElementById('tab-research').hidden=!data.available;
      }).catch(()=>{pending.length=0;});
    document.getElementById('ux-help').onclick=window.uxHelp;
    document.querySelector('.app-tabs')?.addEventListener('click',e=>{
      const b=e.target.closest('[role=tab]');if(!b)return;
      send('tab_opened',b.id.replace('tab-',''));
    });
  });
})();
