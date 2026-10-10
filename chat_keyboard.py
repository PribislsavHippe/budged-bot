"""Remove keyboards left in existing private chats by older bot versions."""
from collections import OrderedDict

from aiogram.methods import SendMessage
from aiogram.types import ReplyKeyboardRemove

from diagnostics import failure


class RemoveLegacyKeyboard:
    def __init__(self):
        self.cleaned = OrderedDict()

    async def __call__(self, make_request, bot, method):
        # Numeric positive chat IDs identify private chats. Never alter group UI.
        if not isinstance(method, SendMessage) or not isinstance(method.chat_id, int) or method.chat_id <= 0:
            return await make_request(bot, method)

        chat_id = method.chat_id
        if method.reply_markup is None:
            method = method.model_copy(update={'reply_markup': ReplyKeyboardRemove()})
        result = await make_request(bot, method)
        if isinstance(method.reply_markup, ReplyKeyboardRemove):
            self._remember(chat_id)
        elif chat_id not in self.cleaned:
            # Telegram accepts either inline buttons or keyboard removal in one
            # sendMessage. Keep the action buttons on the original response.
            try:
                await make_request(bot, SendMessage(
                    chat_id=chat_id,
                    text='История и расходы теперь в «Статистике». Нижнее меню убрал.',
                    reply_markup=ReplyKeyboardRemove(),
                    disable_notification=True,
                ))
            except Exception as error:
                # A failed UI cleanup must not retry an already saved payment.
                failure(error, area='chat_keyboard', stage='remove')
            else:
                self._remember(chat_id)
        return result

    def _remember(self, chat_id):
        self.cleaned[chat_id] = None
        self.cleaned.move_to_end(chat_id)
        if len(self.cleaned) > 10000:
            self.cleaned.popitem(last=False)
