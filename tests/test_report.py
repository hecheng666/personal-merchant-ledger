"""现金、存储与报表口径的测试。

报表口径是这个项目最容易算错的地方，所以用一笔一笔构造的数据来验证，
不依赖解析结果，失败时能一眼看出是哪一项加错了。
"""

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.cash import CashBook
from app.report import build_month_report, export_csv, render_html, render_markdown
from app.store import TransactionStore
from app.wechat.parser import Category, Direction, ParseResult, Transaction, parse_bill

ROOT = Path(__file__).parent.parent
REAL = ROOT / "fixtures" / "wechat-sample-real.csv"


def make_tx(
    amount: str,
    category: Category,
    direction: Direction,
    day: int = 1,
    peer: str = "顾客",
    tx_type: str = "二维码收款",
    excluded_reason: str | None = None,
) -> Transaction:
    return Transaction(
        time=datetime(2026, 10, day, 12, 0, 0),
        tx_type=tx_type,
        peer=peer,
        item="/",
        direction=direction,
        amount=Decimal(amount),
        method="零钱",
        status="已全额退款" if excluded_reason == "refund" else "支付成功",
        tx_no=f"{day}-{amount}-{category.value}",
        mch_no="m",
        note="/",
        category=category,
        excluded=excluded_reason is not None,
        excluded_reason=excluded_reason,
    )


# ------------------------------------------------------------------ 现金


def test_cash_overwrites_same_day(tmp_path):
    with CashBook(tmp_path / "ledger.db") as cash:
        cash.set("2026-10-01", "380")
        cash.set("2026-10-01", "420.50")
        assert cash.get("2026-10-01").amount == Decimal("420.50")


def test_cash_supports_expense_and_totals(tmp_path):
    with CashBook(tmp_path / "ledger.db") as cash:
        cash.set("2026-10-01", "380")
        cash.set("2026-10-02", "120", Direction.EXPENSE)
        assert cash.totals("2026-10") == (Decimal("380.00"), Decimal("120.00"))


def test_cash_rejects_bad_input(tmp_path):
    with CashBook(tmp_path / "ledger.db") as cash:
        with pytest.raises(ValueError):
            cash.set("2026/10/01", "100")
        with pytest.raises(ValueError):
            cash.set("2026-10-01", "-5")
        with pytest.raises(ValueError):
            cash.set("2026-10-01", "1.234")


# ------------------------------------------------------------------ 存储幂等


def test_store_import_is_idempotent(tmp_path):
    store = TransactionStore(tmp_path / "ledger.db")
    result = parse_bill(REAL)
    first = store.save(result, "real.csv")
    second = store.save(result, "real.csv")
    assert first.inserted == 27
    assert second.inserted == 0
    assert second.updated == 27
    assert store.count() == 27
    store.close()


def test_store_keeps_rows_sharing_tx_no(tmp_path):
    """样例里有 8 笔共用同一交易单号，不能被去重吃掉。"""
    store = TransactionStore(tmp_path / "ledger.db")
    store.save(parse_bill(REAL), "real.csv")
    months = dict(store.months())
    assert sum(months.values()) == 27
    store.close()


# ------------------------------------------------------------------ 报表口径


def test_report_revenue_excludes_private_income(tmp_path):
    transactions = [
        make_tx("100.00", Category.BUSINESS_INCOME, Direction.INCOME),
        make_tx("10.00", Category.BUSINESS_INCOME, Direction.EXPENSE, excluded_reason="refund"),
        make_tx("5.00", Category.PRIVATE, Direction.INCOME, peer="妈妈"),
        make_tx("20.00", Category.PENDING, Direction.INCOME, peer="老王", tx_type="转账"),
        make_tx("40.00", Category.BUSINESS_EXPENSE, Direction.EXPENSE, peer="批发市场", tx_type="转账"),
    ]
    with CashBook(tmp_path / "ledger.db") as cash:
        cash.set("2026-10-01", "30")
        cash.set("2026-10-02", "8", Direction.EXPENSE)
        report = build_month_report(
            "2026-10", transactions, cash.list_month("2026-10"), known={}
        )

    assert report.gross_business == Decimal("100.00")
    assert report.refund == Decimal("10.00")
    assert report.wechat_net == Decimal("90.00")
    assert report.cash_income == Decimal("30.00")
    assert report.revenue_total == Decimal("120.00")
    assert report.balance == Decimal("72.00")
    assert report.pending_count == 1
    assert len(report.pending) == 1
    assert report.pending[0].peer == "老王"


def test_report_applies_memory_to_pending(tmp_path):
    transactions = [
        make_tx("40.00", Category.BUSINESS_EXPENSE, Direction.EXPENSE, peer="批发市场", tx_type="转账"),
        make_tx("100.00", Category.BUSINESS_INCOME, Direction.INCOME),
    ]
    tx = make_tx("60.00", Category.PENDING, Direction.EXPENSE, peer="批发市场", tx_type="转账")
    transactions.append(tx)
    # 记忆库说「批发市场」是进货，这笔待确认应该消失并计入进货支出
    report = build_month_report("2026-10", transactions, [], known={"批发市场": "business_expense"})
    assert report.pending == []
    assert report.purchase_expense == Decimal("100.00")
    assert report.balance == Decimal("0.00")


def test_report_daily_has_full_month():
    report = build_month_report("2026-10", [], [], known={})
    assert len(report.daily) == 31
    assert report.daily[0].day == "2026-10-01"
    assert report.daily[-1].day == "2026-10-31"


def test_render_outputs(tmp_path):
    transactions = [make_tx("100.00", Category.BUSINESS_INCOME, Direction.INCOME)]
    report = build_month_report("2026-10", transactions, [], known={})
    markdown = render_markdown(report)
    assert "营业额合计" in markdown and "100.00" in markdown

    page = render_html(report)
    assert "营业额合计" in page
    # 报表必须自包含：不能有外链资源（SVG 的 xmlns 不算资源引用）
    assert 'src="http' not in page and "<link" not in page and "@import" not in page

    paths = export_csv(report, transactions, tmp_path / "out", known={})
    assert len(paths) == 5
    assert all(path.exists() for path in paths)
    names = {path.name for path in paths}
    assert "2026-10-营业收款.csv" in names
    assert "2026-10-未识别.csv" in names
    assert "2026-10-退款与关闭.csv" in names
    assert (tmp_path / "out" / "2026-10-营业收款.csv").read_text(encoding="utf-8-sig").count("\n") >= 2


def test_export_escapes_formula_injection(tmp_path):
    """对手方昵称是别人能改的，以 = 开头会在 Excel 里变公式。"""
    transactions = [
        make_tx("10.00", Category.BUSINESS_INCOME, Direction.INCOME, peer="=cmd|' /C calc'!A0")
    ]
    report = build_month_report("2026-10", transactions, [], known={})
    export_csv(report, transactions, tmp_path / "out", known={})
    text = (tmp_path / "out" / "2026-10-营业收款.csv").read_text(encoding="utf-8-sig")
    assert "'=cmd" in text


def test_cash_only_month_can_report(tmp_path):
    """只记了现金、没导入账单的月份也要能出报表。"""
    from app.cash import CashBook

    with CashBook(tmp_path / "ledger.db") as cash:
        cash.set("2026-09-01", "200")
        assert cash.months() == ["2026-09"]


def test_cash_normalizes_unpadded_date(tmp_path):
    """2026-1-1 这种写法要归一化，否则按月查询会漏掉。"""
    with CashBook(tmp_path / "ledger.db") as cash:
        cash.set("2026-1-1", "100")
        assert cash.list_month("2026-01")[0].day == "2026-01-01"
        assert cash.get("2026-01-01").amount == Decimal("100.00")


def test_cash_clear_wipes_entries(tmp_path):
    with CashBook(tmp_path / "ledger.db") as cash:
        cash.set("2026-10-01", "380")
        cash.clear()
        assert cash.list_month("2026-10") == []


def test_business_income_with_wrong_direction_goes_to_unknown_bucket(tmp_path):
    """营业类型却是支出、状态又没标退款：不猜，放进未识别让人看。"""
    transactions = [
        make_tx("100.00", Category.BUSINESS_INCOME, Direction.INCOME),
        make_tx("20.00", Category.BUSINESS_INCOME, Direction.EXPENSE),
    ]
    report = build_month_report("2026-10", transactions, [], known={})
    export_csv(report, transactions, tmp_path / "out", known={})
    text = (tmp_path / "out" / "2026-10-未识别.csv").read_text(encoding="utf-8-sig")
    assert "20.00" in text


def test_report_counts_private_and_flags_unknown():
    transactions = [
        make_tx("100.00", Category.BUSINESS_INCOME, Direction.INCOME),
        make_tx("30.00", Category.PRIVATE, Direction.EXPENSE, tx_type="微信红包"),
        make_tx("5.00", Category.UNKNOWN, Direction.EXPENSE, tx_type="新类型"),
    ]
    report = build_month_report("2026-10", transactions, [], known={})
    assert report.private_count == 1
    assert report.unknown_count == 1
    page = render_html(report)
    # 有未识别时不能显示「账已对齐」
    assert "未识别" in page
    assert "私人往来 1 笔" in page


def test_store_roundtrip_with_microseconds(tmp_path):
    """带微秒的时间存进去再读出来不能炸。"""
    from app.store import TransactionStore

    tx = make_tx("10.00", Category.BUSINESS_INCOME, Direction.INCOME)
    tx.time = tx.time.replace(microsecond=123456)
    store = TransactionStore(tmp_path / "ledger.db")
    store.save(ParseResult([tx]), "x.csv")
    loaded = store.list_month("2026-10")
    assert len(loaded) == 1
    assert loaded[0].time.microsecond == 0
    store.close()


def test_private_refund_does_not_touch_revenue():
    """买衣服退款和生意无关，不能拿来减营业额。"""
    transactions = [
        make_tx("100.00", Category.BUSINESS_INCOME, Direction.INCOME),
        make_tx(
            "30.00",
            Category.PRIVATE,
            Direction.EXPENSE,
            tx_type="商户消费",
            excluded_reason="refund",
        ),
    ]
    report = build_month_report("2026-10", transactions, [], known={})
    assert report.refund == Decimal("0")
    assert report.wechat_net == Decimal("100.00")


def test_daily_chart_sums_to_wechat_net():
    """每日柱状图加起来必须等于微信净额，否则图和数据对不上。"""
    transactions = [
        make_tx("100.00", Category.BUSINESS_INCOME, Direction.INCOME, day=2),
        make_tx("40.00", Category.BUSINESS_INCOME, Direction.INCOME, day=5),
        make_tx(
            "10.00",
            Category.BUSINESS_INCOME,
            Direction.EXPENSE,
            day=5,
            excluded_reason="refund",
        ),
    ]
    report = build_month_report("2026-10", transactions, [], known={})
    assert sum(point.wechat for point in report.daily) == report.wechat_net
    assert report.daily[4].wechat == Decimal("30.00")


def test_export_keeps_unknown_and_excluded(tmp_path):
    """未识别和退款不能被悄悄塞进私人往来或直接丢掉。"""
    transactions = [
        make_tx("10.00", Category.BUSINESS_INCOME, Direction.INCOME),
        make_tx("5.00", Category.UNKNOWN, Direction.EXPENSE, tx_type="deg-未知"),
        make_tx(
            "8.00",
            Category.BUSINESS_INCOME,
            Direction.EXPENSE,
            excluded_reason="refund",
        ),
    ]
    report = build_month_report("2026-10", transactions, [], known={})
    export_csv(report, transactions, tmp_path / "out", known={})
    unknown = (tmp_path / "out" / "2026-10-未识别.csv").read_text(encoding="utf-8-sig")
    excluded = (tmp_path / "out" / "2026-10-退款与关闭.csv").read_text(encoding="utf-8-sig")
    assert "deg-未知" in unknown
    assert "退款" in excluded
