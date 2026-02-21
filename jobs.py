from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta, timezone
import pytz
import logging

from db import (
    get_all_active_users, get_payments_due_soon,
    get_transactions, get_stats, get_budgets, get_salary_days
)
from google_calendar import create_income_reminder
from ai_service import generate_weekly_ai_report

MOTIVATION_MESSAGES = [
    "💪 Фиксируй расходы сегодня — завтра скажешь себе спасибо!",
    "📊 Контроль над деньгами = контроль над жизнью. Как прошёл день?",
    "💰 Те, кто считает деньги, имеют больше денег. Внеси сегодняшние расходы!",
    "📈 Финансовая дисциплина формируется каждый день.",
]


async def check_expense_reminders(bot):
    """Ежедневное напоминание внести расходы."""
    import random
    users = await get_all_active_users()
    now = datetime.now()

    for user in users:
        reminder_hour = user.get("expense_reminder_hour", 21)
        if now.hour != reminder_hour:
            continue

        user_id = user["id"]
        transactions = await get_transactions(user_id, "week")
        today_transactions = [
            t for t in transactions
            if t["created_at"][:10] == now.strftime("%Y-%m-%d")
        ]

        try:
            if not today_transactions:
                msg = random.choice(MOTIVATION_MESSAGES)
                await bot.send_message(user_id, msg + "\n\n⚠️ Ты ещё не вносил расходы сегодня!")
            else:
                await bot.send_message(
                    user_id,
                    f"✅ Сегодня внесено записей: {len(today_transactions)}. Молодец!"
                )
        except Exception:
            pass


async def check_payment_reminders(bot):
    """Напоминания об обязательных платежах."""
    payments = await get_payments_due_soon(days_ahead=3)

    for payment in payments:
        user = payment.get("users", {})
        if not user:
            continue

        user_id = payment["user_id"]
        now = datetime.now()
        days_left = payment["day_of_month"] - now.day

        if days_left < 0:
            continue

        remind_days = payment.get("remind_days_before", 2)
        if days_left > remind_days:
            continue

        if days_left == 0:
            urgency = "🚨 <b>СЕГОДНЯ</b> нужно оплатить:"
        elif days_left == 1:
            urgency = "⚠️ <b>ЗАВТРА</b> нужно оплатить:"
        else:
            urgency = f"🔔 Через <b>{days_left} дня</b> нужно оплатить:"

        from keyboards import payment_actions_kb
        try:
            await bot.send_message(
                user_id,
                f"{urgency}\n\n"
                f"💳 <b>{payment['name']}</b>\n"
                f"💰 Сумма: {payment['amount']:,.0f} ₽\n"
                f"📅 {payment['day_of_month']}-е число",
                parse_mode="HTML",
                reply_markup=payment_actions_kb(payment["id"])
            )
        except Exception:
            pass


async def check_salary_day_reminders(bot):
    """Напоминание внести доход во все дни зарплаты."""
    users = await get_all_active_users()
    now = datetime.now()

    for user in users:
        user_id = user["id"]
        salary_days = await get_salary_days(user_id)

        if not salary_days or now.day not in salary_days:
            continue

        transactions = await get_transactions(user_id, "week")
        today_income = [
            t for t in transactions
            if t["type"] == "income" and t["created_at"][:10] == now.strftime("%Y-%m-%d")
        ]

        if today_income:
            continue

        try:
            await bot.send_message(
                user_id,
                f"🎉 <b>Сегодня день выплаты!</b>\n\n"
                f"Не забудь зафиксировать доход — просто напиши мне, например:\n"
                f"<i>«получил зарплату 45000»</i>",
                parse_mode="HTML"
            )
        except Exception:
            pass


async def check_budget_alerts(bot):
    """Предупреждения о превышении бюджета."""
    users = await get_all_active_users()

    for user in users:
        user_id = user["id"]
        budgets = await get_budgets(user_id)
        if not budgets:
            continue

        stats = await get_stats(user_id, "month")

        for budget in budgets:
            cat = budget["category"]
            spent = stats["by_category"].get(cat, 0)
            limit = budget["limit_amount"]
            pct = spent / limit * 100 if limit > 0 else 0

            try:
                if 80 <= pct < 85:
                    await bot.send_message(
                        user_id,
                        f"⚠️ <b>{cat}</b>: использовано {pct:.0f}% бюджета\n"
                        f"({spent:,.0f} из {limit:,.0f} ₽)",
                        parse_mode="HTML"
                    )
                elif pct >= 100:
                    await bot.send_message(
                        user_id,
                        f"🔴 <b>Лимит превышен!</b> {cat}\n"
                        f"Потрачено {spent:,.0f} ₽ при лимите {limit:,.0f} ₽",
                        parse_mode="HTML"
                    )
            except Exception:
                pass


async def send_weekly_report(bot):
    """Еженедельный отчёт по воскресеньям с AI-анализом."""
    users = await get_all_active_users()

    for user in users:
        user_id = user["id"]
        stats = await get_stats(user_id, "week")

        if stats["transactions_count"] == 0:
            continue

        balance_emoji = "🟢" if stats["balance"] >= 0 else "🔴"
        ai_insight = await generate_weekly_ai_report(stats)

        base = (
            f"📊 <b>Итоги недели</b>\n\n"
            f"💰 Доходы: {stats['income']:,.0f} ₽\n"
            f"💸 Расходы: {stats['expenses']:,.0f} ₽\n"
            f"{balance_emoji} Баланс: {stats['balance']:,.0f} ₽\n"
            f"Записей: {stats['transactions_count']}"
        )

        full = base + (f"\n\n🧠 <b>AI-анализ:</b>\n{ai_insight}" if ai_insight
                       else ("\n\n💪 Отличная дисциплина!" if stats["balance"] >= 0
                             else "\n\n⚠️ Расходы превысили доходы. В следующую неделю лучше!"))
        try:
            await bot.send_message(user_id, full, parse_mode="HTML")
        except Exception:
            pass


async def send_monthly_report(bot):
    """Месячный отчёт 1-го числа."""
    users = await get_all_active_users()

    for user in users:
        user_id = user["id"]
        stats = await get_stats(user_id, "month")

        if stats["transactions_count"] == 0:
            continue

        top = list(stats["by_category"].items())[:3]
        top_text = "\n".join([f"  {cat}: {amt:,.0f} ₽" for cat, amt in top])
        balance_emoji = "🟢" if stats["balance"] >= 0 else "🔴"

        try:
            await bot.send_message(
                user_id,
                f"📈 <b>Итоги месяца</b>\n\n"
                f"💰 Доходы: <b>{stats['income']:,.0f} ₽</b>\n"
                f"💸 Расходы: <b>{stats['expenses']:,.0f} ₽</b>\n"
                f"{balance_emoji} Итог: <b>{stats['balance']:,.0f} ₽</b>\n\n"
                f"🏆 <b>Топ расходов:</b>\n{top_text}\n\n"
                f"Новый месяц — новые возможности! 🎯",
                parse_mode="HTML"
            )
        except Exception:
            pass


async def send_smart_budget_advice(bot):
    """
    Умные советы по бюджету — каждый понедельник.
    AI считает: сколько дней до зарплаты, какой баланс,
    и даёт конкретный план по категориям.
    """
    users = await get_all_active_users()
    now = datetime.now()

    for user in users:
        user_id = user["id"]
        stats = await get_stats(user_id, "month")

        # Находим ближайший день зарплаты
        salary_days = await get_salary_days(user_id)
        if not salary_days:
            continue

        today = now.day
        next_salary_day = None
        for d in sorted(salary_days):
            if d > today:
                next_salary_day = d
                break
        if next_salary_day is None:
            next_salary_day = sorted(salary_days)[0]  # следующий месяц

        days_until_salary = (next_salary_day - today) if next_salary_day > today else (30 - today + next_salary_day)

        # Считаем обязательные платежи до зарплаты
        from db import get_scheduled_payments
        payments = await get_scheduled_payments(user_id)
        mandatory_before_salary = sum(
            p["amount"] for p in payments
            if today < p["day_of_month"] <= next_salary_day
        )

        try:
            from ai_service import get_smart_budget_advice
            advice = await get_smart_budget_advice(
                stats=stats,
                days_until_salary=days_until_salary,
                mandatory_expenses=mandatory_before_salary,
                salary_day=next_salary_day
            )
            if advice:
                await bot.send_message(
                    user_id,
                    f"💡 <b>Умный совет на неделю</b>\n\n{advice}",
                    parse_mode="HTML"
                )
        except Exception as e:
            logging.error(f"smart advice error for {user_id}: {e}")


def setup_scheduler(bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=pytz.timezone("Europe/Moscow"))

    # Напоминание вносить расходы — каждый час
    scheduler.add_job(check_expense_reminders, "interval", hours=1, args=[bot])

    # Платежи — каждый день в 9:00
    scheduler.add_job(check_payment_reminders, CronTrigger(hour=9, minute=0), args=[bot])

    # День зарплаты — каждый день в 10:00
    scheduler.add_job(check_salary_day_reminders, CronTrigger(hour=10, minute=0), args=[bot])

    # Бюджеты — каждый день в 12:00
    scheduler.add_job(check_budget_alerts, CronTrigger(hour=12, minute=0), args=[bot])

    # Еженедельный отчёт — воскресенье 18:00
    scheduler.add_job(send_weekly_report, CronTrigger(day_of_week="sun", hour=18), args=[bot])

    # Месячный отчёт — 1-е число 10:00
    scheduler.add_job(send_monthly_report, CronTrigger(day=1, hour=10), args=[bot])

    # Умные советы — каждый понедельник 9:00
    scheduler.add_job(send_smart_budget_advice, CronTrigger(day_of_week="mon", hour=9), args=[bot])

    return scheduler
