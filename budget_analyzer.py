"""
budget_analyzer.py — Умная финансовая модель без AI.

Анализирует историю транзакций, строит профиль пользователя,
рассчитывает денежные потоки по зарплатным периодам и передаёт
структурированные данные в Gemini для персонализированных советов.

v2: Исправлены средние, переход на зарплатные периоды,
    улучшен профиль привычек и расчёт can_afford.
"""

from datetime import date, timedelta
from calendar import monthrange
from collections import defaultdict
import statistics
import math


# ─── КОНСТАНТЫ ───────────────────────────────────────────────────────────────

MANDATORY_CATEGORIES = {"Жильё", "Связь", "Обязательные"}
FLEXIBLE_CATEGORIES  = {"Развлечения", "Одежда", "Прочее"}
ESSENTIAL_CATEGORIES = {"Еда", "Транспорт", "Здоровье", "Образование"}

# Минимальное кол-во транзакций для построения надёжного профиля
MIN_TRANSACTIONS_FOR_PROFILE = 10


# ─── ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ ────────────────────────────────────────────────────

def build_user_profile(transactions: list[dict]) -> dict:
    """
    Строит профиль привычек из истории транзакций.

    Исправления v2:
    - Средние считаются только по неделям где категория реально встречалась
      (не занижаются нулями).
    - Добавлен паттерн «трачу больше сразу после зарплаты».
    - Улучшено определение импульсивных трат.
    - Добавлен анализ дохода (регулярность, средний размер).
    """
    expenses = [t for t in transactions if t.get("type") == "expense"]
    incomes  = [t for t in transactions if t.get("type") == "income"]

    if len(expenses) < MIN_TRANSACTIONS_FOR_PROFILE:
        return _empty_profile(len(expenses))

    # ── Группируем расходы по неделям ──
    # weekly_cats[week_key][cat] = total amount
    weekly_cats: dict[tuple, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    day_spending: dict[int, list[float]] = defaultdict(list)
    desc_counter: dict[str, int] = defaultdict(int)
    place_cat_map: dict[str, str] = {}  # место → категория

    for t in expenses:
        try:
            tx_date = date.fromisoformat(t["created_at"][:10])
        except Exception:
            continue

        week_key = tx_date.isocalendar()[:2]   # (year, week_number)
        cat    = t.get("category", "Прочее")
        amount = float(t.get("amount", 0))

        weekly_cats[week_key][cat] += amount
        day_spending[tx_date.weekday()].append(amount)

        desc = (t.get("description") or "").strip().lower()
        if desc and len(desc) > 3:
            place = " ".join(desc.split()[:3])
            desc_counter[place] += 1
            place_cat_map[place] = cat

    num_weeks = len(weekly_cats)
    if num_weeks == 0:
        return _empty_profile(0)

    # ── Средние по категориям — ТОЛЬКО по неделям где категория была ──
    # (v2 fix: не занижать нулями за недели без трат)
    cat_active_weeks: dict[str, list[float]] = defaultdict(list)
    cat_all_weeks:    dict[str, list[float]] = defaultdict(list)

    for week_data in weekly_cats.values():
        all_cats = set(cat_active_weeks.keys()) | set(week_data.keys())
        for cat in all_cats:
            if cat in week_data:
                cat_active_weeks[cat].append(week_data[cat])
            cat_all_weeks[cat].append(week_data.get(cat, 0))

    avg_when_present: dict[str, float] = {}  # средняя трата когда покупают
    avg_weekly:       dict[str, float] = {}  # средняя с учётом «нулевых» недель
    frequency:        dict[str, float] = {}  # доля недель с тратой (0..1)

    for cat in cat_active_weeks:
        active = cat_active_weeks[cat]
        avg_when_present[cat] = statistics.mean(active)
        freq = len(active) / num_weeks
        frequency[cat] = freq
        # Ожидаемая средняя в неделю = (среднее когда есть) × (частота)
        avg_weekly[cat] = avg_when_present[cat] * freq

    total_avg_weekly = sum(avg_weekly.values())

    # ── Стандартное отклонение (для импульсивности) ──
    std_weekly: dict[str, float] = {}
    for cat, amounts in cat_all_weeks.items():
        std_weekly[cat] = statistics.stdev(amounts) if len(amounts) > 1 else 0

    # ── Любимые категории: высокая доля + не обязательные ──
    favourite_cats = []
    for cat, avg in sorted(avg_weekly.items(), key=lambda x: x[1], reverse=True):
        if cat in MANDATORY_CATEGORIES:
            continue
        share = avg / total_avg_weekly if total_avg_weekly > 0 else 0
        if share > 0.15:  # > 15% бюджета
            favourite_cats.append({
                "category": cat,
                "weekly_avg": round(avg_weekly[cat]),
                "when_present_avg": round(avg_when_present[cat]),
                "frequency": round(frequency.get(cat, 0), 2),  # 0.85 = 85% недель
                "share_pct": round(share * 100),
            })

    # ── Импульсивные: высокий коэффициент вариации И нерегулярность ──
    impulse_cats = []
    for cat, avg in avg_weekly.items():
        if cat in MANDATORY_CATEGORIES or avg < 50:
            continue
        std = std_weekly.get(cat, 0)
        freq = frequency.get(cat, 1)
        cv = std / avg_when_present.get(cat, 1) if avg_when_present.get(cat, 0) > 0 else 0
        # Импульсивная: редко но дорого (freq < 0.5) ИЛИ сильный разброс (cv > 1.0)
        if (freq < 0.5 and avg_when_present.get(cat, 0) > 300) or cv > 1.0:
            impulse_cats.append({
                "category": cat,
                "weekly_avg": round(avg),
                "typical_amount": round(avg_when_present.get(cat, avg)),
                "frequency_pct": round(freq * 100),
                "volatility_cv": round(cv, 2),
            })

    # ── Паттерн «после зарплаты тратят больше» ──
    # Сравниваем траты в недели с доходом vs без
    income_weeks: set[tuple] = set()
    for t in incomes:
        try:
            tx_date = date.fromisoformat(t["created_at"][:10])
            income_weeks.add(tx_date.isocalendar()[:2])
        except Exception:
            continue

    salary_week_avg  = 0.0
    regular_week_avg = 0.0
    if income_weeks:
        s_totals = [sum(weekly_cats[w].values()) for w in income_weeks if w in weekly_cats]
        r_totals = [sum(weekly_cats[w].values()) for w in weekly_cats if w not in income_weeks]
        salary_week_avg  = statistics.mean(s_totals)  if s_totals  else 0
        regular_week_avg = statistics.mean(r_totals) if r_totals else total_avg_weekly
    post_salary_uplift = round(salary_week_avg - regular_week_avg) if regular_week_avg > 0 else 0

    # ── Траты по дням недели ──
    day_names = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    spending_days = {}
    for day_num in range(7):
        amounts = day_spending.get(day_num, [])
        spending_days[day_names[day_num]] = round(statistics.mean(amounts)) if amounts else 0

    # ── Топ-мест с категорией ──
    top_places = []
    for place, cnt in sorted(desc_counter.items(), key=lambda x: x[1], reverse=True)[:7]:
        top_places.append({
            "place": place,
            "visits": cnt,
            "category": place_cat_map.get(place, ""),
            "typical_spend": round(
                avg_when_present.get(place_cat_map.get(place, ""), 0) / max(cnt, 1)
            ),
        })

    # ── Анализ доходов ──
    income_profile = _build_income_profile(incomes)

    return {
        "avg_weekly_by_cat": {k: round(v) for k, v in avg_weekly.items()},
        "avg_when_present_by_cat": {k: round(v) for k, v in avg_when_present.items()},
        "frequency_by_cat": {k: round(v, 2) for k, v in frequency.items()},
        "total_avg_weekly": round(total_avg_weekly),
        "favourite_cats": favourite_cats,
        "impulse_cats": impulse_cats,
        "spending_days": spending_days,
        "top_places": top_places,
        "post_salary_uplift": post_salary_uplift,  # рублей сверх нормы после зарплаты
        "income_profile": income_profile,
        "weeks_analyzed": num_weeks,
        "has_enough_data": num_weeks >= 4,
    }


def _build_income_profile(incomes: list[dict]) -> dict:
    """Анализирует регулярность и размер доходов."""
    if not incomes:
        return {"avg_income": 0, "income_count": 0, "regularity": "unknown"}

    amounts = [float(t.get("amount", 0)) for t in incomes]
    avg_income = round(statistics.mean(amounts))
    total = round(sum(amounts))

    # Регулярность: низкий CV = стабильный доход
    cv = (statistics.stdev(amounts) / avg_income) if len(amounts) > 1 and avg_income > 0 else 0
    if cv < 0.2:
        regularity = "стабильный"
    elif cv < 0.5:
        regularity = "умеренно нестабильный"
    else:
        regularity = "нерегулярный (фриланс/подработки)"

    return {
        "avg_income": avg_income,
        "total_income": total,
        "income_count": len(incomes),
        "regularity": regularity,
    }


def _empty_profile(tx_count: int = 0) -> dict:
    return {
        "avg_weekly_by_cat": {},
        "avg_when_present_by_cat": {},
        "frequency_by_cat": {},
        "total_avg_weekly": 0,
        "favourite_cats": [],
        "impulse_cats": [],
        "spending_days": {},
        "top_places": [],
        "post_salary_uplift": 0,
        "income_profile": {"avg_income": 0, "income_count": 0, "regularity": "unknown"},
        "weeks_analyzed": 0,
        "has_enough_data": False,
        "tx_count": tx_count,
    }


# ─── ЗАРПЛАТНЫЕ ПЕРИОДЫ (вместо календарных недель) ──────────────────────────

def get_salary_periods(year: int, month: int, salary_days: list[int]) -> list[dict]:
    """
    Делит месяц на периоды от одной зарплаты до следующей.
    Это гораздо полезнее календарных недель — деньги считаются
    от получки до получки.

    Если зарплат нет — падаем на календарные недели.
    """
    if not salary_days:
        return _calendar_weeks(year, month)

    first_day = date(year, month, 1)
    last_day  = date(year, month, monthrange(year, month)[1])

    # Собираем все даты зарплат в этом и соседних месяцах
    salary_dates = []
    for m_offset in (-1, 0, 1):
        y, m = year, month + m_offset
        if m == 0:
            y, m = y - 1, 12
        elif m == 13:
            y, m = y + 1, 1
        days_in_m = monthrange(y, m)[1]
        for d in sorted(salary_days):
            actual_day = min(d, days_in_m)
            salary_dates.append(date(y, m, actual_day))

    salary_dates = sorted(set(salary_dates))

    # Формируем периоды: от дня зарплаты до дня перед следующей
    periods = []
    period_num = 1
    for i, sal_date in enumerate(salary_dates):
        # Период начинается с даты зарплаты
        period_start = sal_date

        # Конец периода: день перед следующей зарплатой
        if i + 1 < len(salary_dates):
            period_end = salary_dates[i + 1] - timedelta(days=1)
        else:
            period_end = last_day  # до конца месяца

        # Ограничиваем периодом месяца
        period_start = max(period_start, first_day)
        period_end   = min(period_end, last_day)

        if period_start > last_day or period_end < first_day:
            continue

        days = [period_start + timedelta(days=j)
                for j in range((period_end - period_start).days + 1)]

        periods.append({
            "week_num": period_num,
            "start": period_start,
            "end": period_end,
            "days": days,
            "salary_date": sal_date,
            "label": f"Период {period_num} ({period_start.strftime('%d.%m')}–{period_end.strftime('%d.%m')})",
            "is_salary_period": True,
        })
        period_num += 1

    # Если до первой зарплаты есть дни — добавляем как нулевой период
    first_sal = min((p["start"] for p in periods), default=first_day)
    if first_sal > first_day:
        pre_days = [first_day + timedelta(days=j)
                    for j in range((first_sal - first_day).days)]
        if pre_days:
            periods.insert(0, {
                "week_num": 0,
                "start": first_day,
                "end": first_sal - timedelta(days=1),
                "days": pre_days,
                "salary_date": None,
                "label": f"До зарплаты ({first_day.strftime('%d.%m')}–{(first_sal - timedelta(days=1)).strftime('%d.%m')})",
                "is_salary_period": False,
            })
            for p in periods:
                p["week_num"] += 1

    return periods if periods else _calendar_weeks(year, month)


def _calendar_weeks(year: int, month: int) -> list[dict]:
    """Запасной вариант: обычные календарные недели пн-вс."""
    first = date(year, month, 1)
    last  = date(year, month, monthrange(year, month)[1])
    weeks = []
    current = first
    n = 1
    while current <= last:
        # Начало недели — ближайший предыдущий понедельник, но не раньше 1-го
        w_start = max(first, current - timedelta(days=current.weekday()))
        w_end   = min(last, w_start + timedelta(days=6))
        days    = [w_start + timedelta(days=i) for i in range((w_end - w_start).days + 1)]
        weeks.append({
            "week_num": n,
            "start": w_start,
            "end": w_end,
            "days": days,
            "salary_date": None,
            "label": f"Неделя {n} ({w_start.strftime('%d.%m')}–{w_end.strftime('%d.%m')})",
            "is_salary_period": False,
        })
        current = w_end + timedelta(days=1)
        n += 1
    return weeks


# ─── ДЕНЕЖНЫЕ ПОТОКИ ─────────────────────────────────────────────────────────

def calculate_weekly_cashflows(
    year: int,
    month: int,
    salary_days: list[int],
    scheduled_payments: list[dict],
    profile: dict,
    current_balance: float = 0,
    planned_income: list[dict] = None,
    known_salary_amount: float = 0,   # если знаем сумму зарплаты
) -> list[dict]:
    """
    Считает денежные потоки по зарплатным периодам.

    v2 исправления:
    - Периоды от зарплаты до зарплаты (не пн-вс)
    - Корректный учёт зарплаты в балансе
    - can_afford привязан к профилю favourite_cats
    - Учёт «эффекта после зарплаты» (тратят больше в первую неделю)
    """
    periods = get_salary_periods(year, month, salary_days)
    planned_income = planned_income or []

    avg_by_cat    = profile.get("avg_weekly_by_cat", {})
    freq_by_cat   = profile.get("frequency_by_cat", {})
    present_avg   = profile.get("avg_when_present_by_cat", {})
    total_avg     = profile.get("total_avg_weekly", 0)
    favourite_cats = profile.get("favourite_cats", [])
    post_uplift   = profile.get("post_salary_uplift", 0)

    balance = current_balance
    result  = []

    for period in periods:
        period_days = {d.day for d in period["days"]}
        p_start = period["start"]
        p_end   = period["end"]
        period_len = len(period["days"])

        # ── Нормируем профиль на длину периода ──
        # Если период ≠ 7 дней, масштабируем недельные средние
        scale = period_len / 7.0

        # ── Доходы периода ──
        income_amount = 0.0
        income_sources = []
        has_salary = period.get("is_salary_period", False) and period.get("salary_date") is not None

        if has_salary:
            # Знаем дату зарплаты, но сумму — только если передана
            if known_salary_amount > 0:
                income_amount += known_salary_amount
                income_sources.append(f"зарплата {known_salary_amount:,.0f} руб.")
            else:
                # Используем среднее из профиля доходов
                avg_inc = profile.get("income_profile", {}).get("avg_income", 0)
                if avg_inc > 0:
                    income_amount += avg_inc
                    income_sources.append(f"зарплата ~{avg_inc:,.0f} руб. (прогноз)")

        # Планируемые доходы в этом периоде
        for p in planned_income:
            try:
                p_date = date.fromisoformat(str(p.get("expected_date", ""))[:10])
            except Exception:
                continue
            if p_start <= p_date <= p_end and p.get("type") == "income":
                amt = float(p.get("amount", 0))
                income_amount += amt
                income_sources.append(f"+{amt:,.0f} руб. ({p.get('description', 'доход')})")

        # ── Обязательные платежи периода ──
        mandatory_list = []
        mandatory_sum  = 0.0
        for pmt in (scheduled_payments or []):
            if pmt.get("day_of_month") in period_days:
                amt = float(pmt.get("amount", 0))
                mandatory_list.append({
                    "name": pmt["name"],
                    "amount": amt,
                    "day": pmt["day_of_month"],
                })
                mandatory_sum += amt

        # ── Ожидаемые расходы на период (из профиля) ──
        expected_by_cat: dict[str, float] = {}
        for cat, avg in avg_by_cat.items():
            if cat in MANDATORY_CATEGORIES:
                continue
            # Масштабируем на длину периода
            scaled = avg * scale
            # «Эффект после зарплаты»: в зарплатный период тратят больше
            if has_salary and post_uplift > 0:
                uplift_share = avg / total_avg if total_avg > 0 else 0
                scaled += post_uplift * uplift_share * scale
            expected_by_cat[cat] = round(scaled)

        total_expected_discretionary = sum(expected_by_cat.values())

        # ── Баланс с учётом дохода ──
        balance_after_income = balance + income_amount
        balance_after_mandatory = balance_after_income - mandatory_sum
        discretionary = max(0.0, balance_after_mandatory)

        # ── Напряжённость ──
        is_tight = discretionary < total_expected_discretionary * 0.75

        # ── Что можно дополнительно позволить ──
        can_afford = []
        surplus = discretionary - total_expected_discretionary
        if surplus > 0 and favourite_cats:
            for fav in favourite_cats[:3]:
                cat = fav["category"]
                typical = fav.get("when_present_avg", 0)
                if typical > 0 and surplus >= typical * 0.5:
                    can_afford.append({
                        "category": cat,
                        "amount": round(min(typical, surplus * 0.4)),
                        "description": f"обычно тратит {typical:,} руб.",
                    })

        # ── Что придётся сократить (если напряжённо) ──
        must_cut = []
        if is_tight and total_expected_discretionary > 0:
            deficit = total_expected_discretionary - discretionary
            # Сначала режем гибкие, потом любимые
            for cat in sorted(expected_by_cat, key=lambda c: (
                0 if c in FLEXIBLE_CATEGORIES else
                1 if c not in ESSENTIAL_CATEGORIES else 2
            )):
                if deficit <= 0:
                    break
                current_budget = expected_by_cat[cat]
                if current_budget <= 0:
                    continue
                # Режем не более 60% от категории
                max_cut = current_budget * 0.6
                cut = min(max_cut, deficit)
                must_cut.append({
                    "category": cat,
                    "suggested_cut": round(cut),
                    "new_budget": round(current_budget - cut),
                })
                deficit -= cut

        # ── Прогноз баланса на конец периода ──
        actual_spend_expected = min(discretionary, total_expected_discretionary)
        balance_end = balance_after_mandatory - actual_spend_expected

        result.append({
            "week_num": period["week_num"],
            "label": period["label"],
            "start": p_start.isoformat(),
            "end": p_end.isoformat(),
            "days_count": period_len,
            "days_in_month": sorted(period_days),
            "has_salary": has_salary,
            "income_amount": round(income_amount),
            "income_sources": income_sources,
            "mandatory_payments": mandatory_list,
            "mandatory_sum": round(mandatory_sum),
            "expected_by_cat": expected_by_cat,
            "total_expected_discretionary": round(total_expected_discretionary),
            "discretionary_budget": round(discretionary),
            "balance_start": round(balance),
            "balance_after_income": round(balance_after_income),
            "balance_end_projected": round(balance_end),
            "is_tight": is_tight,
            "can_afford": can_afford,
            "must_cut": must_cut,
            "scale": round(scale, 2),  # длина периода / 7
        })

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
    known_salary_amount: float = 0,
) -> dict:
    """
    Главная функция — полный анализ месяца.
    Возвращает профиль + денежные потоки + инсайты.
    """
    today   = date.today()
    target  = target_month or today
    profile = build_user_profile(transactions)

    weekly = calculate_weekly_cashflows(
        year=target.year,
        month=target.month,
        salary_days=salary_days,
        scheduled_payments=scheduled_payments,
        profile=profile,
        current_balance=current_balance,
        planned_income=planned_income or [],
        known_salary_amount=known_salary_amount,
    )

    # Текущий период
    current_week = None
    if target.year == today.year and target.month == today.month:
        for w in weekly:
            s = date.fromisoformat(w["start"])
            e = date.fromisoformat(w["end"])
            if s <= today <= e:
                current_week = w
                break

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

    tight = [w for w in weekly if w["is_tight"]]
    if tight:
        labels = [w["label"] for w in tight]
        insights.append(f"Напряжённые периоды: {'; '.join(labels)}.")

    if profile.get("post_salary_uplift", 0) > 200:
        insights.append(
            f"Паттерн: в неделю после зарплаты тратит на "
            f"{profile['post_salary_uplift']:,} руб. больше обычного — "
            f"типичный «эффект свежей зарплаты»."
        )

    for imp in profile.get("impulse_cats", []):
        insights.append(
            f"{imp['category']}: импульсивные траты — встречается в {imp['frequency_pct']}% недель, "
            f"но когда случается — {imp['typical_amount']:,} руб."
        )

    for fav in profile.get("favourite_cats", [])[:2]:
        freq_pct = round(fav.get("frequency", 0) * 100)
        insights.append(
            f"{fav['category']} — приоритет: {fav['share_pct']}% бюджета "
            f"({fav['weekly_avg']:,} руб/нед, в {freq_pct}% периодов)."
        )

    days = profile.get("spending_days", {})
    if days:
        max_day = max(days, key=days.get)
        if days[max_day] > 0:
            insights.append(f"Больше всего тратит по {max_day} ({days[max_day]:,} руб в среднем).")

    inc = profile.get("income_profile", {})
    if inc.get("regularity") and inc["regularity"] != "unknown":
        insights.append(f"Доход: {inc['regularity']}, средний {inc.get('avg_income', 0):,} руб.")

    return insights


# ─── ФОРМАТИРОВАНИЕ ДЛЯ AI ───────────────────────────────────────────────────

def format_for_ai(analysis: dict, week: dict = None) -> str:
    """
    Форматирует результат анализа в структурированный текст для AI (Gemini/Groq).
    Чёткий, информативный, без воды.
    """
    profile = analysis["profile"]
    target  = week or analysis.get("current_week")
    insights = analysis.get("insights", [])

    lines = []

    lines.append("=== ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ ===")
    lines.append(f"Данных: {profile['weeks_analyzed']} периодов истории.")

    if not profile.get("has_enough_data"):
        lines.append("(Данных мало — советы будут обобщёнными, точность вырастет через 2-3 недели.)")

    if profile["favourite_cats"]:
        fav_str = ", ".join([
            f"{f['category']} ({f['weekly_avg']:,} руб/нед, {f['share_pct']}%, "
            f"частота {round(f.get('frequency',0)*100)}%)"
            for f in profile["favourite_cats"]
        ])
        lines.append(f"Приоритетные категории: {fav_str}")

    if profile["impulse_cats"]:
        imp_str = ", ".join([
            f"{i['category']} (обычно {i['typical_amount']:,} руб, {i['frequency_pct']}% недель)"
            for i in profile["impulse_cats"]
        ])
        lines.append(f"Импульсивные траты: {imp_str}")

    if profile["top_places"]:
        places_str = ", ".join([
            f"{p['place']} ({p['visits']} раз, кат. {p['category']})"
            for p in profile["top_places"][:5]
        ])
        lines.append(f"Частые места: {places_str}")

    if profile.get("post_salary_uplift", 0) > 100:
        lines.append(f"После зарплаты тратит на {profile['post_salary_uplift']:,} руб. больше обычного.")

    inc = profile.get("income_profile", {})
    if inc.get("avg_income", 0) > 0:
        lines.append(f"Доход: {inc['regularity']}, средний {inc['avg_income']:,} руб.")

    lines.append(f"Средний расход в неделю: {profile['total_avg_weekly']:,} руб.")

    lines.append("\n=== ВСЕ ПЕРИОДЫ МЕСЯЦА ===")
    for w in analysis["weekly_cashflows"]:
        status = ("НАПРЯЖЁННЫЙ" if w["is_tight"] else
                  ("ЗАРПЛАТА" if w["has_salary"] else "норма"))
        salary_note = f" [зарплата +{w['income_amount']:,} руб.]" if w["has_salary"] and w["income_amount"] > 0 else ""
        lines.append(
            f"{w['label']}: баланс нач. {w['balance_start']:,} | "
            f"доход {w['income_amount']:,} | "
            f"платежи -{w['mandatory_sum']:,} | "
            f"дискреционный {w['discretionary_budget']:,} | "
            f"прогноз конец {w['balance_end_projected']:,} [{status}]{salary_note}"
        )
        if w["mandatory_payments"]:
            pmts = "; ".join([f"{p['name']} {p['amount']:,} руб." for p in w["mandatory_payments"]])
            lines.append(f"  → Платежи: {pmts}")

    if target:
        lines.append(f"\n=== ФОКУС: {target['label']} ===")
        lines.append(f"Баланс на начало: {target['balance_start']:,} руб.")
        if target["income_amount"] > 0:
            lines.append(f"Ожидаемый доход: +{target['income_amount']:,} руб. ({', '.join(target['income_sources'])})")
        lines.append(f"Обязательные платежи: {target['mandatory_sum']:,} руб.")
        lines.append(f"Свободный бюджет: {target['discretionary_budget']:,} руб.")
        lines.append(f"Обычные траты на такой период: {target['total_expected_discretionary']:,} руб.")

        if target["expected_by_cat"]:
            lines.append("Исторический бюджет по категориям (на этот период):")
            for cat, amt in sorted(target["expected_by_cat"].items(), key=lambda x: x[1], reverse=True):
                if amt > 0:
                    lines.append(f"  {cat}: {amt:,} руб.")

        if target["can_afford"]:
            lines.append("Есть запас — можно позволить:")
            for ca in target["can_afford"]:
                lines.append(f"  {ca['category']}: до {ca['amount']:,} руб. ({ca['description']})")

        if target["must_cut"]:
            lines.append("Рекомендуемые сокращения:")
            for mc in target["must_cut"]:
                lines.append(f"  {mc['category']}: сократить на {mc['suggested_cut']:,} → оставить {mc['new_budget']:,} руб.")

        if target["is_tight"]:
            deficit = target["total_expected_discretionary"] - target["discretionary_budget"]
            lines.append(f"ВНИМАНИЕ: дефицит {deficit:,} руб. — нужно сократить расходы.")

    if insights:
        lines.append("\n=== ВЫЯВЛЕННЫЕ ПАТТЕРНЫ ===")
        for ins in insights:
            lines.append(f"• {ins}")

    return "\n".join(lines)
