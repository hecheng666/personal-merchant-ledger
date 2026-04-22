"""记忆库与待确认聚合的测试。"""

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.wechat.memory import PeerMemory
from app.wechat.parser import Category, Direction, ParseResult, Transaction, parse_bill
from app.wechat.reconcile import aggregate_pending, unresolved

ROOT = Path(__file__).parent.parent
REAL = ROOT / "fixtures" / "wechat-sample-real.csv"
SYNTH = ROOT / "fixtures" / "wechat-synthetic-30k.csv"


@pytest.fixture()
def memory(tmp_path):
    book = PeerMemory(tmp_path / "memory.db")
    yield book
    book.close()


def test_memory_set_and_get(memory):
    memory.set("房东", "private")
    assert memory.get("房东") == "private"
    assert memory.get("陌生人") is None


def test_memory_repeat_write_overwrites(memory):
    memory.set("房东", "private")
    memory.set("房东", "business_expense")
    assert memory.get("房东") == "business_expense"
    assert len(memory.all()) == 1


def test_memory_rejects_unknown_category(memory):
    with pytest.raises(ValueError):
        memory.set("房东", "unknown")


def test_pending_aggregates_by_peer():
    result = parse_bill(REAL)
    pending = aggregate_pending(result)
    # 真实样例里待确认只有「转账 房东」和「其他 赞赏作者的收款」
    assert len(pending) == 2
    assert pending[0].peer == "房东"
    assert pending[0].total == Decimal("500.00")


def test_known_peer_disappears_from_unresolved(memory):
    result = parse_bill(REAL)
    for item in aggregate_pending(result):
        memory.set(item.peer, "private")
    assert unresolved(aggregate_pending(result, memory.all())) == []


def test_synthetic_pending_compression():
    result = parse_bill(SYNTH)
    pending = aggregate_pending(result)
    assert len(pending) == 54
    assert sum(item.count for item in pending) > 3000
    assert len(pending[0].peer) > 0


def test_mixed_pending_splits_income_and_expense():
    """同一个对手方既有收又有支，金额不能直接相加当往来额。"""
    base = dict(
        tx_type="转账",
        item="/",
        method="零钱",
        status="已收钱",
        mch_no="m",
        note="/",
        category=Category.PENDING,
    )
    income = Transaction(
        time=datetime(2026, 10, 1, 10, 0, 0),
        peer="老王",
        direction=Direction.INCOME,
        amount=Decimal("100.00"),
        tx_no="1",
        **base,
    )
    expense = Transaction(
        time=datetime(2026, 10, 2, 10, 0, 0),
        peer="老王",
        direction=Direction.EXPENSE,
        amount=Decimal("40.00"),
        tx_no="2",
        **base,
    )
    pending = aggregate_pending(ParseResult(transactions=[income, expense]))
    assert len(pending) == 1
    item = pending[0]
    assert item.income_total == Decimal("100.00")
    assert item.expense_total == Decimal("40.00")
    assert item.mixed is True
    assert item.amount_text() == "收 100.00 / 支 40.00"


def test_excluded_pending_is_not_asked(tmp_path):
    tx = Transaction(
        time=datetime(2026, 10, 1, 10, 0, 0),
        tx_type="转账",
        peer="退货顾客",
        item="/",
        direction=Direction.EXPENSE,
        amount=Decimal("20.00"),
        method="零钱",
        status="已全额退款",
        tx_no="1",
        mch_no="1",
        note="/",
        category=Category.PENDING,
        excluded=True,
        excluded_reason="refund",
    )
    pending = aggregate_pending(ParseResult(transactions=[tx]))
    assert pending == []
