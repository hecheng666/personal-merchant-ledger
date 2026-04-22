from __future__ import annotations

import io
from datetime import datetime

import pytest
from openpyxl import Workbook

from app.wechat.parser import Category, parse_bill_text
from app.wechat.xlsx import CANONICAL_HEADER, xlsx_to_csv_text


def _workbook_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_standard_header_is_converted() -> None:
    data = _workbook_bytes(
        [
            ["微信支付账单明细"],
            ["导出时间：2026-11-17"],
            CANONICAL_HEADER,
            ["2026-11-02 09:15:22", "二维码收款", "王阿姨", "收款", "收入", "¥120.00", "/", "已存入零钱", "T1", "M1", "/"],
            [datetime(2026, 11, 5, 8, 5, 41), "商户消费", "批发市场", "进货", "支出", 45.5, "零钱", "支付成功", "T2", "M2", "/"],
            ["2026-11-11 14:33:19", "二维码收款", "王阿姨", "退款", "支出", "¥20.00", "/", "已全额退款", "T3", "M3", "/"],
        ]
    )

    result = parse_bill_text(xlsx_to_csv_text(data))

    assert result.stats.parsed == 3
    assert result.stats.skipped == 0
    assert result.stats.unknown_type == 0
    assert result.stats.excluded == 1
    assert str(result.stats.refund_total) == "20.00"
    assert result.transactions[0].category is Category.BUSINESS_INCOME


def test_alias_header_is_mapped() -> None:
    data = _workbook_bytes(
        [
            ["时间", "类型", "对方", "商品", "收/支", "金额", "支付方式", "状态", "订单号", "商户订单号", "备注"],
            ["2026-11-03 11:40:08", "二维码收款", "李先生", "收款", "收入", 35, "/", "已存入零钱", "T9", "M9", "/"],
        ]
    )

    result = parse_bill_text(xlsx_to_csv_text(data))

    assert result.stats.parsed == 1
    assert result.transactions[0].peer == "李先生"
    assert str(result.transactions[0].amount) == "35"


def test_unrecognized_header_reports_found_columns() -> None:
    data = _workbook_bytes([["姓名", "电话"], ["张三", "123"]])

    with pytest.raises(ValueError) as excinfo:
        xlsx_to_csv_text(data)

    assert "没认出" in str(excinfo.value)
    assert "姓名" in str(excinfo.value)


def test_corrupted_file_raises_value_error() -> None:
    with pytest.raises(ValueError):
        xlsx_to_csv_text(b"not a zip file")
