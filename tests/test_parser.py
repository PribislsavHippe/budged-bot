"""Тесты парсера. Запуск: python -m pytest tests/ -q  (или python tests/test_parser.py)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser import (
    CARD,
    CASH,
    extract_amount,
    looks_like_bank_tips,
    parse_bank_tips,
    parse_reconciliation,
    parse_transactions,
)

REAL_BANK_NOTIFICATION = (
    "Уведомление: Получены чаевые\n"
    "Сумма заказа: 7690.00 р.\n"
    "Чаевые: 500.00 р. (7 %)\n"
    "Команда: СЧАСТЬЕ на Итальянской"
)


def test_extract_amount():
    assert extract_amount("чай 500") == 500
    assert extract_amount("зп 30к") == 30000
    assert extract_amount("30 000 рублей") == 30000
    assert extract_amount("1.5к") == 1500
    assert extract_amount("250,50") == 250.5
    assert extract_amount("300р") == 300
    assert extract_amount("300 ₽") == 300
    assert extract_amount("нет суммы") is None
    assert extract_amount("0") is None


def test_tips_manual():
    (tx,) = parse_transactions("чай 500")
    assert tx["kind"] == "income"
    assert tx["category"] == "Чаевые"
    assert tx["account"] == CASH
    assert tx["amount"] == 500


def test_tips_card_explicit():
    (tx,) = parse_transactions("чаевые 800 на карту")
    assert tx["kind"] == "income"
    assert tx["account"] == CARD


def test_salary():
    (tx,) = parse_transactions("зп 30000")
    assert tx["kind"] == "income"
    assert tx["category"] == "Зарплата"
    assert tx["account"] == CARD


def test_shift():
    (tx,) = parse_transactions("смена 2500")
    assert tx["kind"] == "income"
    assert tx["category"] == "Подработка"
    assert tx["account"] == CASH


def test_expense_default_card():
    (tx,) = parse_transactions("кофе 200")
    assert tx["kind"] == "expense"
    assert tx["category"] == "Еда"
    assert tx["account"] == CARD


def test_expense_cash_marker():
    (tx,) = parse_transactions("такси 350 нал")
    assert tx["kind"] == "expense"
    assert tx["category"] == "Транспорт"
    assert tx["account"] == CASH


def test_unknown_expense():
    (tx,) = parse_transactions("шурупы 450")
    assert tx["kind"] == "expense"
    assert tx["category"] == "Прочее"


def test_multiple():
    txs = parse_transactions("кофе 200, такси 350, чай 1000")
    assert len(txs) == 3
    assert [t["kind"] for t in txs] == ["expense", "expense", "income"]
    assert txs[2]["account"] == CASH


def test_fragment_without_amount_skipped():
    txs = parse_transactions("кофе 200, что-то ещё")
    assert len(txs) == 1


def test_reconciliation_both():
    r = parse_reconciliation("наличными 5000, на карте 12000")
    assert r == {CASH: 5000, CARD: 12000}


def test_reconciliation_single():
    assert parse_reconciliation("на карте 8100") == {CARD: 8100}
    assert parse_reconciliation("нал 3200") == {CASH: 3200}
    assert parse_reconciliation("в кармане 500") == {CASH: 500}


def test_reconciliation_with_k_suffix():
    assert parse_reconciliation("на карте 12к") == {CARD: 12000}


def test_reconciliation_zero():
    assert parse_reconciliation("наличными 0") == {CASH: 0}


def test_not_reconciliation():
    # обычная операция не должна распознаваться как сверка
    assert parse_reconciliation("кофе 200") is None
    assert parse_reconciliation("чай 500") is None
    # «такси 350 нал» — счёт есть, но перед ним лишние слова → не сверка
    assert parse_reconciliation("такси 350 нал") is None
    # смешанное сообщение — не сверка
    assert parse_reconciliation("на карте 5000, кофе 200") is None


def test_bank_tips_cloudtips():
    assert parse_bank_tips("Вам оставили чаевые: 350 ₽") == 350
    assert parse_bank_tips("CloudTips. Вам перевели чаевые 1 250,50 ₽ от гостя") == 1250.5


def test_bank_tips_amount_first():
    assert parse_bank_tips("Перевод 500 ₽ — чаевые от гостя") == 500


def test_bank_tips_none():
    assert parse_bank_tips("Покупка 500 ₽ Пятёрочка") is None
    assert parse_bank_tips("чаевые скоро придут") is None


def test_real_bank_notification():
    # Берётся именно сумма чаевых (500), а не сумма заказа (7690)
    assert parse_bank_tips(REAL_BANK_NOTIFICATION) == 500
    assert looks_like_bank_tips(REAL_BANK_NOTIFICATION)


def test_bank_notification_full():
    from parser import parse_bank_notification
    r = parse_bank_notification(REAL_BANK_NOTIFICATION)
    assert r == {"amount": 500, "order_amount": 7690, "tip_percent": 7.0}


def test_bank_notification_percent_computed():
    from parser import parse_bank_notification
    r = parse_bank_notification("Получены чаевые\nСумма заказа: 5000.00 р.\nЧаевые: 400.00 р.")
    assert r["order_amount"] == 5000
    assert r["tip_percent"] == 8.0


def test_bank_notification_minimal():
    from parser import parse_bank_notification
    r = parse_bank_notification("Вам оставили чаевые: 350 ₽")
    assert r == {"amount": 350, "order_amount": None, "tip_percent": None}


def test_looks_like_bank_tips():
    assert looks_like_bank_tips("Вам оставили чаевые: 350 ₽")
    assert looks_like_bank_tips("Получены чаевые\nЧаевые: 120.00 р.")
    # ручной ввод — НЕ банковское уведомление
    assert not looks_like_bank_tips("чай 500")
    assert not looks_like_bank_tips("чаевые 500")
    assert not looks_like_bank_tips("кофе 200")


def test_parse_shift_days():
    from datetime import date
    from parser import parse_shift_days
    today = date(2026, 7, 20)
    assert parse_shift_days("работаю 22 24 26", today) == [
        date(2026, 7, 22), date(2026, 7, 24), date(2026, 7, 26)]
    assert parse_shift_days("смены 5, 7, 9", today) == [
        date(2026, 8, 5), date(2026, 8, 7), date(2026, 8, 9)]  # прошли в июле → август
    assert parse_shift_days("график 20 21", today) == [
        date(2026, 7, 20), date(2026, 7, 21)]  # 20 = сегодня, включаем


def test_parse_shift_days_not_income():
    # «смена 2500» — это доход, НЕ расписание
    from datetime import date
    from parser import parse_shift_days
    assert parse_shift_days("смена 2500", date(2026, 7, 20)) is None
    assert parse_shift_days("чай 500", date(2026, 7, 20)) is None
    # доход по-прежнему парсится как доход
    (tx,) = parse_transactions("смена 2500")
    assert tx["kind"] == "income" and tx["amount"] == 2500


def test_parse_shift_days_dedup_and_invalid():
    from datetime import date
    from parser import parse_shift_days
    today = date(2026, 7, 20)
    # дубли убираются, 31 сентября невалиден
    assert parse_shift_days("работаю 22 22 24", today) == [date(2026, 7, 22), date(2026, 7, 24)]
    # 40 вне диапазона игнорируется
    assert parse_shift_days("работаю 40", today) is None


def test_shift_days_ignore_numbers_outside_the_list():
    """Числа после постороннего слова к расписанию не относятся.

    «работаю сегодня, потратил 200 на такси» раньше ставило смену на 20-е:
    двузначное вырезалось из середины «200».
    """
    from datetime import date
    from parser import parse_shift_days
    today = date(2026, 7, 27)
    assert parse_shift_days("работаю сегодня, потратил 200 на такси", today) is None
    assert parse_shift_days("работаю 1 числа, потратил 1500", today) == [date(2026, 8, 1)]
    assert parse_shift_days("работаю 28 30, купил форму за 3000", today) == [
        date(2026, 7, 28), date(2026, 7, 30)]
    # триггер в конце фразы, перечисления нет вовсе
    assert parse_shift_days("потратил 20 на кофе, такой уж график", today) is None


def test_shift_days_filler_words():
    from datetime import date
    from parser import parse_shift_days
    today = date(2026, 7, 27)
    assert parse_shift_days("работаю 1 числа и 5 го", today) == [
        date(2026, 8, 1), date(2026, 8, 5)]
    # триггер не первый в строке — перечисление всё равно находится
    assert parse_shift_days("работаю в баре, график 5 7", today) == [
        date(2026, 8, 5), date(2026, 8, 7)]


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL  {name}: {e}")
    print("\nFAILED" if failed else "\nALL PASSED")
    sys.exit(1 if failed else 0)
