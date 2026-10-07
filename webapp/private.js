/* Personal finance ledger stored on the current Telegram device.
   The server retains only device-key-encrypted recovery copies for 14 days. */
(function () {
  const tg = window.Telegram && window.Telegram.WebApp;
  const ledgerName = 'money_ledger_v1';
  const damagedLedgerName = 'money_ledger_damaged_v1';
  const exitPendingName = 'money_exit_pending_v1';
  const archiveIndexName = 'money_ledger_archives_v1';
  const aesName = 'money_aes_key_v1';
  const rsaName = 'money_rsa_private_v1';
  const state = {active:false,ready:false,lost:false,lostReason:null,entries:[],receipts:[],
    exitArchivePending:false,exitConflict:false,exiting:false,archiveCount:0,today:null};
  let syncing=null;
  let deactivating=false;
  const storageTimeoutMs=15000;
  const requestTimeoutMs=30000;

  function within(promise,ms,message) {
    let timer;
    return Promise.race([promise,new Promise((_,reject)=>{
      timer=setTimeout(()=>reject(new Error(message)),ms);
    })]).finally(()=>clearTimeout(timer));
  }

  function encode(bytes) {
    let value='';
    for (let i=0;i<bytes.length;i+=8192) value+=String.fromCharCode(...bytes.subarray(i,i+8192));
    return btoa(value).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
  }
  function decode(value) {
    const raw=atob(value.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-value.length%4)%4));
    return Uint8Array.from(raw,c=>c.charCodeAt(0));
  }
  function item(storage,method,key,value) {
    return new Promise((resolve,reject)=>{
      let settled=false;
      const area=storage===tg.SecureStorage?'Защищённое хранилище Telegram':'Журнал на телефоне';
      const areaInSentence=area[0].toLowerCase()+area.slice(1);
      const timer=setTimeout(()=>{
        if (method!=='setItem') {
          done(new Error(area+' не отвечает при чтении. Обнови Telegram и открой приложение снова.'));
          return;
        }
        // Some clients may persist the value without delivering the write callback.
        // Only accept such a write if an independent read confirms the exact value.
        const verifyTimer=setTimeout(()=>done(new Error('Нет подтверждения записи: '+areaInSentence+'. Открой приложение снова и проверь журнал.')),5000);
        try {
          storage.getItem(key,(error,stored)=>{
            clearTimeout(verifyTimer);
            done(error || stored!==value
              ? new Error('Нет подтверждения записи: '+areaInSentence+'. Открой приложение снова и проверь журнал.') : null,true);
          });
        } catch (error) {clearTimeout(verifyTimer);done(error);}
      },storageTimeoutMs);
      const done=(error,result)=>{
        if (settled) return;
        settled=true;clearTimeout(timer);
        if (error) reject(error instanceof Error?error:new Error(String(error)));
        else if (method==='setItem' && result!==true)
          reject(new Error('Устройство не подтвердило сохранение. Записи в базе не удалены.'));
        else resolve(result);
      };
      try {
        if (method==='setItem') storage.setItem(key,value,done);
        else storage.getItem(key,done);
      } catch (error) {done(error);}
    });
  }
  async function request(path,extra) {
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),requestTimeoutMs);
    try {
      const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},cache:'no-store',
        signal:controller.signal,body:JSON.stringify({initData:tg?.initData||'',...extra})});
      const body=await response.json();
      if (!response.ok) throw new Error(body.error||'Не получилось связаться с ботом.');
      return body;
    } catch (error) {
      if (controller.signal.aborted) throw new Error('Бот долго не отвечает. Открой приложение снова и повтори перенос.');
      throw error;
    } finally {clearTimeout(timer);}
  }
  async function aesKey(create) {
    let raw=await item(tg.SecureStorage,'getItem',aesName);
    if (!raw && create) {
      raw=encode(crypto.getRandomValues(new Uint8Array(32)));
      await item(tg.SecureStorage,'setItem',aesName,raw);
    }
    if (!raw) throw new Error('На этом устройстве нет ключа личного журнала.');
    return crypto.subtle.importKey('raw',decode(raw),{name:'AES-GCM'},false,['encrypt','decrypt']);
  }
  async function loadLocal(requireExisting=false) {
    const packed=await item(tg.DeviceStorage,'getItem',ledgerName);
    if (!packed) {
      if (requireExisting) throw new Error('Личный журнал отсутствует на этом устройстве.');
      state.entries=[];state.receipts=[];return;
    }
    const saved=await unpackLocal(packed);
    const entries=Array.isArray(saved)?saved:saved?.entries;
    if (!Array.isArray(entries)) throw new Error('Не удалось прочитать личный журнал.');
    state.entries=entries;
    state.receipts=Array.isArray(saved)?entries.map(e=>String(e.id)):
      Array.isArray(saved.receipts)?saved.receipts.map(String):[];
  }
  async function unpackLocal(packed) {
    const bytes=decode(packed),key=await aesKey(false);
    const plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:bytes.subarray(0,12)},key,bytes.subarray(12));
    const saved=JSON.parse(new TextDecoder().decode(plain));
    const entries=Array.isArray(saved)?saved:saved?.entries;
    if (!Array.isArray(entries)) throw new Error('Не удалось прочитать личный журнал.');
    return saved;
  }
  async function saveLocal() {
    const key=await aesKey(true),iv=crypto.getRandomValues(new Uint8Array(12));
    const plain=new TextEncoder().encode(JSON.stringify({entries:state.entries,receipts:state.receipts}));
    const encrypted=new Uint8Array(await crypto.subtle.encrypt({name:'AES-GCM',iv},key,plain));
    const packed=new Uint8Array(iv.length+encrypted.length);
    packed.set(iv);packed.set(encrypted,iv.length);
    await item(tg.DeviceStorage,'setItem',ledgerName,encode(packed));
  }
  async function rsaKeys(create) {
    const stored=await item(tg.SecureStorage,'getItem',rsaName);
    if (stored) {
      const keys=JSON.parse(stored);
      return {privateKey:await crypto.subtle.importKey('jwk',keys.private,
        {name:'RSA-OAEP',hash:'SHA-256'},false,['decrypt']),publicJwk:keys.public};
    }
    if (!create) throw new Error('На этом устройстве нет ключа для пересланных записей.');
    const pair=await within(crypto.subtle.generateKey({name:'RSA-OAEP',modulusLength:2048,
      publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['encrypt','decrypt']),
      requestTimeoutMs,'Телефон долго готовит ключ. Открой приложение снова и повтори перенос.');
    const publicJwk=await crypto.subtle.exportKey('jwk',pair.publicKey);
    const privateJwk=await crypto.subtle.exportKey('jwk',pair.privateKey);
    publicJwk.alg='RSA-OAEP-256';
    await item(tg.SecureStorage,'setItem',rsaName,JSON.stringify({private:privateJwk,public:publicJwk}));
    return {privateKey:pair.privateKey,publicJwk};
  }
  function available() {
    return !!(tg?.DeviceStorage && tg?.SecureStorage && window.crypto?.subtle &&
      (!tg.isVersionAtLeast || tg.isVersionAtLeast('9.0')));
  }
  async function init() {
    const result=await request('/api/private/prepare',{});
    state.active=!!result.active;
    state.ready=true;
    state.lost=false;state.lostReason=null;state.exitArchivePending=false;
    state.exitConflict=false;state.archiveCount=0;
    if (!available()) {
      state.lost=state.active;state.lostReason='unsupported';
      return state;
    }
    try {
      const [key,ledger,rsa]=await Promise.all([
        item(tg.SecureStorage,'getItem',aesName),
        item(tg.DeviceStorage,'getItem',ledgerName),
        item(tg.SecureStorage,'getItem',rsaName)]);
      try {
        const rawIndex=await item(tg.DeviceStorage,'getItem',archiveIndexName);
        const archives=rawIndex?JSON.parse(rawIndex):[];
        state.archiveCount=Array.isArray(archives)?archives.length:0;
      } catch (error) {state.archiveCount=0;}
      const pending=await item(tg.DeviceStorage,'getItem',exitPendingName);
      if (!state.active && pending && pending===result.last_exit_operation) {
        try {await finishExitArchive();}
        catch (error) {state.exitArchivePending=true;}
        return state;
      }
      if (!state.active && pending) state.exitConflict=true;
      if (state.active && !key && !ledger && !rsa) {
        state.lost=true;state.lostReason='missing';
      } else if (state.active && !ledger) {
        state.lost=true;state.lostReason='pending';
      } else if ((key && !ledger) || (!key && ledger) || (state.active && !rsa && (key || ledger))) {
        state.lost=state.active;state.lostReason='unreadable';
      } else if (ledger) {
        await loadLocal();
        if (state.active) {
          const keys=await rsaKeys(false);
          if (result.public_key &&
              (keys.publicJwk.n!==result.public_key.n || keys.publicJwk.e!==result.public_key.e)) {
            // A previous rotation may have succeeded locally but lost its response.
            if (!state.entries.length) {state.lost=true;state.lostReason='pending';}
          }
        }
      }
    } catch (error) {
      state.lost=state.active;state.lostReason='unreadable';state.entries=[];
    }
    return state;
  }
  async function activate(progress=()=>{}) {
    if (!available()) throw new Error('Обнови Telegram: хранение на устройстве здесь недоступно.');
    if (state.lostReason==='unreadable')
      throw new Error('Не удалось прочитать прежний журнал на устройстве. Данные в базе сохранены.');
    progress('Получаю записи из базы…');
    const result=await request('/api/private/prepare',{});
    if (result.active) throw new Error('Приватный режим уже включён.');
    progress('Готовлю ключи на телефоне…');
    const keys=await rsaKeys(true);
    await aesKey(true);
    const byId=new Map(state.entries.map(e=>[String(e.id),e]));
    result.entries.forEach(e=>byId.set(String(e.id),e));
    state.entries=[...byId.values()];
    state.receipts=[...new Set([...state.receipts,...state.entries.map(e=>String(e.id))])];
    const saved=JSON.stringify({entries:state.entries,receipts:state.receipts});
    progress('Сохраняю и проверяю записи на телефоне…');
    await saveLocal();
    await loadLocal();
    if (JSON.stringify({entries:state.entries,receipts:state.receipts})!==saved)
      throw new Error('Перенос не подтвердился. Серверные записи сохранены.');
    progress('Подтверждаю перенос в базе…');
    try {
      await request('/api/private/activate',{entry_ids:result.entries.map(e=>e.id),public_key:keys.publicJwk});
    } catch (error) {
      const check=await request('/api/private/prepare',{});
      if (!check.active) throw error;
    }
    state.active=true;state.lost=false;
    return state.entries.length;
  }
  async function rotate() {
    if (!available()) throw new Error('На этом устройстве нет безопасного хранилища Telegram.');
    if (!state.active || !['missing','pending'].includes(state.lostReason))
      throw new Error('Прежний журнал на устройстве требует проверки. Новый ключ не создан.');
    // A new device has no previous private key. Existing data cannot be recovered here.
    let publicJwk;
    if (state.lostReason==='pending') {
      publicJwk=(await rsaKeys(true)).publicJwk;
      await aesKey(true);
      const existing=await item(tg.DeviceStorage,'getItem',ledgerName);
      if (!existing) {state.entries=[];await saveLocal();await loadLocal();}
    } else {
      const pair=await crypto.subtle.generateKey({name:'RSA-OAEP',modulusLength:2048,
        publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['encrypt','decrypt']);
      publicJwk=await crypto.subtle.exportKey('jwk',pair.publicKey);
      const privateJwk=await crypto.subtle.exportKey('jwk',pair.privateKey);
      publicJwk.alg='RSA-OAEP-256';
      await item(tg.SecureStorage,'setItem',rsaName,JSON.stringify({private:privateJwk,public:publicJwk}));
      await item(tg.SecureStorage,'setItem',aesName,encode(crypto.getRandomValues(new Uint8Array(32))));
      state.entries=[];await saveLocal();await loadLocal();
      state.lostReason='pending';
    }
    await request('/api/private/rotate',{public_key:publicJwk});
    state.lost=false;state.lostReason=null;
  }
  async function add(record) {
    if (deactivating) throw new Error('Дождись завершения переноса личного журнала.');
    if (!state.active || state.lost) throw new Error('Личный журнал на этом устройстве недоступен.');
    if (state.entries.some(e=>String(e.id)===String(record.id))) return false;
    const previous=state.entries,receipts=state.receipts;
    state.entries=[...previous,record];state.receipts=[...new Set([...receipts,String(record.id)])];
    try {await saveLocal();} catch (e) {state.entries=previous;state.receipts=receipts;throw e;}
    if (record.kind==='income' && record.category==='Чаевые') window.uxEvent?.('tip_added','earnings');
    else if (record.kind==='expense') window.uxEvent?.('expense_added','earnings');
    return true;
  }
  async function remove(id) {
    if (deactivating) throw new Error('Дождись завершения переноса личного журнала.');
    const previous=state.entries;
    state.entries=previous.filter(e=>String(e.id)!==String(id));
    try {await saveLocal();} catch (e) {state.entries=previous;throw e;}
  }
  async function clear() {
    if (deactivating) throw new Error('Дождись завершения переноса личного журнала.');
    if (!state.active || state.lost) throw new Error('Личный журнал на этом устройстве недоступен.');
    await request('/api/private/clear_backups',{});
    const previous=state.entries,receipts=state.receipts;
    state.entries=[];state.receipts=[];
    try {await saveLocal();} catch (e) {state.entries=previous;state.receipts=receipts;throw e;}
  }
  async function edit(id,patch) {
    if (deactivating) throw new Error('Дождись завершения переноса личного журнала.');
    const previous=state.entries;
    state.entries=previous.map(e=>String(e.id)===String(id)?{...e,...patch}:e);
    try {await saveLocal();} catch (e) {state.entries=previous;throw e;}
  }
  async function decryptRecord(payload) {
    const bytes=decode(payload);
    if (bytes[0]!==1) throw new Error('Формат зашифрованной записи не поддерживается.');
    const privateKey=(await rsaKeys(false)).privateKey;
    const aesRaw=await crypto.subtle.decrypt({name:'RSA-OAEP'},privateKey,bytes.subarray(1,257));
    const aes=await crypto.subtle.importKey('raw',aesRaw,{name:'AES-GCM'},false,['decrypt']);
    const uid=String(tg.initDataUnsafe?.user?.id||'');
    const plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:bytes.subarray(257,269),
      additionalData:new TextEncoder().encode(uid)},aes,bytes.subarray(269));
    const record=JSON.parse(new TextDecoder().decode(plain));
    if (!record || !['string','number'].includes(typeof record.id) ||
        !Number.isFinite(Number(record.signed_amount)))
      throw new Error('Зашифрованная запись повреждена.');
    return record;
  }
  async function backupRecords() {
    const result=await request('/api/private/backups',{});
    if (!Array.isArray(result.backups) || result.backups.length>5000)
      throw new Error('Не удалось прочитать временные копии.');
    const records=[];
    for (const row of result.backups) {
      if (!row || typeof row.record_id!=='string' || typeof row.payload!=='string')
        throw new Error('Временная копия повреждена.');
      const record=await decryptRecord(row.payload);
      if (String(record.id)!==row.record_id)
        throw new Error('Временная копия не совпадает с записью.');
      records.push(record);
    }
    return records;
  }
  async function sync() {
    if (syncing) return syncing;
    syncing=syncOnce();
    try {return await syncing;} finally {syncing=null;}
  }
  async function syncOnce() {
    if (!state.active || state.lost || state.exiting) return 0;
    const records=await backupRecords();
    const seen=new Set(state.receipts);
    const fresh=records.filter(e=>!seen.has(String(e.id)));
    if (!fresh.length) return 0;
    const previous=state.entries,receipts=state.receipts;
    const byId=new Map(previous.map(e=>[String(e.id),e]));
    fresh.forEach(e=>byId.set(String(e.id),e));
    state.entries=[...byId.values()];
    state.receipts=[...new Set([...receipts,...fresh.map(e=>String(e.id))])];
    const saved=JSON.stringify({entries:state.entries,receipts:state.receipts});
    try {
      await saveLocal();await loadLocal();
      if (JSON.stringify({entries:state.entries,receipts:state.receipts})!==saved)
        throw new Error('Телефон не подтвердил сохранение личных записей.');
    } catch (error) {state.entries=previous;state.receipts=receipts;throw error;}
    fresh.forEach(record=>{
      if (record.kind==='income' && record.category==='Чаевые') window.uxEvent?.('tip_added','earnings');
      else if (record.kind==='expense') window.uxEvent?.('expense_added','earnings');
    });
    return fresh.length;
  }
  async function finishExitArchive() {
    if (state.active) throw new Error('Сначала перенеси личный журнал в обычный режим.');
    const operation=await item(tg.DeviceStorage,'getItem',exitPendingName);
    if (!operation) throw new Error('Не нашёл подтверждение переноса на этом телефоне.');
    const profile=await request('/api/private/prepare',{});
    if (profile.active || profile.last_exit_operation!==operation)
      throw new Error('Сервер не подтвердил выход из приватного режима. Журнал сохранён.');
    const archiveName='money_ledger_archive_'+operation.replace(/-/g,'');
    let archived=await item(tg.DeviceStorage,'getItem',archiveName);
    if (!archived) {
      const current=await item(tg.DeviceStorage,'getItem',ledgerName);
      if (!current) throw new Error('Не нашёл журнал для архивной копии.');
      await unpackLocal(current);
      await item(tg.DeviceStorage,'setItem',archiveName,current);
      archived=await item(tg.DeviceStorage,'getItem',archiveName);
      if (archived!==current) throw new Error('Телефон не подтвердил архивную копию журнала.');
    }
    await unpackLocal(archived);
    const rawIndex=await item(tg.DeviceStorage,'getItem',archiveIndexName);
    const index=rawIndex?JSON.parse(rawIndex):[];
    if (!Array.isArray(index) || index.some(id=>typeof id!=='string'))
      throw new Error('Не удалось прочитать список архивных копий.');
    if (!index.includes(operation)) {
      const updated=JSON.stringify([...index,operation]);
      await item(tg.DeviceStorage,'setItem',archiveIndexName,updated);
      if (await item(tg.DeviceStorage,'getItem',archiveIndexName)!==updated)
        throw new Error('Телефон не подтвердил список архивных копий.');
    }
    state.archiveCount=index.includes(operation)?index.length:index.length+1;
    const previous=state.entries,receipts=state.receipts;
    state.entries=[];state.receipts=[];
    try {
      await saveLocal();await loadLocal();
      if (state.entries.length || state.receipts.length)
        throw new Error('Телефон не подтвердил отделение архивной копии от рабочего журнала.');
      await item(tg.DeviceStorage,'setItem',exitPendingName,'');
      state.exitArchivePending=false;state.exitConflict=false;
    } catch (error) {state.entries=previous;state.receipts=receipts;throw error;}
  }
  async function deactivate() {
    if (!state.active || state.lost || deactivating)
      throw new Error('Открой личный журнал на телефоне, где он был создан.');
    deactivating=true;
    try {
      await sync();
      const publicKey=(await rsaKeys(false)).publicJwk;
      const random=crypto.getRandomValues(new Uint8Array(16));
      random[6]=(random[6]&15)|64;random[8]=(random[8]&63)|128;
      const hex=[...random].map(b=>b.toString(16).padStart(2,'0')).join('');
      const operation=[hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
      state.exiting=true;
      await item(tg.DeviceStorage,'setItem',exitPendingName,operation);
      const count=state.entries.length;
      try {
        await request('/api/private/deactivate',{operation_id:operation,
          public_key:publicKey,private_entries:state.entries,receipts:state.receipts});
      } catch (error) {
        const profile=await request('/api/private/prepare',{});
        if (profile.active || profile.last_exit_operation!==operation) throw error;
      }
      state.active=false;state.lost=false;state.lostReason=null;
      try {await finishExitArchive();return {count,archivePending:false};}
      catch (error) {state.exitArchivePending=true;return {count,archivePending:true};}
    } finally {state.exiting=false;deactivating=false;}
  }
  async function restoreRecent() {
    if (!state.active || !state.lost || !['pending','unreadable'].includes(state.lostReason))
      throw new Error('Восстановление здесь не требуется.');
    const keys=await rsaKeys(false),profile=await request('/api/private/prepare',{});
    if (!profile.public_key || keys.publicJwk.n!==profile.public_key.n ||
        keys.publicJwk.e!==profile.public_key.e)
      throw new Error('Ключ на этом телефоне не подходит к временным копиям.');
    const records=await backupRecords();
    if (!records.length) throw new Error('Временных копий за последние 14 дней нет.');
    const old=await item(tg.DeviceStorage,'getItem',ledgerName);
    if (old) await item(tg.DeviceStorage,'setItem',damagedLedgerName,old);
    const previous=state.entries,receipts=state.receipts;
    state.entries=records;state.receipts=[...new Set(records.map(e=>String(e.id)))];
    const saved=JSON.stringify({entries:state.entries,receipts:state.receipts});
    try {
      await saveLocal();await loadLocal();
      if (JSON.stringify({entries:state.entries,receipts:state.receipts})!==saved)
        throw new Error('Телефон не подтвердил восстановление личных записей.');
    }
    catch (error) {state.entries=previous;state.receipts=receipts;throw error;}
    state.lost=false;state.lostReason=null;
    return records.length;
  }
  async function importUrl() {
    const url=new URL(location.href),payload=url.searchParams.get('private_entry');
    const signature=url.searchParams.get('private_sig');
    if (!payload) return null;
    if (!state.active || state.lost) throw new Error('Открой ссылку на телефоне, где включён личный журнал.');
    const verified=await request('/api/private/verify',{payload,signature});
    if (!verified.valid) throw new Error('Не удалось проверить пересланную запись.');
    const record=await decryptRecord(payload);
    const added=await add(record);
    url.searchParams.delete('private_entry');url.searchParams.delete('private_sig');
    history.replaceState(null,'',url.pathname+url.search+url.hash);
    return added ? 'Запись сохранена на этом устройстве.' : 'Эта запись уже есть на устройстве.';
  }
  window.privateMoney={state,init,activate,deactivate,finishExitArchive,rotate,add,remove,clear,edit,sync,restoreRecent,importUrl,request,available,
    get active(){return state.active && !state.lost;},get entries(){return state.entries;},
    get payload(){return state.active && !state.lost?{private_entries:state.entries}:{}}};
})();
