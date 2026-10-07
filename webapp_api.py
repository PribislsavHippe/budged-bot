"""API мини-апа: раздача страницы и /api/stats с проверкой подписи Telegram.

initData подписан ботовским токеном — подделать user_id нельзя.
"""
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import time
import uuid
from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlsplit

from aiohttp import web

import db
import calendar_feed
import google_calendar as gcal
import private_payload
from stats import _entry_date, compute_month, compute_stats, month_bounds
from workday import op_today

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
        age = time.time() - int(pairs.get("auth_date", "0"))
        if not -60 <= age <= 86400:
            return None
        user = json.loads(pairs.get("user", "{}"))
        return int(user["id"])
    except Exception as e:
        logging.warning("initData validation failed")
        return None


NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


async def serve_app(request: web.Request) -> web.Response:
    # Telegram WebView агрессивно кэширует — запрещаем явно
    return web.FileResponse(os.path.join(WEBAPP_DIR, "index.html"), headers=NO_CACHE)


def _private_entries(body: dict) -> list[dict]:
    entries=body.get('private_entries',[])
    if not isinstance(entries,list) or len(entries)>5000:raise ValueError('private_entries_invalid')
    for e in entries:
        if not isinstance(e,dict) or e.get('kind') not in {'income','expense','adjustment','accrual'} \
                or e.get('account') not in {'cash','card','pending'} \
                or (e['kind']=='accrual') != (e['account']=='pending') \
                or not isinstance(e.get('category'),str) \
                or not 1<=len(e['category'])<=100 \
                or type(e.get('id')) not in {str,int} or not str(e['id']):
            raise ValueError('private_entries_invalid')
        amount=Decimal(str(e.get('signed_amount')))
        if not amount.is_finite() or abs(amount)>10000000:raise ValueError('private_entries_invalid')
        date.fromisoformat(e.get('work_date',''))
    return entries


async def _entries_for_view(user_id: int, body: dict) -> tuple[list[dict],bool]:
    user=await db.get_or_create_user(user_id)
    private=bool(user.get('private_money_mode'))
    return (_private_entries(body) if private else await db.get_all_entries(user_id)),private


async def _stats_payload(app: web.Application, user_id: int, body: dict | None = None) -> dict:
    entries,private=await _entries_for_view(user_id,body or {})
    goal = await db.get_shift_goal(user_id)
    payload = compute_stats(entries, shift_goal=goal)
    payload["bot_username"] = app.get("bot_username")
    from tips_stats import summarize
    today = op_today()
    payload["tip_month"] = summarize(entries, today.replace(day=1), today, today)
    payload["operational_today"] = today.isoformat()
    payload["private_money_mode"] = private
    # Смены берём все разом: из них и календарь текущего месяца, и понимание,
    # есть ли соседние месяцы, куда листать.
    today = op_today()
    shifts = await db.get_shift_dates(user_id)
    prefix = today.strftime("%Y-%m")
    payload["scheduled_shifts"] = [s for s in shifts if s.startswith(prefix)]
    payload.update(month_bounds(entries, shifts, today.year, today.month, today))
    first=today.replace(day=1).isoformat()
    last=today.replace(day=monthrange(today.year,today.month)[1]).isoformat()
    payload['planned_times']=await db.get_shift_details(user_id,first,last)
    payload['worked_times']=await db.get_worked_shift_details(user_id,first,last)
    payload['tip_entries']=[{'id':e['id'],'date':_entry_date(e).isoformat(),
                             'amount':float(e['signed_amount']),'account':e['account']}
                            for e in entries if e['kind']=='income' and e['category']=='Чаевые'
                            and first<=_entry_date(e).isoformat()<=last]
    return payload


async def api_private_prepare(request: web.Request) -> web.Response:
    user_id, body=await _auth(request)
    if user_id is None:return body
    user=await db.get_or_create_user(user_id)
    if user.get('private_money_mode'):
        return web.json_response({'active':True,'entries':[],
                                  'public_key':user.get('private_money_public_key')},headers=NO_CACHE)
    entries=await db.get_all_entries(user_id)
    return web.json_response({'active':False,'entries':entries},headers=NO_CACHE)


async def api_private_activate(request: web.Request) -> web.Response:
    user_id, body=await _auth(request)
    if user_id is None:return body
    try:
        public_key=body['public_key']
        private_payload.validate_public_key(public_key)
        ids=body['entry_ids']
        if not isinstance(ids,list) or len(ids)>100000 or any(type(i) is not int or i<=0 for i in ids):raise ValueError()
        ids=sorted(ids)
        if len(ids)!=len(set(ids)):raise ValueError()
    except (KeyError,ValueError,TypeError):
        return web.json_response({'error':'Не получилось подготовить приватный режим.'},status=400,headers=NO_CACHE)
    try:
        entries=await db.get_all_entries(user_id)
        if sorted(e['id'] for e in entries)!=ids:
            return web.json_response({'error':'Записи изменились во время переноса. Повтори перенос.'},status=409,headers=NO_CACHE)
        backups=[{'record_id':str(e['id']),
                  'payload':private_payload.seal(user_id,public_key,e,request.app['bot_token'])[0]}
                 for e in entries]
        await db._execute(db.supabase.rpc('activate_private_money_with_backups',
            {'actor':user_id,'expected_ids':ids,'public_key':public_key,'backups':backups}))
    except Exception as error:
        from diagnostics import failure
        failure(error,area='private_money',stage='activate')
        if (await db.get_or_create_user(user_id)).get('private_money_mode'):
            return web.json_response({'active':True},headers=NO_CACHE)
        return web.json_response({'error':'Не получил подтверждение переноса. Проверь режим и повтори попытку.'},status=409,headers=NO_CACHE)
    return web.json_response({'active':True},headers=NO_CACHE)


async def api_private_backups(request: web.Request) -> web.Response:
    user_id, body=await _auth(request)
    if user_id is None:return body
    user=await db.get_or_create_user(user_id)
    if not user.get('private_money_mode'):
        return web.json_response({'error':'Личный журнал ещё не включён.'},status=409,headers=NO_CACHE)
    key=user.get('private_money_public_key')
    try:key_id=private_payload.key_id(key)
    except ValueError:
        return web.json_response({'error':'Ключ личного журнала недоступен.'},status=409,headers=NO_CACHE)
    rows=await db.get_private_backups(user_id,key_id)
    return web.json_response({'backups':rows},headers=NO_CACHE)


async def api_private_clear_backups(request: web.Request) -> web.Response:
    user_id, body=await _auth(request)
    if user_id is None:return body
    if not (await db.get_or_create_user(user_id)).get('private_money_mode'):
        return web.json_response({'error':'Личный журнал ещё не включён.'},status=409,headers=NO_CACHE)
    await db.clear_private_backups(user_id)
    return web.json_response({'cleared':True},headers=NO_CACHE)


async def api_private_rotate(request: web.Request) -> web.Response:
    user_id, body=await _auth(request)
    if user_id is None:return body
    try:
        public_key=body['public_key']
        private_payload.validate_public_key(public_key)
    except (KeyError,ValueError,TypeError):
        return web.json_response({'error':'Ключ устройства не подходит.'},status=400,headers=NO_CACHE)
    await db._execute(db.supabase.rpc('rotate_private_money_device',
        {'actor':user_id,'public_key':public_key}))
    return web.json_response({'active':True},headers=NO_CACHE)


async def api_private_verify(request: web.Request) -> web.Response:
    user_id, body=await _auth(request)
    if user_id is None:return body
    if not (await db.get_or_create_user(user_id)).get('private_money_mode'):
        return web.json_response({'valid':False},status=409,headers=NO_CACHE)
    valid=private_payload.verify(user_id,body.get('payload'),body.get('signature'),request.app['bot_token'])
    return web.json_response({'valid':valid},status=200 if valid else 400,headers=NO_CACHE)


async def _auth(request: web.Request):
    """Возвращает (user_id, body) или (None, response-с-ошибкой)."""
    try:
        body = await request.json()
    except Exception:
        return None, web.json_response({"error": "Не получилось прочитать запрос. Открой приложение ещё раз."}, status=400, headers=NO_CACHE)
    if not isinstance(body, dict):
        return None, web.json_response({"error": "Не получилось прочитать запрос. Открой приложение ещё раз."}, status=400, headers=NO_CACHE)
    user_id = validate_init_data(body.get("initData", ""), request.app["bot_token"])
    if user_id is None:
        return None, web.json_response({"error": "Открой приложение заново через бота."}, status=401, headers=NO_CACHE)
    request["verified_uid"] = user_id
    return user_id, body


async def api_stats(request: web.Request) -> web.Response:
    user_id, body = await _auth(request)
    if user_id is None:
        return body
    try:result=await _stats_payload(request.app, user_id, body)
    except (ValueError,TypeError,InvalidOperation):
        return web.json_response({'error':'Не получилось прочитать личные записи на этом устройстве.'},status=400,headers=NO_CACHE)
    return web.json_response(result, headers=NO_CACHE)


def _track_saved_money_entry(user_id: int, entry_id: int, event: str) -> None:
    """Count a confirmed miniapp save once, without sending financial fields."""
    from uuid import uuid5, NAMESPACE_URL
    import research
    research.track(user_id, event, source='miniapp', screen='earnings',
                   operation=str(uuid5(NAMESPACE_URL, f'ux-entry:{user_id}:{entry_id}')))


async def api_shift_spend(request: web.Request) -> web.Response:
    from decimal import Decimal,InvalidOperation
    from uuid import UUID
    user_id, body = await _auth(request)
    if user_id is None:return body
    if (await db.get_or_create_user(user_id)).get('private_money_mode'):
        return web.json_response({'error':'В приватном режиме сохраняй расход на устройстве.'},status=409,headers=NO_CACHE)
    try:
        operation=str(UUID(str(body.get('operation_id',''))))
        raw=body.get('amount')
        if isinstance(raw,bool):raise ValueError()
        amount=Decimal(str(raw).replace(' ','').replace(',','.'))
        if not amount.is_finite() or not 0<amount<=10000000 or amount!=amount.quantize(Decimal('.01')):raise ValueError()
        category=body.get('category','Прочее')
        if not isinstance(category,str):raise ValueError()
        category=category.strip()
        if not 1<=len(category)<=60 or any(ord(c)<32 for c in category):raise ValueError()
    except (ValueError,InvalidOperation,TypeError):
        return web.json_response({'error':'Проверь сумму и название расхода.'},status=400,headers=NO_CACHE)
    await db.get_or_create_user(user_id)
    try:
        result=(await db._execute(db.supabase.rpc('add_miniapp_expense',
            {'actor':user_id,'operation':operation,'amount':float(amount),'category_name':category}))).data
    except Exception as error:
        from diagnostics import failure
        code,reference=failure(error,area='expense',stage='save')
        return web.json_response({'error':'Не получил подтверждение. Повтори — второй расход не появится.',
                                  'code':code,'reference':reference},status=503,headers=NO_CACHE)
    _track_saved_money_entry(user_id,result['id'],'expense_added')
    # Saving succeeded even if refreshing the chart fails. Never ask to re-enter it.
    try:stats=await _stats_payload(request.app,user_id)
    except Exception as error:
        from diagnostics import failure
        failure(error,area='expense',stage='refresh')
        stats=None
    return web.json_response({'saved':True,'stats':stats},headers=NO_CACHE)


async def api_tips_compare(request: web.Request) -> web.Response:
    from datetime import date
    from tips_stats import compare_tips
    user_id, body = await _auth(request)
    if user_id is None:
        return body
    try:
        kind = body.get("kind", "month")
        anchor = date.fromisoformat(body["anchor"]) if body.get("anchor") else None
        other = date.fromisoformat(body["other"]) if body.get("other") else None
        aligned = body.get("aligned", True)
        if not isinstance(aligned, bool):
            raise ValueError("Недопустимый режим сравнения")
        entries,_ = await _entries_for_view(user_id,body)
        result = compare_tips(entries, kind, anchor, other, aligned)
    except (ValueError, TypeError, InvalidOperation):
        return web.json_response({"error": "Проверь даты выбранного периода."}, status=400)
    return web.json_response(result, headers=NO_CACHE)


async def api_tips_range(request: web.Request) -> web.Response:
    """Home day or a selected period, with the entries needed for its detail sheet."""
    from tips_stats import summarize
    user_id, body = await _auth(request)
    if user_id is None:
        return body
    try:
        today = op_today()
        entries, _ = await _entries_for_view(user_id, body)
        custom = 'start' in body or 'end' in body
        if custom:
            start = date.fromisoformat(body['start'])
            end = date.fromisoformat(body['end'])
        else:
            shifts = await db.get_shift_dates(user_id, since=today.isoformat(), until=today.isoformat())
            tip_days = [_entry_date(e) for e in entries if e['kind'] == 'income'
                        and e['category'] == 'Чаевые' and _entry_date(e) <= today]
            start = end = today if today.isoformat() in shifts or not tip_days else max(tip_days)
        if start.year < 2000 or end.year > 2100 or start > end or end > today:
            raise ValueError()
    except (ValueError, TypeError, InvalidOperation):
        return web.json_response({'error': 'Проверь начало и конец периода.'}, status=400, headers=NO_CACHE)
    rows = [{'id':e['id'], 'date':_entry_date(e).isoformat(), 'kind':e['kind'],
             'account':e['account'], 'category':e['category'], 'amount':float(e['signed_amount'])}
            for e in entries if start <= _entry_date(e) <= end and
            (e['kind'] == 'expense' or e['kind'] == 'income' and e['category'] == 'Чаевые')]
    rows.sort(key=lambda e:(e['date'],str(e['id'])),reverse=True)
    return web.json_response({'period':summarize(entries,start,end,today),
                              'today':today.isoformat(),'custom':custom,'entries':rows},headers=NO_CACHE)


async def api_month(request: web.Request) -> web.Response:
    """Календарь произвольного месяца — для листания стрелками."""
    user_id, body = await _auth(request)
    if user_id is None:
        return body
    try:
        year = int(body.get("year"))
        month = int(body.get("month"))
    except (TypeError, ValueError):
        return web.json_response({"error": "Не получилось открыть этот месяц. Выбери другой."}, status=400)
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        return web.json_response({"error": "Не получилось открыть этот месяц. Выбери другой."}, status=400)

    try:entries,private = await _entries_for_view(user_id,body)
    except (ValueError,TypeError,InvalidOperation):
        return web.json_response({'error':'Не получилось прочитать личные записи на этом устройстве.'},status=400,headers=NO_CACHE)
    shifts = await db.get_shift_dates(user_id)
    result=compute_month(entries, shifts, year, month)
    result['private_money_mode']=private
    first=f'{year:04d}-{month:02d}-01'
    last=f'{year:04d}-{month:02d}-{monthrange(year,month)[1]:02d}'
    result['planned_times']=await db.get_shift_details(user_id,first,last)
    result['worked_times']=await db.get_worked_shift_details(user_id,first,last)
    result['tip_entries']=[{'id':e['id'],'date':_entry_date(e).isoformat(),
                            'amount':float(e['signed_amount']),'account':e['account']}
                           for e in entries if e['kind']=='income' and e['category']=='Чаевые'
                           and first<=_entry_date(e).isoformat()<=last]
    return web.json_response(result, headers=NO_CACHE)


async def api_calendar_edit(request: web.Request) -> web.Response:
    """Edit a single date from the calendar. Every operation is scoped to the signed user."""
    from uuid import UUID
    import schedule
    user_id, body = await _auth(request)
    if user_id is None:return body
    try:
        day=date.fromisoformat(body.get('date',''))
        if not 2000<=day.year<=2100:raise ValueError()
        action=body.get('action')
        if action not in {'shift_save','shift_delete','tip_add','tip_delete','expense_add'}:raise ValueError()
    except (TypeError,ValueError):
        return web.json_response({'error':'Проверь дату и действие.'},status=400,headers=NO_CACHE)
    day_iso=day.isoformat()
    if (action.startswith('tip_') or action=='expense_add') and (await db.get_or_create_user(user_id)).get('private_money_mode'):
        return web.json_response({'error':'Деньги в приватном режиме хранятся на устройстве.'},status=409,headers=NO_CACHE)
    if action=='shift_save':
        try:
            raw_start=body.get('start');raw_end=body.get('end')
            if bool(raw_start)!=bool(raw_end):raise ValueError()
            start=schedule.clock(raw_start).strftime('%H:%M') if raw_start else None
            end=schedule.clock(raw_end).strftime('%H:%M') if raw_end else None
            if start==end and start is not None:raise ValueError()
        except (TypeError,ValueError):
            return web.json_response({'error':'Укажи оба времени или оставь оба пустыми.'},status=400,headers=NO_CACHE)
        await db.save_shift(user_id,day_iso,start,end)
        import research
        research.track(user_id,'shift_planned',source='miniapp',screen='calendar')
        warning=None
        try:
            if await gcal.is_connected(user_id):
                synced=await gcal.sync_shifts(user_id,[day_iso])
                if not synced.get('synced'):warning='Смена сохранена, но Google Календарь не обновился.'
        except Exception:
            warning='Смена сохранена, но Google Календарь не обновился.'
        return web.json_response({'saved':True,'warning':warning},headers=NO_CACHE)
    if action=='shift_delete':
        removed=await db.delete_shift(user_id,day_iso)
        warning=None
        if removed:
            try:
                if await gcal.is_connected(user_id):await gcal.delete_shift_event(user_id,day_iso)
            except Exception:
                warning='Смена удалена здесь. Проверь её в Google Календаре.'
        return web.json_response({'saved':True,'warning':warning},headers=NO_CACHE)
    if action=='tip_add':
        try:
            raw=body.get('amount')
            if isinstance(raw,bool):raise ValueError()
            amount=Decimal(str(raw).replace(',','.'))
            if not amount.is_finite() or not 0<amount<=10000000 or amount!=amount.quantize(Decimal('.01')):raise ValueError()
            account=body.get('account')
            if account not in {'cash','card'}:raise ValueError()
            operation=str(UUID(str(body.get('operation_id',''))))
        except (ValueError,TypeError,InvalidOperation):
            return web.json_response({'error':'Проверь сумму и выбери: наличные или карта.'},status=400,headers=NO_CACHE)
        try:
            entry=await db.add_entry(user_id,'income',account,float(amount),category='Чаевые',
                                     note='из календаря',work_date=day_iso,source_key='calendar:'+operation)
        except ValueError:
            return web.json_response({'error':'Эта попытка уже сохранила другую сумму. Обнови календарь.'},status=409,headers=NO_CACHE)
        _track_saved_money_entry(user_id,entry['id'],'tip_added')
        return web.json_response({'saved':True,'id':entry['id']},headers=NO_CACHE)
    if action=='expense_add':
        try:
            if day > op_today():raise ValueError()
            raw=body.get('amount')
            if isinstance(raw,bool):raise ValueError()
            amount=Decimal(str(raw).replace(',','.'))
            if not amount.is_finite() or not 0<amount<=10000000 or amount!=amount.quantize(Decimal('.01')):raise ValueError()
            category=body.get('category','Прочее')
            if not isinstance(category,str):raise ValueError()
            category=category.strip()
            if not 1<=len(category)<=60 or any(ord(c)<32 for c in category):raise ValueError()
            operation=str(UUID(str(body.get('operation_id',''))))
        except (ValueError,TypeError,InvalidOperation):
            return web.json_response({'error':'Проверь дату, сумму и название расхода.'},status=400,headers=NO_CACHE)
        try:
            entry=await db.add_entry(user_id,'expense','cash',-float(amount),category=category,
                                     note='из миниаппа',work_date=day_iso,source_key='calendar-expense:'+operation)
        except ValueError:
            return web.json_response({'error':'Эта попытка уже сохранила другой расход. Обнови календарь.'},status=409,headers=NO_CACHE)
        _track_saved_money_entry(user_id,entry['id'],'expense_added')
        return web.json_response({'saved':True,'id':entry['id']},headers=NO_CACHE)
    try:
        entry_id=int(body.get('entry_id'))
        if isinstance(body.get('entry_id'),bool):raise ValueError()
    except (ValueError,TypeError):
        return web.json_response({'error':'Не нашёл запись.'},status=400,headers=NO_CACHE)
    entry=await db.get_entry(entry_id,user_id)
    if not entry or entry['kind']!='income' or entry['category']!='Чаевые' or _entry_date(entry)!=day:
        return web.json_response({'error':'Не нашёл чаевые за этот день.'},status=404,headers=NO_CACHE)
    await db.delete_entry(entry_id,user_id)
    return web.json_response({'saved':True},headers=NO_CACHE)


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
        if isinstance(body.get("entry_id"), bool):
            raise ValueError()
        entry_id = int(body.get("entry_id"))
    except (TypeError, ValueError):
        return web.json_response({"error": "Не получилось найти запись. Обнови историю."}, status=400)

    entry = await db.get_entry(entry_id, user_id)
    if entry is None:
        return web.json_response({"error": "Запись уже удалена или недоступна. Обнови историю."}, status=404)

    action = body.get("action")
    if action == "delete":
        await db.delete_entry(entry_id, user_id)
    elif action == "tip_details":
        try:
            if entry['kind'] != 'income' or entry['category'] != 'Чаевые':raise ValueError()
            if body.get('account') not in ('cash','card') or isinstance(body.get('amount'),bool):raise ValueError()
            amount=Decimal(str(body.get('amount')).replace(',','.'))
            if not amount.is_finite() or not 0<amount<=10000000 or amount!=amount.quantize(Decimal('.01')):raise ValueError()
        except (ValueError,TypeError,InvalidOperation):
            return web.json_response({'error':'Проверь сумму чаевых и выбери: наличные или карта.'},status=400,headers=NO_CACHE)
        await db.update_tip_details(entry_id,user_id,float(amount),body['account'])
    elif action == "account":
        if entry["kind"] == "accrual":
            return web.json_response({"error": "Начисление не поступило на счёт."}, status=400)
        account = body.get("account")
        if account not in ("cash", "card"):
            return web.json_response({"error": "Выбери, куда поступили деньги: наличными или на карту."}, status=400)
        await db.update_entry_account(entry_id, user_id, account)
    elif action == "amount":
        try:
            raw = body.get("amount")
            if isinstance(raw, bool):
                raise ValueError()
            amount = Decimal(str(raw))
            if (not amount.is_finite() or not 0 < amount <= 10_000_000
                    or amount != amount.quantize(Decimal("0.01"))):
                raise ValueError()
        except (TypeError, ValueError, InvalidOperation):
            return web.json_response({"error": "Проверь сумму: она должна быть больше нуля, с точностью до копеек."}, status=400)
        sign = 1 if entry["kind"] in ("income", "accrual") else -1
        await db.update_entry_amount(entry_id, user_id, sign * float(amount))
    else:
        return web.json_response({"error": "Не получилось выполнить изменение. Обнови историю."}, status=400)

    # The write has succeeded. A failed read must not make the client retry it.
    try:
        stats = await _stats_payload(request.app, user_id)
        entries = await db.get_recent_entries(user_id, limit=30)
    except Exception as error:
        from diagnostics import failure
        failure(error, area="history", stage="refresh")
        stats = entries = None
    return web.json_response({"saved": True, "stats": stats, "entries": entries}, headers=NO_CACHE)


async def api_gcal(request: web.Request) -> web.Response:
    """Статус Google Календаря + ссылка для подключения (внешний браузер)."""
    user_id, body = await _auth(request)
    if user_id is None:
        return body
    if not gcal.is_configured():
        return web.json_response({"configured": False, "connected": False}, headers=NO_CACHE)
    status = await gcal.connection_status(user_id)
    connected = status["connected"]
    return web.json_response({
        "configured": True,
        "message": status["message"],
        "error": status.get("error"),
        "connected": connected,
        "auth_url": None if connected or status.get("error") == "temporary" else gcal.auth_url(user_id),
        # Приложение не прошло проверку Google, доступ выдаётся вручную —
        # мини-ап предупреждает об этом до нажатия кнопки.
        "invite_only": gcal.INVITE_ONLY,
        "support": os.getenv("SUPPORT_CONTACT") or None,
    }, headers=NO_CACHE)


def _calendar_base_url(request: web.Request) -> str | None:
    """Never build a bearer URL from an untrusted Host or Forwarded header."""
    raw = (request.app.get('public_base_url') or os.getenv('WEBHOOK_HOST') or '').rstrip('/')
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password \
            or parsed.path or parsed.query or parsed.fragment:
        return None
    return raw


def _calendar_urls(base: str, row: dict, bot_token: str) -> tuple[str, str]:
    feed_id = str(uuid.UUID(str(row['feed_id'])))
    token = calendar_feed.feed_token(bot_token, feed_id, row['secret_salt'])
    root = f'{base}/calendar/iphone/{feed_id}/{token}'
    return root + '.ics', root


async def api_iphone_calendar(request: web.Request) -> web.Response:
    """Manage a revocable schedule-only feed from signed Telegram initData."""
    user_id, body = await _auth(request)
    if user_id is None:
        return body
    base = _calendar_base_url(request)
    if not base:
        return web.json_response({'available': False, 'enabled': False}, headers=NO_CACHE)
    action = body.get('action', 'status')
    if action not in {'status', 'create', 'rotate', 'revoke'}:
        return web.json_response({'error': 'Не получилось выполнить действие. Открой страницу ещё раз.'}, status=400, headers=NO_CACHE)
    if action == 'create':
        row = await db.create_calendar_subscription(user_id, str(uuid.uuid4()), secrets.token_urlsafe(32))
    elif action == 'rotate':
        row = await db.rotate_calendar_subscription(user_id, str(uuid.uuid4()), secrets.token_urlsafe(32))
        if not row:
            return web.json_response({'error': 'Подключение не найдено.'}, status=404, headers=NO_CACHE)
    elif action == 'revoke':
        await db.revoke_calendar_subscription(user_id)
        return web.json_response({'available': True, 'enabled': False}, headers=NO_CACHE)
    else:
        row = await db.get_calendar_subscription(user_id)
    if not row:
        return web.json_response({'available': True, 'enabled': False}, headers=NO_CACHE)
    feed_url, setup_url = _calendar_urls(base, row, request.app['bot_token'])
    return web.json_response({'available': True, 'enabled': True,
                              'feed_url': feed_url, 'setup_url': setup_url}, headers=NO_CACHE)


async def _verified_calendar_feed(request: web.Request) -> dict | None:
    try:
        feed_id = str(uuid.UUID(request.match_info['feed_id']))
        token = request.match_info['token']
        if len(token) != 64 or any(c not in '0123456789abcdef' for c in token):
            return None
    except (KeyError, ValueError):
        return None
    row = await db.get_calendar_subscription_by_feed(feed_id)
    if not row:
        return None
    expected = calendar_feed.feed_token(request.app['bot_token'], feed_id, row['secret_salt'])
    return row if hmac.compare_digest(expected, token) else None


CALENDAR_HEADERS = {**NO_CACHE, 'Referrer-Policy': 'no-referrer',
                    'X-Robots-Tag': 'noindex, nofollow',
                    'X-Content-Type-Options': 'nosniff'}


async def iphone_calendar_feed(request: web.Request) -> web.Response:
    row = await _verified_calendar_feed(request)
    if not row:
        return web.Response(status=404, headers=CALENDAR_HEADERS)
    shifts = await db.get_calendar_shift_details(row['user_id'])
    body = calendar_feed.render_shifts(shifts, row['user_id'], request.app['bot_token'])
    return web.Response(body=body, content_type='text/calendar', charset='utf-8',
                        headers=CALENDAR_HEADERS)


async def iphone_calendar_setup(request: web.Request) -> web.Response:
    row = await _verified_calendar_feed(request)
    if not row:
        return web.Response(status=404, headers=CALENDAR_HEADERS)
    base = _calendar_base_url(request)
    if not base:
        return web.Response(status=503, headers=CALENDAR_HEADERS)
    feed_url, _ = _calendar_urls(base, row, request.app['bot_token'])
    safe = html.escape(feed_url, quote=True)
    page = ("<!doctype html><html lang='ru'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Календарь смен</title><style>body{max-width:36rem;margin:3rem auto;"
            "padding:0 1.25rem;background:#fde9eb;color:#181617;font:1rem/1.5 -apple-system,"
            "BlinkMacSystemFont,sans-serif}h1{font-weight:400}input{box-sizing:border-box;"
            "width:100%;padding:.8rem;border:1px solid #aaa;border-radius:.5rem;font:inherit}"
            "li{margin:.8rem 0}</style><h1>Календарь смен</h1>"
            "<p>Скопируй личную ссылку. В приложении «Календарь» открой «Календари» → "
            "«Добавить календарь» → «Добавить календарь подписки» и вставь её.</p>"
            f"<input aria-label='Личная ссылка на календарь' readonly value='{safe}'>"
            f"<p><a href='{safe}'>Открыть ссылку на iPhone</a>. Если откроется импорт файла, "
            "используй подписку по шагам выше.</p>"
            "<p>Подписка обновляет смены автоматически. Открытие файла .ics как обычного "
            "файла создаст разовый импорт. Ссылка даёт доступ только к датам и времени смен; "
            "не передавай её другим людям.</p></html>")
    return web.Response(text=page, content_type='text/html', charset='utf-8',
                        headers={**CALENDAR_HEADERS,
                                 'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; "
                                                            "script-src 'none'; form-action 'none'"})


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
        from diagnostics import failure
        failure(e,area='calendar',stage='callback')
        return web.Response(text=page + "<h2>Ошибка</h2><p>Не удалось подключить календарь.</p>",
                            content_type="text/html", status=500)
    try:
        sync = await gcal.sync_pending(user_id)
    except Exception as error:
        from diagnostics import failure
        failure(error,area='calendar',stage='backfill')
        sync = {"synced": 0, "pending": "неизвестно", "message": "Подключение сохранено. В чате бота отправь /calendar, чтобы повторить добавление смен."}
    return web.Response(
        text=page + f"<h2>Календарь подключён ✓</h2><p>Добавлено смен: {sync['synced']}. Пока не добавлены: {sync['pending']}.</p><p>{sync['message']}</p><p>Возвращайся в Telegram.</p>",
        content_type="text/html",
    )


@web.middleware
async def api_errors(request, handler):
    if not request.path.startswith('/api/'):
        return await handler(request)
    import research
    screens={'/api/stats':'earnings','/api/month':'calendar','/api/entries':'history',
             '/api/tips_compare':'earnings','/api/restaurant/view':'restaurant',
             '/api/restaurant/access':'restaurant','/api/sales/view':'sales'}
    screen=screens.get(request.path)
    try:
        response=await handler(request)
    except Exception as error:
        from diagnostics import failure
        code,reference=failure(error,area='miniapp',stage=screen or 'api')
        uid=request.get('verified_uid')
        if uid and screen:research.track(uid,'cabinet_load_error',source='server',screen=screen,error_code=code)
        return web.json_response({'error':'Не получилось загрузить данные. Попробуй ещё раз.',
                                  'code':code,'reference':reference},status=503,headers=NO_CACHE)
    uid=request.get('verified_uid')
    if uid and screen and response.status==200 and request.path not in {'/api/restaurant/access'}:
        research.track(uid,'cabinet_loaded',source='server',screen=screen)
    elif uid and screen and response.status>=500 and not request.path.startswith('/api/restaurant/') and not request.get('research_error_tracked'):
        research.track(uid,'cabinet_load_error',source='server',screen=screen,error_code='backend')
    return response


async def service_charge_view(request: web.Request) -> web.Response:
    user_id, body = await _auth(request)
    if user_id is None:
        return body
    from sales import month_key
    from service_charge import summarize
    try:
        month = month_key(body.get("month", op_today().strftime("%Y-%m")))
        entries, private = await _entries_for_view(user_id, body)
    except ValueError:
        return web.json_response({"error": "Не получилось открыть личные начисления. Обнови страницу."}, status=400, headers=NO_CACHE)
    return web.json_response({**summarize(entries, month), "private": private}, headers=NO_CACHE)


def register_webapp_routes(app: web.Application, bot_token: str, bot_username: str | None = None):
    app["bot_token"] = bot_token
    app["bot_username"] = bot_username
    from sales_api import register_sales_routes
    register_sales_routes(app)
    app.router.add_post("/api/service_charge/view", service_charge_view)
    from research_api import register as register_research
    register_research(app)
    app.middlewares.append(api_errors)
    from restaurant_api import register
    register(app)
    app.router.add_get("/app", serve_app)
    app.router.add_get("/app/sales.js", lambda _: web.FileResponse(os.path.join(WEBAPP_DIR, "sales.js"), headers=NO_CACHE))
    app.router.add_get("/app/sales.css", lambda _: web.FileResponse(os.path.join(WEBAPP_DIR, "sales.css"), headers=NO_CACHE))
    app.router.add_post("/api/stats", api_stats)
    app.router.add_post("/api/tips_compare", api_tips_compare)
    app.router.add_post("/api/tips_range", api_tips_range)
    for asset in ("tips.js", "tips.css", "private.js", "restaurant.js", "restaurant.css", "ux.js", "research.js", "research.css"):
        async def serve_asset(request, asset=asset):
            return web.FileResponse(os.path.join(WEBAPP_DIR, asset), headers=NO_CACHE)
        app.router.add_get("/app/" + asset, serve_asset)
    app.router.add_post("/api/shift_spend", api_shift_spend)
    app.router.add_post("/api/month", api_month)
    app.router.add_post("/api/calendar_edit", api_calendar_edit)
    app.router.add_post("/api/private/prepare", api_private_prepare)
    app.router.add_post("/api/private/activate", api_private_activate)
    app.router.add_post("/api/private/backups", api_private_backups)
    app.router.add_post("/api/private/clear_backups", api_private_clear_backups)
    app.router.add_post("/api/private/rotate", api_private_rotate)
    app.router.add_post("/api/private/verify", api_private_verify)
    app.router.add_post("/api/entries", api_entries)
    app.router.add_post("/api/entry_edit", api_entry_edit)
    app.router.add_post("/api/gcal", api_gcal)
    app.router.add_post("/api/iphone_calendar", api_iphone_calendar)
    app.router.add_get("/calendar/iphone/{feed_id}/{token}.ics", iphone_calendar_feed)
    app.router.add_get("/calendar/iphone/{feed_id}/{token}", iphone_calendar_setup)
    app.router.add_get("/google/callback", google_callback)
