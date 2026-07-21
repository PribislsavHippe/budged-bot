"""Офлайн-тесты API-слоя мини-апа: подпись initData и приём трат.

aiohttp/supabase не установлены локально и не нужны — подменяем их заглушками
до импорта webapp_api, чтобы проверить именно нашу логику (безопасность и
валидацию входных данных), а не инфраструктуру.

Запуск: python tests/test_api.py
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import types
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─── заглушка aiohttp.web ────────────────────────────────────────────────────

_web = types.ModuleType("aiohttp.web")


class _Resp:
    def __init__(self, data=None, status=200, headers=None, **kw):
        self.data = data
        self.status = status
        self.headers = headers


def _json_response(data=None, status=200, headers=None):
    return _Resp(data=data, status=status, headers=headers)


class _FileResponse(_Resp):
    def __init__(self, path, headers=None):
        super().__init__(status=200, headers=headers)
        self.path = path


_web.json_response = _json_response
_web.FileResponse = _FileResponse
_web.Response = _Resp
_web.Application = dict
_web.Request = object

_aiohttp = types.ModuleType("aiohttp")
_aiohttp.web = _web
sys.modules["aiohttp"] = _aiohttp
sys.modules["aiohttp.web"] = _web

# ─── заглушка db (без реального Supabase) ────────────────────────────────────

_db = types.ModuleType("db")
_db.CASH = "cash"
_db.CARD = "card"
_db.added = []


async def _add_entry(user_id, kind, account, signed_amount, category="Прочее",
                     note=None, order_amount=None, tip_percent=None):
    _db.added.append({
        "user_id": user_id, "kind": kind, "account": account,
        "signed_amount": signed_amount, "category": category, "note": note,
    })
    return {"id": len(_db.added)}


async def _get_all_entries(uid):
    return []


async def _get_shift_goal(uid):
    return None


_db.add_entry = _add_entry
_db.get_all_entries = _get_all_entries
_db.get_shift_goal = _get_shift_goal
sys.modules["db"] = _db

import webapp_api  # noqa: E402

TOKEN = "123456:TEST"


def init_data(token, uid):
    user = json.dumps({"id": uid, "first_name": "T"})
    pairs = {"user": user, "auth_date": "1789000000", "query_id": "AAA"}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode({**pairs, "hash": h})


class Req:
    def __init__(self, body):
        self.app = {"bot_token": TOKEN, "bot_username": "b"}
        self._body = body

    async def json(self):
        if self._body is _BAD_JSON:
            raise ValueError("bad json")
        return self._body


_BAD_JSON = object()


def run(coro):
    return asyncio.run(coro)


# ─── подпись initData ────────────────────────────────────────────────────────

def test_valid_signature():
    assert webapp_api.validate_init_data(init_data(TOKEN, 844587778), TOKEN) == 844587778


def test_wrong_token_rejected():
    assert webapp_api.validate_init_data(init_data(TOKEN, 1), "999:OTHER") is None


def test_tampered_rejected():
    data = init_data(TOKEN, 844587778).replace("844587778", "1")
    assert webapp_api.validate_init_data(data, TOKEN) is None


def test_empty_rejected():
    assert webapp_api.validate_init_data("", TOKEN) is None


# ─── /api/shift_spend ────────────────────────────────────────────────────────

def test_shift_spend_happy():
    _db.added.clear()
    r = run(webapp_api.api_shift_spend(Req({
        "initData": init_data(TOKEN, 42), "category": "Бар", "amount": 500,
    })))
    assert r.status == 200
    assert len(_db.added) == 1
    e = _db.added[0]
    assert e["kind"] == "expense" and e["account"] == "cash"
    assert e["signed_amount"] == -500 and e["category"] == "Бар"
    assert e["note"] == "трата смены" and e["user_id"] == 42


def test_shift_spend_unauthorized():
    _db.added.clear()
    r = run(webapp_api.api_shift_spend(Req({"initData": "", "amount": 500})))
    assert r.status == 401
    assert not _db.added


def test_shift_spend_bad_amount():
    for bad in ["abc", 0, -100, 2_000_000, None]:
        _db.added.clear()
        r = run(webapp_api.api_shift_spend(Req({
            "initData": init_data(TOKEN, 42), "category": "Бар", "amount": bad,
        })))
        assert r.status == 400, f"amount={bad!r} should be rejected"
        assert not _db.added


def test_shift_spend_category_default():
    _db.added.clear()
    run(webapp_api.api_shift_spend(Req({
        "initData": init_data(TOKEN, 42), "category": "  ", "amount": 100,
    })))
    assert _db.added[0]["category"] == "Прочее"


def test_shift_spend_category_truncated():
    _db.added.clear()
    run(webapp_api.api_shift_spend(Req({
        "initData": init_data(TOKEN, 42), "category": "Ч" * 100, "amount": 100,
    })))
    assert len(_db.added[0]["category"]) == 40


def test_bad_json_400():
    r = run(webapp_api.api_shift_spend(Req(_BAD_JSON)))
    assert r.status == 400


def test_api_stats_unauthorized():
    r = run(webapp_api.api_stats(Req({"initData": "bad"})))
    assert r.status == 401


def test_api_stats_ok():
    r = run(webapp_api.api_stats(Req({"initData": init_data(TOKEN, 42)})))
    assert r.status == 200
    assert "today_net" in r.data and r.data["bot_username"] == "b"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except AssertionError as ex:
                failed += 1
                print(f"FAIL  {name}: {ex}")
    print("\nFAILED" if failed else "\nALL PASSED")
    sys.exit(1 if failed else 0)
