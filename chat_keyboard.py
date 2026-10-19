"""Remove legacy keyboards with ordinary replies, without extra messages."""
from aiogram.methods import SendMessage
from aiogram.types import ReplyKeyboardRemove


class RemoveLegacyKeyboard:
    async def __call__(self, make_request, bot, method):
        # Numeric positive chat IDs identify private chats. Never alter group UI.
        if (isinstance(method, SendMessage) and isinstance(method.chat_id, int)
                and method.chat_id > 0 and method.reply_markup is None):
            method = method.model_copy(update={'reply_markup': ReplyKeyboardRemove()})
        # Inline actions must survive. Remove the old keyboard on the next plain
        # reply: Telegram cannot combine inline buttons and removal in one reply.
        return await make_request(bot, method)
