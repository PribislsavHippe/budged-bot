"""Сайт ресторана в браузере: вход кнопкой Telegram и сессия в cookie.

Сессия открывает только кабинет ресторана (/api/restaurant/*); личные записи, чаевые и
календарь по-прежнему требуют подписи мини-приложения. Права проверяет restaurant_api.
"""
import hashlib
import hmac
import html
import os
import time

from aiohttp import web

COOKIE = "restaurant_session"
SESSION_SECONDS = 7 * 86400
LOGIN_MAX_AGE = 86400
LOGIN_FIELDS = {"id", "first_name", "last_name", "username", "photo_url", "auth_date", "hash"}
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp", "restaurant.html")
NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


def verify_login(data, bot_token: str, now: float | None = None) -> int | None:
    """Проверяет данные кнопки входа Telegram, возвращает user_id или None."""
    if not isinstance(data, dict) or not set(data) <= LOGIN_FIELDS:
        return None
    received = str(data.get("hash") or "")
    fields = {key: str(value) for key, value in data.items() if key != "hash" and value is not None}
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not received or not hmac.compare_digest(expected, received):
        return None
    try:
        user_id = int(fields["id"])
        age = (now or time.time()) - int(fields["auth_date"])
    except (KeyError, ValueError):
        return None
    return user_id if -60 <= age <= LOGIN_MAX_AGE else None


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
    """Одна страница для сайта и для мини-приложения; имя бота нужно кнопке входа."""
    with open(PAGE, encoding="utf-8") as file:
        page = file.read()
    bot = html.escape(request.app.get("bot_username") or "", quote=True)
    headers = dict(NO_CACHE)
    if request.path == "/restaurant":
        # Сайт нельзя встроить в чужую страницу и подсунуть нажатия; мини-приложение Telegram Web встраивает.
        headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    return web.Response(text=page.replace('data-bot=""', f'data-bot="{bot}"', 1),
                        content_type="text/html", headers=headers)


async def login(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        data = None
    user_id = verify_login(data, request.app["bot_token"])
    if user_id is None:
        return web.json_response({"error": "Не получилось войти. Попробуй ещё раз."}, status=401, headers=NO_CACHE)
    response = web.json_response({"ok": True}, headers=NO_CACHE)
    response.set_cookie(COOKIE, issue(user_id, request.app["bot_token"]), max_age=SESSION_SECONDS,
                        path="/", secure=True, httponly=True, samesite="Lax")
    return response


async def logout(request: web.Request) -> web.Response:
    response = web.json_response({"ok": True}, headers=NO_CACHE)
    response.del_cookie(COOKIE, path="/")
    return response


def register(app: web.Application):
    app.router.add_get("/restaurant", serve_page)
    app.router.add_get("/app/restaurant", serve_page)
    app.router.add_post("/api/site/login", login)
    app.router.add_post("/api/site/logout", logout)
