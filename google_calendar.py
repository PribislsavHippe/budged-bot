"""Google Календарь через OAuth 2.0 и Calendar API — на чистом httpx.

Поток: мини-ап открывает auth_url во ВНЕШНЕМ браузере (Google блокирует OAuth
внутри вебвью Telegram) → пользователь соглашается → Google редиректит на
/google/callback → exchange_code сохраняет токены. Дальше смены создаются
как события-на-весь-день в его календаре.
"""
import hashlib
import hmac
import logging
import os
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

import db

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
SCOPE = "https://www.googleapis.com/auth/calendar.events"

_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN = "https://oauth2.googleapis.com/token"
_EVENTS = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

_SECRET = (os.getenv("BOT_TOKEN") or "dev").encode()


def is_configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET and REDIRECT_URI)


# ─── подпись state (чтобы пользователь мог авторизовать только себя) ──────────

def _sign(user_id: int) -> str:
    mac = hmac.new(_SECRET, str(user_id).encode(), hashlib.sha256).hexdigest()[:32]
    return f"{user_id}.{mac}"


def verify_state(state: str) -> int | None:
    try:
        uid_s, mac = state.split(".", 1)
        expected = _sign(int(uid_s)).split(".", 1)[1]
        if hmac.compare_digest(expected, mac):
            return int(uid_s)
    except Exception:
        pass
    return None


def auth_url(user_id: int) -> str:
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": _sign(user_id),
    }
    return f"{_AUTH}?{urlencode(params)}"


# ─── обмен кода и обновление токена ──────────────────────────────────────────

async def exchange_code(user_id: int, code: str) -> None:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(_TOKEN, data={
            "code": code, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code",
        })
        r.raise_for_status()
        tok = r.json()
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=tok.get("expires_in", 3600))).isoformat()
    await db.save_google_token(user_id, tok["access_token"], tok.get("refresh_token"), expiry)


async def _valid_token(user_id: int) -> str | None:
    data = await db.get_google_token(user_id)
    if not data or not data.get("google_refresh_token"):
        return None
    access = data.get("google_access_token")
    expiry = data.get("google_token_expiry")
    if access and expiry:
        try:
            exp = datetime.fromisoformat(expiry)
            if exp > datetime.now(timezone.utc) + timedelta(minutes=2):
                return access
        except ValueError:
            pass
    # обновляем по refresh_token
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(_TOKEN, data={
            "refresh_token": data["google_refresh_token"], "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET, "grant_type": "refresh_token",
        })
    if r.status_code != 200:
        logging.warning(f"google token refresh failed for {user_id}: {r.text[:200]}")
        return None
    tok = r.json()
    new_exp = (datetime.now(timezone.utc) + timedelta(seconds=tok.get("expires_in", 3600))).isoformat()
    await db.save_google_token(user_id, tok["access_token"], None, new_exp)
    return tok["access_token"]


async def is_connected(user_id: int) -> bool:
    data = await db.get_google_token(user_id)
    return bool(data and data.get("google_refresh_token"))


# ─── события ─────────────────────────────────────────────────────────────────

async def create_shift_event(user_id: int, date_iso: str) -> bool:
    """Создаёт событие-на-весь-день «Смена» на дату (YYYY-MM-DD)."""
    token = await _valid_token(user_id)
    if not token:
        return False
    d = date.fromisoformat(date_iso)
    end = (d + timedelta(days=1)).isoformat()  # all-day: end = следующий день
    body = {
        "summary": "Смена",
        "start": {"date": date_iso},
        "end": {"date": end},
        "transparency": "transparent",
        "extendedProperties": {"private": {"budgetbot": "shift"}},
    }
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(_EVENTS, headers={"Authorization": f"Bearer {token}"}, json=body)
        return r.status_code in (200, 201)
    except Exception as e:
        logging.warning(f"create_shift_event failed for {user_id}: {e}")
        return False


async def create_shift_events(user_id: int, dates_iso: list[str]) -> int:
    created = 0
    for d in dates_iso:
        if await create_shift_event(user_id, d):
            created += 1
    return created
