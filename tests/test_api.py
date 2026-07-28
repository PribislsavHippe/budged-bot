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

# ─── заглушка httpx (google_calendar импортирует его, сеть в тестах не нужна) ─

_httpx = types.ModuleType("httpx")


class _AsyncClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        raise RuntimeError("no network in tests")


_httpx.AsyncClient = _AsyncClient
sys.modules["httpx"] = _httpx

# ─── заглушка db (без реального Supabase) ────────────────────────────────────

_db = types.ModuleType("db")
_db.CASH = "cash"
_db.CARD = "card"
_db.added = []
_db.store = []      # list of entry dicts
_db.next_id = [1]


def _seed(user_id, kind, account, signed_amount, category="Чаевые"):
    e = {
        "id": _db.next_id[0], "user_id": user_id, "kind": kind, "account": account,
        "signed_amount": signed_amount, "category": category, "note": None,
        "created_at": "2026-07-20T18:00:00+00:00",
    }
    _db.next_id[0] += 1
    _db.store.append(e)
    return e


_db.seed = _seed


async def _add_entry(user_id, kind, account, signed_amount, category="Прочее",
                     note=None, order_amount=None, tip_percent=None):
    _db.added.append({
        "user_id": user_id, "kind": kind, "account": account,
        "signed_amount": signed_amount, "category": category, "note": note,
    })
    e = _seed(user_id, kind, account, signed_amount, category)
    e["note"] = note
    return e


async def _get_all_entries(uid):
    return [e for e in _db.store if e["user_id"] == uid]


async def _get_recent_entries(uid, limit=15):
    return list(reversed([e for e in _db.store if e["user_id"] == uid]))[:limit]


async def _get_entry(eid, uid):
    for e in _db.store:
        if e["id"] == eid and e["user_id"] == uid:
            return e
    return None


async def _delete_entry(eid, uid):
    before = len(_db.store)
    _db.store[:] = [e for e in _db.store if not (e["id"] == eid and e["user_id"] == uid)]
    return len(_db.store) < before


async def _update_entry_account(eid, uid, account):
    e = await _get_entry(eid, uid)
    if e:
        e["account"] = account
    return e


async def _update_entry_amount(eid, uid, signed_amount):
    e = await _get_entry(eid, uid)
    if e:
        e["signed_amount"] = signed_amount
    return e


async def _get_shift_goal(uid):
    return None


async def _get_shift_dates(uid, since=None, until=None):
    return []


async def _get_google_token(uid):
    return None


_db.add_entry = _add_entry
_db.get_all_entries = _get_all_entries
_db.get_recent_entries = _get_recent_entries
_db.get_entry = _get_entry
_db.delete_entry = _delete_entry
_db.update_entry_account = _update_entry_account
_db.update_entry_amount = _update_entry_amount
_db.get_shift_goal = _get_shift_goal
_db.get_shift_dates = _get_shift_dates
_db.get_google_token = _get_google_token
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


# ─── /api/entries и /api/entry_edit ──────────────────────────────────────────

def test_entries_list():
    _db.store.clear()
    _db.seed(42, "income", "card", 500)
    _db.seed(42, "income", "cash", 300)
    _db.seed(99, "income", "card", 700)  # чужой — не должен попасть
    r = run(webapp_api.api_entries(Req({"initData": init_data(TOKEN, 42)})))
    assert r.status == 200
    assert len(r.data["entries"]) == 2


def test_entry_delete():
    _db.store.clear()
    e = _db.seed(42, "income", "card", 500)
    r = run(webapp_api.api_entry_edit(Req({
        "initData": init_data(TOKEN, 42), "entry_id": e["id"], "action": "delete",
    })))
    assert r.status == 200
    assert "stats" in r.data and "entries" in r.data
    assert run(_db.get_entry(e["id"], 42)) is None


def test_entry_amount_keeps_sign():
    _db.store.clear()
    inc = _db.seed(42, "income", "card", 500)
    exp = _db.seed(42, "expense", "cash", -300, category="Бар")
    run(webapp_api.api_entry_edit(Req({
        "initData": init_data(TOKEN, 42), "entry_id": inc["id"], "action": "amount", "amount": 700,
    })))
    run(webapp_api.api_entry_edit(Req({
        "initData": init_data(TOKEN, 42), "entry_id": exp["id"], "action": "amount", "amount": 350,
    })))
    assert run(_db.get_entry(inc["id"], 42))["signed_amount"] == 700
    assert run(_db.get_entry(exp["id"], 42))["signed_amount"] == -350


def test_entry_account_change():
    _db.store.clear()
    e = _db.seed(42, "income", "card", 500)
    run(webapp_api.api_entry_edit(Req({
        "initData": init_data(TOKEN, 42), "entry_id": e["id"], "action": "account", "account": "cash",
    })))
    assert run(_db.get_entry(e["id"], 42))["account"] == "cash"


def test_entry_bad_amount():
    _db.store.clear()
    e = _db.seed(42, "income", "card", 500)
    for bad in [0, -5, "x", 20_000_000]:
        r = run(webapp_api.api_entry_edit(Req({
            "initData": init_data(TOKEN, 42), "entry_id": e["id"], "action": "amount", "amount": bad,
        })))
        assert r.status == 400, f"amount={bad!r}"
    assert run(_db.get_entry(e["id"], 42))["signed_amount"] == 500


def test_entry_not_found():
    _db.store.clear()
    r = run(webapp_api.api_entry_edit(Req({
        "initData": init_data(TOKEN, 42), "entry_id": 12345, "action": "delete",
    })))
    assert r.status == 404


def test_entry_foreign_forbidden():
    _db.store.clear()
    e = _db.seed(99, "income", "card", 500)  # чужая запись
    r = run(webapp_api.api_entry_edit(Req({
        "initData": init_data(TOKEN, 42), "entry_id": e["id"], "action": "delete",
    })))
    assert r.status == 404  # для user 42 её не существует
    assert run(_db.get_entry(e["id"], 99)) is not None  # чужая цела


def test_entry_bad_action():
    _db.store.clear()
    e = _db.seed(42, "income", "card", 500)
    r = run(webapp_api.api_entry_edit(Req({
        "initData": init_data(TOKEN, 42), "entry_id": e["id"], "action": "nuke",
    })))
    assert r.status == 400


def test_entry_edit_unauthorized():
    r = run(webapp_api.api_entry_edit(Req({"initData": "", "entry_id": 1, "action": "delete"})))
    assert r.status == 401


# ─── Google Календарь: подпись state и статус ────────────────────────────────

def test_gcal_state_roundtrip():
    import google_calendar as gc
    assert gc.verify_state(gc._sign(844587778)) == 844587778


def test_gcal_state_tamper():
    import google_calendar as gc
    bad = gc._sign(844587778).replace("844587778", "1")
    assert gc.verify_state(bad) is None
    assert gc.verify_state("garbage") is None


def test_api_gcal_not_configured():
    # без GOOGLE_* переменных — configured False, без обращения к сети/бд
    r = run(webapp_api.api_gcal(Req({"initData": init_data(TOKEN, 42)})))
    assert r.status == 200 and r.data["configured"] is False


def test_api_gcal_unauthorized():
    r = run(webapp_api.api_gcal(Req({"initData": ""})))
    assert r.status == 401


# ─── доступ к чужим записям (IDOR) ───────────────────────────────────────────
# Идентификаторы записей идут подряд, поэтому чужой id угадывается тривиально.
# Каждая операция обязана проверять владельца, а не только существование.

def test_idor_cannot_delete_other_users_entry():
    _db.store.clear()
    victim = _db.seed(99, "income", "card", 5000)
    r = run(webapp_api.api_entry_edit(Req({
        "initData": init_data(TOKEN, 42), "entry_id": victim["id"], "action": "delete",
    })))
    assert r.status == 404
    assert run(_db.get_entry(victim["id"], 99)) is not None   # запись цела


def test_idor_cannot_change_other_users_amount():
    _db.store.clear()
    victim = _db.seed(99, "income", "card", 5000)
    r = run(webapp_api.api_entry_edit(Req({
        "initData": init_data(TOKEN, 42), "entry_id": victim["id"],
        "action": "amount", "amount": 1,
    })))
    assert r.status == 404
    assert float(run(_db.get_entry(victim["id"], 99))["signed_amount"]) == 5000


def test_idor_cannot_move_other_users_entry():
    _db.store.clear()
    victim = _db.seed(99, "income", "card", 5000)
    r = run(webapp_api.api_entry_edit(Req({
        "initData": init_data(TOKEN, 42), "entry_id": victim["id"],
        "action": "account", "account": "cash",
    })))
    assert r.status == 404
    assert run(_db.get_entry(victim["id"], 99))["account"] == "card"


def test_idor_missing_and_foreign_look_identical():
    """Чужая и несуществующая запись отвечают одинаково — иначе по разнице
    ответов можно перебором выяснить, какие идентификаторы заняты."""
    _db.store.clear()
    victim = _db.seed(99, "income", "card", 5000)
    foreign = run(webapp_api.api_entry_edit(Req({
        "initData": init_data(TOKEN, 42), "entry_id": victim["id"], "action": "delete",
    })))
    missing = run(webapp_api.api_entry_edit(Req({
        "initData": init_data(TOKEN, 42), "entry_id": 10 ** 9, "action": "delete",
    })))
    assert foreign.status == missing.status == 404
    assert foreign.data == missing.data


def test_idor_stats_never_include_foreign_entries():
    _db.store.clear()
    _db.seed(42, "income", "card", 1000)
    _db.seed(99, "income", "card", 777000)
    r = run(webapp_api.api_stats(Req({"initData": init_data(TOKEN, 42)})))
    assert r.status == 200
    assert r.data["total_net"] == 1000


def test_idor_forged_signature_gets_nothing():
    """Подменить чужой id в initData нельзя: подпись перестаёт сходиться."""
    _db.store.clear()
    _db.seed(99, "income", "card", 5000)
    forged = init_data(TOKEN, 42).replace("%22id%22%3A+42", "%22id%22%3A+99")
    r = run(webapp_api.api_entries(Req({"initData": forged})))
    assert r.status == 401


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
