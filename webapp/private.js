/* Personal finance ledger stored on the current Telegram device.
   The server retains only device-key-encrypted recovery copies for 14 days. */
(function () {
  const tg = window.Telegram && window.Telegram.WebApp;
  const ledgerName = 'money_ledger_v1';
  const damagedLedgerName = 'money_ledger_damaged_v1';
  const aesName = 'money_aes_key_v1';
  const rsaName = 'money_rsa_private_v1';
  const state = {active:false,ready:false,lost:false,lostReason:null,entries:[],receipts:[],today:null};
  let syncing=null;

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
      const done=(error,result)=>error ? reject(new Error(String(error)))
        : method==='setItem' && result!==true
          ? reject(new Error('Устройство не подтвердило сохранение. Записи в базе не удалены.'))
          : resolve(result);
      if (method==='setItem') storage.setItem(key,value,done);
      else storage.getItem(key,done);
    });
  }
  async function request(path,extra) {
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},cache:'no-store',
      body:JSON.stringify({initData:tg?.initData||'',...extra})});
    const body=await response.json();
    if (!response.ok) throw new Error(body.error||'Не получилось связаться с ботом.');
    return body;
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
    const bytes=decode(packed),key=await aesKey(false);
    const plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:bytes.subarray(0,12)},key,bytes.subarray(12));
    const saved=JSON.parse(new TextDecoder().decode(plain));
    const entries=Array.isArray(saved)?saved:saved?.entries;
    if (!Array.isArray(entries)) throw new Error('Не удалось прочитать личный журнал.');
    state.entries=entries;
    state.receipts=Array.isArray(saved)?entries.map(e=>String(e.id)):
      Array.isArray(saved.receipts)?saved.receipts.map(String):[];
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
    const pair=await crypto.subtle.generateKey({name:'RSA-OAEP',modulusLength:2048,
      publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['encrypt','decrypt']);
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
    state.lost=false;state.lostReason=null;
    if (!available()) {
      state.lost=state.active;state.lostReason='unsupported';
      return state;
    }
    try {
      const [key,ledger,rsa]=await Promise.all([
        item(tg.SecureStorage,'getItem',aesName),
        item(tg.DeviceStorage,'getItem',ledgerName),
        item(tg.SecureStorage,'getItem',rsaName)]);
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
  async function activate() {
    if (!available()) throw new Error('Обнови Telegram: хранение на устройстве здесь недоступно.');
    if (state.lostReason==='unreadable')
      throw new Error('Не удалось прочитать прежний журнал на устройстве. Данные в базе сохранены.');
    const result=await request('/api/private/prepare',{});
    if (result.active) throw new Error('Приватный режим уже включён.');
    const keys=await rsaKeys(true);
    await aesKey(true);
    const byId=new Map(state.entries.map(e=>[String(e.id),e]));
    result.entries.forEach(e=>byId.set(String(e.id),e));
    state.entries=[...byId.values()];
    state.receipts=[...new Set([...state.receipts,...state.entries.map(e=>String(e.id))])];
    const saved=JSON.stringify({entries:state.entries,receipts:state.receipts});
    await saveLocal();
    await loadLocal();
    if (JSON.stringify({entries:state.entries,receipts:state.receipts})!==saved)
      throw new Error('Перенос не подтвердился. Серверные записи сохранены.');
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
    const previous=state.entries;
    state.entries=previous.filter(e=>String(e.id)!==String(id));
    try {await saveLocal();} catch (e) {state.entries=previous;throw e;}
  }
  async function clear() {
    if (!state.active || state.lost) throw new Error('Личный журнал на этом устройстве недоступен.');
    await request('/api/private/clear_backups',{});
    const previous=state.entries,receipts=state.receipts;
    state.entries=[];state.receipts=[];
    try {await saveLocal();} catch (e) {state.entries=previous;state.receipts=receipts;throw e;}
  }
  async function edit(id,patch) {
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
    if (!state.active || state.lost) return 0;
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
  window.privateMoney={state,init,activate,rotate,add,remove,clear,edit,sync,restoreRecent,importUrl,request,available,
    get active(){return state.active && !state.lost;},get entries(){return state.entries;},
    get payload(){return state.active && !state.lost?{private_entries:state.entries}:{}}};
})();
