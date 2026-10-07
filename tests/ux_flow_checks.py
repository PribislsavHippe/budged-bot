"""Learn by doing invokes the ordinary transaction parser and persistence."""
import asyncio,os,sys,unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with patch.dict(os.environ,{'SUPABASE_URL':'https://example.invalid','SUPABASE_KEY':'test'}),patch('supabase.create_client'):
    import handlers,ux_chat,research

class Flows(unittest.IsolatedAsyncioTestCase):
    def message(self,text):
        return NS(text=text,from_user=NS(id=5,first_name='Test'),forward_origin=None,answer=AsyncMock(),chat=NS(id=5),bot=NS(id=1),message_id=100)
    async def run_entry(self,text,analytics):
        message=self.message(text);entry={'id':10,'kind':'income','account':'cash','signed_amount':1500,'category':'Чаевые','note':None}
        with patch.object(handlers.db,'get_or_create_user',new=AsyncMock(return_value={'id':5,'onboarded':True,'tutorial_step':'tip'})),patch.object(handlers.db,'get_recent_entries',new=AsyncMock(return_value=[])),patch.object(handlers.db,'add_entry',new=AsyncMock(return_value=entry)) as saved,patch.object(handlers,'today_block',new=AsyncMock(return_value='Итог')),patch.object(research,'record',new=analytics):
            await handlers.handle_text(message,NS(clear=AsyncMock()))
            saved.assert_awaited_once();self.assertEqual(saved.await_args.args[:4],(5,'income','cash',1500.0))
        return message
    async def test_start_real_tip_and_hint(self):
        message=self.message('/start');state=NS(clear=AsyncMock())
        with patch.object(handlers.db,'get_or_create_user',new=AsyncMock(return_value={'id':5,'onboarded':False,'tutorial_step':'new'})),patch.object(handlers.db,'set_onboarded',new=AsyncMock()),patch.object(research,'subject',new=AsyncMock(return_value={'cohort':'new','onboarding_state':'not_started'})),patch.object(research,'record',new=AsyncMock(return_value={'state':'started'})),patch.object(research,'track'):
            await handlers.cmd_start(message,state)
        self.assertEqual(message.answer.await_count,1)
        self.assertIn('чай 1500',message.answer.await_args.args[0])
        self.assertNotIn('Твои смены — в одном месте',message.answer.await_args.args[0])
        self.assertTrue(message.answer.await_args.kwargs['reply_markup'].remove_keyboard)
        recorded=AsyncMock(return_value={'first_value':True,'was_learning':True})
        message=await self.run_entry('чай 1500',recorded)
        self.assertEqual(recorded.await_args.args,(5,'tip_added'))
        self.assertNotIn('1500',str(recorded.await_args))
        self.assertIn('такси 430',message.answer.await_args_list[-1].args[0])
    async def test_existing_no_forced_onboarding(self):
        message=self.message('/start')
        with patch.object(handlers.db,'get_or_create_user',new=AsyncMock(return_value={'id':5,'onboarded':True,'tutorial_step':'tip'})),patch.object(handlers,'today_block',new=AsyncMock(return_value='Итог')),patch.object(ux_chat,'begin',new=AsyncMock()) as begin,patch.object(research,'track'):
            await handlers.cmd_start(message,NS(clear=AsyncMock()));begin.assert_not_awaited()
        self.assertTrue(message.answer.await_args.kwargs['reply_markup'].remove_keyboard)
    async def test_repeat_for_existing_user_without_analytics(self):
        message=self.message('/learn')
        with patch.object(handlers.db,'get_or_create_user',new=AsyncMock(return_value={'id':5,'onboarded':True,'tutorial_step':None})),patch.object(handlers.db,'_execute',new=AsyncMock()) as saved,patch.object(research,'record',new=AsyncMock(return_value=None)),patch.object(research,'track'):
            await ux_chat.learn_message(message)
            saved.assert_awaited_once()
            self.assertIn('Что хочешь сделать сейчас?',message.answer.await_args.args[0])
    async def test_invite_name_then_new_user_lesson(self):
        import identity_chat
        message=self.message('Тест Имя')
        state=NS(get_data=AsyncMock(return_value={'invite_token':'test-token'}),clear=AsyncMock())
        with patch.object(identity_chat.identity,'request',new=AsyncMock(return_value={'status':'pending','report_name':'Тест Имя'})),patch.object(handlers.db,'get_or_create_user',new=AsyncMock(return_value={'id':5,'tutorial_step':'new'})),patch.object(handlers.db,'_execute',new=AsyncMock()),patch.object(research,'record',new=AsyncMock(return_value=None)),patch.object(research,'track'):
            await identity_chat.register_name(message,state)
            state.clear.assert_awaited_once()
            self.assertIn('Теперь можно пользоваться ботом',message.answer.await_args_list[0].args[0])
            self.assertNotIn('подтверждения',message.answer.await_args_list[0].args[0])
            self.assertIn('чай 1500',message.answer.await_args.args[0])

    async def test_help_is_one_short_choice_and_faq_has_an_answer(self):
        message=self.message('/help')
        with patch.object(research,'track'):
            await handlers.send_help(message,5)
        self.assertEqual(message.answer.await_count,1)
        self.assertNotIn('/reset',message.answer.await_args.args[0])
        markup=message.answer.await_args.kwargs['reply_markup']
        self.assertEqual(markup.inline_keyboard[0][0].text,'Написать Леше')
        self.assertEqual(markup.inline_keyboard[0][0].url,'https://t.me/lechatsergeev')
        self.assertEqual(markup.inline_keyboard[1][0].text,'Частые вопросы')
        callback=NS(data='ux:faq:tips',message=message,answer=AsyncMock())
        await ux_chat.faq_answer(callback)
        self.assertIn('чай 1500',message.answer.await_args.args[0])

    async def test_first_action_buttons_keep_real_input(self):
        message=self.message('');message.edit_reply_markup=AsyncMock()
        callback=NS(from_user=NS(id=5),answer=AsyncMock(),message=message)
        await ux_chat.choose_tip(callback)
        self.assertIn('чай 1500',message.answer.await_args.args[0])
        message.edit_reply_markup.assert_awaited_once()
        message.answer.reset_mock();message.edit_reply_markup.reset_mock()
        await ux_chat.choose_schedule(callback)
        self.assertIn('фото графика',message.answer.await_args.args[0])
        self.assertNotIn('1500 ₽ записаны',message.answer.await_args.args[0])

    async def test_skip_then_normal_work(self):
        message=self.message('чай 1500');message.edit_reply_markup=AsyncMock()
        callback=NS(from_user=NS(id=5),answer=AsyncMock(),message=message)
        with patch.object(research,'record',new=AsyncMock(return_value={})) as recorded:
            await ux_chat.skip(callback);self.assertEqual(recorded.await_args.args,(5,'onboarding_skipped'))
        await self.run_entry('чай 1500',AsyncMock(return_value={'first_value':True,'was_learning':False}))
    async def test_analytics_failure_still_returns_saved_confirmation(self):
        with patch.dict(os.environ,{'UX_RESEARCH_ENABLED':'1'}),patch.object(research,'client'),patch.object(research,'execute',new=AsyncMock(side_effect=RuntimeError('failure'))):
            real_record=research.record
            message=await self.run_entry('чай 1500',real_record)
            self.assertTrue(any('1 500' in call.args[0] or '1500' in call.args[0] for call in message.answer.await_args_list))

    async def test_history_undo_button_uses_clicker_and_rejects_stale_view(self):
        message=self.message('');message.from_user.id=999
        entry={'id':42,'kind':'income','account':'cash','signed_amount':500,'category':'Чаевые','note':None}
        with patch.object(handlers.db,'get_or_create_user',new=AsyncMock(return_value={'private_money_mode':False})),patch.object(handlers.db,'get_recent_entries',new=AsyncMock(return_value=[entry])),patch.object(handlers.db,'delete_entry',new=AsyncMock()) as delete,patch.object(handlers,'today_block',new=AsyncMock(return_value='Итог')):
            await handlers.cmd_undo(message,user_id=5,expected_id='41')
            delete.assert_not_awaited()
            await handlers.cmd_undo(message,user_id=5,expected_id='42')
            delete.assert_awaited_once_with(42,5)

    async def test_private_tip_has_undo_and_saves_cancellation_for_clicker(self):
        message=self.message('чай 1500');message.bot.delete_message=AsyncMock()
        key={'kty':'RSA','alg':'RSA-OAEP-256','n':'placeholder','e':'AQAB'}
        user={'id':5,'onboarded':True,'private_money_mode':True,
              'private_money_public_key':key}
        with patch.dict(os.environ,{'BOT_TOKEN':'test-bot-token'}),\
             patch.object(handlers.db,'get_or_create_user',new=AsyncMock(return_value=user)),\
             patch('private_payload.seal',return_value=('sealed','signature')),\
             patch('private_payload.key_id',return_value='key-id'),\
             patch.object(handlers.db,'save_private_backup',new=AsyncMock()) as save:
            await handlers.handle_text(message,NS(clear=AsyncMock()))
            reply=message.answer.await_args.kwargs['reply_markup']
            undo=reply.inline_keyboard[-1][0]
            self.assertEqual(undo.callback_data,'pundo:100:0')
            self.assertEqual(save.await_count,1)
            callback=NS(data=undo.callback_data,from_user=NS(id=5),
                        message=NS(chat=NS(id=5),text='Записал чаевые: 1 500 ₽ · наличные.',
                                   edit_text=AsyncMock()),answer=AsyncMock(),id='click-1')
            await handlers.cb_private_undo(callback)
            self.assertEqual(save.await_count,2)
            self.assertEqual(save.await_args.args[:2],(5,'telegram:5:100:0:cancel'))
            callback.message.edit_text.assert_awaited_once()

            save.side_effect=RuntimeError('storage unavailable')
            failed=NS(data='pundo:100:1',from_user=NS(id=5),
                      message=NS(chat=NS(id=5),text='Записал чаевые: 500 ₽ · наличные.',
                                 edit_text=AsyncMock()),answer=AsyncMock(),id='click-2')
            await handlers.cb_private_undo(failed)
            failed.message.edit_text.assert_not_awaited()
            self.assertTrue(failed.answer.await_args.kwargs['show_alert'])

    async def test_spend_cancel_button_clears_pending_amount(self):
        message=self.message('');message.edit_text=AsyncMock()
        callback=NS(data='ss:cancel',from_user=NS(id=5),message=message,answer=AsyncMock())
        state=NS(get_state=AsyncMock(return_value=handlers.ShiftSpend.waiting_amount.state),clear=AsyncMock())
        await handlers.shift_spend_chip(callback,state)
        state.clear.assert_awaited_once()
        message.edit_text.assert_awaited_once_with('Расход не записан.')

    async def test_calendar_retry_button_uses_clicker(self):
        import google_calendar
        message=self.message('');message.from_user.id=999
        callback=NS(data='calendar:retry',from_user=NS(id=5),message=message,answer=AsyncMock())
        with patch.object(google_calendar,'is_configured',return_value=True),patch.object(google_calendar,'connection_status',new=AsyncMock(return_value={'connected':True})),patch.object(google_calendar,'sync_pending',new=AsyncMock(return_value={'synced':1,'pending':0,'message':''})) as sync:
            await handlers.calendar_retry(callback)
            sync.assert_awaited_once_with(5)
if __name__=='__main__':unittest.main()
