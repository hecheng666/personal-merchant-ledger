"""账单解析的契约测试。

这些测试里所有数字都来自 fixtures/wechat-sample-real.csv 的实测，
不是估算值。实现做到全绿，就等于通过了真实数据的验收。

运行：pytest -v
"""

from decimal import Decimal
from pathlib import Path

import pytest

from app.wechat.parser import (
    Category,
    Direction,
    clean_amount,
    classify,
    extract_fee,
    find_header_row,
    parse_bill,
    parse_direction,
)

REAL = Path(__file__).parent.parent / "fixtures" / "wechat-sample-real.csv"
SYNTH = Path(__file__).parent.parent / "fixtures" / "wechat-synthetic-30k.csv"


# ---------------------------------------------------------------- 金额清洗


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("¥28.16", Decimal("28.16")),
        ("¥50.0", Decimal("50.0")),
        ("¥1,234.56", Decimal("1234.56")),
        ("¥0.01", Decimal("0.01")),
        (" ¥12.00 ", Decimal("12.00")),
    ],
)
def test_clean_amount(raw, expected):
    assert clean_amount(raw) == expected


def test_clean_amount_must_not_use_float():
    """0.1 + 0.2 这类问题在钱上不可接受，必须 Decimal。"""
    total = clean_amount("¥0.10") + clean_amount("¥0.20")
    assert total == Decimal("0.30")
    assert isinstance(total, Decimal)


# ---------------------------------------------------------------- 方向判定


def test_parse_direction_three_states():
    assert parse_direction("收入") is Direction.INCOME
    assert parse_direction("支出") is Direction.EXPENSE
    assert parse_direction("/") is Direction.NEUTRAL


# ---------------------------------------------------------------- 表头定位


def test_find_header_row():
    """真实文件的表头在第 17 行（0-based 索引 16），前面有 16 行说明。"""
    import csv
    import io

    text = REAL.read_text(encoding="utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert find_header_row(rows) == 16


def test_find_header_row_raises_on_garbage():
    with pytest.raises(ValueError):
        find_header_row([["随便什么"], ["也不是账单"]])


# ---------------------------------------------------------------- 分类规则


def test_qr_income_is_business_income():
    assert classify("二维码收款", Direction.INCOME) is Category.BUSINESS_INCOME


def test_transfer_income_is_pending_not_guessed():
    """转账可能是顾客、亲友、退货返款 —— 必须留给人工确认。"""
    assert classify("转账", Direction.INCOME) is Category.PENDING


def test_neutral_types_are_private():
    for tx_type in [
        "零钱提现",
        "零钱充值",
        "信用卡还款",
        "购买理财通",
        "分分捐",
        "微信红包",
    ]:
        assert classify(tx_type, Direction.NEUTRAL) is Category.PRIVATE, tx_type


def test_prefix_match_on_variant_type_names():
    """「转入零钱通-来自工商银行(9876)」和「转入零钱通-来自零钱」是同一语义。"""
    assert classify("转入零钱通-来自工商银行(9876)", Direction.NEUTRAL) is Category.PRIVATE
    assert classify("零钱通转出-到零钱", Direction.NEUTRAL) is Category.PRIVATE


def test_unknown_type_goes_to_unknown_not_dropped():
    assert classify("deg-不认识的-txType", Direction.EXPENSE) is Category.UNKNOWN


# ---------------------------------------------------------------- 手续费


def test_extract_fee():
    assert extract_fee("服务费¥0.10") == Decimal("0.10")
    assert extract_fee("服务费¥1.00") == Decimal("1.00")
    assert extract_fee("/") is None
    assert extract_fee("") is None


# ---------------------------------------------------------------- 整份解析


@pytest.fixture(scope="module")
def real_result():
    return parse_bill(REAL)


def test_real_bill_row_count(real_result):
    """实测：表头之后 27 行数据，全部 11 列完整。"""
    assert real_result.stats.total_data_lines == 27
    assert real_result.stats.parsed == 27
    assert real_result.stats.skipped == 0
    assert len(real_result.transactions) == 27


def test_real_bill_direction_counts(real_result):
    """实测：收入 5 笔、支出 11 笔、中性 11 笔。"""
    counts = {d: 0 for d in Direction}
    for tx in real_result.transactions:
        counts[tx.direction] += 1
    assert counts[Direction.INCOME] == 5
    assert counts[Direction.EXPENSE] == 11
    assert counts[Direction.NEUTRAL] == 11


def test_real_bill_income_total(real_result):
    """实测：收入方向金额合计 28.49 元。"""
    assert real_result.stats.income_total == Decimal("28.49")


def test_real_bill_fee_total(real_result):
    """实测：3 笔提现，服务费 0.10 + 0.10 + 1.00 = 1.20。"""
    assert real_result.stats.fee_total == Decimal("1.20")


def test_real_bill_unknown_type_count(real_result):
    """实测：样例中故意放了 1 条未知交易类型，必须被识别而不是丢弃。"""
    assert real_result.stats.unknown_type == 1
    assert sum(1 for t in real_result.transactions if t.category is Category.UNKNOWN) == 1


def test_tx_no_has_no_tab(real_result):
    """实测：33 个字段尾部带 TAB，必须全部 strip 干净。"""
    for tx in real_result.transactions:
        assert "\t" not in tx.tx_no
        assert "\t" not in tx.mch_no


def test_first_transaction_fields(real_result):
    tx = real_result.transactions[0]
    assert tx.tx_type == "商户消费"
    assert tx.peer == "云膳过桥米线(传奇广场店)"
    assert tx.amount == Decimal("28.16")
    assert tx.tx_no == "3985734"


def test_no_refund_in_real_bill(real_result):
    """这份真实样例里没有退款行，所以 excluded 应为 0。

    有退款的场景由合成样本覆盖（下面 test_synthetic_*）。
    """
    assert real_result.stats.excluded == 0


# ---------------------------------------------------------------- 合成样本：规模与聚合


@pytest.fixture(scope="module")
def synth_result():
    return parse_bill(SYNTH)


def test_synthetic_row_count(synth_result):
    assert synth_result.stats.parsed == 30000


def test_synthetic_refund_and_closed_are_excluded(synth_result):
    """实测：已全额退款 304 笔 + 已关闭 154 笔 = 458 笔必须被剔除。"""
    assert synth_result.stats.excluded == 458
    excluded = [t for t in synth_result.transactions if t.excluded]
    assert len(excluded) == 458
    # 被剔除的不能从列表里消失，用户要能看到
    assert len(synth_result.transactions) == 30000


def test_synthetic_refund_total(synth_result):
    """实测：退款金额合计 15274.88 元。

    退款行在账单里是「二维码收款 + 支出方向」，不是收入方向，
    所以它不会自动从 income_total 里被排除 —— 必须单独统计再相减。
    """
    assert synth_result.stats.refund_total == Decimal("15274.88")


def test_synthetic_closed_total(synth_result):
    """已关闭交易金额 6326.04 元，钱没动过，不计入退款也不计入营业额。"""
    assert synth_result.stats.closed_total == Decimal("6326.04")


def test_synthetic_net_revenue(synth_result):
    """净营业额 = 收入 1371925.92 − 退款 15274.88 = 1356651.04。

    这是整个项目最重要的一个数字。如果不减退款，账会虚高一万五。
    """
    assert synth_result.stats.income_total == Decimal("1371925.92")
    assert synth_result.stats.net_revenue == Decimal("1356651.04")
    assert synth_result.stats.net_revenue < synth_result.stats.income_total


def test_synthetic_has_no_unknown_type(synth_result):
    """合成样本只用了已知交易类型，所以未识别必须是 0。

    这条同时守着一个设计边界：退款行虽然是「二维码收款 + 支出」，
    也必须归入 BUSINESS_INCOME 由 excluded 冲销，不能掉进 UNKNOWN。
    """
    assert synth_result.stats.unknown_type == 0


def test_synthetic_pending_aggregates_by_peer(synth_result):
    """核心设计验证：3191 笔待确认转账，聚合后只有 54 个对手方。

    这是产品能活下来的原因 —— 用户要点的是 54 次，不是 3191 次。
    """
    pending = [t for t in synth_result.transactions if t.category is Category.PENDING]
    peers = {t.peer for t in pending}
    assert len(peers) == 54
    assert len(pending) > 3000
    assert len(pending) / len(peers) > 50, "聚合压缩比应大于 50 倍"
