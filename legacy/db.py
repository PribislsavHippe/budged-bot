import logging
from supabase import create_client, Client
import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)


# ─── USERS ────────────────────────────────────────────────

async def get_or_create_user(user_id: int, username: str = None, first_name: str = None):
    res = supabase.table("users").select("*").eq("id", user_id).execute()
    if res.data:
        return res.data[0]
    user = {"id": user_id, "username": username, "first_name": first_name}
    res = supabase.table("users").insert(user).execute()
    return res.data[0]


async def get_user(user_id: int):
    res = supabase.table("users").select("*").eq("id", user_id).execute()
    return res.data[0] if res.data else None


async def update_user(user_id: int, data: dict):
    supabase.table("users").update(data).eq("id", user_id).execute()


async def get_all_active_users():
    res = supabase.table("users").select("*").eq("is_active", True).execute()
    return res.data


# ─── TRANSACTIONS ─────────────────────────────────────────

async def add_transaction(user_id: int, type_: str, amount: float, category: str, description: str = None):
    data = {
        "user_id": user_id,
        "type": type_,
        "amount": amount,
        "category": category,
        "description": description
    }
    res = supabase.table("transactions").insert(data).execute()
    return res.data[0]


async def delete_transaction(transaction_id: int, user_id: int):
    supabase.table("transactions").delete().eq("id", transaction_id).eq("user_id", user_id).execute()


async def get_recent_transactions(user_id: int, limit: int = 10):
    res = supabase.table("transactions").select("*")\
        .eq("user_id", user_id)\
        .order("created_at", desc=True)\
        .limit(limit)\
        .execute()
    return res.data


def _since_datetime(period: str) -> str | None:
    now = datetime.now(timezone.utc)
    if period == "week":
        return (now - timedelta(days=7)).isoformat()
    elif period == "month":
        # Начало текущего календарного месяца — точный бюджетный счётчик
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start_of_month.isoformat()
    elif period == "all":
        return None
    return (now - timedelta(days=30)).isoformat()


async def get_transactions(user_id: int, period: str = "month"):
    query = supabase.table("transactions").select("*").eq("user_id", user_id)
    since = _since_datetime(period)
    if since:
        query = query.gte("created_at", since)
    res = query.order("created_at", desc=True).execute()
    return res.data


async def get_stats(user_id: int, period: str = "month") -> dict:
    transactions = await get_transactions(user_id, period)

    income   = sum(t["amount"] for t in transactions if t["type"] == "income")
    expenses = sum(t["amount"] for t in transactions if t["type"] == "expense")
    balance  = income - expenses

    by_category = {}
    by_income_category = {}

    for t in transactions:
        cat = t.get("category", "Прочее")
        if t["type"] == "expense":
            by_category[cat] = by_category.get(cat, 0) + t["amount"]
        else:
            by_income_category[cat] = by_income_category.get(cat, 0) + t["amount"]

    # Сортируем по убыванию
    by_category       = dict(sorted(by_category.items(),       key=lambda x: x[1], reverse=True))
    by_income_category = dict(sorted(by_income_category.items(), key=lambda x: x[1], reverse=True))

    return {
        "income": income,
        "expenses": expenses,
        "balance": balance,
        "by_category": by_category,
        "by_income_category": by_income_category,
        "transactions_count": len(transactions),
    }


# ─── SCHEDULED PAYMENTS ──────────────────────────────────

async def add_scheduled_payment(user_id: int, name: str, amount: float, day: int,
                                 category: str = "Обязательные",
                                 remind_days_before: int = 2, calendar_event_id: str = None):
    data = {
        "user_id": user_id,
        "name": name,
        "amount": amount,
        "day_of_month": day,
        "category": category,
        "remind_days_before": remind_days_before,
    }
    if calendar_event_id:
        data["calendar_event_id"] = calendar_event_id
    res = supabase.table("scheduled_payments").insert(data).execute()
    return res.data[0]


async def get_scheduled_payments(user_id: int):
    res = supabase.table("scheduled_payments").select("*").eq("user_id", user_id).execute()
    return res.data


async def delete_scheduled_payment(payment_id: int, user_id: int):
    supabase.table("scheduled_payments").delete().eq("id", payment_id).eq("user_id", user_id).execute()


async def get_payments_due_soon(days_ahead: int = 3):
    from datetime import date
    today = date.today().day
    target_day = today + days_ahead
    res = supabase.table("scheduled_payments")\
        .select("*, users(*)")\
        .lte("day_of_month", target_day)\
        .gte("day_of_month", today)\
        .execute()
    return res.data


# ─── BUDGETS ─────────────────────────────────────────────

async def set_budget(user_id: int, category: str, limit_amount: float, period: str = "monthly"):
    existing = supabase.table("budgets").select("id")\
        .eq("user_id", user_id)\
        .eq("category", category)\
        .execute()
    data = {
        "user_id": user_id,
        "category": category,
        "limit_amount": limit_amount,
        "period": period,
    }
    if existing.data:
        supabase.table("budgets").update({"limit_amount": limit_amount})\
            .eq("user_id", user_id).eq("category", category).execute()
    else:
        supabase.table("budgets").insert(data).execute()


async def get_budgets(user_id: int):
    res = supabase.table("budgets").select("*").eq("user_id", user_id).execute()
    return res.data


# ─── GOOGLE TOKENS ───────────────────────────────────────

async def save_google_token(user_id: int, access_token: str, refresh_token: str, expiry, calendar_id: str = "primary"):
    existing = supabase.table("google_tokens").select("id").eq("user_id", user_id).execute()
    data = {
        "user_id": user_id,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expiry": expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry),
        "calendar_id": calendar_id,
    }
    if existing.data:
        supabase.table("google_tokens").update(data).eq("user_id", user_id).execute()
    else:
        supabase.table("google_tokens").insert(data).execute()


async def get_google_token(user_id: int):
    res = supabase.table("google_tokens").select("*").eq("user_id", user_id).execute()
    return res.data[0] if res.data else None


# ─── SALARY DAYS ────────────────────────────────────────

async def get_salary_days(user_id: int) -> list[int]:
    user = await get_user(user_id)
    if not user:
        return []
    days = []
    if user.get("salary_day"):
        days.append(int(user["salary_day"]))
    if user.get("salary_day_2"):
        days.append(int(user["salary_day_2"]))
    # Поддержка salary_days как JSON-массива (если добавлено)
    if user.get("salary_days"):
        try:
            import json
            raw = user["salary_days"]
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, list):
                return sorted(set(int(d) for d in parsed if d))
        except Exception:
            pass
    return sorted(set(days))


async def set_salary_days(user_id: int, days: list[int]):
    days_sorted = sorted(set(days))
    update = {}
    if len(days_sorted) >= 1:
        update["salary_day"] = days_sorted[0]
    if len(days_sorted) >= 2:
        update["salary_day_2"] = days_sorted[1]
    else:
        update["salary_day_2"] = None
    # Сохраняем полный список в salary_days если поле есть
    try:
        import json
        update["salary_days"] = json.dumps(days_sorted)
    except Exception:
        pass
    await update_user(user_id, update)


# ─── PLANNED INCOME ──────────────────────────────────────

async def add_planned_income(
    user_id: int,
    amount: float,
    expected_date: str,
    description: str = None,
    type_: str = "income",
):
    data = {
        "user_id": user_id,
        "amount": amount,
        "expected_date": expected_date,
        "description": description,
    }
    # Пробуем с type_ (если migration.sql уже применён)
    try:
        data_with_type = {**data, "type": type_}
        res = supabase.table("planned_income").insert(data_with_type).execute()
        return res.data[0]
    except Exception:
        # Fallback без type
        res = supabase.table("planned_income").insert(data).execute()
        return res.data[0]


async def get_planned_income(user_id: int, from_date: str = None, to_date: str = None):
    query = supabase.table("planned_income").select("*").eq("user_id", user_id)
    if from_date:
        query = query.gte("expected_date", from_date)
    if to_date:
        query = query.lte("expected_date", to_date)
    res = query.order("expected_date").execute()
    return res.data


async def delete_planned_income(income_id: int, user_id: int):
    supabase.table("planned_income").delete().eq("id", income_id).eq("user_id", user_id).execute()


# ─── GOALS ───────────────────────────────────────────────

async def add_goal(user_id: int, name: str, target_amount: float, target_months: int, monthly_amount: float):
    data = {
        "user_id": user_id,
        "name": name,
        "target_amount": target_amount,
        "target_months": target_months,
        "monthly_amount": monthly_amount,
    }
    res = supabase.table("goals").insert(data).execute()
    return res.data[0]


async def get_goals(user_id: int, active_only: bool = True):
    query = supabase.table("goals").select("*").eq("user_id", user_id)
    if active_only:
        query = query.eq("is_active", True)
    res = query.order("created_at", desc=True).execute()
    return res.data


async def set_goal_inactive(goal_id: int, user_id: int):
    supabase.table("goals").update({"is_active": False}).eq("id", goal_id).eq("user_id", user_id).execute()


# ─── ПОЛНЫЙ СБРОС ДАННЫХ ПОЛЬЗОВАТЕЛЯ ────────────────────

async def delete_all_user_data(user_id: int) -> dict:
    """
    Удаляет ВСЕ данные пользователя из всех таблиц.
    Оставляет запись в users, сбрасывает настройки.
    Возвращает словарь с количеством удалённых записей.
    """
    counts = {}
    tables = [
        "transactions",
        "scheduled_payments",
        "budgets",
        "planned_income",
        "goals",
        "google_tokens",
    ]
    for table in tables:
        try:
            res = supabase.table(table).delete().eq("user_id", user_id).execute()
            counts[table] = len(res.data) if res.data else 0
        except Exception as e:
            logging.error(f"delete_all_user_data: error clearing {table}: {e}")
            counts[table] = -1

    # Сбрасываем настройки пользователя
    try:
        supabase.table("users").update({
            "salary_day": None,
            "salary_day_2": None,
            "expense_reminder_hour": 21,
        }).eq("id", user_id).execute()
    except Exception as e:
        logging.error(f"delete_all_user_data: reset user error: {e}")

    return counts


# ─── GEMINI RATE LIMIT (персистентный через Supabase) ────────────────────────

async def get_gemini_last_date(user_id: int) -> str | None:
    """Возвращает дату последнего вызова Gemini (YYYY-MM-DD) или None."""
    res = supabase.table("users").select("gemini_last_analysis_date").eq("id", user_id).execute()
    if res.data:
        return res.data[0].get("gemini_last_analysis_date")
    return None


async def set_gemini_last_date(user_id: int, date_str: str):
    """Записывает дату последнего вызова Gemini."""
    supabase.table("users").update(
        {"gemini_last_analysis_date": date_str}
    ).eq("id", user_id).execute()


# ─── ТЕКУЩИЙ БАЛАНС ──────────────────────────────────────────────────────────

async def get_current_balance(user_id: int) -> float | None:
    """Возвращает текущий баланс пользователя (заявленный при онбординге)."""
    res = supabase.table("users").select("current_balance").eq("id", user_id).execute()
    if res.data:
        val = res.data[0].get("current_balance")
        return float(val) if val is not None else None
    return None


async def set_current_balance(user_id: int, balance: float):
    """Сохраняет текущий баланс пользователя."""
    supabase.table("users").update(
        {"current_balance": balance}
    ).eq("id", user_id).execute()


# ─── БЮДЖЕТЫ — удаление ──────────────────────────────────────────────────────

async def delete_budget(user_id: int, category: str, period: str = "monthly"):
    """Удаляет лимит бюджета по категории."""
    supabase.table("budgets").delete()\
        .eq("user_id", user_id)\
        .eq("category", category)\
        .eq("period", period)\
        .execute()
