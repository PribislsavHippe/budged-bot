"""Forwarded bank notifications form one confirmed tip entry per short batch."""
import asyncio
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid', 'SUPABASE_KEY': 'test'}), \
     patch('supabase.create_client'):
    import handlers
    import private_payload
    import ux_chat


class ForwardedTips(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        seen=patch.object(handlers.db,"money_source_seen",new=AsyncMock(return_value=False));seen.start();self.addCleanup(seen.stop)
        self.delay=handlers._forward_quiet_seconds
        handlers._forward_quiet_seconds=0.02
        self.bot=NS(delete_message=AsyncMock())

    def tearDown(self):
        for batch in handlers._forward_batches.values():
            batch['timer'].cancel()
        handlers._forward_batches.clear()
        handlers._forward_quiet_seconds=self.delay

    def message(self, mid, amount=None):
        text=f'Получены чаевые\nЧаевые: {amount}.00 р.' if amount is not None else 'Неизвестное уведомление'
        return NS(text=text,from_user=NS(id=7,first_name='Test'),chat=NS(id=7),
                  message_id=mid,forward_origin=NS(sender_user=NS(id=42)),
                  answer=AsyncMock(return_value=NS(edit_text=AsyncMock())),bot=self.bot)

    async def test_three_forwards_make_one_entry_and_one_amount_reply(self):
        messages=[self.message(i,amount) for i,amount in [(11,100),(12,250),(13,50)]]
        messages[0].text='Получены чаевые\nСумма заказа: 7690.00 р.\nЧаевые: 100.00 р.'
        entry={'id':9,'kind':'income','account':'card','signed_amount':400,
               'category':'Чаевые','note':'из банка'}
        with patch.object(handlers.db,'get_or_create_user',new=AsyncMock(return_value={'onboarded':True,'private_money_mode':False})), \
             patch.object(handlers.db,'add_entry',new=AsyncMock(return_value=entry)) as save, \
             patch.object(handlers,'today_block',new=AsyncMock(return_value='Итог')), \
             patch.object(ux_chat,'value_saved',new=AsyncMock()):
            for message in messages:
                await handlers.handle_text(message,NS())
            await handlers.handle_text(messages[1],NS())  # repeated webhook update
            await asyncio.sleep(0.08)
        save.assert_awaited_once()
        self.assertEqual(save.await_args.args[:4],(7,'income','card',400.0))
        self.assertEqual(save.await_args.kwargs['source_key'],'telegram:7:11:0')
        messages[0].answer.assert_awaited_once()
        self.assertIn('400 ₽',messages[0].answer.await_args.args[0])
        self.assertIn('3 уведомлений',messages[0].answer.await_args.args[0])
        for message in messages[1:]:message.answer.assert_not_awaited()

    async def test_private_batch_stores_one_copy_and_removes_all_sources(self):
        messages=[self.message(i,amount) for i,amount in [(21,100),(22,250),(23,50)]]
        sealed=[]
        def seal(_uid,_key,record,_token):
            sealed.append(record)
            return 'encrypted','signature'
        with patch.dict(os.environ,{'BOT_TOKEN':'token'}), \
             patch.object(handlers.db,'get_or_create_user',new=AsyncMock(return_value={
                 'onboarded':True,'private_money_mode':True,'private_money_public_key':{'n':'key'}})), \
             patch.object(handlers.db,'save_private_backup',new=AsyncMock()) as save, \
             patch.object(private_payload,'seal',side_effect=seal), \
             patch.object(private_payload,'key_id',return_value='key-id'):
            for message in messages:
                await handlers.handle_text(message,NS())
            await asyncio.sleep(0.08)
        save.assert_awaited_once()
        self.assertEqual(sealed[0]['signed_amount'],400.0)
        self.assertIn('400 ₽',messages[0].answer.await_args.args[0])
        self.assertIn('безналичные',messages[0].answer.await_args.args[0])
        self.assertNotIn('14 дней',messages[0].answer.await_args.args[0])
        self.assertEqual(messages[0].answer.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data,
                         'pacc:21:0:cash')
        self.assertEqual(self.bot.delete_message.await_count,3)

    async def test_private_account_change_is_encrypted_and_confirmed(self):
        change=[]
        callback=NS(id='callback-1',data='pacc:21:0:cash',from_user=NS(id=7),
                    message=NS(chat=NS(id=7),text='Записал чаевые: 400 ₽ · безналичные.\nВ «Статистике» появится автоматически.',
                               edit_text=AsyncMock()),answer=AsyncMock())
        def seal(_uid,_key,record,_token):
            change.append(record)
            return 'encrypted','signature'
        with patch.dict(os.environ,{'BOT_TOKEN':'token'}), \
             patch.object(handlers.db,'get_or_create_user',new=AsyncMock(return_value={
                 'private_money_mode':True,'private_money_public_key':{'n':'key'}})), \
             patch.object(handlers.db,'save_private_backup',new=AsyncMock()) as save, \
             patch.object(private_payload,'seal',side_effect=seal), \
             patch.object(private_payload,'key_id',return_value='key-id'):
            await handlers.cb_private_account(callback)
        self.assertEqual(change[0]['target_id'],'telegram:7:21:0')
        self.assertEqual(change[0]['account'],'cash')
        self.assertEqual(change[0]['kind'],'account_change')
        save.assert_awaited_once()
        self.assertIn('наличные',callback.message.edit_text.await_args.args[0])

    async def test_private_chat_expense_reply_shows_amount_without_deadline(self):
        message=self.message(45)
        with patch.dict(os.environ,{'BOT_TOKEN':'token'}), \
             patch.object(handlers.db,'get_or_create_user',new=AsyncMock(return_value={
                 'private_money_mode':True,'private_money_public_key':{'n':'key'}})), \
             patch.object(handlers.db,'save_private_backup',new=AsyncMock()), \
             patch.object(private_payload,'seal',return_value=('encrypted','signature')), \
             patch.object(private_payload,'key_id',return_value='key-id'):
            saved=await handlers._send_private_record(message,{
                'kind':'expense','account':'cash','signed_amount':-430,
                'category':'Такси','note':'такси 430'},0)
        self.assertTrue(saved)
        reply=message.answer.await_args.args[0]
        self.assertIn('430 ₽',reply)
        self.assertIn('наличные',reply)
        self.assertNotIn('14 дней',reply)

    async def test_failed_account_change_does_not_claim_success(self):
        callback=NS(id='callback-2',data='pacc:21:0:card',from_user=NS(id=7),
                    message=NS(chat=NS(id=7),text='Записал чаевые: 400 ₽ · наличные.',
                               edit_text=AsyncMock()),answer=AsyncMock())
        with patch.dict(os.environ,{'BOT_TOKEN':'token'}), \
             patch.object(handlers.db,'get_or_create_user',new=AsyncMock(return_value={
                 'private_money_mode':True,'private_money_public_key':{'n':'key'}})), \
             patch.object(handlers.db,'save_private_backup',new=AsyncMock(side_effect=RuntimeError('write failed'))), \
             patch.object(private_payload,'seal',return_value=('encrypted','signature')), \
             patch.object(private_payload,'key_id',return_value='key-id'):
            await handlers.cb_private_account(callback)
        callback.message.edit_text.assert_not_awaited()
        self.assertIn('Не получилось',callback.answer.await_args.args[0])

    async def test_unreadable_forward_stops_whole_batch(self):
        messages=[self.message(31,100),self.message(32,None)]
        with patch.object(handlers.db,'get_or_create_user',new=AsyncMock(return_value={
                 'onboarded':True,'private_money_mode':False})), \
             patch.object(handlers.db,'add_entry',new=AsyncMock()) as save:
            for message in messages:
                await handlers.handle_text(message,NS())
            await asyncio.sleep(0.08)
        save.assert_not_awaited()
        self.assertIn('Ничего не записал',messages[0].answer.await_args.args[0])
        messages[1].answer.assert_not_awaited()

    async def test_write_failure_never_claims_amount_saved(self):
        message=self.message(41,250)
        with patch.object(handlers.db,'get_or_create_user',new=AsyncMock(return_value={
                 'onboarded':True,'private_money_mode':False})), \
             patch.object(handlers.db,'add_entry',new=AsyncMock(side_effect=RuntimeError('database'))):
            await handlers.handle_text(message,NS())
            await asyncio.sleep(0.08)
        message.answer.assert_awaited_once()
        self.assertIn('Не получил подтверждение',message.answer.await_args.args[0])
        self.assertNotIn('250 ₽',message.answer.await_args.args[0])


if __name__=='__main__':unittest.main()
