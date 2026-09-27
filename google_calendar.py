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


# Пока приложение не прошло проверку Google, оно живёт в режиме «Testing»:
# подключиться могут только аккаунты, вручную добавленные в тестовые
# пользователи. Остальные упираются в экран «Доступ заблокирован». Честнее
# предупредить до нажатия кнопки, чем показать ошибку Google после.
INVITE_ONLY = (os.getenv("GOOGLE_INVITE_ONLY", "1") != "0")


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


class CalendarError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


ERROR_MESSAGES = {
    "reconnect": "Google требует повторного подключения. Открой Статистику → Google Календарь.",
    "temporary": "Google временно недоступен. Смены сохранены, отправку повторим автоматически.",
    "forbidden": "Google не разрешил запись в календарь. Проверь доступ приложения и настройки Calendar API.",
    "not_configured": "Google Календарь не настроен на сервере.",
}


async def _valid_token(user_id: int, force_refresh=False) -> str:
    data = await db.get_google_token(user_id)
    if not data or data.get("google_reconnect_required"):
        raise CalendarError("reconnect")
    access, expiry = data.get("google_access_token"), data.get("google_token_expiry")
    if not force_refresh and access and expiry:
        try:
            exp = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if exp.tzinfo is None: exp = exp.replace(tzinfo=timezone.utc)
            if exp > datetime.now(timezone.utc) + timedelta(minutes=2): return access
        except (ValueError, TypeError):
            pass
    if not data.get("google_refresh_token"):
        await db.mark_google_reconnect(user_id, True)
        raise CalendarError("reconnect")
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(_TOKEN, data={"refresh_token":data["google_refresh_token"],
                "client_id":CLIENT_ID,"client_secret":CLIENT_SECRET,"grant_type":"refresh_token"})
    except httpx.HTTPError:
        raise CalendarError("temporary") from None
    if r.status_code != 200:
        try:
            code = r.json().get("error") if r.status_code in (400,401) else None
        except ValueError:
            raise CalendarError("temporary") from None
        if code == "invalid_grant":
            await db.mark_google_reconnect(user_id, True)
            raise CalendarError("reconnect")
        raise CalendarError("forbidden" if r.status_code in (400,401,403) else "temporary")
    tok = r.json()
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=tok.get("expires_in",3600))).isoformat()
    await db.save_google_token(user_id,tok["access_token"],tok.get("refresh_token"),expiry)
    return tok["access_token"]


async def is_connected(user_id: int) -> bool:
    data = await db.get_google_token(user_id)
    return bool(data and (data.get("google_refresh_token") or data.get("google_access_token"))
                and not data.get("google_reconnect_required"))


async def connection_status(user_id: int) -> dict:
    data = await db.get_google_token(user_id)
    if not data or not (data.get("google_refresh_token") or data.get("google_access_token")):
        return {"connected":False,"message":""}
    try:
        await _valid_token(user_id)
        return {"connected":True,"message":"Смены отправляются в основной календарь Google."}
    except CalendarError as e:
        return {"connected":False,"message":ERROR_MESSAGES[e.code],"error":e.code}


async def _request(user_id, method, **kwargs):
    token = await _valid_token(user_id)
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await getattr(c, method)(_EVENTS, headers={"Authorization":f"Bearer {token}"}, **kwargs)
        except httpx.HTTPError:
            raise CalendarError("temporary") from None
        if r.status_code != 401: break
        if attempt == 0: token = await _valid_token(user_id, force_refresh=True)
    if r.status_code == 401:
        await db.mark_google_reconnect(user_id,True)
        raise CalendarError("reconnect")
    if r.status_code == 403: raise CalendarError("forbidden")
    if r.status_code >= 400 and r.status_code != 409: raise CalendarError("temporary")
    return r


async def create_shift_event(user_id: int, date_iso: str) -> bool:
    if not is_configured(): raise CalendarError("not_configured")
    d = date.fromisoformat(date_iso)
    # Detect events created by older versions with random IDs before retrying.
    params = {"privateExtendedProperty":"budgetbot=shift",
        "timeMin":(d-timedelta(days=1)).isoformat()+"T00:00:00Z",
        "timeMax":(d+timedelta(days=2)).isoformat()+"T00:00:00Z", "maxResults":250}
    while True:
        existing = (await _request(user_id,"get",params=params)).json()
        if any(e.get("start",{}).get("date")==date_iso and e.get("status")!="cancelled" for e in existing.get("items",[])):
            return True
        if not existing.get("nextPageToken"): break
        params["pageToken"] = existing["nextPageToken"]
    # Hex is valid base32hex for Google event IDs. A lost response can be retried safely.
    event_id = hashlib.sha256(f"budgetbot-shift:{user_id}:{date_iso}".encode()).hexdigest()
    r = await _request(user_id,"post",json={"id":event_id,"summary":"Смена",
        "start":{"date":date_iso},"end":{"date":(d+timedelta(days=1)).isoformat()},
        "transparency":"transparent","extendedProperties":{"private":{"budgetbot":"shift"}}})
    return r.status_code in (200,201,409)


async def sync_shifts(user_id: int, dates_iso: list[str]) -> dict:
    synced = 0; error = None
    for day in dates_iso:
        try:
            ok = await create_shift_event(user_id,day)
            await db.mark_google_shift(user_id,day,ok,None if ok else "temporary")
            synced += int(ok)
        except CalendarError as e:
            error = e.code
            await db.mark_google_shift(user_id,day,False,error)
            if error in ("reconnect","forbidden","not_configured"): break
    return {"synced":synced,"pending":len(dates_iso)-synced,"error":error,
            "message":ERROR_MESSAGES.get(error,"")}


async def sync_pending(user_id: int) -> dict:
    rows = await db.pending_google_shifts(user_id)
    return await sync_shifts(user_id,[r["shift_date"] for r in rows])


async def retry_pending_shifts():
    if not is_configured(): return
    rows = await db.pending_google_shifts()
    grouped = {}
    for row in rows: grouped.setdefault(row["user_id"],[]).append(row["shift_date"])
    for uid, dates in grouped.items():
        try:
            if await is_connected(uid): await sync_shifts(uid,dates)
        except Exception:
            logging.exception("Calendar retry failed for user %s",uid)


async def create_shift_events(user_id: int, dates_iso: list[str]) -> int:
    return (await sync_shifts(user_id, dates_iso))["synced"]
