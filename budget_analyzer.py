"""
budget_analyzer.py — Умная финансовая модель без AI.

Анализирует историю транзакций, строит профиль пользователя,
рассчитывает денежные потоки по неделям и передаёт структурированные
данные в Gemini для персонализированных советов.
"""

from datetime import date, timedelta
from calendar import monthrange
from collections import defaultdict
import statistics


# ─── КОНСТАНТЫ ───────────────────────────────────────────────────────────────

# Категории которые считаем "обязательными" по умолчанию
MANDATORY_CATEGORIES = {"Жильё", "Связь", "Обязательные"}

# Категории которые считаем "гибкими" (можно урезать)
FLEXIBLE_CATEGORIES = {"Развлечения", "Одежда", "Прочее"}

# Категории первой необходимости (нельзя урезать до нуля)
ESSENTIAL_CATEGORIES = {"Еда", "Транспорт", "Здоровье", "Образование"}


# ─── ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ ────────────────────────────────────────────────────

def build_user_profile(transactions: list[dict]) -> dict:
    """
    Строит профиль привычек пользователя из истории транзакций.
    
    Возвращает:
    - avg_weekly_by_cat: средние траты в неделю по категориям
    - favourite_cats: категории где тратит больше нормы (любит)
    - impulse_cats: категории с высокой дисперсией (импульсивные траты)
    - lean_cats: категории где тратит мало (не интересует)
    - total_avg_weekly: средний общий расход в неделю
    - spending_days: {0-6: средняя трата}, 0=пн
    - descriptions: частые описания (чтобы знать любимые места)
    """
    if not transactions:
        return _empty_profile()

    # Группируем по неделям
    weeks = defaultdict(lambda: defaultdict(float))
    day_spending = defaultdict(list)
    desc_counter = defaultdict(int)

    for t in transactions:
        if t.get("type") != "expense":
            continue
        try:
            tx_date = date.fromisoformat(t["created_at"][:10])
        except Exception:
            continue

        # ISO-неделя как ключ
        week_key = tx_date.isocalendar()[:2]  # (year, week)
        cat = t.get("category", "Прочее")
        amount = float(t.get("amount", 0))

        weeks[week_key][cat] += amount
        day_spending[tx_date.weekday()].append(amount)

        desc = (t.get("description") or "").strip().lower()
        if desc and len(desc) > 3:
            # Берём первые 2 слова как "место"
            place = " ".join(desc.split()[:2])
            desc_counter[place] += 1

    if not weeks:
        return _empty_profile()

    num_weeks = len(weeks)

    # Средние траты по категориям за неделю
    cat_totals = defaultdict(list)
    for week_data in weeks.values():
        all_cats = set(cat_totals.keys()) | set(week_data.keys())
        for cat in all_cats:
            cat_totals[cat].append(week_data.get(cat, 0))

    avg_weekly_by_cat = {}
    std_weekly_by_cat = {}
    for cat, weekly_amounts in cat_totals.items():
        # Дополняем нулями для недель без трат в категории
        padded = weekly_amounts + [0] * (num_weeks - len(weekly_amounts))
        avg_weekly_by_cat[cat] = statistics.mean(padded)
        std_weekly_by_cat[cat] = statistics.stdev(padded) if len(padded) > 1 else 0

    total_avg_weekly = sum(avg_weekly_by_cat.values())

    # Любимые категории: тратит > 20% от своего среднего
    favourite_cats = []
    lean_cats = []
    for cat, avg in sorted(avg_weekly_by_cat.items(), key=lambda x: x[1], reverse=True):
        share = avg / total_avg_weekly if total_avg_weekly > 0 else 0
        if share > 0.20 and cat not in MANDATORY_CATEGORIES:
            favourite_cats.append({"category": cat, "weekly_avg": round(avg), "share_pct": round(share * 100)})
        if share < 0.03 and avg > 0:
            lean_cats.append(cat)

    # Импульсивные категории: высокая дисперсия (CV > 0.8)
    impulse_cats = []
    for cat, avg in avg_weekly_by_cat.items():
        std = std_weekly_by_cat.get(cat, 0)
        cv = std / avg if avg > 5 else 0  # коэффициент вариации
        if cv > 0.8 and cat not in MANDATORY_CATEGORIES:
            impulse_cats.append({
                "category": cat,
                "weekly_avg": round(avg),
                "weekly_std": round(std),
                "volatility": round(cv, 2)
            })

    # Средние траты по дням недели
    spending_days = {}
    day_names = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    for day_num in range(7):
        amounts = day_spending.get(day_num, [0])
        spending_days[day_names[day_num]] = round(statistics.mean(amounts))

    # Топ-5 мест/описаний
    top_places = sorted(desc_counter.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "avg_weekly_by_cat": {k: round(v) for k, v in avg_weekly_by_cat.items()},
        "total_avg_weekly": round(total_avg_weekly),
        "favourite_cats": favourite_cats,
        "impulse_cats": impulse_cats,
        "lean_cats": lean_cats,
        "spending_days": spending_days,
        "top_places": [{"place": p, "visits": v} for p, v in top_places],
        "weeks_analyzed": num_weeks,
    }


def _empty_profile() -> dict:
    return {
        "avg_weekly_by_cat": {},
        "total_avg_weekly": 0,
        "favourite_cats": [],
        "impulse_cats": [],
        "lean_cats": [],
        "spending_days": {},
        "top_places": [],
        "weeks_analyzed": 0,
    }


# ─── НЕДЕЛЬНЫЕ ДЕНЕЖНЫЕ ПОТОКИ ────────────────────────────────────────────────

def get_weeks_of_month(year: int, month: int) -> list[dict]:
    """
    Делит месяц на недели (с понедельника по воскресенье).
    Возвращает список {"week_num": 1..5, "start": date, "end": date, "days": [date,...]}.
    """
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])

    weeks = []
    current = first
    week_num = 1

    while current <= last:
        # Начало недели — понедельник текущей даты (или первый день месяца)
        week_start = max(first, current - timedelta(days=current.weekday()))
        week_end = min(last, week_start + timedelta(days=6))

        days = [week_start + timedelta(days=i)
                for i in range((week_end - week_start).days + 1)]

        weeks.append({
            "week_num": week_num,
            "start": week_start,
            "end": week_end,
            "days": days,
            "label": f"Неделя {week_num} ({week_start.strftime('%d.%m')}–{week_end.strftime('%d.%m')})",
        })
        current = week_end + timedelta(days=1)
        week_num += 1

    return weeks


def calculate_weekly_cashflows(
    year: int,
    month: int,
    salary_days: list[int],
    scheduled_payments: list[dict],
    profile: dict,
    current_balance: float = 0,
    planned_income: list[dict] = None,
) -> list[dict]:
    """
    Считает денежные потоки по каждой неделе месяца.

    Для каждой недели возвращает:
    - income_expected: ожидаемые доходы (зарплата + планируемые)
    - mandatory_expenses: обязательные платежи в эту неделю
    - discretionary_budget: бюджет на свободные траты
    - weekly_budget_by_cat: бюджет по категориям
    - balance_start: баланс на начало недели
    - balance_end: прогноз баланса на конец недели
    - is_tight: напряжённая неделя (мало свободных денег)
    - can_afford: на что можно потратиться дополнительно
    """
    weeks = get_weeks_of_month(year, month)
    avg_by_cat = profile.get("avg_weekly_by_cat", {})
    total_avg_weekly = profile.get("total_avg_weekly", 0)

    planned_income = planned_income or []
    balance = current_balance

    result = []

    for week in weeks:
        week_days = {d.day for d in week["days"]}
        week_start = week["start"]
        week_end = week["end"]

        # ── Доходы этой недели ──
        income_this_week = 0.0
        income_sources = []

        # Зарплата
        for sal_day in (salary_days or []):
            if sal_day in week_days:
                income_sources.append(f"зарплата ({sal_day}-го)")
                # Не знаем сумму зарплаты — отмечаем факт
                income_this_week = None  # сигнал что зарплата есть

        # Планируемые доходы
        for p in planned_income:
            try:
                p_date = date.fromisoformat(str(p.get("expected_date", ""))[:10])
            except Exception:
                continue
            if week_start <= p_date <= week_end and p.get("type") == "income":
                amount = float(p.get("amount", 0))
                income_this_week = (income_this_week or 0) + amount
                income_sources.append(f"+{amount:,.0f} руб. ({p.get('description', 'доход')})")

        has_salary = income_this_week is None
        if has_salary and not isinstance(income_this_week, (int, float)):
            income_this_week = 0  # Сумма зарплаты неизвестна — считаем 0 для потока

        # ── Обязательные платежи этой недели ──
        mandatory_this_week = []
        mandatory_sum = 0.0
        for p in (scheduled_payments or []):
            if p.get("day_of_month") in week_days:
                amount = float(p.get("amount", 0))
                mandatory_this_week.append({
                    "name": p["name"],
                    "amount": amount,
                    "day": p["day_of_month"],
                    "category": p.get("category", "Обязательные"),
                })
                mandatory_sum += amount

        # ── Бюджет по категориям ──
        # Берём средние недельные траты из профиля
        # Если мало данных — используем разумные пропорции
        budget_by_cat = {}

        if avg_by_cat and total_avg_weekly > 0:
            for cat, avg in avg_by_cat.items():
                if cat not in MANDATORY_CATEGORIES:
                    budget_by_cat[cat] = round(avg)
        else:
            # Дефолтные пропорции если нет истории
            budget_by_cat = {
                "Еда": 0,  # будет заполнено ниже
                "Транспорт": 0,
                "Развлечения": 0,
                "Здоровье": 0,
                "Прочее": 0,
            }

        # Свободный бюджет = баланс - обязательные
        discretionary = max(0, balance - mandatory_sum)

        # Если это неделя с зарплатой — отмечаем особо
        week_label = week["label"]
        if has_salary:
            week_label += " 💰 ЗАРПЛАТА"

        # Считаем среднюю трату в неделю (всё кроме обязательных)
        avg_discretionary = sum(
            v for k, v in avg_by_cat.items()
            if k not in MANDATORY_CATEGORIES
        ) if avg_by_cat else total_avg_weekly

        # Напряжённость: свободных денег меньше 80% от среднего
        is_tight = (discretionary < avg_discretionary * 0.8) if avg_discretionary > 0 else False

        # Что можно дополнительно позволить (если неделя нормальная)
        can_afford = []
        if not is_tight and discretionary > avg_discretionary * 1.2:
            surplus = discretionary - avg_discretionary
            can_afford.append({
                "description": "Есть запас",
                "amount": round(surplus),
                "suggestion": "можно отложить или позволить что-то приятное"
            })

        # Прогноз баланса на конец недели
        expected_spend = mandatory_sum + min(discretionary, avg_discretionary)
        balance_end = balance + (income_this_week or 0) - expected_spend

        result.append({
            "week_num": week["week_num"],
            "label": week_label,
            "start": week["start"].isoformat(),
            "end": week["end"].isoformat(),
            "days_in_month": sorted(week_days),
            "has_salary": has_salary,
            "income_sources": income_sources,
            "income_amount": income_this_week or 0,
            "mandatory_payments": mandatory_this_week,
            "mandatory_sum": round(mandatory_sum),
            "discretionary_budget": round(discretionary),
            "budget_by_cat": budget_by_cat,
            "avg_weekly_spend": round(avg_discretionary),
            "balance_start": round(balance),
            "balance_end_projected": round(balance_end),
            "is_tight": is_tight,
            "can_afford": can_afford,
        })

        # Обновляем баланс для следующей недели
        balance = balance_end

    return result


# ─── СВОДНЫЙ АНАЛИЗ ──────────────────────────────────────────────────────────

def analyze_month(
    transactions: list[dict],
    salary_days: list[int],
    scheduled_payments: list[dict],
    current_balance: float,
    planned_income: list[dict] = None,
    target_month: date = None,
) -> dict:
    """
    Главная функция — полный анализ месяца.
    
    Возвращает всё что нужно Gemini для персональных советов:
    - profile: профиль привычек
    - weekly_cashflows: денежные потоки по неделям
    - current_week: текущая неделя (если target_month — текущий)
    - insights: автоматически выявленные паттерны
    """
    today = date.today()
    target = target_month or today

    profile = build_user_profile(transactions)

    weekly = calculate_weekly_cashflows(
        year=target.year,
        month=target.month,
        salary_days=salary_days,
        scheduled_payments=scheduled_payments,
        profile=profile,
        current_balance=current_balance,
        planned_income=planned_income,
    )

    # Текущая неделя
    current_week = None
    if target.year == today.year and target.month == today.month:
        for w in weekly:
            start = date.fromisoformat(w["start"])
            end = date.fromisoformat(w["end"])
            if start <= today <= end:
                current_week = w
                break

    # Инсайты
    insights = _generate_insights(profile, weekly, today)

    return {
        "profile": profile,
        "weekly_cashflows": weekly,
        "current_week": current_week,
        "insights": insights,
        "analyzed_at": today.isoformat(),
    }


def _generate_insights(profile: dict, weekly: list[dict], today: date) -> list[str]:
    """Автоматически выявляет паттерны без AI."""
    insights = []

    # Напряжённые недели
    tight_weeks = [w for w in weekly if w["is_tight"]]
    if tight_weeks:
        labels = [w["label"].split("(")[0].strip() for w in tight_weeks]
        insights.append(f"Напряжённые недели: {', '.join(labels)} — свободных денег меньше обычного.")

    # Импульсивные категории
    for imp in profile.get("impulse_cats", []):
        insights.append(
            f"{imp['category']}: траты сильно скачут от недели к неделе "
            f"(в среднем {imp['weekly_avg']} руб., разброс ±{imp['weekly_std']} руб.) — типичные импульсивные расходы."
        )

    # Любимые категории
    for fav in profile.get("favourite_cats", [])[:2]:
        insights.append(
            f"{fav['category']} — приоритетная категория: {fav['share_pct']}% всех расходов "
            f"({fav['weekly_avg']} руб./нед.). Это важно для пользователя."
        )

    # День недели с максимальными тратами
    spending_days = profile.get("spending_days", {})
    if spending_days:
        max_day = max(spending_days, key=spending_days.get)
        max_amt = spending_days[max_day]
        if max_amt > 0:
            insights.append(f"Больше всего тратит по {max_day}: в среднем {max_amt} руб.")

    # Неделя с зарплатой
    salary_weeks = [w for w in weekly if w["has_salary"]]
    for sw in salary_weeks:
        mandatory = sw["mandatory_sum"]
        if mandatory > 0:
            insights.append(
                f"В {sw['label'].split(' 💰')[0].strip()} ожидается зарплата. "
                f"В эту же неделю платежей на {mandatory:,.0f} руб."
            )

    return insights


# ─── ФОРМАТИРОВАНИЕ ДЛЯ GEMINI ───────────────────────────────────────────────

def format_for_gemini(analysis: dict, week: dict = None) -> str:
    """
    Форматирует результат анализа в текстовый контекст для Gemini.
    Если week указана — фокус на конкретной неделе.
    """
    profile = analysis["profile"]
    target_week = week or analysis.get("current_week")
    insights = analysis.get("insights", [])

    lines = []

    # Профиль привычек
    lines.append("=== ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ ===")
    lines.append(f"Анализ за {profile['weeks_analyzed']} недель истории.")

    if profile["favourite_cats"]:
        fav = ", ".join([f"{f['category']} ({f['weekly_avg']} руб/нед, {f['share_pct']}%)"
                         for f in profile["favourite_cats"]])
        lines.append(f"Приоритетные категории (тратит много и охотно): {fav}")

    if profile["impulse_cats"]:
        imp = ", ".join([f"{i['category']} (нестабильно, разброс ±{i['weekly_std']} руб)"
                         for i in profile["impulse_cats"]])
        lines.append(f"Импульсивные траты: {imp}")

    if profile["top_places"]:
        places = ", ".join([f"{p['place']} ({p['visits']} раз)" for p in profile["top_places"]])
        lines.append(f"Частые места/описания: {places}")

    avg_day = profile.get("spending_days", {})
    if avg_day:
        day_str = ", ".join([f"{d}: {a} руб" for d, a in avg_day.items() if a > 0])
        lines.append(f"Среднее по дням недели: {day_str}")

    lines.append(f"Средний расход в неделю: {profile['total_avg_weekly']:,} руб.")

    # Все недели месяца
    lines.append("\n=== ДЕНЕЖНЫЕ ПОТОКИ ПО НЕДЕЛЯМ ===")
    for w in analysis["weekly_cashflows"]:
        status = "НАПРЯЖЁННАЯ" if w["is_tight"] else ("с ЗАРПЛАТОЙ" if w["has_salary"] else "норма")
        lines.append(
            f"{w['label']}: баланс нач. {w['balance_start']:,} руб. | "
            f"платежи -{w['mandatory_sum']:,} руб. | "
            f"свободных {w['discretionary_budget']:,} руб. | "
            f"прогноз конец {w['balance_end_projected']:,} руб. [{status}]"
        )
        if w["mandatory_payments"]:
            pmts = ", ".join([f"{p['name']} {p['amount']:,.0f} руб" for p in w["mandatory_payments"]])
            lines.append(f"  Обязательные платежи: {pmts}")

    # Целевая неделя
    if target_week:
        lines.append(f"\n=== ФОКУС: {target_week['label']} ===")
        lines.append(f"Баланс на начало: {target_week['balance_start']:,} руб.")
        lines.append(f"Обязательные платежи: {target_week['mandatory_sum']:,} руб.")
        lines.append(f"Свободный бюджет: {target_week['discretionary_budget']:,} руб.")
        lines.append(f"Средний расход на такую неделю: {target_week['avg_weekly_spend']:,} руб.")

        if target_week["budget_by_cat"]:
            lines.append("Исторический бюджет по категориям:")
            for cat, amt in sorted(target_week["budget_by_cat"].items(),
                                   key=lambda x: x[1], reverse=True):
                if amt > 0:
                    lines.append(f"  {cat}: {amt:,} руб.")

        if target_week["can_afford"]:
            for ca in target_week["can_afford"]:
                lines.append(f"Есть запас {ca['amount']:,} руб. — {ca['suggestion']}")

        if target_week["is_tight"]:
            lines.append("ВНИМАНИЕ: напряжённая неделя. Свободных денег меньше обычного.")

    # Инсайты
    if insights:
        lines.append("\n=== АВТОМАТИЧЕСКИ ВЫЯВЛЕННЫЕ ПАТТЕРНЫ ===")
        for ins in insights:
            lines.append(f"• {ins}")

    return "\n".join(lines)
