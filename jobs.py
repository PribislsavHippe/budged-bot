from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
import pytz

from db import (
    get_all_active_users, get_payments_due_soon,
    get_transactions, get_stats, get_budgets
)
from google_calendar import create_income_reminder

# Мотивационные сообщения
MOTIVATION_MESSAGES = [
    "💪 Каждая рубль на счету — фиксируй расходы сегодня!",
    "📊 Контроль над деньгами = контроль над жизнью. Как прошёл день?",
    "🎯 Маленький шаг — записать расход. Большой результат — финансовая свобода!",
    "💰 Те, кто считает деньги, имеют больше денег. Не забудь внести сегодняшние расходы!",
    "🔥 Продолжай в том же духе! Финансовая дисциплина формируется каждый день.",
    "📈 Знаешь куда уходят деньги? Запиши расходы за сегодня!",
]


async def check_expense_reminders(bot):
    """Ежедневное напоминание внести расходы"""
    import random
    users = await get_all_active_users()
    now = datetime.now()

    for user in users:
        reminder_hour = user.get("expense_reminder_hour", 21)
        if now.hour != reminder_hour:
            continue

        user_id = user["id"]
        # Проверяем, были ли расходы сегодня
        transactions = await get_transactions(user_id, "week")
        today_transactions = [
            t for t in transactions
            if t["created_at"][:10] == now.strftime("%Y-%m-%d")
        ]

        if not today_transactions:
            msg = random.choice(MOTIVATION_MESSAGES)
            streak_msg = "\n\n⚠️ Ты ещё не вносил расходы сегодня — самое время!" if not today_transactions else ""
            try:
                await bot.send_message(user_id, msg + streak_msg)
            except Exception:
                pass
        else:
            # Пользователь молодец — краткая мотивация
            msg = f"✅ Сегодня ты уже внёс {len(today_transactions)} запис(ей). Молодец!"
            try:
                await bot.send_message(user_id, msg)
            except Exception:
                pass


async def check_payment_reminders(bot):
    """Напоминания об обязательных платежах"""
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
        if days_left <= remind_days:
            if days_left == 0:
                urgency = "🚨 <b>СЕГОДНЯ</b> нужно оплатить:"
            elif days_left == 1:
                urgency = "⚠️ <b>ЗАВТРА</b> нужно оплатить:"
            else:
                urgency = f"🔔 Через <b>{days_left} дня</b> нужно оплатить:"

            from utils.keyboards import payment_actions_kb
            try:
                await bot.send_message(
                    user_id,
                    f"{urgency}\n\n"
                    f"💳 <b>{payment['name']}</b>\n"
                    f"💰 Сумма: {payment['amount']:,.2f} ₽\n"
                    f"📅 {payment['day_of_month']}-е число\n\n"
                    f"Нажми 'Оплачено' когда внесёшь платёж",
                    parse_mode="HTML",
                    reply_markup=payment_actions_kb(payment["id"])
                )
            except Exception:
                pass


async def check_salary_day_reminders(bot):
    """Напоминание внести доход в день зарплаты"""
    users = await get_all_active_users()
    now = datetime.now()

    for user in users:
        user_id = user["id"]
        salary_day = user.get("salary_day")

        if not salary_day or now.day != salary_day:
            continue

        # Проверяем, вносил ли уже доход сегодня
        transactions = await get_transactions(user_id, "week")
        today_income = [
            t for t in transactions
            if t["type"] == "income" and t["created_at"][:10] == now.strftime("%Y-%m-%d")
        ]

        if not today_income:
            try:
                await bot.send_message(
                    user_id,
                    f"🎉 <b>Сегодня день зарплаты!</b>\n\n"
                    f"Не забудь зафиксировать доход в боте.\n"
                    f"Нажми 💰 Добавить доход в главном меню.",
                    parse_mode="HTML"
                )
                # Создаём событие в Google Calendar на следующий месяц
                next_month = now.replace(month=now.month % 12 + 1)
                await create_income_reminder(user_id, next_month.replace(day=salary_day))
            except Exception:
                pass


async def check_budget_alerts(bot):
    """Предупреждения о превышении бюджета"""
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

            # Предупреждаем при 80% и 100%
            if 80 <= pct < 85:
                try:
                    await bot.send_message(
                        user_id,
                        f"⚠️ <b>Внимание!</b> По категории <b>{cat}</b>\n"
                        f"израсходовано {pct:.0f}% от лимита\n"
                        f"({spent:,.0f} из {limit:,.0f} ₽)\n\n"
                        f"Будь осторожен с тратами в этой категории!",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
            elif pct >= 100:
                try:
                    await bot.send_message(
                        user_id,
                        f"🔴 <b>Лимит превышен!</b> Категория <b>{cat}</b>\n"
                        f"Потрачено {spent:,.0f} ₽ при лимите {limit:,.0f} ₽\n"
                        f"Превышение: <b>{spent - limit:,.0f} ₽</b>",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass


async def send_weekly_report(bot):
    """Еженедельный отчёт (по воскресеньям)"""
    users = await get_all_active_users()

    for user in users:
        user_id = user["id"]
        stats = await get_stats(user_id, "week")

        if stats["transactions_count"] == 0:
            continue

        balance_emoji = "🟢" if stats["balance"] >= 0 else "🔴"

        try:
            await bot.send_message(
                user_id,
                f"📊 <b>Итоги недели</b>\n\n"
                f"💰 Доходы: {stats['income']:,.2f} ₽\n"
                f"💸 Расходы: {stats['expenses']:,.2f} ₽\n"
                f"{balance_emoji} Баланс: {stats['balance']:,.2f} ₽\n\n"
                f"Записей за неделю: {stats['transactions_count']}\n\n"
                f"{'💪 Отличная финансовая дисциплина!' if stats['balance'] >= 0 else '⚠️ В этот раз расходы превысили доходы. В следующую неделю будет лучше!'}",
                parse_mode="HTML"
            )
        except Exception:
            pass


async def send_monthly_report(bot):
    """Месячный отчёт (1-го числа)"""
    users = await get_all_active_users()

    for user in users:
        user_id = user["id"]
        stats = await get_stats(user_id, "month")

        if stats["transactions_count"] == 0:
            continue

        top_categories = list(stats["by_category"].items())[:3]
        top_text = "\n".join([f"  {cat}: {amt:,.0f} ₽" for cat, amt in top_categories])
        balance_emoji = "🟢" if stats["balance"] >= 0 else "🔴"

        try:
            await bot.send_message(
                user_id,
                f"📈 <b>Итоги прошлого месяца</b>\n\n"
                f"💰 Доходы: <b>{stats['income']:,.2f} ₽</b>\n"
                f"💸 Расходы: <b>{stats['expenses']:,.2f} ₽</b>\n"
                f"{balance_emoji} Итог: <b>{stats['balance']:,.2f} ₽</b>\n\n"
                f"🏆 <b>Топ расходов:</b>\n{top_text}\n\n"
                f"Новый месяц — новые возможности! Поставь финансовые цели 🎯",
                parse_mode="HTML"
            )
        except Exception:
            pass


def setup_scheduler(bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=pytz.timezone("Europe/Moscow"))

    # Напоминание вносить расходы — каждый час проверяем у кого настроен этот час
    scheduler.add_job(check_expense_reminders, "interval", hours=1, args=[bot])

    # Проверка платежей — каждый день в 9:00
    scheduler.add_job(check_payment_reminders, CronTrigger(hour=9, minute=0), args=[bot])

    # Проверка дня зарплаты — каждый день в 10:00
    scheduler.add_job(check_salary_day_reminders, CronTrigger(hour=10, minute=0), args=[bot])

    # Проверка бюджетов — каждый день в 12:00
    scheduler.add_job(check_budget_alerts, CronTrigger(hour=12, minute=0), args=[bot])

    # Еженедельный отчёт — воскресенье в 18:00
    scheduler.add_job(send_weekly_report, CronTrigger(day_of_week="sun", hour=18, minute=0), args=[bot])

    # Месячный отчёт — 1-е число в 10:00
    scheduler.add_job(send_monthly_report, CronTrigger(day=1, hour=10, minute=0), args=[bot])

    return scheduler
