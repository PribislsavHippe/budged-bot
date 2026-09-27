"""One application service for chat writes and mini-app corrections."""
import sales_db
from sales import compute_sales, DEFAULT_GLASS_PRICE


async def save_report(uid, oid, month, cutoff, totals, complete=False):
    settings, events, reports = await sales_db.load(uid,month)
    forecast=compute_sales(month,settings,events,reports,through=cutoff)
    data={'month':month,'cutoff':cutoff,'totals':totals,'records_complete':complete,
          'source':'manual_official_report','forecast':{'glass_price':forecast['glass_price'],'metrics':forecast['metrics']}}
    return await sales_db.insert_once('sales_reports',uid,oid,data,('month','cutoff','totals','records_complete'))


async def chat_write(uid, oid, command):
    action=command['action'];month=command['month']
    if action=='save':
        data={k:command[k] for k in ('month','kind','value','work_date')}
        return await sales_db.insert_once('sales_events',uid,oid,data,tuple(data))
    if action=='settings':
        settings,_,_=await sales_db.load(uid,month)
        settings=settings or {}
        targets={**settings.get('targets',{}),**command.get('targets',{})}
        await sales_db.save_settings(uid,month,targets,command.get('glass_price',settings.get('glass_price',DEFAULT_GLASS_PRICE)))
    if action=='report':return await save_report(uid,oid,month,command['cutoff'],command['totals'])
