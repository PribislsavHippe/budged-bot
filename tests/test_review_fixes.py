"""Failure and concurrency regressions. All stores and Telegram calls are local."""
import asyncio
import io
import logging
import os
import threading
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery,EditMessageText,SendMessage

with patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid', 'SUPABASE_KEY': 'test'}), \
        patch('supabase.create_client', return_value=Mock()):
    import db
    import handlers
    import schedule_chat
    import notices
    from telegram_inbox import TelegramInbox,replay_safe_responses


TOKEN = '123456:abcdefghijklmnopqrstuvwxyzABCDE'


def update(number=100, text='чай 500', chat_type='private'):
    return {'update_id': number, 'message': {
        'message_id': number, 'date': 1791486000,
        'chat': {'id': 7 if chat_type == 'private' else -7, 'type': chat_type},
        'from': {'id': 7, 'is_bot': False, 'first_name': 'Test'}, 'text': text,
    }}


class ReviewFixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = Bot(TOKEN)
        self.dispatcher = NS(feed_raw_update=AsyncMock())
        self.inbox = TelegramInbox(self.bot, self.dispatcher)

    async def asyncTearDown(self):
        await self.bot.session.close()

    async def test_group_export_and_money_are_not_dispatched(self):
        dispatcher = Dispatcher()
        dispatcher.include_router(handlers.router)
        send = AsyncMock(side_effect=AssertionError('personal data sent to group'))
        try:
            with patch.object(self.bot.session, 'make_request', new=send), \
                    patch.object(db, 'get_all_entries', new=AsyncMock(side_effect=AssertionError('group export read'))):
                for number, text in enumerate(['/export', '500', '/history'], 1):
                    await dispatcher.feed_raw_update(self.bot, update(number, text, 'group'))
                await dispatcher.feed_raw_update(self.bot, {
                    'update_id': 10, 'callback_query': {
                        'id': 'x', 'from': {'id': 7, 'is_bot': False, 'first_name': 'Test'},
                        'chat_instance': 'x', 'data': 'export:all', 'message': update(9, 'old', 'group')['message']}})
            send.assert_not_awaited()
        finally:
            dispatcher.sub_routers.remove(handlers.router)
            handlers.router._parent_router = None

    async def test_ack_waits_for_durable_encrypted_receipt(self):
        request = NS(app={'webhook_secret': 'secret'}, headers={'X-Telegram-Bot-Api-Secret-Token': 'secret'},
                     json=AsyncMock(return_value=update()))
        entered, release = asyncio.Event(), asyncio.Event()
        captured = []

        async def persist(query):
            captured.append(query)
            entered.set()
            await release.wait()
            return NS(data=None)

        with patch.object(db.supabase, 'rpc', side_effect=lambda name, args: NS(name=name, args=args)), \
                patch.object(db, '_execute', side_effect=persist):
            response = asyncio.create_task(self.inbox.webhook(request))
            await entered.wait()
            self.assertFalse(response.done())
            sealed = captured[0].args['sealed']
            self.assertNotIn('чай', sealed)
            decoded = self.inbox.unpack({'payload': sealed, 'update_id': 100})
            self.assertEqual(decoded.message.text, 'чай 500')
            release.set()
            self.assertEqual((await response).status, 200)

    async def test_failed_receipt_returns_retry_and_safe_logs(self):
        request = NS(app={'webhook_secret': 'secret'}, headers={'X-Telegram-Bot-Api-Secret-Token': 'secret'},
                     json=AsyncMock(return_value=update()))
        output = io.StringIO()
        log = logging.StreamHandler(output)
        logging.getLogger().addHandler(log)
        try:
            with patch.object(db, '_execute', new=AsyncMock(side_effect=RuntimeError('PRIVATE_AMOUNT_54321 TOKEN'))), \
                    patch.object(db.supabase, 'rpc', return_value=NS()):
                self.assertEqual((await self.inbox.webhook(request)).status, 503)
        finally:
            logging.getLogger().removeHandler(log)
        self.assertNotIn('PRIVATE_AMOUNT', output.getvalue())
        self.assertNotIn('TOKEN', output.getvalue())
        self.assertIn('telegram_inbox', output.getvalue())

    async def test_wrong_unicode_secret_does_not_read_body(self):
        request = NS(app={'webhook_secret': 'secret'}, headers={'X-Telegram-Bot-Api-Secret-Token': 'чужой'},
                     json=AsyncMock())
        self.assertEqual((await self.inbox.webhook(request)).status, 403)
        request.json.assert_not_awaited()

    async def test_processing_failure_keeps_update_for_retry(self):
        number, actor, sealed, _ = self.inbox.pack(update())
        row = {'update_id': number, 'actor_key': actor, 'payload': sealed, 'batch_id': '00000000-0000-4000-8000-000000000001'}
        calls = []

        async def execute(query):
            calls.append(query)
            return NS(data=[row] if query.name == 'claim_telegram_batch' else None)

        self.dispatcher.feed_raw_update.side_effect = [RuntimeError('failed save'), None]
        with patch.object(db.supabase, 'rpc', side_effect=lambda name, args: NS(name=name, args=args)), \
                patch.object(db, '_execute', side_effect=execute):
            with self.assertRaises(RuntimeError):
                await self.inbox.process([row])
            self.assertFalse(calls[-1].args['succeeded'])
            await self.inbox.process([row])
            self.assertTrue(calls[-1].args['succeeded'])
        self.assertEqual(self.dispatcher.feed_raw_update.await_count, 2)
        self.assertEqual(calls[0].args['batch'], calls[2].args['batch'])

    async def test_group_forward_cannot_bypass_router(self):
        raw = update(text='Получены чаевые\nЧаевые: 500 р.', chat_type='group')
        raw['message']['forward_origin'] = {'type': 'user', 'date': 1791486000,
                                           'sender_user': {'id': 42, 'is_bot': True, 'first_name': 'Bank'}}
        self.assertIsNone(self.inbox.forwarded_key(Update.model_validate(raw)))

    async def test_slow_actor_does_not_block_another_worker(self):
        slow_started, other_finished, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        count = 0

        async def work():
            nonlocal count
            count += 1
            if count == 1:
                slow_started.set()
                await release.wait()
                return True
            other_finished.set()
            return False

        with patch.object(self.inbox, 'run_once', side_effect=work):
            self.inbox.start()
            try:
                await asyncio.wait_for(slow_started.wait(), .5)
                await asyncio.wait_for(other_finished.wait(), .5)
                self.assertFalse(release.is_set())
            finally:
                release.set()
                await self.inbox.stop()

    async def test_expired_ack_and_repeated_edit_do_not_retry_money_action(self):
        for method,reason in [(AnswerCallbackQuery(callback_query_id='old'), 'query is too old and response timeout expired'),
                              (EditMessageText(chat_id=7,message_id=1,text='Записано'), 'message is not modified')]:
            request=AsyncMock(side_effect=TelegramBadRequest(method=method,message=reason))
            self.assertTrue(await replay_safe_responses(request,self.bot,method))
        method=SendMessage(chat_id=7,text='Записано')
        request=AsyncMock(side_effect=TelegramBadRequest(method=method,message='bad request'))
        with self.assertRaises(TelegramBadRequest):
            await replay_safe_responses(request,self.bot,method)

    async def test_account_button_retry_keeps_its_target(self):
        entry={'id':42,'kind':'income','account':'cash','signed_amount':500,'category':'Чаевые','note':None}
        callback=NS(data='acc:42:card',from_user=NS(id=7),answer=AsyncMock(),
                    message=NS(edit_text=AsyncMock()))
        async def change(_id,_uid,target):
            entry['account']=target
            return entry.copy()
        with patch.object(db,'get_entry',new=AsyncMock(side_effect=lambda *args:entry.copy())), \
                patch.object(db,'update_entry_account',new=AsyncMock(side_effect=change)) as write, \
                patch.object(handlers,'today_block',new=AsyncMock(return_value='Итог')):
            await handlers.cb_toggle_account(callback)
            await handlers.cb_toggle_account(callback)
        self.assertEqual(entry['account'],'card')
        write.assert_awaited_once_with(42,7,'card')

    async def test_legacy_account_button_target_comes_from_original_label(self):
        callback=NS(data='acc:42',from_user=NS(id=7),answer=AsyncMock(),
                    message=NS(edit_text=AsyncMock(),reply_markup=NS(inline_keyboard=[[
                        NS(callback_data='acc:42',text='Изменить на безналичные')]])))
        entry={'id':42,'kind':'income','account':'card','signed_amount':500,'category':'Чаевые','note':None}
        with patch.object(db,'get_entry',new=AsyncMock(return_value=entry)), \
                patch.object(db,'update_entry_account',new=AsyncMock(return_value=entry)) as write, \
                patch.object(handlers,'today_block',new=AsyncMock(return_value='Итог')):
            await handlers.cb_toggle_account(callback)
        write.assert_not_awaited()

    async def test_reminder_cancellation_does_not_mark_delivered(self):
        entered = asyncio.Event()
        never = asyncio.Event()

        async def send():
            entered.set()
            await never.wait()

        execute = AsyncMock(return_value=NS(data=True))
        with patch.object(db.supabase, 'rpc', side_effect=lambda name, args: NS(name=name, args=args)), \
                patch.object(db, '_execute', new=execute):
            task = asyncio.create_task(notices.send_shift_notice(9, 'end', send))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(execute.await_args_list[0].args[0].name, 'claim_shift_notice')
        self.assertEqual(execute.await_args.args[0].name, 'finish_shift_notice')
        self.assertFalse(execute.await_args.args[0].args['delivered'])

    async def test_fast_query_is_not_blocked_by_other_users(self):
        entered, release = threading.Event(), threading.Event()

        def slow():
            entered.set()
            if not release.wait(2):
                raise AssertionError('test did not release slow query')
            return 'slow'

        with patch.object(db, '_db_slots', asyncio.BoundedSemaphore(2)):
            task = asyncio.create_task(db._execute(NS(execute=slow)))
            try:
                await asyncio.to_thread(entered.wait)
                fast = await asyncio.wait_for(db._execute(NS(execute=lambda: 'fast')), .5)
                self.assertEqual(fast, 'fast')
                self.assertFalse(task.done())
            finally:
                release.set()
                await task

    async def test_reply_date_overrides_pending_time_dialog(self):
        message = NS(text='23:30', from_user=NS(id=7), bot=NS(id=123456), answer=AsyncMock(),
                     reply_to_message=NS(from_user=NS(id=123456),
                                         reply_markup=NS(inline_keyboard=[[NS(callback_data='work:close:2026-10-09')]])))
        state = NS(get_state=AsyncMock(return_value=schedule_chat.Work.end.state),
                   get_data=AsyncMock(return_value={'work_day': '2026-10-02'}))
        with patch.object(schedule_chat, 'op_today', return_value=date(2026, 10, 9)), \
                patch.object(schedule_chat, 'ask_end', new=AsyncMock()) as ask, \
                patch.object(schedule_chat, 'end_text', new=AsyncMock()) as old:
            await schedule_chat.time_message(message, state)
        self.assertEqual(ask.await_args.args[2], '2026-10-09')
        old.assert_not_awaited()

    async def test_private_retry_does_not_restore_cancelled_or_transferred_money(self):
        message = NS(chat=NS(id=7), message_id=20, from_user=NS(id=7), answer=AsyncMock())
        with patch.object(db, 'get_or_create_user', new=AsyncMock(return_value={'private_money_public_key': {'n': 'key'}})), \
                patch.object(db, 'money_source_seen', new=AsyncMock(return_value=True)), \
                patch.object(db, 'save_private_backup', new=AsyncMock()) as save:
            await handlers._send_private_record(message, {'kind': 'income', 'account': 'cash',
                'signed_amount': 500, 'category': 'Чаевые'}, 0)
        save.assert_not_awaited()
        self.assertIn('уже обработал', message.answer.await_args.args[0])

    async def test_expense_dialog_and_replay_context_survive_restart(self):
        original=Dispatcher();inbox=TelegramInbox(self.bot,original)
        message=Update.model_validate(update(text='350'))
        actor=inbox.pack(update(text='350'))[1]
        row={'update_id':100,'actor_key':actor,'execution_context':None}
        token='00000000-0000-4000-8000-000000000001'
        context=inbox.dialog_context(message)
        await context.set_state(handlers.ShiftSpend.waiting_amount)
        await context.set_data({'shift_category':'Такси'})
        saved_actor=None;saved_execution=None
        class Read:
            def select(self,*args):return self
            def eq(self,*args):return self
            def limit(self,*args):return self
        async def execute(query):
            nonlocal saved_actor,saved_execution
            if isinstance(query,Read):return NS(data=[{'payload':saved_actor}] if saved_actor else [])
            if query.name=='save_telegram_actor_state':
                saved_actor=query.args['sealed_state'];return NS(data=None)
            if query.name=='remember_telegram_context':
                saved_execution=saved_execution or query.args['sealed_context'];return NS(data=saved_execution)
            raise AssertionError(query.name)
        with patch.object(db.supabase,'rpc',side_effect=lambda name,args:NS(name=name,args=args)), \
                patch.object(db.supabase,'table',side_effect=lambda *args:Read()),patch.object(db,'_execute',side_effect=execute):
            await inbox.persist_dialog_context(message,row,token)
            self.assertNotIn('Такси',saved_actor)
            restarted=TelegramInbox(self.bot,Dispatcher())
            await restarted.restore_execution_context(message,row,token)
            after=restarted.dialog_context(message)
            self.assertEqual(await after.get_state(),handlers.ShiftSpend.waiting_amount.state)
            self.assertEqual(await after.get_data(),{'shift_category':'Такси'})
            await after.clear()
            await restarted.persist_dialog_context(message,row,token)
            self.assertIsNone(saved_actor)
            row['execution_context']=saved_execution
            again=TelegramInbox(self.bot,Dispatcher())
            await again.restore_execution_context(message,row,token)
            self.assertEqual(await again.dialog_context(message).get_state(),handlers.ShiftSpend.waiting_amount.state)

    async def test_bad_expense_amount_does_not_poison_the_queue(self):
        message=NS(text='-100',answer=AsyncMock())
        with patch.object(db,'add_entry',new=AsyncMock()) as write:
            await handlers.shift_spend_amount(message,NS())
        write.assert_not_awaited()
        message.answer.assert_awaited_once()

    def test_message_date_stays_stable_after_restart(self):
        stamp = datetime(2026, 10, 9, 0, 30, tzinfo=timezone.utc)
        with patch.object(handlers, 'op_today', return_value=date(2026, 10, 10)):
            self.assertEqual(handlers.message_work_date(NS(date=stamp)), '2026-10-08')


if __name__ == '__main__':
    unittest.main()
