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
import google_calendar as gcal
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
    from datetime import datetime
    from stats import MSK
    entries = await db.get_all_entries(user_id)
    goal = await db.get_shift_goal(user_id)
    payload = compute_stats(entries, shift_goal=goal)
    payload["bot_username"] = app.get("bot_username")
    # Запланированные смены текущего месяца — для календаря в мини-апе.
    today = datetime.now(MSK).date()
    m_start = today.replace(day=1).isoformat()
    m_end = today.replace(day=payload["days_in_month"]).isoformat()
    payload["scheduled_shifts"] = await db.get_shift_dates(user_id, m_start, m_end)
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


async def api_entries(request: web.Request) -> web.Response:
    """Последние записи для правки в мини-апе."""
    user_id, body = await _auth(request)
    if user_id is None:
        return body
    entries = await db.get_recent_entries(user_id, limit=30)
    return web.json_response({"entries": entries}, headers=NO_CACHE)


async def api_entry_edit(request: web.Request) -> web.Response:
    """Правка записи из мини-апа: изменить сумму/счёт или удалить.

    action: 'delete' | 'amount' (+amount) | 'account' (+account).
    Возвращает свежие stats и список записей.
    """
    user_id, body = await _auth(request)
    if user_id is None:
        return body
    try:
        entry_id = int(body.get("entry_id"))
    except (TypeError, ValueError):
        return web.json_response({"error": "bad id"}, status=400)

    entry = await db.get_entry(entry_id, user_id)
    if entry is None:
        return web.json_response({"error": "not found"}, status=404)

    action = body.get("action")
    if action == "delete":
        await db.delete_entry(entry_id, user_id)
    elif action == "account":
        account = body.get("account")
        if account not in ("cash", "card"):
            return web.json_response({"error": "bad account"}, status=400)
        await db.update_entry_account(entry_id, user_id, account)
    elif action == "amount":
        try:
            amount = float(body.get("amount"))
        except (TypeError, ValueError):
            return web.json_response({"error": "bad amount"}, status=400)
        if not (0 < amount <= 10_000_000):
            return web.json_response({"error": "bad amount"}, status=400)
        sign = 1 if entry["kind"] == "income" else -1
        await db.update_entry_amount(entry_id, user_id, sign * round(amount, 2))
    else:
        return web.json_response({"error": "bad action"}, status=400)

    stats = await _stats_payload(request.app, user_id)
    entries = await db.get_recent_entries(user_id, limit=30)
    return web.json_response({"stats": stats, "entries": entries}, headers=NO_CACHE)


async def api_gcal(request: web.Request) -> web.Response:
    """Статус Google Календаря + ссылка для подключения (внешний браузер)."""
    user_id, body = await _auth(request)
    if user_id is None:
        return body
    if not gcal.is_configured():
        return web.json_response({"configured": False, "connected": False}, headers=NO_CACHE)
    connected = await gcal.is_connected(user_id)
    return web.json_response({
        "configured": True,
        "connected": connected,
        "auth_url": None if connected else gcal.auth_url(user_id),
    }, headers=NO_CACHE)


async def google_callback(request: web.Request) -> web.Response:
    """Редирект от Google после согласия. Меняем код на токен, сохраняем."""
    code = request.query.get("code")
    state = request.query.get("state", "")
    user_id = gcal.verify_state(state)
    page = ("<!doctype html><meta charset=utf-8><meta name=viewport "
            "content='width=device-width,initial-scale=1'>"
            "<body style='font-family:-apple-system,sans-serif;text-align:center;padding:60px 24px'>")
    if not code or user_id is None:
        return web.Response(text=page + "<h2>Не получилось</h2><p>Ссылка недействительна.</p>",
                            content_type="text/html", status=400)
    try:
        await gcal.exchange_code(user_id, code)
    except Exception as e:
        logging.error(f"google callback error: {e}")
        return web.Response(text=page + "<h2>Ошибка</h2><p>Не удалось подключить календарь.</p>",
                            content_type="text/html", status=500)
    return web.Response(
        text=page + "<h2>Готово ✓</h2><p>Google Календарь подключён.<br>Возвращайся в Telegram.</p>",
        content_type="text/html",
    )


def register_webapp_routes(app: web.Application, bot_token: str, bot_username: str | None = None):
    app["bot_token"] = bot_token
    app["bot_username"] = bot_username
    app.router.add_get("/app", serve_app)
    app.router.add_post("/api/stats", api_stats)
    app.router.add_post("/api/shift_spend", api_shift_spend)
    app.router.add_post("/api/entries", api_entries)
    app.router.add_post("/api/entry_edit", api_entry_edit)
    app.router.add_post("/api/gcal", api_gcal)
    app.router.add_get("/google/callback", google_callback)
