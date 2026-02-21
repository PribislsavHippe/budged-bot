from supabase import create_client, Client
import os
from dotenv import load_dotenv

load_dotenv()

supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)


# ─── USERS ──────────────────────────────────────────────

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


# ─── TRANSACTIONS ────────────────────────────────────────

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


async def get_transactions(user_id: int, period: str = "month"):
    """period: 'week', 'month', 'all'"""
    query = supabase.table("transactions").select("*").eq("user_id", user_id)

    if period == "week":
        query = query.gte("created_at", "now() - interval '7 days'")
    elif period == "month":
        query = query.gte("created_at", "now() - interval '30 days'")

    res = query.order("created_at", desc=True).execute()
    return res.data


async def get_stats(user_id: int, period: str = "month") -> dict:
    """Возвращает сводку доходов/расходов по категориям."""
    transactions = await get_transactions(user_id, period)

    income = sum(t["amount"] for t in transactions if t["type"] == "income")
    expenses = sum(t["amount"] for t in transactions if t["type"] == "expense")

    by_category = {}
    by_income_category = {}
    for t in transactions:
        cat = t["category"]
        if t["type"] == "expense":
            by_category[cat] = by_category.get(cat, 0) + t["amount"]
        else:
            by_income_category[cat] = by_income_category.get(cat, 0) + t["amount"]

    return {
        "income": income,
        "expenses": expenses,
        "balance": income - expenses,
        "by_category": dict(sorted(by_category.items(), key=lambda x: x[1], reverse=True)),
        "by_income_category": dict(sorted(by_income_category.items(), key=lambda x: x[1], reverse=True)),
        "transactions_count": len(transactions)
    }


# ─── SCHEDULED PAYMENTS ──────────────────────────────────

async def add_scheduled_payment(user_id: int, name: str, amount: float, day: int, category: str = "Обязательные", remind_days: int = 2):
    data = {
        "user_id": user_id,
        "name": name,
        "amount": amount,
        "day_of_month": day,
        "category": category,
        "remind_days_before": remind_days
    }
    res = supabase.table("scheduled_payments").insert(data).execute()
    return res.data[0]


async def get_scheduled_payments(user_id: int):
    res = supabase.table("scheduled_payments").select("*")\
        .eq("user_id", user_id).eq("is_active", True).execute()
    return res.data


async def delete_scheduled_payment(payment_id: int, user_id: int):
    supabase.table("scheduled_payments").update({"is_active": False})\
        .eq("id", payment_id).eq("user_id", user_id).execute()


async def get_payments_due_soon(days_ahead: int = 3):
    """Найти платежи, которые наступят через days_ahead дней"""
    from datetime import datetime
    today = datetime.now().day
    target_days = [(today + i - 1) % 31 + 1 for i in range(days_ahead + 1)]

    res = supabase.table("scheduled_payments").select("*, users(*)")\
        .in_("day_of_month", target_days).eq("is_active", True).execute()
    return res.data


# ─── BUDGETS ─────────────────────────────────────────────

async def set_budget(user_id: int, category: str, limit_amount: float, period: str = "monthly"):
    data = {
        "user_id": user_id,
        "category": category,
        "limit_amount": limit_amount,
        "period": period
    }
    supabase.table("budgets").upsert(data, on_conflict="user_id,category,period").execute()


async def get_budgets(user_id: int):
    res = supabase.table("budgets").select("*").eq("user_id", user_id).execute()
    return res.data


# ─── GOOGLE TOKENS ───────────────────────────────────────

async def save_google_token(user_id: int, access_token: str, refresh_token: str, expiry, calendar_id: str = "primary"):
    data = {
        "user_id": user_id,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_expiry": expiry.isoformat() if expiry else None,
        "calendar_id": calendar_id
    }
    supabase.table("google_tokens").upsert(data, on_conflict="user_id").execute()


async def get_google_token(user_id: int):
    res = supabase.table("google_tokens").select("*").eq("user_id", user_id).execute()
    return res.data[0] if res.data else None


# ─── SALARY DAYS ─────────────────────────────────────────

async def get_salary_days(user_id: int) -> list[int]:
    """Возвращает список дней зарплаты пользователя."""
    user = await get_user(user_id)
    if not user:
        return []
    
    # Сначала проверяем новое поле salary_days
    days_str = user.get("salary_days", "")
    if days_str:
        try:
            return [int(d.strip()) for d in days_str.split(",") if d.strip().isdigit()]
        except Exception:
            pass
    
    # Fallback на старое поле salary_day
    day = user.get("salary_day")
    return [day] if day else []


async def set_salary_days(user_id: int, days: list[int]):
    """Сохраняет дни зарплаты."""
    days_str = ",".join(str(d) for d in sorted(days))
    await update_user(user_id, {"salary_days": days_str, "salary_day": days[0] if days else None})
