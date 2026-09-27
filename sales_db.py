"""Server-only Supabase access; every query is scoped to its owner."""
import db


async def load(user_id, month):
    settings = (await db._execute(db.supabase.table("sales_months").select("*")
                                 .eq("user_id", user_id).eq("month", month))).data
    events = await db._pages(lambda: db.supabase.table("sales_events").select("*")
                            .eq("user_id", user_id).eq("month", month).order("created_at").order("id"))
    reports = await db._pages(lambda: db.supabase.table("sales_reports").select("*")
                             .eq("user_id", user_id).eq("month", month).order("created_at").order("id"))
    return settings[0] if settings else None, events, reports


async def save_settings(user_id, month, targets, glass_price):
    await db._execute(db.supabase.table("sales_months").upsert(
        {"user_id": user_id, "month": month, "targets": targets, "glass_price": glass_price},
        on_conflict="user_id,month"))


async def insert_once(table, user_id, operation_id, data, compare_keys):
    # A retry never resurrects a voided event or overwrites a previous request.
    await db._execute(db.supabase.table(table).upsert(
        {**data, "user_id": user_id, "id": operation_id},
        on_conflict="user_id,id", ignore_duplicates=True))
    saved = (await db._execute(db.supabase.table(table).select("*")
                              .eq("user_id", user_id).eq("id", operation_id))).data[0]
    if any(saved[k] != data[k] for k in compare_keys):
        raise ValueError("Этот идентификатор уже использован для другой операции")
    return saved


async def undo(user_id, operation_id):
    rows = (await db._execute(db.supabase.table("sales_events").update({"voided": True})
                             .eq("user_id", user_id).eq("id", operation_id))).data
    return rows[0] if rows else None


async def export(user_id):
    result = {}
    for table in ("sales_months", "sales_events", "sales_reports"):
        result[table] = await db._pages(lambda table=table: db.supabase.table(table).select("*")
                                      .eq("user_id", user_id).order("month").order("created_at")
                                      .order("user_id" if table == "sales_months" else "id"))
    return result


async def edit_event(user_id, operation_id, value, work_date):
    rows = (await db._execute(db.supabase.table('sales_events').update(
        {'value':value,'work_date':work_date,'month':work_date[:7]})
        .eq('user_id',user_id).eq('id',operation_id).eq('voided',False))).data
    return rows[0] if rows else None


async def get_event(user_id, operation_id):
    rows=(await db._execute(db.supabase.table('sales_events').select('*')
                           .eq('user_id',user_id).eq('id',operation_id))).data
    return rows[0] if rows else None
