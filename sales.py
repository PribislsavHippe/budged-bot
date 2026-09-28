"""Personal restaurant sales, deliberately separate from earnings entries."""
from datetime import date
from decimal import Decimal, InvalidOperation

METRICS = ("wine", "cocktails", "desserts", "turnover", "postcards", "dvd")
TARGETS = METRICS[:4]
COUNTS = {"cocktails", "postcards", "dvd", "glass"}
KINDS = {"glass": "wine", "bottle": "wine", **{m: m for m in METRICS if m != "wine"}}
DEFAULT_GLASS_PRICE = 850


def number(value, *, count=False, zero=False):
    if isinstance(value, bool) or value is None:
        raise ValueError("Напиши число, например 250 или 250,50")
    try:
        n = Decimal(str(value).replace(" ", "").replace(",", "."))
    except InvalidOperation:
        raise ValueError("Напиши число, например 250 или 250,50") from None
    if not n.is_finite() or n < 0 or (n == 0 and not zero) or n > 100_000_000:
        raise ValueError("Нужно число от нуля до 100 миллионов; для новой продажи — больше нуля")
    if n != n.quantize(Decimal("1") if count else Decimal("0.01")):
        raise ValueError("Количество укажи целым числом, а сумму — с копейками, например 250,50")
    return int(n) if count else float(n)


def month_key(value):
    if not isinstance(value, str):
        raise ValueError("Укажи месяц")
    try:
        d = date.fromisoformat(value + "-01")
    except ValueError:
        raise ValueError("Выбери месяц ещё раз") from None
    if not 2000 <= d.year <= 2100 or d.strftime("%Y-%m") != value:
        raise ValueError("Этот месяц не поддерживается. Выбери другой")
    return value


def validate_values(values, keys):
    if not isinstance(values, dict) or not set(values) <= set(keys):
        raise ValueError("Не понял, что это за показатель")
    return {k: number(v, count=k in COUNTS, zero=True) for k, v in values.items()}


def compute_sales(month, settings, events, reports, through=None):
    """Each metric has its own latest cumulative cutoff. Missing != zero.

    Reports at the same cutoff are immutable revisions; newest replaces the
    entire report at that cutoff. Prior revisions remain available for export.
    """
    price = Decimal(str((settings or {}).get("glass_price", DEFAULT_GLASS_PRICE)))
    targets = (settings or {}).get("targets", {})
    revisions = {}
    for r in sorted(reports, key=lambda r: (r["created_at"], r["id"])):
        if r["cutoff"].startswith(month) and (through is None or r["cutoff"] <= through):
            revisions[r["cutoff"]] = r
    rows = {}
    for metric in METRICS:
        applicable = [r for r in revisions.values() if metric in r["totals"]]
        report = max(applicable, key=lambda r: r["cutoff"]) if applicable else None
        cutoff = report["cutoff"] if report else None
        official = Decimal(str(report["totals"][metric])) if report else Decimal(0)
        exact, estimated, count = Decimal(0), Decimal(0), 0
        for e in events:
            if e.get("voided") or not e["work_date"].startswith(month):
                continue
            if (cutoff and e["work_date"] <= cutoff) or (through and e["work_date"] > through):
                continue
            if KINDS[e["kind"]] != metric:
                continue
            count += 1
            value = Decimal(str(e["value"]))
            if e["kind"] == "glass":
                estimated += value * price
            else:
                exact += value
        known = report is not None or count > 0
        total = official + exact + estimated
        target = targets.get(metric) or None
        rows[metric] = {
            "total": float(total) if known else None,
            "official": float(official) if report else None,
            "cutoff": cutoff, "recorded": float(exact), "estimated": float(estimated),
            "target": target, "remaining": float(max(Decimal(str(target)) - total, 0)) if target and known else None,
            "pct": round(float(total) / target * 100, 2) if target and known else None,
        }
    return {"month": month, "glass_price": float(price), "targets": targets,
            "metrics": rows, "reports": sorted(revisions.values(), key=lambda r: r["cutoff"], reverse=True)}
