"""Доступ к Supabase. Личный журнал — таблица entries.

Баланс поступивших денег не хранится отдельно: он считается по доходам и
расходам журнала. Начисления до выплаты в этот баланс не входят.
"""
import os
import asyncio
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import Client, create_client
from supabase.lib.client_options import ClientOptions

load_dotenv()

supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_KEY"],
    options=ClientOptions(auto_refresh_token=False,persist_session=False),
)

CASH = "cash"
CARD = "card"
PENDING = "pending"
ACCOUNTS = (CASH, CARD, PENDING)

ACCOUNT_LABELS = {CASH: "Наличные", CARD: "Карта", PENDING: "Начислено"}


# HTTPX's client supports threads. Queries have separate builders; service-role
# auth is immutable. Bound concurrency without putting all users in one queue.
_db_slots = asyncio.BoundedSemaphore(8)


async def _execute(query):
    async with _db_slots:
        task=asyncio.create_task(asyncio.to_thread(query.execute))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # A cancelled waiter must not free a slot while its thread still runs.
            try:await task
            finally:raise


async def _pages(query_factory):
    rows = []
    offset = 0
    while True:
        batch = (await _execute(query_factory().range(offset, offset + 499))).data
        if not batch:
            return rows
        rows.extend(batch)
        offset += len(batch)


# ─── users ───────────────────────────────────────────────────────────────────

async def get_or_create_user(user_id: int) -> dict:
    """Профиль по Telegram id. Имя и @username не храним — см. schema.sql."""
    res = await _execute(supabase.table("users").select("*").eq("id", user_id))
    if res.data:
        return res.data[0]
    await _execute(supabase.table("users").upsert({"id": user_id}, on_conflict="id", ignore_duplicates=True))
    res = await _execute(supabase.table("users").select("*").eq("id", user_id))
    return res.data[0]


async def set_onboarded(user_id: int) -> None:
    await _execute(supabase.table("users").update({"onboarded": True}).eq("id", user_id))


async def clear_entries(user_id: int) -> None:
    """Очистить журнал, оставив профиль (/reset)."""
    await _execute(supabase.table("entries").delete().eq("user_id", user_id))


async def clear_entries_once(user_id: int,update_id: int) -> None:
    await _execute(supabase.rpc('apply_telegram_user_action',{
        'actor':user_id,'update_no':update_id,'action':'clear'}))


async def delete_user_once(user_id: int,update_id: int) -> None:
    await _execute(supabase.rpc('apply_telegram_user_action',{
        'actor':user_id,'update_no':update_id,'action':'delete'}))


async def delete_user(user_id: int) -> None:
    """Стереть человека целиком: журнал, смены, профиль с токенами (/delete).

    Удаляем явно, а не полагаясь на ON DELETE CASCADE, чтобы результат не
    зависел от того, как заведены внешние ключи в конкретной базе.
    """
    await _execute(supabase.table("private_money_backups").delete().eq("user_id", user_id))
    await _execute(supabase.table("entries").delete().eq("user_id", user_id))
    await _execute(supabase.table("shifts").delete().eq("user_id", user_id))
    await _execute(supabase.table("users").delete().eq("id", user_id))


async def save_private_backup(user_id: int, record_id: str, payload: str, key_id: str) -> None:
    """Store ciphertext only after checking the user's current device key in SQL."""
    await _execute(supabase.rpc("store_private_money_backup", {
        "actor": user_id, "source_id": record_id, "sealed": payload,
        "expected_key_id": key_id,
    }))


async def get_private_backups(user_id: int, key_id: str) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    return await _pages(lambda: supabase.table("private_money_backups")
                        .select("record_id,payload,expires_at")
                        .eq("user_id", user_id).eq("key_id", key_id)
                        .gt("expires_at", now).order("created_at").order("record_id"))


async def clear_private_backups(user_id: int) -> None:
    await _execute(supabase.table("private_money_backups").delete().eq("user_id", user_id))


async def prune_private_backups() -> None:
    await _execute(supabase.table("private_money_backups").delete()
                   .lt("expires_at", datetime.now(timezone.utc).isoformat()))


# ─── entries ─────────────────────────────────────────────────────────────────

async def add_entries(user_id: int,items: list[dict]) -> list[dict]:
    """An entire Telegram message commits or rolls back together."""
    if not 1<=len(items)<=32:raise ValueError('money_batch_invalid')
    return (await _execute(supabase.rpc('add_money_entries',{'actor':user_id,'items':items}))).data


async def undo_entry_batch(user_id: int,chat_id: int,message_id: int) -> int:
    return (await _execute(supabase.rpc('undo_money_batch',{
        'actor':user_id,'batch_prefix':f'telegram:{chat_id}:{message_id}:'}))).data

async def money_source_seen(user_id: int,source_key: str) -> bool:
    return bool((await _execute(supabase.table('money_source_receipts').select('source_key')
                .eq('user_id',user_id).eq('source_key',source_key).limit(1))).data)


async def add_entry(
    user_id: int,
    kind: str,
    account: str,
    signed_amount: float,
    category: str = "Прочее",
    note: str | None = None,
    order_amount: float | None = None,
    tip_percent: float | None = None,
    work_date: str | None = None,
    source_key: str | None = None,
) -> dict | None:
    assert kind in ("income", "expense", "adjustment", "accrual"), kind
    assert account in ACCOUNTS, account
    assert (kind == "accrual") == (account == PENDING)
    data = {
        "user_id": user_id,
        "kind": kind,
        "account": account,
        "signed_amount": round(signed_amount, 2),
        "category": category,
        "note": note,
    }
    if order_amount is not None:
        data["order_amount"] = order_amount
    if tip_percent is not None:
        data["tip_percent"] = tip_percent
    if work_date is not None:
        data["work_date"] = work_date
    if source_key is not None:
        data["source_key"] = source_key
        previous = (await _execute(supabase.table("entries").select("*")
                    .eq("user_id", user_id).eq("source_key", source_key).limit(1))).data
        if previous:
            original=previous[0].get("source_payload") or previous[0]
            if (original["kind"] != kind or original["account"] != account
                    or float(original["signed_amount"]) != round(signed_amount, 2)
                    or original["category"] != category
                    or (work_date is not None and original.get("work_date") != work_date)):
                raise ValueError("source_conflict")
            return previous[0]
    if source_key is not None and await money_source_seen(user_id,source_key):return None
    try:
        res = await _execute(supabase.table("entries").insert(data))
    except Exception:
        if source_key is None:
            raise
        previous = (await _execute(supabase.table("entries").select("*")
                    .eq("user_id", user_id).eq("source_key", source_key).limit(1))).data
        if previous:
            original=previous[0].get("source_payload") or previous[0]
            if (original["kind"] != kind or original["account"] != account
                    or float(original["signed_amount"]) != round(signed_amount, 2)
                    or original["category"] != category
                    or (work_date is not None and original.get("work_date") != work_date)):
                raise ValueError("source_conflict")
            return previous[0]
        if await money_source_seen(user_id,source_key):return None
        raise
    return res.data[0]


async def get_entry(entry_id: int, user_id: int) -> dict | None:
    res = await _execute(supabase.table("entries").select("*") \
        .eq("id", entry_id).eq("user_id", user_id))
    return res.data[0] if res.data else None


async def update_entry_account(entry_id: int, user_id: int, account: str) -> dict | None:
    assert account in ACCOUNTS, account
    res = await _execute(supabase.table("entries").update({"account": account}) \
        .eq("id", entry_id).eq("user_id", user_id))
    return res.data[0] if res.data else None


async def update_entry_amount(entry_id: int, user_id: int, signed_amount: float) -> dict | None:
    res = await _execute(supabase.table("entries").update({"signed_amount": round(signed_amount, 2)}) \
        .eq("id", entry_id).eq("user_id", user_id))
    return res.data[0] if res.data else None


async def update_tip_details(entry_id: int, user_id: int, amount: float, account: str) -> dict | None:
    """Save both tip fields in one scoped database update."""
    assert account in ACCOUNTS, account
    res = await _execute(supabase.table("entries").update(
        {"signed_amount": round(amount, 2), "account": account})
        .eq("id", entry_id).eq("user_id", user_id)
        .eq("kind", "income").eq("category", "Чаевые"))
    return res.data[0] if res.data else None


async def delete_entry(entry_id: int, user_id: int) -> bool:
    """Возвращает True, если запись существовала и была удалена."""
    res = await _execute(supabase.table("entries").delete() \
        .eq("id", entry_id).eq("user_id", user_id))
    return bool(res.data)


async def get_recent_entries(user_id: int, limit: int = 15) -> list[dict]:
    res = await _execute(supabase.table("entries").select("*") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True).order("id", desc=True) \
        .limit(limit))
    return res.data


async def get_entries_since(user_id: int, since_iso: str) -> list[dict]:
    return await _pages(lambda: supabase.table("entries").select("*")
                        .eq("user_id", user_id).gte("created_at", since_iso)
                        .order("created_at").order("id"))


async def get_entries_for_work_date(user_id: int, work_date: str) -> list[dict]:
    return await _pages(lambda: supabase.table("entries").select("*")
                        .eq("user_id", user_id).eq("work_date", work_date)
                        .order("created_at").order("id"))


async def get_all_entries(user_id: int) -> list[dict]:
    return await _pages(lambda: supabase.table("entries").select("*")
                        .eq("user_id", user_id).order("created_at").order("id"))


# ─── план смены ──────────────────────────────────────────────────────────────

async def get_shift_goal(user_id: int) -> float | None:
    res = await _execute(supabase.table("users").select("shift_goal").eq("id", user_id))
    if res.data and res.data[0].get("shift_goal") is not None:
        return float(res.data[0]["shift_goal"])
    return None


async def set_shift_goal(user_id: int, goal: float | None) -> None:
    await _execute(supabase.table("users").update({"shift_goal": goal}).eq("id", user_id))


async def get_onboarded_user_ids() -> list[int]:
    rows = await _pages(lambda: supabase.table("users").select("id").eq("onboarded", True).order("id"))
    return [row["id"] for row in rows]


# ─── агрегаты для админки ────────────────────────────────────────────────────
# Только счётчики и идентификаторы. Сумм конкретного человека эти запросы не
# возвращают и возвращать не должны — на этом держится раздел «Приватность»
# в README, ради которого из базы убирали имена.

async def count_users() -> int:
    res = await _execute(supabase.table("users").select("id", count="exact"))
    return res.count or 0


async def count_onboarded_users() -> int:
    res = await _execute(supabase.table("users").select("id", count="exact").eq("onboarded", True))
    return res.count or 0


async def count_users_since(since_iso: str) -> int:
    res = await _execute(supabase.table("users").select("id", count="exact") \
        .gte("created_at", since_iso))
    return res.count or 0


async def count_entries_since(since_iso: str) -> int:
    res = await _execute(supabase.table("entries").select("id", count="exact") \
        .gte("created_at", since_iso))
    return res.count or 0


async def active_user_ids_since(since_iso: str) -> set[int]:
    """Кто вообще что-то записал за период — только id, без сумм."""
    rows = await _pages(lambda: supabase.table("entries").select("id,user_id")
                        .gte("created_at", since_iso).order("id"))
    return {row["user_id"] for row in rows}


async def count_shifts_on(date_iso: str) -> int:
    res = await _execute(supabase.table("shifts").select("id", count="exact") \
        .eq("shift_date", date_iso))
    return res.count or 0


# ─── расписание смен ─────────────────────────────────────────────────────────

async def add_shifts(user_id: int, dates: list[str]) -> None:
    """Ставит смены на даты (ISO YYYY-MM-DD). Дубли игнорируются."""
    if not dates:
        return
    rows = [{"user_id": user_id, "shift_date": d} for d in dates]
    await _execute(supabase.table("shifts").upsert(
        rows, on_conflict="user_id,shift_date", ignore_duplicates=True
    ))


async def get_shift_dates(user_id: int, since: str | None = None, until: str | None = None) -> list[str]:
    def query():
        q = supabase.table("shifts").select("shift_date").eq("user_id", user_id)
        if since:
            q = q.gte("shift_date", since)
        if until:
            q = q.lte("shift_date", until)
        return q.order("shift_date")
    return [row["shift_date"] for row in await _pages(query)]


async def get_shift_details(user_id: int, since: str, until: str) -> list[dict]:
    return await _pages(lambda: supabase.table("shifts")
                        .select("shift_date,starts_at,ends_at")
                        .eq("user_id", user_id).gte("shift_date", since)
                        .lte("shift_date", until).order("shift_date"))


async def get_calendar_shift_details(user_id: int) -> list[dict]:
    """Only planned dates and times: a calendar feed never reads financial data."""
    return await _pages(lambda: supabase.table("shifts")
                        .select("shift_date,starts_at,ends_at,created_at,calendar_updated_at")
                        .eq("user_id", user_id).order("shift_date"))


async def get_calendar_subscription(user_id: int) -> dict | None:
    res = await _execute(supabase.table("calendar_subscriptions").select("*")
                         .eq("user_id", user_id).limit(1))
    return res.data[0] if res.data else None


async def get_calendar_subscription_by_feed(feed_id: str) -> dict | None:
    res = await _execute(supabase.table("calendar_subscriptions")
                         .select("user_id,feed_id,secret_salt")
                         .eq("feed_id", feed_id).limit(1))
    return res.data[0] if res.data else None


async def create_calendar_subscription(user_id: int, feed_id: str, salt: str) -> dict:
    await get_or_create_user(user_id)
    await _execute(supabase.table("calendar_subscriptions").upsert(
        {"user_id": user_id, "feed_id": feed_id, "secret_salt": salt},
        on_conflict="user_id", ignore_duplicates=True))
    return await get_calendar_subscription(user_id)


async def rotate_calendar_subscription(user_id: int, feed_id: str, salt: str) -> dict | None:
    res = await _execute(supabase.table("calendar_subscriptions").update({
        "feed_id": feed_id, "secret_salt": salt,
        "rotated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("user_id", user_id))
    return res.data[0] if res.data else None


async def revoke_calendar_subscription(user_id: int) -> None:
    await _execute(supabase.table("calendar_subscriptions").delete().eq("user_id", user_id))


async def get_worked_shift_details(user_id: int, since: str, until: str) -> list[dict]:
    return await _pages(lambda: supabase.table("worked_shifts")
                        .select("shift_date,actual_start,actual_end,hourly_rate")
                        .eq("user_id", user_id).gte("shift_date", since)
                        .lte("shift_date", until).order("shift_date"))


async def save_shift(user_id: int, day: str, start: str | None, end: str | None) -> None:
    await get_or_create_user(user_id)
    await _execute(supabase.table("shifts").upsert({
        "user_id": user_id, "shift_date": day,
        "starts_at": start, "ends_at": end,
    }, on_conflict="user_id,shift_date"))


async def has_shift_on(user_id: int, date_iso: str) -> bool:
    res = await _execute(supabase.table("shifts").select("id") \
        .eq("user_id", user_id).eq("shift_date", date_iso).limit(1))
    return bool(res.data)


async def delete_shift(user_id: int, date_iso: str) -> bool:
    res = await _execute(supabase.table("shifts").delete() \
        .eq("user_id", user_id).eq("shift_date", date_iso))
    return bool(res.data)


async def get_user_ids_with_shift_on(date_iso: str) -> list[int]:
    rows = await _pages(lambda: supabase.table("shifts").select("id,user_id")
                        .eq("shift_date", date_iso).order("id"))
    return [row["user_id"] for row in rows]
