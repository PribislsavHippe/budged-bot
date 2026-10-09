"""Persist encrypted updates before acknowledging Telegram; retry after crashes.

Finished rows retain only delivery metadata and a keyed digest. No message body
or amount is stored after successful processing. Pending rows are never pruned.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
from datetime import datetime,timezone,timedelta
from uuid import uuid4

from aiohttp import web
from aiogram.types import Update
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery,EditMessageText,EditMessageReplyMarkup,DeleteMessage
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import db
from diagnostics import failure


async def replay_safe_responses(make_request,bot,method):
    """Expired UI acknowledgments must not keep retrying a saved money action."""
    try:return await make_request(bot,method)
    except TelegramBadRequest as error:
        reason=error.message.lower()
        if isinstance(method,AnswerCallbackQuery) and ('query is too old' in reason or 'query id is invalid' in reason):
            return True
        if isinstance(method,(EditMessageText,EditMessageReplyMarkup)) and 'message is not modified' in reason:
            return True
        if isinstance(method,DeleteMessage) and 'message to delete not found' in reason:return True
        if isinstance(method,EditMessageText) and method.chat_id is not None and (
                "message can't be edited" in reason or 'message to edit not found' in reason):
            return await bot.send_message(method.chat_id,method.text,reply_markup=method.reply_markup,
                                          parse_mode=method.parse_mode)
        raise


class TelegramInbox:
    def __init__(self,bot,dispatcher):
        self.bot=bot;self.dispatcher=dispatcher
        secret=os.getenv('INBOX_ENCRYPTION_KEY') or bot.token
        self.key=hashlib.sha256(('budget-bot-inbox-v1:'+secret).encode()).digest()
        self.wake=asyncio.Event();self.tasks=[]

    def pack(self,raw):
        update=Update.model_validate(raw)
        normalized=update.model_dump(mode='json',exclude_none=True)
        plain=json.dumps(normalized,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        if len(plain)>1000000:raise ValueError('telegram_update_too_large')
        nonce=os.urandom(12)
        sealed=base64.urlsafe_b64encode(nonce+AESGCM(self.key).encrypt(nonce,plain,str(update.update_id).encode())).decode()
        user=getattr(getattr(update,'message',None),'from_user',None) or getattr(getattr(update,'callback_query',None),'from_user',None)
        actor=str(user.id) if user else 'update:'+str(update.update_id)
        actor=hmac.new(self.key,actor.encode(),hashlib.sha256).hexdigest()
        digest=hmac.new(self.key,plain,hashlib.sha256).hexdigest()
        return update.update_id,actor,sealed,digest

    def unpack(self,row):
        sealed=base64.urlsafe_b64decode(row['payload'])
        raw=AESGCM(self.key).decrypt(sealed[:12],sealed[12:],str(row['update_id']).encode())
        return Update.model_validate_json(raw,context={'bot':self.bot})

    async def enqueue(self,raw):
        number,actor,sealed,digest=self.pack(raw)
        await db._execute(db.supabase.rpc('store_telegram_update',{
            'update_no':number,'actor_name':actor,'sealed':sealed,'digest':digest}))
        self.wake.set()

    async def webhook(self,request):
        expected=request.app['webhook_secret']
        if not hmac.compare_digest(request.headers.get('X-Telegram-Bot-Api-Secret-Token','').encode(),expected.encode()):
            return web.Response(status=403)
        try:
            raw=await request.json()
            await self.enqueue(raw)
        except Exception as error:
            failure(error,area='telegram_inbox',stage='receive')
            # Telegram must retry when durable receipt is unconfirmed.
            return web.Response(status=503)
        return web.json_response({})

    def start(self):
        # SQL claims keep each person's messages in order. Independent actors
        # can progress while another employee's photo or provider request waits.
        self.tasks=[asyncio.create_task(self.run()) for _ in range(4)]

    async def stop(self):
        for task in self.tasks:task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        self.tasks=[]

    @staticmethod
    def forwarded_key(update):
        message=update.message
        if not message or message.chat.type!='private' or not message.forward_origin or not message.text:return None
        import parser
        if not (parser.looks_like_bank_tips(message.text) or parser.parse_bank_notification(message.text)):
            return None
        from handlers import _forward_source,message_work_date
        return (_forward_source(message.forward_origin),message_work_date(message))

    async def choose(self,rows):
        by_actor={}
        for row in rows:by_actor.setdefault(row['actor_key'],[]).append(row)
        now=datetime.now(timezone.utc)
        for group in by_actor.values():
            first=group[0]
            def ready(row):
                return datetime.fromisoformat(row['next_attempt_at'].replace('Z','+00:00'))<=now and (
                    not row['claimed_until'] or datetime.fromisoformat(row['claimed_until'].replace('Z','+00:00'))<=now)
            if not ready(first):continue
            if first['batch_id']:
                # Keep exactly the same members when retrying an assigned batch.
                group=(await db._execute(db.supabase.table('telegram_inbox').select('*')
                        .eq('batch_id',first['batch_id']).is_('finished_at','null').order('update_id'))).data
                return group
            key=self.forwarded_key(self.unpack(first))
            if key is None:return [first]
            group=(await db._execute(db.supabase.table('telegram_inbox').select('*')
                   .eq('actor_key',first['actor_key']).is_('finished_at','null').order('update_id').limit(100))).data
            selected=[]
            for row in group[:100]:
                if row['batch_id'] or not ready(row) or self.forwarded_key(self.unpack(row))!=key:break
                selected.append(row)
            received=datetime.fromisoformat(selected[-1]['received_at'].replace('Z','+00:00'))
            if len(selected)==100 or now-received>=timedelta(seconds=2):return selected
        return []

    async def heartbeat(self,batch,token):
        while True:
            await asyncio.sleep(30)
            await db._execute(db.supabase.rpc('renew_telegram_batch',{'batch':batch,'token':token}))

    def dialog_context(self,update):
        # A replay after restarting must keep the original meaning of a number
        # entered inside an expense or hours dialog, not reinterpret it as tips.
        if not getattr(self.dispatcher,'fsm',None):return None
        message=update.message or getattr(update.callback_query,'message',None)
        user=getattr(update.message,'from_user',None) or getattr(update.callback_query,'from_user',None)
        if not message or not user or message.chat.type!='private':return None
        return self.dispatcher.fsm.get_context(bot=self.bot,chat_id=message.chat.id,user_id=user.id)

    def seal_context(self,snapshot,aad):
        plain=json.dumps(snapshot,ensure_ascii=False,default=str).encode()
        if len(plain)>1000000:raise ValueError('telegram_context_too_large')
        nonce=os.urandom(12)
        return base64.urlsafe_b64encode(nonce+AESGCM(self.key).encrypt(nonce,plain,aad)).decode()

    def open_context(self,sealed,aad):
        packed=base64.urlsafe_b64decode(sealed)
        return json.loads(AESGCM(self.key).decrypt(packed[:12],packed[12:],aad))

    async def restore_execution_context(self,update,row,token):
        context=self.dialog_context(update)
        if context is None:return
        sealed=row.get('execution_context')
        aad=('execution:'+str(update.update_id)).encode()
        if sealed is None:
            prior=(await db._execute(db.supabase.table('telegram_actor_state').select('payload')
                   .eq('actor_key',row['actor_key']).limit(1))).data
            snapshot=(self.open_context(prior[0]['payload'],('actor:'+row['actor_key']).encode()) if prior else
                      {'state':await context.get_state(),'data':await context.get_data()})
            candidate=self.seal_context(snapshot,aad)
            sealed=(await db._execute(db.supabase.rpc('remember_telegram_context',{
                'update_no':update.update_id,'token':token,'sealed_context':candidate}))).data
        snapshot=self.open_context(sealed,aad)
        await context.set_state(snapshot['state']);await context.set_data(snapshot['data'])

    async def persist_dialog_context(self,update,row,token):
        context=self.dialog_context(update)
        if context is None:return
        snapshot={'state':await context.get_state(),'data':await context.get_data()}
        sealed=self.seal_context(snapshot,('actor:'+row['actor_key']).encode()) if snapshot['state'] or snapshot['data'] else None
        await db._execute(db.supabase.rpc('save_telegram_actor_state',{
            'update_no':update.update_id,'token':token,'sealed_state':sealed}))

    async def process(self,rows):
        batch=str(rows[0]['batch_id'] or uuid4());token=str(uuid4())
        claimed=(await db._execute(db.supabase.rpc('claim_telegram_batch',{
            'ids':[r['update_id'] for r in rows],'batch':batch,'token':token}))).data
        if not claimed:return
        claimed.sort(key=lambda r:r['update_id'])
        beat=asyncio.create_task(self.heartbeat(batch,token));succeeded=False
        try:
            updates=[self.unpack(row) for row in claimed]
            if self.forwarded_key(updates[0]) is not None:
                from handlers import save_forwarded_tips
                import parser
                await save_forwarded_tips([(u.message,parser.parse_bank_notification(u.message.text)) for u in updates])
            else:
                for update,row in zip(updates,claimed):
                    await self.restore_execution_context(update,row,token)
                    await self.dispatcher.feed_raw_update(self.bot,update.model_dump(mode='json',exclude_none=True),
                                                          inbox_update_id=row['update_id'])
                    await self.persist_dialog_context(update,row,token)
            succeeded=True
        finally:
            beat.cancel()
            try:await beat
            except asyncio.CancelledError:pass
            except Exception as error:
                failure(error,area='telegram_inbox',stage='renew')
            # On a killed process the claim expires; retrying preserves source IDs.
            await asyncio.shield(db._execute(db.supabase.rpc('finish_telegram_batch',{
                'batch':batch,'token':token,'succeeded':succeeded})))

    async def run_once(self):
        rows=(await db._execute(db.supabase.rpc('ready_telegram_heads',{}))).data
        chosen=await self.choose(rows)
        if chosen:await self.process(chosen)
        return bool(chosen)

    async def run(self):
        idle=.5
        while True:
            # Clear before reading the database, so a receipt committed during
            # that read wakes us immediately instead of waiting for the poll.
            self.wake.clear()
            try:
                if await self.run_once():
                    idle=.5
                    continue
            except Exception as error:
                failure(error,area='telegram_inbox',stage='process')
            try:
                await asyncio.wait_for(self.wake.wait(),timeout=idle)
                idle=.5
            except TimeoutError:
                idle=min(10,idle*2)

    async def prune_finished(self):
        cutoff=(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
        await db._execute(db.supabase.table('telegram_inbox').delete().lt('finished_at',cutoff))
