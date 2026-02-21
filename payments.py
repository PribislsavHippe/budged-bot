from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from db import add_scheduled_payment, get_scheduled_payments, delete_scheduled_payment, add_transaction
from keyboards import main_menu, payments_menu_kb, payment_actions_kb
from google_calendar import create_payment_event
from datetime import datetime

router = Router()


class PaymentState(StatesGroup):
    waiting_name = State()
    waiting_amount = State()
    waiting_day = State()


# ─── МЕНЮ ПЛАТЕЖЕЙ ───────────────────────────────────────

@router.message(F.text == "Платежи")
async def payments_menu(message: Message):
    await message.answer("<b>Обязательные платежи</b>", parse_mode="HTML", reply_markup=payments_menu_kb())


@router.callback_query(F.data == "payment:list")
async def list_payments(callback: CallbackQuery):
    payments = await get_scheduled_payments(callback.from_user.id)

    if not payments:
        await callback.message.answer(
            "Платежей пока нет. Можешь добавить аренду, подписки и прочие радости жизни."
        )
        await callback.answer()
        return

    for p in payments:
        await callback.message.answer(
            f"<b>{p['name']}</b>\n"
            f"Сумма: {p['amount']:,.2f} ₽\n"
            f"День: {p['day_of_month']}-е число. Напомню за {p['remind_days_before']} дн.",
            parse_mode="HTML",
            reply_markup=payment_actions_kb(p["id"])
        )
    await callback.answer()


# ─── ДОБАВЛЕНИЕ ПЛАТЕЖА ───────────────────────────────────

@router.callback_query(F.data == "payment:add")
async def add_payment_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Как называется платёж?\n"
        "Например: Аренда, Netflix, Спортзал"
    )
    await state.set_state(PaymentState.waiting_name)
    await callback.answer()


@router.message(PaymentState.waiting_name)
async def payment_name_entered(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("Введи сумму платежа:")
    await state.set_state(PaymentState.waiting_amount)


@router.message(PaymentState.waiting_amount)
async def payment_amount_entered(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",", ".").replace(" ", ""))
        await state.update_data(amount=amount)
        await message.answer("В какой день месяца нужно оплачивать? (1–31)")
        await state.set_state(PaymentState.waiting_day)
    except ValueError:
        await message.answer("Сумма должна быть числом. Попробуй ещё раз.")


@router.message(PaymentState.waiting_day)
async def payment_day_entered(message: Message, state: FSMContext):
    try:
        day = int(message.text.strip())
        if not 1 <= day <= 31:
            raise ValueError

        data = await state.get_data()
        payment = await add_scheduled_payment(
            user_id=message.from_user.id,
            name=data["name"],
            amount=data["amount"],
            day=day
        )

        # Создаём событие в Google Calendar
        now = datetime.now()
        event_date = now.replace(day=day) if day >= now.day else now.replace(month=now.month + 1, day=day)
        calendar_created = await create_payment_event(
            user_id=message.from_user.id,
            payment_name=data["name"],
            amount=data["amount"],
            date=event_date
        )

        calendar_note = " Плюс кинул в твой Google Calendar." if calendar_created else ""
        await message.answer(
            f"<b>Платёж в деле.{calendar_note}</b>\n\n"
            f"{data['name']} — {data['amount']:,.2f} ₽, {day}-е число. Напомню за 2 дня.",
            parse_mode="HTML",
            reply_markup=main_menu()
        )
        await state.clear()
    except ValueError:
        await message.answer("День месяца — число от 1 до 31. Не изобретай.")


# ─── ДЕЙСТВИЯ С ПЛАТЕЖОМ ──────────────────────────────────

@router.callback_query(F.data.startswith("payment:paid:"))
async def mark_payment_paid(callback: CallbackQuery):
    payment_id = int(callback.data.split(":")[2])
    payments = await get_scheduled_payments(callback.from_user.id)
    payment = next((p for p in payments if p["id"] == payment_id), None)

    if payment:
        await add_transaction(
            user_id=callback.from_user.id,
            type_="expense",
            amount=payment["amount"],
            category=payment["category"],
            description=f"Обязательный платёж: {payment['name']}"
        )
        await callback.message.answer(
            f"Платёж <b>{payment['name']}</b> на {payment['amount']:,.2f} ₽ списан. Чек в архиве.",
            parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data.startswith("payment:delete:"))
async def delete_payment(callback: CallbackQuery):
    payment_id = int(callback.data.split(":")[2])
    await delete_scheduled_payment(payment_id, callback.from_user.id)
    await callback.message.answer("Платёж вычеркнул. Как будто и не было.")
    await callback.answer()
