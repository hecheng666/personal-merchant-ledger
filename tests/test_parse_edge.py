"""解析层边界用例。

下面这些是边界场景：这些场景不会出现在正常账单里，
但一旦出现，静默吞掉就是丢钱。
"""

import csv
from decimal import Decimal
from pathlib import Path

import pytest

from app.wechat.parser import parse_bill

HEADER = [
    "交易时间",
    "交易类型",
    "交易对方",
    "商品",
    "收/支",
    "金额(元)",
    "支付方式",
    "当前状态",
    "交易单号",
    "商户单号",
    "备注",
]


def write_bill(path: Path, rows: list[list[str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["微信支付账单明细"] + [""] * 10)
        writer.writerow(["微信昵称：[测试]"] + [""] * 10)
        writer.writerow(HEADER)
        writer.writerows(rows)
    return path


def row(**overrides) -> list[str]:
    base = {
        "time": "2026-10-01 12:00:00",
        "type": "二维码收款",
        "peer": "顾客A",
        "item": "收款方备注:二维码收款",
        "direction": "收入",
        "amount": "¥12.00",
        "method": "零钱",
        "status": "已收钱",
        "tx_no": "1001\t",
        "mch_no": "2001\t",
        "note": "/",
    }
    base.update(overrides)
    return [
        base["time"],
        base["type"],
        base["peer"],
        base["item"],
        base["direction"],
        base["amount"],
        base["method"],
        base["status"],
        base["tx_no"],
        base["mch_no"],
        base["note"],
    ]


def test_header_only_is_not_an_error(tmp_path):
    result = parse_bill(write_bill(tmp_path / "empty.csv", []))
    assert result.transactions == []
    assert result.stats.parsed == 0
    assert result.stats.skipped == 0


def test_short_row_is_counted_not_swallowed(tmp_path):
    path = write_bill(tmp_path / "short.csv", [row(), ["2026-10-02 10:00:00", "转账"]])
    result = parse_bill(path)
    assert result.stats.parsed == 1
    assert result.stats.skipped == 1
    assert result.stats.total_data_lines == 2


def test_empty_amount_is_skipped(tmp_path):
    path = write_bill(tmp_path / "bad-amount.csv", [row(amount="¥"), row(amount="")])
    result = parse_bill(path)
    assert result.stats.parsed == 0
    assert result.stats.skipped == 2


def test_bad_time_is_skipped(tmp_path):
    path = write_bill(tmp_path / "bad-time.csv", [row(time="2026/10/01 12:00:00")])
    result = parse_bill(path)
    assert result.stats.skipped == 1
    assert result.stats.parsed == 0


def test_empty_peer_is_allowed(tmp_path):
    path = write_bill(tmp_path / "empty-peer.csv", [row(peer="")])
    result = parse_bill(path)
    assert result.stats.parsed == 1
    assert result.transactions[0].peer == ""


def test_parse_twice_is_identical(tmp_path):
    path = write_bill(tmp_path / "same.csv", [row(), row(direction="支出", amount="¥3.50")])
    first = parse_bill(path)
    second = parse_bill(path)
    assert first.transactions == second.transactions
    assert first.stats == second.stats


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_bill(tmp_path / "nope.csv")


def test_broken_xlsx_reports_clear_error(tmp_path):
    """xlsx 已支持，但损坏的文件要给出可读提示，而不是抛底层异常。"""
    path = tmp_path / "账单.xlsx"
    path.write_bytes(b"PK\x03\x04")
    with pytest.raises(ValueError, match="xlsx"):
        parse_bill(path)


def test_legacy_xls_is_rejected(tmp_path):
    path = tmp_path / "账单.xls"
    path.write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(ValueError, match="xls"):
        parse_bill(path)


def test_extra_non_empty_columns_are_skipped(tmp_path):
    """多出来的列非空，说明这一行被多余的逗号撑开了，字段已经错位。"""
    broken = row() + ["多余的一列"]
    result = parse_bill(write_bill(tmp_path / "shifted.csv", [broken, row()]))
    assert result.stats.skipped == 1
    assert result.stats.parsed == 1


def test_trailing_empty_columns_are_tolerated(tmp_path):
    """有些导出会在行尾补几个空列，这种不该算坏行。"""
    padded = row() + ["", ""]
    result = parse_bill(write_bill(tmp_path / "padded.csv", [padded]))
    assert result.stats.skipped == 0
    assert result.stats.parsed == 1


def test_fee_and_refund_together(tmp_path):
    """提现手续费与退款同文件时，两个统计互不干扰。"""
    path = write_bill(
        tmp_path / "mixed.csv",
        [
            row(type="零钱提现", direction="/", amount="¥100.10", status="提现已到账", note="服务费¥0.10"),
            row(direction="支出", amount="¥20.00", status="已全额退款"),
        ],
    )
    result = parse_bill(path)
    assert result.stats.fee_total == Decimal("0.10")
    assert result.stats.refund_total == Decimal("20.00")
    assert result.stats.income_total == Decimal("0")
