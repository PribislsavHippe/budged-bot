"""Explicit variants of selected replies for one private Telegram chat."""

import os


def friend_text(recipient, standard: str, personal: str) -> str:
    """Select a variant for a private message or a known personal chat ID."""
    configured = os.getenv('FRIEND_TELEGRAM_ID', '').strip()
    if not configured:
        return standard
    chat_id = recipient
    if not isinstance(recipient, int):
        chat = getattr(recipient, 'chat', None)
        if getattr(chat, 'type', None) != 'private':
            return standard
        chat_id = chat.id
    return personal if type(chat_id) is int and chat_id > 0 and str(chat_id) == configured else standard
