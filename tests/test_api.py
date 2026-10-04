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
import time
from datetime import date
from unittest.mock import patch
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
_web.middleware = lambda handler: handler

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
_db.private_ids = set()
_db.shifts = []


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
                     note=None, order_amount=None, tip_percent=None, work_date=None, source_key=None):
    _db.added.append({
        "user_id": user_id, "kind": kind, "account": account,
        "signed_amount": signed_amount, "category": category, "note": note,
    })
    e = _seed(user_id, kind, account, signed_amount, category)
    e["note"] = note
    if work_date:e['work_date']=work_date
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


async def _update_tip_details(eid, uid, amount, account):
    e = await _get_entry(eid, uid)
    if e and e['kind'] == 'income' and e['category'] == 'Чаевые':
        e['signed_amount'] = amount
        e['account'] = account
        return e
    return None


async def _get_shift_goal(uid):
    return None


async def _get_shift_dates(uid, since=None, until=None):
    return sorted(s['shift_date'] for s in _db.shifts if s['user_id']==uid
                  and (since is None or s['shift_date']>=since)
                  and (until is None or s['shift_date']<=until))


async def _get_or_create_user(uid):
    return {"id":uid,"private_money_mode":uid in _db.private_ids}


async def _get_shift_details(uid, since, until):
    return [s for s in _db.shifts if s['user_id']==uid and since<=s['shift_date']<=until]


async def _get_worked_shift_details(uid, since, until):
    return []


async def _save_shift(uid, day, start, end):
    _db.shifts[:]=[s for s in _db.shifts if not (s['user_id']==uid and s['shift_date']==day)]
    _db.shifts.append({'user_id':uid,'shift_date':day,'starts_at':start,'ends_at':end})


async def _delete_shift(uid, day):
    before=len(_db.shifts)
    _db.shifts[:]=[s for s in _db.shifts if not (s['user_id']==uid and s['shift_date']==day)]
    return len(_db.shifts)<before


async def _get_google_token(uid):
    return None


_db.add_entry = _add_entry
_db.get_all_entries = _get_all_entries
_db.get_recent_entries = _get_recent_entries
_db.get_entry = _get_entry
_db.delete_entry = _delete_entry
_db.update_entry_account = _update_entry_account
_db.update_entry_amount = _update_entry_amount
_db.update_tip_details = _update_tip_details
_db.get_shift_goal = _get_shift_goal
_db.get_shift_dates = _get_shift_dates
_db.get_or_create_user = _get_or_create_user
_db.get_shift_details = _get_shift_details
_db.get_worked_shift_details = _get_worked_shift_details
_db.save_shift = _save_shift
_db.delete_shift = _delete_shift
_db.get_google_token = _get_google_token
sys.modules["db"] = _db

import webapp_api  # noqa: E402

TOKEN = "123456:TEST"


def init_data(token, uid):
    user = json.dumps({"id": uid, "first_name": "T"})
    pairs = {"user": user, "auth_date": str(int(time.time())), "query_id": "AAA"}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode({**pairs, "hash": h})


class Req(dict):
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

def test_shift_spend_requires_idempotency_key():
    _db.added.clear()
    r = run(webapp_api.api_shift_spend(Req({"initData":init_data(TOKEN,42),"amount":500})))
    assert r.status == 400
    assert not _db.added


def test_shift_spend_unauthorized():
    r = run(webapp_api.api_shift_spend(Req({"initData":"","amount":500})))
    assert r.status == 401


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


def test_tips_range_scopes_user_and_dates():
    _db.store.clear()
    first = _db.seed(42, 'income', 'card', 1000)
    first['work_date'] = '2026-07-18'
    spent = _db.seed(42, 'expense', 'cash', -200, category='Такси')
    spent['work_date'] = '2026-07-18'
    outside = _db.seed(42, 'income', 'card', 500)
    outside['work_date'] = '2026-07-19'
    foreign = _db.seed(99, 'income', 'card', 9000)
    foreign['work_date'] = '2026-07-18'
    with patch.object(webapp_api, 'op_today', return_value=date(2026, 7, 20)):
        r = run(webapp_api.api_tips_range(Req({'initData': init_data(TOKEN, 42),
            'start': '2026-07-18', 'end': '2026-07-18'})))
    assert r.status == 200
    assert (r.data['period']['gross'], r.data['period']['expenses'], r.data['period']['net']) == (1000, 200, 800)


def test_tips_range_rejects_future_and_unauthorized():
    with patch.object(webapp_api, 'op_today', return_value=date(2026, 7, 20)):
        r = run(webapp_api.api_tips_range(Req({'initData': init_data(TOKEN, 42),
            'start': '2026-07-18', 'end': '2026-07-21'})))
    assert r.status == 400
    assert run(webapp_api.api_tips_range(Req({'initData': ''}))).status == 401


def test_tips_range_defaults_to_shift_today_even_without_tips():
    _db.store.clear(); _db.shifts.clear()
    old = _db.seed(42, 'income', 'cash', 600)
    old['work_date'] = '2026-10-02'
    _db.shifts.append({'user_id':42,'shift_date':'2026-10-04','starts_at':'14:00','ends_at':'23:30'})
    with patch.object(webapp_api, 'op_today', return_value=date(2026, 10, 4)):
        r = run(webapp_api.api_tips_range(Req({'initData':init_data(TOKEN,42)})))
    assert r.status == 200
    assert r.data['period']['start'] == '2026-10-04'
    assert r.data['custom'] is False and r.data['entries'] == []


def test_tips_range_defaults_to_last_tip_date_without_today_shift():
    _db.store.clear(); _db.shifts.clear()
    old = _db.seed(42, 'income', 'cash', 600)
    old['work_date'] = '2026-10-02'
    expense = _db.seed(42, 'expense', 'cash', -150, category='Такси')
    expense['work_date'] = '2026-10-02'
    foreign = _db.seed(99, 'income', 'card', 9000)
    foreign['work_date'] = '2026-10-03'
    with patch.object(webapp_api, 'op_today', return_value=date(2026, 10, 4)):
        r = run(webapp_api.api_tips_range(Req({'initData':init_data(TOKEN,42)})))
    assert r.status == 200
    assert r.data['period']['start'] == '2026-10-02'
    assert {e['id'] for e in r.data['entries']} == {old['id'], expense['id']}
    assert (r.data['period']['gross'], r.data['period']['expenses']) == (600,150)


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


def test_tip_details_update_amount_and_account_only_for_own_tip():
    _db.store.clear()
    tip = _db.seed(42, 'income', 'card', 500)
    expense = _db.seed(42, 'expense', 'cash', -100, category='Бар')
    foreign = _db.seed(99, 'income', 'card', 900)
    signed = init_data(TOKEN,42)
    result = run(webapp_api.api_entry_edit(Req({
        'initData':signed,'entry_id':tip['id'],'action':'tip_details',
        'amount':'650.25','account':'cash'})))
    assert result.status == 200
    assert (tip['signed_amount'],tip['account']) == (650.25,'cash')
    for entry in (expense,foreign):
        rejected = run(webapp_api.api_entry_edit(Req({
            'initData':signed,'entry_id':entry['id'],'action':'tip_details',
            'amount':20,'account':'card'})))
        assert rejected.status in (400,404)
    assert expense['signed_amount'] == -100 and foreign['signed_amount'] == 900


def test_tip_details_rejects_bad_amount_and_account():
    _db.store.clear()
    tip = _db.seed(42,'income','card',500)
    for amount,account in ((0,'cash'),('1.234','cash'),(True,'cash'),(20,'other')):
        r = run(webapp_api.api_entry_edit(Req({
            'initData':init_data(TOKEN,42),'entry_id':tip['id'],
            'action':'tip_details','amount':amount,'account':account})))
        assert r.status == 400
    assert (tip['signed_amount'],tip['account']) == (500,'card')


def test_entry_bad_amount():
    _db.store.clear()
    e = _db.seed(42, "income", "card", 500)
    for bad in [0, -5, "x", 20_000_000, True, "NaN", "Infinity", 1.234]:
        r = run(webapp_api.api_entry_edit(Req({
            "initData": init_data(TOKEN, 42), "entry_id": e["id"], "action": "amount", "amount": bad,
        })))
        assert r.status == 400, f"amount={bad!r}"
    assert run(_db.get_entry(e["id"], 42))["signed_amount"] == 500


def test_entry_saved_even_when_refresh_fails():
    from unittest.mock import patch
    _db.store.clear()
    e = _db.seed(42, "income", "card", 500)
    async def fail_refresh(*_):
        raise RuntimeError("temporary read failure")
    with patch.object(webapp_api, "_stats_payload", fail_refresh):
        r = run(webapp_api.api_entry_edit(Req({
            "initData": init_data(TOKEN, 42), "entry_id": e["id"],
            "action": "amount", "amount": 700,
        })))
    assert r.status == 200 and r.data == {"saved": True, "stats": None, "entries": None}
    assert run(_db.get_entry(e["id"], 42))["signed_amount"] == 700


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
    from unittest.mock import patch
    bad = gc._sign(844587778).replace("844587778", "1")
    assert gc.verify_state(bad) is None
    assert gc.verify_state("garbage") is None
    state = gc._sign(844587778)
    with patch.object(gc.time, 'time', return_value=int(state.split('.')[1])+601):
        assert gc.verify_state(state) is None


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


def test_calendar_shift_can_be_added_changed_and_removed():
    from unittest.mock import AsyncMock, patch
    _db.shifts.clear()
    signed = init_data(TOKEN, 42)
    with patch.object(webapp_api.gcal, 'is_connected', new=AsyncMock(return_value=False)):
        created = run(webapp_api.api_calendar_edit(Req({
            'initData': signed, 'date': '2026-10-05', 'action': 'shift_save',
            'start': '14:00', 'end': '23:30'})))
        assert created.status == 200
        assert _db.shifts == [{'user_id': 42, 'shift_date': '2026-10-05',
                               'starts_at': '14:00', 'ends_at': '23:30'}]
        changed = run(webapp_api.api_calendar_edit(Req({
            'initData': signed, 'date': '2026-10-05', 'action': 'shift_save',
            'start': '10:00', 'end': '22:00'})))
        assert changed.status == 200 and len(_db.shifts) == 1
        assert _db.shifts[0]['ends_at'] == '22:00'
        removed = run(webapp_api.api_calendar_edit(Req({
            'initData': signed, 'date': '2026-10-05', 'action': 'shift_delete'})))
        assert removed.status == 200 and not _db.shifts


def test_calendar_tip_edits_are_user_scoped_and_private_mode_stays_local():
    from uuid import uuid4
    _db.store.clear()
    victim = _db.seed(99, 'income', 'card', 700)
    victim['work_date'] = '2026-10-05'
    signed = init_data(TOKEN, 42)
    foreign = run(webapp_api.api_calendar_edit(Req({
        'initData': signed, 'date': '2026-10-05', 'action': 'tip_delete',
        'entry_id': victim['id']})))
    assert foreign.status == 404 and victim in _db.store
    added = run(webapp_api.api_calendar_edit(Req({
        'initData': signed, 'date': '2026-10-05', 'action': 'tip_add',
        'amount': '350.50', 'account': 'cash', 'operation_id': str(uuid4())})))
    assert added.status == 200
    entry = run(_db.get_entry(added.data['id'], 42))
    assert entry['work_date'] == '2026-10-05' and entry['signed_amount'] == 350.5
    _db.private_ids.add(42)
    try:
        rejected = run(webapp_api.api_calendar_edit(Req({
            'initData': signed, 'date': '2026-10-05', 'action': 'tip_add',
            'amount': '10', 'account': 'cash', 'operation_id': str(uuid4())})))
        assert rejected.status == 409
    finally:
        _db.private_ids.remove(42)


def test_historical_expense_add_uses_selected_day_and_rejects_future():
    from uuid import uuid4
    _db.store.clear()
    signed = init_data(TOKEN,42)
    with patch.object(webapp_api,'op_today',return_value=date(2026,10,4)):
        r = run(webapp_api.api_calendar_edit(Req({
            'initData':signed,'date':'2026-10-02','action':'expense_add',
            'amount':'180.50','category':'Такси','operation_id':str(uuid4())})))
        future = run(webapp_api.api_calendar_edit(Req({
            'initData':signed,'date':'2026-10-05','action':'expense_add',
            'amount':20,'category':'Такси','operation_id':str(uuid4())})))
    assert r.status == 200 and future.status == 400
    entry = run(_db.get_entry(r.data['id'],42))
    assert entry['work_date'] == '2026-10-02' and entry['signed_amount'] == -180.5


def test_private_stats_use_only_device_entries():
    _db.store.clear()
    _db.seed(42, 'income', 'card', 999999)
    _db.private_ids.add(42)
    try:
        entry = {'id': 'local:1', 'kind': 'income', 'account': 'card',
                 'signed_amount': 500, 'category': 'Чаевые',
                 'work_date': '2026-10-04', 'created_at': '2026-10-04T15:00:00+03:00'}
        result = run(webapp_api.api_stats(Req({
            'initData': init_data(TOKEN, 42), 'private_entries': [entry]})))
        assert result.status == 200
        assert result.data['total_net'] == 500
        assert result.data['private_money_mode'] is True
        assert result.data['tip_entries'][0]['id'] == 'local:1'
        invalid = run(webapp_api.api_stats(Req({
            'initData': init_data(TOKEN, 42), 'private_entries': [{**entry, 'work_date': 'bad'}]})))
        assert invalid.status == 400
    finally:
        _db.private_ids.remove(42)


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
