"""Authenticated mini-app endpoints for plans, sales and cumulative reports."""
from datetime import date
from uuid import UUID
import logging

from aiohttp import web
import db
import sales_db
from sales import compute_sales, number, month_key, validate_values, METRICS, TARGETS, KINDS, COUNTS
from workday import op_today


def operation_id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Нужен идентификатор операции") from None


def work_date(value, month):
    try:
        d = date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError("Укажи дату смены") from None
    if not d.isoformat().startswith(month) or d > op_today():
        raise ValueError("Дата должна быть в выбранном месяце и не в будущем")
    return d.isoformat()


async def payload(user_id, month):
    settings, events, reports = await sales_db.load(user_id, month)
    result = compute_sales(month, settings, events, reports)
    result["has_settings"] = settings is not None
    result["events"] = list(reversed(events))[:50]
    result["today"] = op_today().isoformat()
    return result


async def handle(request):
    from webapp_api import _auth, NO_CACHE
    user_id, body = await _auth(request)
    if user_id is None:
        return body
    try:
        month = month_key(body.get("month", op_today().strftime("%Y-%m")))
        action = request.match_info["action"]
        if action == "view":
            pass
        elif action == "settings":
            current, _, _ = await sales_db.load(user_id, month)
            if current is None:
                return web.json_response({"error": "Первый план месяца задай в чате: план продаж вино 143000; коктейли 110"}, status=409)
            targets = validate_values(body.get("targets"), TARGETS)
            if any(v == 0 for v in targets.values()):
                raise ValueError("План должен быть больше нуля; ненужное поле оставь пустым")
            price = number(body.get("glass_price"))
            await db.get_or_create_user(user_id)
            await sales_db.save_settings(user_id, month, targets, price)
        elif action == "save":
            return web.json_response({"error": "Новые продажи записываются в чате бота"}, status=405)
        elif action == "edit":
            oid = operation_id(body.get("operation_id"))
            old = await sales_db.get_event(user_id, oid)
            if not old or old.get("voided"):
                return web.json_response({"error": "Запись не найдена"}, status=404)
            value = number(body.get("value"), count=old["kind"] in COUNTS)
            day = work_date(body.get("work_date"), month)
            saved = await sales_db.edit_event(user_id, oid, value, day)
            if not saved:
                return web.json_response({"error": "Запись уже отменена"}, status=409)
        elif action == "undo":
            saved = await sales_db.undo(user_id, operation_id(body.get("operation_id")))
            if not saved:
                return web.json_response({"error": "Запись не найдена"}, status=404)
            month = saved["month"]
        elif action == "report":
            cutoff = work_date(body.get("cutoff"), month)
            totals = validate_values(body.get("totals"), METRICS)
            if not totals:
                raise ValueError("Заполни хотя бы один результат")
            complete = body.get("records_complete", False)
            if not isinstance(complete, bool):
                raise ValueError("Укажи полноту записей")
            oid = operation_id(body.get("operation_id"))
            settings, events, reports = await sales_db.load(user_id, month)
            if not any(r["cutoff"] == cutoff for r in reports):
                return web.json_response({"error": "Первый отчет на эту дату запиши в чате бота"}, status=409)
            from sales_service import save_report
            await save_report(user_id, oid, month, cutoff, totals, complete)
        else:
            return web.json_response({"error": "Не найдено"}, status=404)
        return web.json_response(await payload(user_id, month), headers=NO_CACHE)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    except Exception:
        logging.exception("Sales request failed: %s", request.match_info.get("action"))
        return web.json_response({"error": "Не удалось получить подтверждение. Повтори запрос; повтор не создаст вторую продажу."}, status=503)


def register_sales_routes(app):
    app.router.add_post("/api/sales/{action}", handle)
