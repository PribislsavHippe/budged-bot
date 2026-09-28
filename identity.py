"""Verified employee identities; no earnings are exposed to restaurant owners."""
import hashlib
import secrets
import unicodedata
from uuid import uuid4

from postgrest.exceptions import APIError
import db

STATUSES = {'pending':'Ожидает подтверждения', 'approved':'Подтверждено',
            'rejected':'Заявка отклонена', 'revoked':'Привязка отозвана'}


def name(value):
    value = ' '.join(unicodedata.normalize('NFKC', value).split())
    if not 2 <= len(value) <= 80 or any(not (unicodedata.category(c)[0] in 'LMN' or c in " .'-") for c in value):
        raise ValueError('Имя должно содержать от 2 до 80 букв, цифр, пробелов, точек или дефисов.')
    return value


def name_key(value):
    return name(value).casefold().replace('ё','е')


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


async def action(uid, kind, args):
    try:
        return (await db._execute(db.supabase.rpc('identity_action', {'actor':uid,'action':kind,'args':args}))).data
    except APIError as error:
        # Provider error text is used only for fixed classification, never echoed.
        messages = {'identity_forbidden':'Нет прав для этого действия.',
                    'identity_stale':'Эта заявка уже изменилась. Открой /team заново.',
                    'identity_invite_expired':'Приглашение недействительно. Попроси новое у администратора.',
                    'identity_other_restaurant':'Сначала отключи прежнюю привязку в /profile.'}
        for code, message in messages.items():
            if code in str(error):
                raise ValueError(message) from None
        if error.code == '23505':
            raise ValueError('Это имя уже привязано. Проверь сотрудника; для тёзок нужен уникальный код в отчёте.') from None
        raise ValueError('Идентификация недоступна. Проверь миграцию v9 и серверный ключ Supabase.') from None


async def owner_restaurant(uid):
    rows=(await db._execute(db.supabase.table('restaurants').select('id,name').eq('owner_id',uid))).data
    return rows[0] if rows else None


async def profile(uid):
    rows=(await db._execute(db.supabase.table('employee_links').select('*').eq('user_id',uid))).data
    if not rows: return None
    row=rows[0]
    restaurants=(await db._execute(db.supabase.table('restaurants').select('name,owner_id').eq('id',row['restaurant_id']))).data
    row['restaurant_name']=restaurants[0]['name'] if restaurants else 'Ресторан'
    row['has_admin']=bool(restaurants and restaurants[0]['owner_id'])
    return row


async def create(uid, title):
    # Bootstrap authority belongs to the configured bot owner, not invite holders.
    from admin import is_admin
    if not is_admin(uid): raise ValueError('Создать ресторан может владелец бота.')
    await db.get_or_create_user(uid)
    return await action(uid,'create',{'id':str(uuid4()),'name':name(title)})


async def invite(uid):
    token=secrets.token_urlsafe(24)
    restaurant=await action(uid,'invite',{'hash':token_hash(token)})
    return restaurant,token


async def request(uid, token, report_name):
    cleaned=name(report_name)
    await db.get_or_create_user(uid)
    return await action(uid,'request',{'hash':token_hash(token),'id':str(uuid4()),'name':cleaned,'name_key':name_key(cleaned)})


async def team(uid, page=0):
    restaurant=await owner_restaurant(uid)
    if not restaurant: raise ValueError('Ты не администратор ресторана.')
    rows=(await db._execute(db.supabase.table('employee_links').select('id,user_id,report_name,status')
                           .eq('restaurant_id',restaurant['id']).order('requested_at').order('id').range(page*10,page*10+10))).data
    return restaurant,rows[:10],len(rows)>10


async def export(uid):
    row=await profile(uid)
    owned=await owner_restaurant(uid)
    return {'employee_link':row,'owned_restaurant':owned}


async def report_row(uid, rows):
    """Exact unique match only. Never use fuzzy names to assign financial data."""
    link=await profile(uid)
    if not link or link['status']!='approved' or not link['has_admin']:return None
    matches=[]
    for index,row in enumerate(rows):
        try:
            if name_key(row.get('name',''))==link['name_key']:matches.append(index)
        except ValueError:continue
    return matches[0] if len(matches)==1 else None
