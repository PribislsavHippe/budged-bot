"""
weekly_advice.py — Генератор еженедельных советов.

Архитектура:
- budget_analyzer.py (Python): вся математика, профиль, денежные потоки
- Gemini: 1 запрос в день — персональные советы на основе готовых данных
- Groq: для вопросов в чате (быстро, бесплатно)
"""

import logging
import os
from datetime import date, timedelta

import google.generativeai as genai

from budget_analyzer import analyze_month, format_for_ai

_model = None


def _get_gemini():
    global _model
    if _model is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY не задан")
        genai.configure(api_key=api_key)
        _model = genai.GenerativeModel("gemini-2.5-flash-lite")
    return _model


# ─── ГЛАВНАЯ ФУНКЦИЯ ─────────────────────────────────────────────────────────

async def get_weekly_advice(
    transactions: list[dict],
    salary_days: list[int],
    scheduled_payments: list[dict],
    current_balance: float,
    planned_income: list[dict] = None,
    week_num: int = None,
    known_salary_amount: float = 0,
) -> dict:
    """
    Полный цикл: Python-расчёт → Gemini совет.
    Возвращает dict с текстом для бота и полным анализом.
    """
    analysis = analyze_month(
        transactions=transactions,
        salary_days=salary_days,
        scheduled_payments=scheduled_payments,
        current_balance=current_balance,
        planned_income=planned_income or [],
        known_salary_amount=known_salary_amount,
    )

    # Определяем целевой период
    target_week = None
    if week_num is not None:
        for w in analysis["weekly_cashflows"]:
            if w["week_num"] == week_num:
                target_week = w
                break
    if not target_week:
        target_week = analysis.get("current_week")
    if not target_week and analysis["weekly_cashflows"]:
        target_week = analysis["weekly_cashflows"][0]

    # Форматируем для Gemini
    context = format_for_ai(analysis, target_week)

    # Запрос к Gemini
    gemini_advice = await _ask_gemini(context, target_week, analysis["profile"])

    # Краткая сводка для показа в боте (без AI)
    budget_summary = _make_summary(target_week, analysis["profile"])

    return {
        "week_label": target_week["label"] if target_week else "Период",
        "analysis": analysis,
        "gemini_advice": gemini_advice,
        "budget_summary": budget_summary,
    }


async def _ask_gemini(context: str, week: dict, profile: dict) -> str:
    """Один запрос к Gemini с полным контекстом Python-модели."""
    if not week:
        return "Недостаточно данных. Вноси расходы несколько дней — тогда дам точный анализ."

    week_label   = week["label"]
    is_tight     = week.get("is_tight", False)
    discretionary = week.get("discretionary_budget", 0)
    can_afford   = week.get("can_afford", [])
    must_cut     = week.get("must_cut", [])
    has_salary   = week.get("has_salary", False)
    fav_cats     = profile.get("favourite_cats", [])
    top_places   = profile.get("top_places", [])

    # Строим подсказки для Gemini
    hints = []
    if is_tight:
        hints.append(f"Это напряжённый период — дефицит. Нужно объяснить что сократить и почему.")
    if can_afford:
        items = ", ".join([f"{ca['category']} ({ca['amount']:,} руб)" for ca in can_afford])
        hints.append(f"Есть запас — пользователь может позволить: {items}.")
    if fav_cats:
        favs = ", ".join([f["category"] for f in fav_cats[:2]])
        hints.append(f"Пользователь любит тратить на: {favs} — упомяни конкретно.")
    if top_places:
        places = ", ".join([p["place"] for p in top_places[:3]])
        hints.append(f"Любимые места трат: {places}.")
    if has_salary:
        hints.append("Этот период начинается с зарплаты — предупреди об эффекте импульсных трат.")

    hints_text = "\n".join(f"- {h}" for h in hints) if hints else ""

    prompt = f"""{context}

ЗАДАЧА: Дай персональный совет по бюджету на {week_label}.

Контекст для совета:
{hints_text}

Требования:
1. Конкретные суммы для каждой ключевой категории этой недели
2. Если напряжённо — скажи что сократить и на сколько рублей
3. Если есть запас — скажи что можно позволить (опирайся на любимые категории и места)
4. Один лайфхак: оптовая покупка, замена дорогой привычки, что перенести
5. Одна мотивирующая фраза в конце

Стиль: на «ты», конкретно, живо, без занудства. Без эмодзи. 6-10 предложений."""

    try:
        model = _get_gemini()
        response = await model.generate_content_async(prompt)
        return response.text.strip()
    except Exception as e:
        logging.error(f"Gemini error: {e}")
        return _fallback_advice(week)


def _fallback_advice(week: dict) -> str:
    """Резервный совет без Gemini."""
    disc = week.get("discretionary_budget", 0)
    mandatory = week.get("mandatory_sum", 0)
    avg = week.get("total_expected_discretionary", 0)
    is_tight = week.get("is_tight", False)

    if is_tight:
        deficit = avg - disc
        return (
            f"Период напряжённый: свободных {disc:,} руб., "
            f"а обычно требуется {avg:,} руб. Дефицит {deficit:,} руб. "
            f"Обязательных платежей {mandatory:,} руб. "
            f"Рекомендую временно урезать развлечения и незапланированные покупки."
        )
    surplus = disc - avg
    return (
        f"Период нормальный: {disc:,} руб. свободных, "
        f"обычные траты около {avg:,} руб."
        + (f" Запас {surplus:,} руб. — можно отложить или позволить себе что-то приятное." if surplus > 0 else "")
    )


def _make_summary(week: dict, profile: dict) -> dict:
    """Краткая сводка (без AI) для отображения цифр в боте."""
    if not week:
        return {}

    avg_by_cat = profile.get("avg_weekly_by_cat", {})
    disc = week.get("discretionary_budget", 0)
    scale = week.get("scale", 1.0)

    # Бюджет по категориям на период (масштабированный)
    cat_budgets = {}
    total_avg = profile.get("total_avg_weekly", 0)
    if total_avg > 0:
        ratio = disc / (total_avg * scale) if total_avg * scale > 0 else 1
        for cat, avg in avg_by_cat.items():
            adjusted = round(avg * scale * ratio)
            if adjusted > 0:
                cat_budgets[cat] = adjusted

    return {
        "balance_start": week.get("balance_start", 0),
        "income_amount": week.get("income_amount", 0),
        "mandatory_sum": week.get("mandatory_sum", 0),
        "discretionary": disc,
        "cat_budgets": cat_budgets,
        "is_tight": week.get("is_tight", False),
        "has_salary": week.get("has_salary", False),
        "mandatory_payments": week.get("mandatory_payments", []),
        "can_afford": week.get("can_afford", []),
        "must_cut": week.get("must_cut", []),
    }


# ─── ХЕНДЛЕР ДЛЯ БОТА ────────────────────────────────────────────────────────

async def handle_weekly_advice_request(user_id: int) -> str:
    """
    Вызывается из jobs.py (по понедельникам) и по команде /week.
    Собирает данные из БД, возвращает готовый текст.
    """
    from db import (
        get_transactions, get_salary_days, get_scheduled_payments,
        get_stats, get_planned_income
    )

    today = date.today()
    transactions = await get_transactions(user_id, "all")
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
    advice  = result["gemini_advice"]
    label   = result["week_label"]
    analysis = result["analysis"]

    lines = [f"<b>Бюджет на {label}</b>", ""]

    if summary:
        lines.append(f"Баланс: <b>{summary['balance_start']:,} руб.</b>")

        if summary.get("income_amount", 0) > 0:
            lines.append(f"Ожидаемый доход: <b>+{summary['income_amount']:,} руб.</b>")

        if summary.get("mandatory_payments"):
            pmts = ", ".join([
                f"{p['name']} {p['amount']:,} руб."
                for p in summary["mandatory_payments"]
            ])
            lines.append(f"Платежи: {pmts}")

        lines.append(f"Свободных: <b>{summary['discretionary']:,} руб.</b>")

        if summary.get("cat_budgets"):
            lines.append("\n<b>По категориям:</b>")
            for cat, amt in sorted(summary["cat_budgets"].items(),
                                   key=lambda x: x[1], reverse=True)[:6]:
                marker = "⚠️ " if summary.get("is_tight") and cat not in {"Еда", "Транспорт"} else ""
                lines.append(f"  {marker}{cat}: {amt:,} руб.")

        if summary.get("can_afford"):
            lines.append("\nМожно позволить:")
            for ca in summary["can_afford"]:
                lines.append(f"  {ca['category']}: до {ca['amount']:,} руб.")

        if summary.get("must_cut"):
            lines.append("\nРекомендуется сократить:")
            for mc in summary["must_cut"]:
                lines.append(f"  {mc['category']}: -{mc['suggested_cut']:,} руб.")

        lines.append("")

    # Статус данных
    profile = analysis.get("profile", {})
    if not profile.get("has_enough_data"):
        weeks = profile.get("weeks_analyzed", 0)
        lines.append(f"<i>(Истории пока {weeks} нед. — советы станут точнее через 2-3 недели)</i>\n")

    lines.append(f"<b>Совет:</b>\n{advice}")

    return "\n".join(lines)
