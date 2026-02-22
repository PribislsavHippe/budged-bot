"""
weekly_advice.py — Генератор еженедельных советов через Gemini.

Использует budget_analyzer.py для расчётов (без AI),
а Gemini — только для финального персонального совета (1 запрос/день).
"""

import logging
import os
from datetime import date

import google.generativeai as genai

from budget_analyzer import analyze_month, format_for_gemini, get_weeks_of_month

_model = None


def _get_model():
    global _model
    if _model is None:
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        _model = genai.GenerativeModel("gemini-2.5-flash-lite")
    return _model


# ─── ГЛАВНАЯ ФУНКЦИЯ ─────────────────────────────────────────────────────────

async def get_weekly_advice(
    transactions: list[dict],
    salary_days: list[int],
    scheduled_payments: list[dict],
    current_balance: float,
    planned_income: list[dict] = None,
    week_num: int = None,       # Если None — текущая неделя
) -> dict:
    """
    Полный цикл: расчёт → форматирование → Gemini.

    Возвращает:
    {
        "week_label": str,
        "analysis": dict,          # полный результат Python-модели
        "gemini_advice": str,      # текст совета от Gemini
        "budget_summary": dict,    # краткая сводка для показа в боте
    }
    """
    today = date.today()

    # 1. Python-модель считает всё
    analysis = analyze_month(
        transactions=transactions,
        salary_days=salary_days,
        scheduled_payments=scheduled_payments,
        current_balance=current_balance,
        planned_income=planned_income or [],
    )

    # 2. Определяем целевую неделю
    target_week = None
    if week_num is not None:
        for w in analysis["weekly_cashflows"]:
            if w["week_num"] == week_num:
                target_week = w
                break
    else:
        target_week = analysis.get("current_week")

    if not target_week:
        # Берём первую будущую неделю
        weeks = get_weeks_of_month(today.year, today.month)
        if weeks:
            target_week = analysis["weekly_cashflows"][0]

    # 3. Форматируем контекст для Gemini
    context = format_for_gemini(analysis, target_week)

    # 4. Запрос к Gemini
    gemini_advice = await _ask_gemini(context, target_week)

    # 5. Краткая сводка для отображения в боте (без Gemini)
    budget_summary = _make_budget_summary(target_week, analysis["profile"])

    return {
        "week_label": target_week["label"] if target_week else "Неделя",
        "analysis": analysis,
        "gemini_advice": gemini_advice,
        "budget_summary": budget_summary,
    }


async def _ask_gemini(context: str, week: dict) -> str:
    """Один запрос к Gemini с полным контекстом."""
    week_label = week["label"] if week else "текущая неделя"
    is_tight = week.get("is_tight", False) if week else False
    discretionary = week.get("discretionary_budget", 0) if week else 0
    profile_context = ""
    if week:
        avg = week.get("avg_weekly_spend", 0)
        if avg > 0 and discretionary < avg * 0.8:
            profile_context = (
                f"Свободных денег {discretionary:,} руб., "
                f"а обычно тратит {avg:,} руб/нед — нужно сократить расходы."
            )

    prompt = f"""{context}

ЗАДАЧА: Дай персональный совет по бюджету на {week_label}.

Требования к совету:
1. Конкретные суммы: сколько можно потратить на каждую приоритетную категорию этой недели
2. Если неделя напряжённая — скажи что придётся сократить и на сколько
3. Если есть запас — скажи что можно позволить себе дополнительно (кофейня, кино и т.д. — опирайся на профиль пользователя)
4. Один практический лайфхак для этой конкретной недели (оптовая покупка, замена дорогой привычки и т.д.)
5. Заверши одной фразой-мотивацией

Пиши от первого лица обращения к пользователю на "ты". Конкретно. Без лишних слов. Без эмодзи. 6-10 предложений."""

    try:
        model = _get_model()
        response = await model.generate_content_async(prompt)
        return response.text.strip()
    except Exception as e:
        logging.error(f"Gemini weekly advice error: {e}")
        return _fallback_advice(week)


def _fallback_advice(week: dict) -> str:
    """Совет без Gemini если API недоступен."""
    if not week:
        return "Данных пока недостаточно. Вноси расходы каждый день — через неделю дам точный анализ."

    disc = week.get("discretionary_budget", 0)
    mandatory = week.get("mandatory_sum", 0)
    is_tight = week.get("is_tight", False)
    avg = week.get("avg_weekly_spend", 0)

    if is_tight:
        return (
            f"Эта неделя напряжённая: свободных {disc:,} руб. при обычных тратах {avg:,} руб. "
            f"Обязательных платежей {mandatory:,} руб. "
            f"Рекомендую временно урезать развлечения и незапланированные покупки."
        )
    surplus = disc - avg
    return (
        f"Эта неделя нормальная: {disc:,} руб. свободных. "
        f"Обычные траты составляют около {avg:,} руб. "
        + (f"Запас {surplus:,} руб. — можно отложить или позволить что-то приятное." if surplus > 0 else "")
    )


def _make_budget_summary(week: dict, profile: dict) -> dict:
    """Краткая сводка без Gemini — для быстрого показа в боте."""
    if not week:
        return {}

    avg_by_cat = profile.get("avg_weekly_by_cat", {})
    disc = week.get("discretionary_budget", 0)
    avg_total = week.get("avg_weekly_spend", 0)

    # Распределяем дискреционный бюджет пропорционально истории
    cat_budgets = {}
    if avg_total > 0 and avg_by_cat:
        ratio = disc / avg_total if avg_total > 0 else 1
        for cat, avg in avg_by_cat.items():
            adjusted = round(avg * ratio)
            if adjusted > 0:
                cat_budgets[cat] = adjusted

    return {
        "balance_start": week.get("balance_start", 0),
        "mandatory_sum": week.get("mandatory_sum", 0),
        "discretionary": disc,
        "cat_budgets": cat_budgets,
        "is_tight": week.get("is_tight", False),
        "has_salary": week.get("has_salary", False),
        "mandatory_payments": week.get("mandatory_payments", []),
    }


# ─── ХЕНДЛЕР ДЛЯ БОТА ────────────────────────────────────────────────────────

async def handle_weekly_advice_request(user_id: int) -> str:
    """
    Готовая функция для вызова из jobs.py или по команде.
    Собирает данные из БД и возвращает готовый текст для отправки.
    """
    from db import (
        get_transactions, get_salary_days, get_scheduled_payments,
        get_stats, get_planned_income
    )
    from datetime import timedelta

    today = date.today()

    transactions = await get_transactions(user_id, "all")  # вся история
    salary_days = await get_salary_days(user_id)
    scheduled_payments = await get_scheduled_payments(user_id)
    stats = await get_stats(user_id, "month")
    current_balance = stats.get("balance", 0)

    planned = []
    try:
        planned = await get_planned_income(
            user_id,
            from_date=today.isoformat(),
            to_date=(today + timedelta(days=60)).isoformat()
        )
    except Exception:
        pass

    result = await get_weekly_advice(
        transactions=transactions,
        salary_days=salary_days,
        scheduled_payments=scheduled_payments,
        current_balance=current_balance,
        planned_income=planned,
    )

    summary = result["budget_summary"]
    advice = result["gemini_advice"]
    week_label = result["week_label"]

    # Форматируем сообщение для бота
    lines = [f"<b>Бюджет на {week_label}</b>", ""]

    if summary:
        lines.append(f"Баланс: {summary['balance_start']:,} руб.")
        if summary.get("mandatory_payments"):
            pmts = ", ".join([f"{p['name']} {p['amount']:,.0f} руб."
                              for p in summary["mandatory_payments"]])
            lines.append(f"Платежи: {pmts}")
        lines.append(f"Свободных: {summary['discretionary']:,} руб.")

        if summary.get("cat_budgets"):
            lines.append("\n<b>По категориям на неделю:</b>")
            for cat, amt in sorted(summary["cat_budgets"].items(),
                                   key=lambda x: x[1], reverse=True)[:6]:
                lines.append(f"  {cat}: {amt:,} руб.")

        lines.append("")

    lines.append(f"<b>Совет ИИ:</b>\n{advice}")

    return "\n".join(lines)
