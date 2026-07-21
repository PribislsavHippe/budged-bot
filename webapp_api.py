"""API мини-апа: раздача страницы и /api/stats с проверкой подписи Telegram.

initData подписан ботовским токеном — подделать user_id нельзя.
"""
import hashlib
import hmac
import json
import logging
import os
from urllib.parse import parse_qsl

from aiohttp import web

import db
from stats import compute_stats

WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")


def validate_init_data(init_data: str, bot_token: str) -> int | None:
    """Проверяет подпись initData, возвращает telegram user_id или None."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", None)
        if not received_hash:
            return None
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calculated = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated, received_hash):
            return None
        user = json.loads(pairs.get("user", "{}"))
        return int(user["id"])
    except Exception as e:
        logging.warning(f"initData validation error: {e}")
        return None


NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


async def serve_app(request: web.Request) -> web.Response:
    # Telegram WebView агрессивно кэширует — запрещаем явно
    return web.FileResponse(os.path.join(WEBAPP_DIR, "index.html"), headers=NO_CACHE)


async def _stats_payload(app: web.Application, user_id: int) -> dict:
    entries = await db.get_all_entries(user_id)
    goal = await db.get_shift_goal(user_id)
    payload = compute_stats(entries, shift_goal=goal)
    payload["bot_username"] = app.get("bot_username")
    return payload


async def _auth(request: web.Request):
    """Возвращает (user_id, body) или (None, response-с-ошибкой)."""
    try:
        body = await request.json()
    except Exception:
        return None, web.json_response({"error": "bad request"}, status=400)
    user_id = validate_init_data(body.get("initData", ""), request.app["bot_token"])
    if user_id is None:
        return None, web.json_response({"error": "unauthorized"}, status=401)
    return user_id, body


async def api_stats(request: web.Request) -> web.Response:
    user_id, body = await _auth(request)
    if user_id is None:
        return body
    return web.json_response(await _stats_payload(request.app, user_id), headers=NO_CACHE)


async def api_shift_spend(request: web.Request) -> web.Response:
    """Внести трату за смену прямо из мини-апа. Возвращает свежую статистику."""
    user_id, body = await _auth(request)
    if user_id is None:
        return body
    category = (str(body.get("category") or "Прочее").strip() or "Прочее")[:40]
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        return web.json_response({"error": "bad amount"}, status=400)
    if not (0 < amount <= 1_000_000):
        return web.json_response({"error": "bad amount"}, status=400)
    await db.add_entry(
        user_id, "expense", db.CASH, -round(amount, 2),
        category=category, note="трата смены",
    )
    return web.json_response(await _stats_payload(request.app, user_id), headers=NO_CACHE)


def register_webapp_routes(app: web.Application, bot_token: str, bot_username: str | None = None):
    app["bot_token"] = bot_token
    app["bot_username"] = bot_username
    app.router.add_get("/app", serve_app)
    app.router.add_post("/api/stats", api_stats)
    app.router.add_post("/api/shift_spend", api_shift_spend)
