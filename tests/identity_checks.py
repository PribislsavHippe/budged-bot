"""Isolated identity routing and authorization checks; no external services."""
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with patch.dict(os.environ, {'SUPABASE_URL':'https://example.invalid','SUPABASE_KEY':'test'}), patch('supabase.create_client'):
    import identity
    import identity_chat as chat
    import report_photo as photo

class IdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_profile_describes_personal_access_without_waiting(self):
        message=NS(from_user=NS(id=42),answer=AsyncMock())
        row={'id':'00000000-0000-0000-0000-000000000001','restaurant_name':'Кафе',
             'report_name':'Семён Тестов','status':'pending','has_admin':True}
        with patch.object(identity,'profile',new=AsyncMock(return_value=row)):
            await chat.profile(message)
        answer=message.answer.await_args.args[0]
        self.assertIn('Личные записи уже доступны',answer)
        self.assertIn('Данные из отчёта ресторана пока не подключены',answer)
        self.assertNotIn('администратора',answer)

    async def test_exact_unique_verified_match(self):
        link={'status':'approved','has_admin':True,'name_key':'семен тестов'}
        rows=[{'name':'Другой человек'},{'name':' Семён   Тестов '}]
        with patch.object(identity,'profile',new=AsyncMock(return_value=link)) as profile:
            self.assertEqual(await identity.report_row(42,rows),1)
            profile.assert_awaited_with(42)
            self.assertIsNone(await identity.report_row(42,rows+[rows[1]]))
            self.assertIsNone(await identity.report_row(42,[{'name':'Семён Тестовв'}]))
            for status in ('pending','rejected','revoked'):
                link['status']=status
                self.assertIsNone(await identity.report_row(42,rows))
            link.update(status='approved',has_admin=False)
            self.assertIsNone(await identity.report_row(42,rows))

    async def test_bootstrap_and_team_permissions(self):
        with patch('admin.is_admin',return_value=False),patch.object(identity,'action',new=AsyncMock()) as action:
            with self.assertRaises(ValueError):await identity.create(99,'Ресторан')
            action.assert_not_awaited()
        with patch.object(identity,'owner_restaurant',new=AsyncMock(return_value=None)):
            with self.assertRaises(ValueError):await identity.team(99)

    async def test_callback_uses_sender_and_checks_leave(self):
        target='00000000-0000-0000-0000-000000000001'
        msg=NS(answer=AsyncMock(),edit_text=AsyncMock(),from_user=NS(id=1))
        query=NS(answer=AsyncMock(),message=msg,from_user=NS(id=99),data=f'ident:approve:{target}')
        with patch.object(identity,'action',new=AsyncMock(side_effect=ValueError('Нет прав.'))) as action:
            await chat.callback(query)
            action.assert_awaited_once_with(99,'approve',{'id':target})
            msg.edit_text.assert_not_awaited()
        query.data=f'ident:leave:{target}'
        with patch.object(identity,'profile',new=AsyncMock(return_value={'id':'different'})),patch.object(identity,'action',new=AsyncMock()) as action:
            await chat.callback(query)
            action.assert_not_awaited()

    def test_name_validation(self):
        self.assertEqual(identity.name_key('  Семён   Тестов  '),'семен тестов')
        for value in ('x','<b>Имя</b>','Имя\x00', 'x'*81):
            with self.assertRaises(ValueError):identity.name(value)

    async def test_invitation_preserves_uncertain_save(self):
        draft=photo.Draft('nonce','report-id','file',phase='uncertain')
        photo.drafts[42]=draft
        message=NS(from_user=NS(id=42),answer=AsyncMock())
        state=NS(clear=AsyncMock())
        try:
            await chat.start_invite(message,NS(args='team_token'),state)
            state.clear.assert_not_awaited()
            self.assertIs(photo.drafts[42],draft)
        finally:photo.expire(42,draft)

    async def test_verified_photo_still_requires_confirmation(self):
        draft=photo.Draft('nonce','report-id','file')
        photo.drafts[42]=draft
        message=NS(edit_text=AsyncMock(),answer=AsyncMock())
        bot=NS(get_file=AsyncMock(return_value=NS(file_path='file',file_size=10)),download_file=AsyncMock())
        query=NS(from_user=NS(id=42),data='ocr:nonce:read',message=message,bot=bot,answer=AsyncMock())
        result={'cutoff':'2026-09-13','targets':{},'rows':[{'name':'Семён Тестов','totals':{'wine':100}},{'name':'Другой','totals':{'wine':200}}]}
        try:
            with patch.dict(os.environ,{'IDENTITY_ENABLED':'1'}),patch.object(photo.vision,'recognize',new=AsyncMock(return_value=result)),patch.object(identity,'report_row',new=AsyncMock(return_value=0)),patch.object(photo,'save_report',new=AsyncMock()) as save:
                await photo.photo_callback(query)
                self.assertEqual(draft.phase,'review')
                self.assertEqual(draft.totals,{'wine':100})
                self.assertNotIn('rows',draft.report)
                save.assert_not_awaited()
        finally:photo.expire(42,draft)

    async def test_dispatch_invitation_cancel_registration_photo_and_disabled(self):
        from aiogram import Bot, Dispatcher, Router
        from aiogram.types import Message
        dp=Dispatcher();dp.include_router(chat.router);dp.include_router(photo.router)
        fallback=Router();received=[]
        @fallback.message()
        async def handle(message):received.append(message.text)
        dp.include_router(fallback)
        bot=Bot('123456:test-only')
        async def send(text=None, image=False):
            msg={'message_id':1,'date':1790530000,'from':{'id':42,'is_bot':False,'first_name':'Test'},'chat':{'id':42,'type':'private'}}
            if image:msg['photo']=[{'file_id':'file','file_unique_id':'unique','width':100,'height':100,'file_size':10}]
            else:
                msg['text']=text
                if text.startswith('/'):msg['entities']=[{'type':'bot_command','offset':0,'length':len(text.split()[0])}]
            await dp.feed_raw_update(bot,{'update_id':1,'message':msg})
        state=dp.fsm.get_context(bot=bot,chat_id=42,user_id=42)
        with patch.dict(os.environ,{'IDENTITY_ENABLED':'1'}),patch.object(Message,'answer',new=AsyncMock()),patch.object(identity,'request',new=AsyncMock(return_value={'status':'pending','report_name':'Семён Тестов'})) as request,patch.object(photo.vision,'configured',return_value=True):
            await send('/start team_token');self.assertEqual(await state.get_state(),chat.Identification.name.state)
            await send('/cancel');self.assertIsNone(await state.get_state());request.assert_not_awaited()
            await send('/start team_token');await send('Семён Тестов')
            request.assert_awaited_once_with(42,'token','Семён Тестов');self.assertIsNone(await state.get_state())
            await send('/start team_token');await send(image=True)
            self.assertIsNone(await state.get_state());self.assertIn(42,photo.drafts)
            photo.expire(42,photo.drafts[42])
            with patch.dict(os.environ,{'IDENTITY_ENABLED':'0'}):
                await send('/start team_disabled')
                self.assertIsNone(await state.get_state());self.assertIn('/start team_disabled',received)
        await bot.session.close()

if __name__=='__main__':unittest.main()
