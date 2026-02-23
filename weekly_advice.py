"""
weekly_advice.py — Финансовый анализ через Gemini 2.5 Flash.

Разделение ответственности:
- Gemini 2.5 Flash: глубокий анализ денежных потоков (раз в сутки на пользователя)
- Groq: только советы в чате (быстро, бесплатно, без ограничений)

Gemini делает:
- Расчёт денежных потоков на неделю и на месяц
- Подробный отчёт с цифрами
- Прогноз по каждому периоду

Groq делает:
- Ответы в чате
- Короткие ситуативные советы
- НЕ делает аналитику денежных потоков
"""

import logging
import os
from datetime import date, timedelta, datetime, timezone

import google.generativeai as genai

from budget_analyzer import analyze_month, format_for_ai

_model = None

# Лимит: один запрос к Gemini в сутки на пользователя
# Хранится в памяти процесса. При рестарте сервера — сбрасывается.
# Для продакшна можно перенести в Supabase (поле gemini_last_analysis_date в users).
# Лимит хранится в Supabase (поле gemini_last_analysis_date в users).
# При рестарте Render счётчик не сбрасывается.


def _get_gemini():
    global _model
    if _model is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY не задан в окружении")
        genai.configure(api_key=api_key)
        _model = genai.GenerativeModel("gemini-2.5-flash")
    return _model


async def can_use_gemini_today(user_id: int) -> bool:
    """Проверяет: использовал ли пользователь Gemini сегодня. Данные из Supabase."""
    try:
        from db import get_gemini_last_date
        last = await get_gemini_last_date(user_id)
        return last != date.today().isoformat()
    except Exception:
        return True  # если БД недоступна — разрешаем


async def mark_gemini_used(user_id: int):
    """Отмечает, что Gemini использован сегодня. Сохраняет в Supabase."""
    try:
        from db import set_gemini_last_date
        await set_gemini_last_date(user_id, date.today().isoformat())
    except Exception as e:
        logging.error(f"mark_gemini_used error: {e}")


def _strip_markdown(text: str) -> str:
    """Убирает markdown-разметку из текста."""
    import re
    text = re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'`{1,3}(.+?)`{1,3}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'^[-_*]{3,}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ─── ГЛАВНАЯ ФУНКЦИЯ АНАЛИЗА ─────────────────────────────────────────────────

async def get_weekly_advice(
    transactions: list[dict],
    salary_days: list[int],
    scheduled_payments: list[dict],
    current_balance: float,
    planned_income: list[dict] = None,
    week_num: int = None,
    known_salary_amount: float = 0,
    user_id: int = None,
) -> dict:
    """
    Полный цикл: Python-расчёт → Gemini-анализ.
    Если Gemini уже использовался сегодня — возвращает fallback без AI.
    """
    analysis = analyze_month(
        transactions=transactions,
        salary_days=salary_days,
        scheduled_payments=scheduled_payments,
        current_balance=current_balance,
        planned_income=planned_income or [],
        known_salary_amount=known_salary_amount,
    )

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

    context = format_for_ai(analysis, target_week)

    # Проверяем лимит Gemini
    if user_id and not await can_use_gemini_today(user_id):
        ai_report = None
        used_ai = False
    else:
        ai_report = await _ask_gemini_full_report(context, analysis, user_id)
        used_ai = True

    budget_summary = _make_summary(target_week, analysis["profile"])

    return {
        "week_label": target_week["label"] if target_week else "Период",
        "analysis": analysis,
        "ai_report": ai_report,
        "gemini_advice": ai_report,  # ключ для совместимости
        "budget_summary": budget_summary,
        "used_ai": used_ai,
    }


async def _ask_gemini_full_report(context: str, analysis: dict, user_id: int = None) -> str:
    """
    Подробный финансовый отчёт через Gemini 2.5 Flash.
    Анализ денежных потоков на неделю и на месяц.
    Без markdown-разметки.
    """
    profile = analysis.get("profile", {})
    weekly = analysis.get("weekly_cashflows", [])

    # Строим данные по каждому периоду
    periods_text = ""
    for w in weekly:
        status = "ДЕФИЦИТ" if w["is_tight"] else ("ЗАРПЛАТА" if w["has_salary"] else "норма")
        periods_text += (
            f"\n{w['label']} [{status}]:\n"
            f"  Баланс нач: {w['balance_start']:,} руб.\n"
            f"  Доход: +{w['income_amount']:,} руб.\n"
            f"  Обяз. платежи: -{w['mandatory_sum']:,} руб.\n"
            f"  Свободный бюджет: {w['discretionary_budget']:,} руб.\n"
            f"  Прогноз баланс конец: {w['balance_end_projected']:,} руб.\n"
        )
        if w.get("mandatory_payments"):
            pmts = ", ".join([f"{p['name']} {p['amount']:,} руб." for p in w["mandatory_payments"]])
            periods_text += f"  Платежи: {pmts}\n"
        if w.get("can_afford"):
            ca = ", ".join([f"{c['category']} до {c['amount']:,} руб." for c in w["can_afford"]])
            periods_text += f"  Можно позволить: {ca}\n"
        if w.get("must_cut"):
            mc = ", ".join([f"{c['category']} сократить на {c['suggested_cut']:,} руб." for c in w["must_cut"]])
            periods_text += f"  Сократить: {mc}\n"

    prompt = f"""Сегодня {date.today().strftime('%d %B %Y')} ({date.today().strftime('%A')}).

{context}

ЗАДАНИЕ: Сделай подробный финансовый отчёт по следующим разделам.

ВАЖНО: Пиши ТОЛЬКО обычным текстом. Никаких символов *, **, #, -, _, ```. Структурируй через заголовки заглавными буквами и отступы, как показано в формате ниже. Обращайся на "ты".

ФОРМАТ ОТЧЁТА:

ФИНАНСОВЫЙ ОТЧЁТ

ТЕКУЩЕЕ ПОЛОЖЕНИЕ
(2-3 предложения: баланс, общая картина, честная оценка ситуации)

АНАЛИЗ ПО НЕДЕЛЯМ
(для каждого периода: название, ключевые цифры, риски или возможности, конкретная рекомендация)

ПРОГНОЗ НА МЕСЯЦ
(Итоговый баланс к концу месяца. Сколько останется. Хватит ли. Какие периоды критические.)

ГДЕ МОЖНО СЭКОНОМИТЬ
(Конкретные категории с суммами. Что сократить и на сколько рублей.)

ГЛАВНЫЙ СОВЕТ
(Одно конкретное действие которое нужно сделать прямо сейчас. С суммой и категорией.)

Стиль: конкретно, с цифрами в рублях, без воды, без занудства, на "ты"."""

    try:
        model = _get_gemini()
        response = await model.generate_content_async(prompt)
        raw = response.text.strip()
        if user_id:
            await mark_gemini_used(user_id)
        return _strip_markdown(raw)
    except Exception as e:
        logging.error(f"Gemini analysis error: {e}")
        return None


# ─── ФОРМИРОВАНИЕ БЮДЖЕТОВ С ПРИОРИТИЗАЦИЕЙ ──────────────────────────────────
#
# Логика основана на реальных тратах россиян (Росстат + банковские данные 2024):
# Приоритеты:
#   1. Жильё — уже в обязательных платежах, пропускаем
#   2. Еда (продукты) — базовая потребность, минимум 15 000 р.
#   3. Транспорт — минимум 3 000 р.
#   4. Кредиты — уже в платежах, пропускаем
#   5. Подписки — телефон, интернет, стриминг, минимум 1 000 р.
#   6. Здоровье — минимум 2 000 р.
#   --- всё ниже только если осталось ---
#   7. Кафе и рестораны
#   8. Развлечения
#   9. Одежда
#  10. Образование
#  11. Прочее

# Минимальные жизненные нормы (рублей в месяц)
BUDGET_MINIMUMS = {
    "Еда":               15_000,
    "Транспорт":          3_000,
    "Подписки":           1_000,
    "Здоровье":           2_000,
    "Кафе и рестораны":   1_500,
    "Хобби":              1_000,
    "Одежда":             1_500,
    "Образование":            0,
    "Прочее":             1_000,
}

# Целевые доли от свободных денег (после обязательных платежей)
# Выстроены по приоритету — сначала насыщаем важные категории
BUDGET_PRIORITY = [
    ("Еда",               0.28),   # 28% — главное
    ("Транспорт",         0.10),   # 10%
    ("Подписки",          0.03),   # 3% — телефон, интернет, стриминг
    ("Здоровье",          0.07),   # 7%
    ("Кафе и рестораны",  0.10),   # 10%
    ("Хобби",             0.09),   # 9%
    ("Одежда",            0.08),   # 8%
    ("Образование",       0.04),   # 4%
    ("Прочее",            0.06),   # 6%
]
# Итого: 84% → остаток 16% идёт в запас/накопления


def calculate_priority_budgets(
    monthly_income: float,
    scheduled_payments: list[dict],
    user_description: str = "",
) -> dict[str, float]:
    """
    Чистая Python-математика без AI.
    Распределяет свободные деньги по приоритету:
    - Сначала насыщаем важные категории до минимума
    - Потом распределяем остаток по долям
    - Если денег не хватает — не заполняем низкоприоритетные совсем
    """
    payments_sum = sum(float(p.get("amount", 0)) for p in (scheduled_payments or []))
    free = monthly_income - payments_sum

    if free <= 0:
        return {}

    # Корректировка: если пользователь упомянул специфику трат
    desc_lower = (user_description or "").lower()
    share_overrides: dict[str, float] = {}
    if any(w in desc_lower for w in ["кафе", "ресторан", "бар", "часто хожу"]):
        share_overrides["Кафе и рестораны"] = 0.15
    if any(w in desc_lower for w in ["спорт", "фитнес", "спортзал", "здоровь"]):
        share_overrides["Здоровье"] = 0.10
    if any(w in desc_lower for w in ["одежд", "обувь", "шопинг"]):
        share_overrides["Одежда"] = 0.12
    if any(w in desc_lower for w in ["курс", "обучени", "образовани", "учус"]):
        share_overrides["Образование"] = 0.08
    if any(w in desc_lower for w in ["машин", "авто", "бензин", "парковк"]):
        share_overrides["Транспорт"] = 0.15

    remaining = free
    result: dict[str, float] = {}

    for cat, target_share in BUDGET_PRIORITY:
        minimum = BUDGET_MINIMUMS.get(cat, 0)
        share = share_overrides.get(cat, target_share)
        target = round(free * share)
        amount = max(target, minimum)

        if remaining <= 0:
            break  # деньги кончились — низкоприоритетные не добавляем

        if remaining < minimum and minimum > 0:
            # Денег меньше минимума — даём что есть, дальше стоп
            result[cat] = round(remaining)
            remaining = 0
        else:
            amount = min(amount, remaining)
            result[cat] = amount
            remaining -= amount

    return result


async def generate_initial_budgets(
    monthly_income: float,
    scheduled_payments: list[dict],
    user_description: str = "",
) -> dict[str, float]:
    """
    Генерирует начальные лимиты бюджета через новый budget_engine.
    - Конкретные суммы пользователя неприкосновенны (STRICT)
    - Иерархия приоритетов: Еда > Транспорт > Подписки > Хобби > ... > Одежда
    - Образование: только если упомянуто
    - Одежда: только при профиците
    """
    from budget_engine import parse_user_spending_ai, build_budgets

    user_expenses = None
    if user_description:
        try:
            user_expenses = await parse_user_spending_ai(user_description)
        except Exception as e:
            logging.error(f"parse_user_spending_ai error: {e}")
            from budget_engine import parse_user_spending
            user_expenses = parse_user_spending(user_description)

    result = build_budgets(
        monthly_income=monthly_income,
        scheduled_payments=scheduled_payments,
        user_expenses=user_expenses,
    )
    return result.budgets


# ─── GEMINI-АНАЛИЗ ПОСЛЕ ОНБОРДИНГА (режим новичка) ─────────────────────────

async def generate_onboarding_gemini_analysis(
    user_id: int,
    monthly_income: float,
    budgets: dict[str, float],
    payments: list[dict],
    salary_days: list[int],
    planned_income: float = 0,
    average_income: float = 0,
) -> str | None:
    """
    Сразу после онбординга: Gemini делает предварительный анализ
    финансового состояния на основе заявленных данных (без истории транзакций).
    Использует лимит Gemini — если уже потрачен, возвращает None.
    """
    if not await can_use_gemini_today(user_id):
        return None

    today = date.today()
    payments_sum = sum(float(p.get("amount", 0)) for p in payments)
    free = monthly_income - payments_sum
    savings_potential = monthly_income - payments_sum - sum(budgets.values())

    days_str = ", ".join(str(d) for d in salary_days) if salary_days else "не указаны"
    budgets_str = "\n".join(f"  {cat}: {amt:,.0f} руб." for cat, amt in budgets.items())
    payments_str = "\n".join(
        f"  {p.get('name', '?')}: {float(p.get('amount', 0)):,.0f} руб., {p.get('day_of_month', '?')}-е"
        for p in payments
    ) or "  нет"

    income_context = ""
    if planned_income and average_income and planned_income != average_income:
        income_context = (
            f"\nПрогнозируемый доход (ближайшее время): {planned_income:,.0f} руб.\n"
            f"Средний доход в месяц: {average_income:,.0f} руб.\n"
            f"ВАЖНО: у пользователя нестабильный доход. "
            f"Советы строить исходя из прогнозируемого ({planned_income:,.0f} руб.), "
            f"а не из среднего."
        )
    elif planned_income:
        income_context = f"\nПрогнозируемый доход (ближайшее время): {planned_income:,.0f} руб."
    elif average_income:
        income_context = f"\nСредний доход: {average_income:,.0f} руб./мес."

    prompt = f"""Сегодня {today.strftime('%d %B %Y')} ({today.strftime('%A')}).
Новый пользователь только что настроил финансовый трекер.

ДАННЫЕ ПОЛЬЗОВАТЕЛЯ:
Ежемесячный доход (для расчёта бюджетов): {monthly_income:,.0f} руб.
Дни зарплаты: {days_str}{income_context}

Обязательные кредиты ({payments_sum:,.0f} руб./мес.):
{payments_str}

Свободные деньги после кредитов: {free:,.0f} руб./мес.

Сформированные бюджеты по категориям:
{budgets_str}

Потенциал накопления: {savings_potential:,.0f} руб./мес.

ЗАДАНИЕ: Сделай предварительный анализ финансового состояния и дай практические советы.
Учти текущую дату — советы должны быть актуальны именно сейчас (какой день месяца, сколько до следующей зарплаты и т.д.).

ФОРМАТ (обычный текст, без *, #, ```. Обращайся на "ты"):

ПЕРВЫЙ ВЗГЛЯД НА ТВОИ ФИНАНСЫ
(Честная оценка: доля кредитов в доходе, сколько остаётся, реалистично ли это. Учти текущую дату.)

ГДЕ ПОТЕНЦИАЛЬНЫЕ РИСКИ
(Что бросается в глаза: перекос в сторону кредитов? мало на здоровье? нет накоплений? нестабильный доход?)

КАК ЛУЧШЕ ВЕСТИ БЮДЖЕТЫ
(3-4 конкретных совета: какие категории контролировать в первую очередь, как не выйти за лимиты, когда пересматривать)

ПЕРВЫЙ ШАГ
(Одно конкретное действие прямо сейчас — с суммой и категорией, с учётом того, какое сегодня число)

Стиль: честно, конкретно, с цифрами в рублях. Без воды и без занудства."""

    try:
        model = _get_gemini()
        response = await model.generate_content_async(prompt)
        raw = response.text.strip()
        await mark_gemini_used(user_id)
        return _strip_markdown(raw)
    except Exception as e:
        logging.error(f"onboarding gemini analysis error: {e}")
        return None


# ─── ХЕНДЛЕР ДЛЯ БОТА ────────────────────────────────────────────────────────

async def handle_weekly_advice_request(user_id: int) -> str:
    """
    Вызывается из jobs.py (по понедельникам) и по команде /week.
    Единый gate для Gemini — не позволяет запустить дважды в день.
    """
    from db import (
        get_transactions, get_salary_days, get_scheduled_payments,
        get_stats, get_planned_income, get_user,
    )

    today = date.today()
    transactions = await get_transactions(user_id, "all")
    salary_days = await get_salary_days(user_id)
    scheduled_payments = await get_scheduled_payments(user_id)
    stats = await get_stats(user_id, "month")
    current_balance = stats.get("balance", 0)

    # Берём заявленный доход из профиля пользователя (режим новичка)
    user = await get_user(user_id)
    known_salary_amount = float(user.get("monthly_income") or 0) if user else 0

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
        user_id=user_id,
        known_salary_amount=known_salary_amount,
    )

    summary  = result["budget_summary"]
    label    = result["week_label"]
    analysis = result["analysis"]
    ai_report = result.get("ai_report")
    used_ai   = result.get("used_ai", False)

    lines = [f"Бюджет на {label}", ""]

    if summary:
        lines.append(f"Баланс: {summary['balance_start']:,} руб.")
        if summary.get("income_amount", 0) > 0:
            lines.append(f"Ожидаемый доход: +{summary['income_amount']:,} руб.")
        if summary.get("mandatory_payments"):
            pmts = ", ".join([
                f"{p['name']} {p['amount']:,} руб."
                for p in summary["mandatory_payments"]
            ])
            lines.append(f"Платежи: {pmts}")
        lines.append(f"Свободных: {summary['discretionary']:,} руб.")

        if summary.get("cat_budgets"):
            lines.append("\nПо категориям:")
            for cat, amt in sorted(summary["cat_budgets"].items(),
                                   key=lambda x: x[1], reverse=True)[:6]:
                marker = "[!] " if summary.get("is_tight") and cat not in {"Еда", "Транспорт"} else ""
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

    profile = analysis.get("profile", {})
    beginner_mode = analysis.get("beginner_mode", False) or profile.get("beginner_mode", False)

    if beginner_mode:
        lines.append("(Режим новичка: прогноз строится от заявленного дохода. Точность вырастет через 2-3 недели реальных трат.)\n")
    elif not profile.get("has_enough_data"):
        weeks = profile.get("weeks_analyzed", 0)
        lines.append(f"(История: {weeks} нед. Советы станут точнее через 2-3 недели)\n")

    if ai_report:
        lines.append(ai_report)
    elif not used_ai:
        lines.append("(Подробный анализ уже был сегодня. Следующий — завтра.)")
    else:
        lines.append(_fallback_advice_text(summary))

    return "\n".join(lines)


def _fallback_advice_text(summary: dict) -> str:
    if not summary:
        return "Данных пока мало. Вноси расходы пару дней."
    disc = summary.get("discretionary", 0)
    is_tight = summary.get("is_tight", False)
    if is_tight:
        return f"Период напряжённый. Свободных {disc:,} руб. Держи расходы под контролем."
    return f"Свободный бюджет: {disc:,} руб. Укладывайся в него."


# ─── ВСПОМОГАТЕЛЬНЫЕ ─────────────────────────────────────────────────────────

def _make_summary(week: dict, profile: dict) -> dict:
    if not week:
        return {}

    avg_by_cat = profile.get("avg_weekly_by_cat", {})
    disc = week.get("discretionary_budget", 0)
    scale = week.get("scale", 1.0)

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
