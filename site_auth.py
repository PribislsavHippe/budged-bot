"""Сайт ресторана в браузере: вход через бота и сессия в cookie.

Сайт показывает код и ссылку в бота (кнопкой на компьютере или QR-кодом для телефона).
Человек подтверждает вход в боте, сайт узнаёт об этом опросом и получает cookie.
Сессия открывает только кабинет ресторана (/api/restaurant/*); личные записи, чаевые и
календарь по-прежнему требуют подписи мини-приложения. Права проверяет restaurant_api.
Незавершённые входы живут в памяти процесса: после перезапуска вход начинают заново.
"""
import hashlib
import hmac
import html
import os
import secrets
import time
from dataclasses import dataclass

from aiohttp import web

COOKIE = "restaurant_session"
SESSION_SECONDS = 7 * 86400
LOGIN_SECONDS = 5 * 60
MAX_PENDING = 500
START_PREFIX = "site_"
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp", "restaurant.html")
NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


@dataclass
class PendingLogin:
    poll_id: str
    code: str
    created: float
    user_id: int | None = None
    state: str = "pending"  # pending → approved | rejected


PENDING: dict[str, PendingLogin] = {}  # токен из ссылки в бота → вход
BY_POLL: dict[str, str] = {}           # секрет браузера → токен


def _expired(login: PendingLogin, now: float) -> bool:
    return now - login.created > LOGIN_SECONDS


def _purge(now: float):
    for token, login in list(PENDING.items()):
        if _expired(login, now):
            PENDING.pop(token, None)
            BY_POLL.pop(login.poll_id, None)


def start_login(now: float | None = None):
    """Новый вход: (секрет для опроса, токен для ссылки, код) или None, если входов слишком много."""
    now = now or time.time()
    _purge(now)
    if len(PENDING) >= MAX_PENDING:
        return None
    token, poll_id = secrets.token_urlsafe(16), secrets.token_urlsafe(24)
    code = f"{secrets.randbelow(10000):04d}"
    PENDING[token] = PendingLogin(poll_id, code, now)
    BY_POLL[poll_id] = token
    return poll_id, token, code


def claim(token: str, user_id: int, now: float | None = None) -> str | None:
    """Человек открыл ссылку в боте: запоминаем его и возвращаем код для сверки."""
    login = PENDING.get(token)
    if not login or login.state != "pending" or _expired(login, now or time.time()):
        return None
    if login.user_id not in (None, user_id):
        return None
    login.user_id = user_id
    return login.code


def decide(token: str, user_id: int, approve: bool, now: float | None = None) -> bool:
    """Кнопка в боте. Решает только тот, кто открыл ссылку, и только пока вход не устарел."""
    login = PENDING.get(token)
    if not login or login.state != "pending" or login.user_id != user_id or _expired(login, now or time.time()):
        return False
    login.state = "approved" if approve else "rejected"
    return True


def poll(poll_id: str, now: float | None = None):
    """Состояние для сайта: ('approved', user_id), ('pending',), ('rejected',) или ('expired',)."""
    token = BY_POLL.get(poll_id or "")
    login = PENDING.get(token or "")
    if not login or _expired(login, now or time.time()):
        return ("expired",)
    if login.state == "pending":
        return ("pending",)
    PENDING.pop(token, None)
    BY_POLL.pop(poll_id, None)
    return ("approved", login.user_id) if login.state == "approved" else ("rejected",)


def _sign(payload: str, bot_token: str) -> str:
    key = hmac.new(b"restaurant-site-session", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def issue(user_id: int, bot_token: str, now: float | None = None) -> str:
    payload = f"{user_id}.{int(now or time.time()) + SESSION_SECONDS}"
    return f"{payload}.{_sign(payload, bot_token)}"


def read(value: str, bot_token: str, now: float | None = None) -> int | None:
    try:
        user_id, expires, signature = value.split(".")
        if not hmac.compare_digest(_sign(f"{user_id}.{expires}", bot_token), signature):
            return None
        return int(user_id) if int(expires) > (now or time.time()) else None
    except (AttributeError, ValueError):
        return None


def session_user(request: web.Request) -> int | None:
    # Простая форма с другого сайта не может отправить application/json без предварительного запроса.
    if request.content_type != "application/json":
        return None
    return read(request.cookies.get(COOKIE, ""), request.app["bot_token"])


async def serve_page(request: web.Request) -> web.Response:
    """Одна страница для сайта и для мини-приложения."""
    with open(PAGE, encoding="utf-8") as file:
        page = file.read()
    headers = dict(NO_CACHE)
    if request.path == "/restaurant":
        # Сайт нельзя встроить в чужую страницу и подсунуть нажатия; мини-приложение Telegram Web встраивает.
        headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    return web.Response(text=page, content_type="text/html", headers=headers)


async def login_start(request: web.Request) -> web.Response:
    bot = request.app.get("bot_username")
    started = start_login() if bot else None
    if not started:
        return web.json_response({"error": "Вход сейчас недоступен. Попробуй через минуту."}, status=503, headers=NO_CACHE)
    poll_id, token, code = started
    return web.json_response({"poll": poll_id, "code": code, "expires_in": LOGIN_SECONDS,
                              "link": f"https://t.me/{html.escape(bot)}?start={START_PREFIX}{token}"}, headers=NO_CACHE)


async def login_poll(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        body = {}
    result = poll(str(body.get("poll", "")) if isinstance(body, dict) else "")
    if result[0] != "approved":
        return web.json_response({"state": result[0]}, headers=NO_CACHE)
    response = web.json_response({"state": "approved"}, headers=NO_CACHE)
    response.set_cookie(COOKIE, issue(result[1], request.app["bot_token"]), max_age=SESSION_SECONDS,
                        path="/", secure=True, httponly=True, samesite="Lax")
    return response


async def logout(request: web.Request) -> web.Response:
    response = web.json_response({"ok": True}, headers=NO_CACHE)
    response.del_cookie(COOKIE, path="/")
    return response


def register(app: web.Application):
    app.router.add_get("/restaurant", serve_page)
    app.router.add_get("/app/restaurant", serve_page)
    app.router.add_post("/api/site/login/start", login_start)
    app.router.add_post("/api/site/login/poll", login_poll)
    app.router.add_post("/api/site/logout", logout)
