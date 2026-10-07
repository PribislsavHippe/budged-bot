"""Verified employee identities; no earnings are exposed to restaurant owners."""
import hashlib
import secrets
import unicodedata
from uuid import uuid4

from postgrest.exceptions import APIError
import db

STATUSES = {'pending':'Ждём подтверждения администратора', 'approved':'Ты в команде',
            'rejected':'Заявка отклонена', 'revoked':'Доступ к ресторану закрыт'}


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
        messages = {'identity_select_restaurant':'Выбери ресторан в миниаппе — там у каждого своя ссылка и список сотрудников.',
                    'identity_forbidden':'Это может сделать администратор ресторана.',
                    'identity_stale':'Заявка уже изменилась. Обнови список сотрудников.',
                    'identity_invite_expired':'Эта ссылка больше не работает. Попроси того, кто её прислал, отправить новую.',
                    'identity_other_restaurant':'Сначала открой свой ресторан и выйди из него.'}
        for code, message in messages.items():
            if code in str(error):
                raise ValueError(message) from None
        if error.code == '23505':
            raise ValueError('Под этим именем уже есть сотрудник. Если это тёзки, в отчёте нужны разные имена или коды.') from None
        raise ValueError('Пока не получается сохранить имя. Попробуй ещё раз или напиши Лёше.') from None


async def transfer(uid, link_id, source_id, target_id):
    try:
        return (await db._execute(db.supabase.rpc('identity_transfer', {
            'actor':uid,'link_id':link_id,'source_id':source_id,'target_id':target_id}))).data
    except APIError as error:
        detail=str(error)
        if 'identity_forbidden' in detail:
            raise ValueError('Переносить можно только между своими ресторанами.') from None
        if 'identity_stale' in detail:
            raise ValueError('Сотрудник уже перемещён или его статус изменился. Обнови список.') from None
        if error.code == '23505':
            raise ValueError('В новом ресторане уже есть сотрудник с таким именем или кодом.') from None
        if error.code in ('PGRST202','42883'):
            raise ValueError('Перенос пока недоступен. Попроси владельца проекта обновить базу.') from None
        raise ValueError('Не удалось перенести сотрудника. Обнови списки ресторанов и проверь результат.') from None


async def owner_restaurants(uid):
    return await db._pages(lambda:db.supabase.table('restaurants').select('id,name')
                           .eq('owner_id',uid).order('created_at').order('id'))


async def owner_restaurant(uid, restaurant_id=None):
    query=db.supabase.table('restaurants').select('id,name').eq('owner_id',uid)
    if restaurant_id is not None:
        from uuid import UUID
        query=query.eq('id',str(UUID(str(restaurant_id))))
    rows=(await db._execute(query.order('created_at').order('id').limit(2))).data
    if restaurant_id is None and len(rows)>1:
        raise ValueError('Выбери ресторан в миниаппе — у каждого свой список сотрудников.')
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


async def invite(uid, restaurant_id=None):
    token=secrets.token_urlsafe(24)
    args={'hash':token_hash(token)}
    if restaurant_id is not None:args['restaurant_id']=restaurant_id
    restaurant=await action(uid,'invite',args)
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
    owned=await owner_restaurants(uid)
    return {'employee_link':row,'owned_restaurant':owned[0] if len(owned)==1 else None,'owned_restaurants':owned}


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
