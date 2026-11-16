"""A reminder is delivered only after Telegram accepts it; claims expire."""
import asyncio
from uuid import uuid4
import db
from diagnostics import failure


REMINDER_STEPS={'start':'shift_tomorrow','end':'shift_end'}


async def send_shift_notice(shift_id,kind,send,uid=None):
    token=str(uuid4())
    claimed=(await db._execute(db.supabase.rpc('claim_shift_notice',{
        'shift':shift_id,'notice':kind,'token':token}))).data
    if not claimed:return False
    delivered=False
    try:
        await send()
        delivered=True
        if uid is not None:
            import research
            research.track(uid,'reminder_sent',source='server',screen='calendar',step=REMINDER_STEPS[kind])
        return True
    finally:
        try:
            await asyncio.shield(db._execute(db.supabase.rpc('finish_shift_notice',{
                'shift':shift_id,'notice':kind,'token':token,'delivered':delivered})))
        except Exception as error:
            # A hard crash or an unknown response is recovered by lease expiry.
            failure(error,area='shift_notice',stage='finish')
