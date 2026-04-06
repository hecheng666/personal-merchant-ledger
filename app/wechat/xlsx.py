"""把微信账单 xlsx 转成 CSV 文本，复用同一套解析逻辑。

小程序端不能解析二进制 xlsx，所以统一由后端转换：
xlsx → 标准 11 列 CSV 文本 → parse_bill_text。
转换只做「表头对齐」，不做任何金额或分类加工，避免出现第二套口径。
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

from openpyxl import load_workbook

CANONICAL_HEADER = [
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

# 下标对应 CANONICAL_HEADER；不同导出入口的列名不一样，这里做别名归并
_ALIASES: dict[int, tuple[str, ...]] = {
    0: ("交易时间", "时间", "交易日期", "日期", "创建时间"),
    1: ("交易类型", "类型", "业务类型", "交易分类", "支付类型"),
    2: ("交易对方", "对方", "商户名称", "商户", "交易对象", "收款方", "付款方"),
    3: ("商品", "商品说明", "商品名称", "说明", "商品信息"),
    4: ("收/支", "收支", "收支类型", "收支方向", "方向"),
    5: ("金额(元)", "金额（元）", "金额", "交易金额", "收款金额", "订单金额"),
    6: ("支付方式", "付款方式", "收付方式"),
    7: ("当前状态", "状态", "交易状态"),
    8: ("交易单号", "交易订单号", "订单号", "微信支付订单号"),
    9: ("商户单号", "商户订单号", "商家订单号"),
    10: ("备注", "备注信息", "附言"),
}

_HEADER_SCAN_LIMIT = 30
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_REQUIRED_COLUMNS = (0, 4, 5)  # 时间、收/支、金额，缺这三列无法出报表


def _cell_text(value: object) -> str:
    """把单元格转成字符串，日期统一成 CSV 里的时间格式。"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime(_TIME_FORMAT)
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, float):
        text = f"{value:.10f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value).strip()


def _normalize_header(value: object) -> str:
    text = _cell_text(value)
    return text.replace(" ", "").replace("（", "(").replace("）", ")")


def _map_columns(header_row: list[object]) -> dict[int, int]:
    """返回 {标准列下标: 源列下标}，同一标准列只取第一个命中的源列。"""
    mapping: dict[int, int] = {}
    for source_index, cell in enumerate(header_row):
        name = _normalize_header(cell)
        if not name:
            continue
        for target, aliases in _ALIASES.items():
            if target in mapping:
                continue
            if name in {alias.replace("（", "(").replace("）", ")") for alias in aliases}:
                mapping[target] = source_index
                break
    return mapping


def _find_header(rows: list[list[object]]) -> tuple[int, dict[int, int]]:
    best_row: list[object] = []
    best_mapping: dict[int, int] = {}
    for index, row in enumerate(rows[:_HEADER_SCAN_LIMIT]):
        mapping = _map_columns(row)
        if len(mapping) > len(best_mapping):
            best_row, best_mapping = row, mapping
        if all(column in mapping for column in _REQUIRED_COLUMNS):
            return index, mapping
        if not best_mapping and len([cell for cell in row if _cell_text(cell)]) > len(
            [cell for cell in best_row if _cell_text(cell)]
        ):
            best_row = row

    found = " | ".join(_cell_text(cell) for cell in best_row if _cell_text(cell))
    raise ValueError(
        "没认出这份表格的表头，至少要有「时间、收/支、金额」三列。"
        f"识别到的表头是：{found or '（空）'}"
    )


def xlsx_to_csv_text(data: bytes) -> str:
    """把 xlsx 字节流转成标准 11 列 CSV 文本。

    Raises:
        ValueError: 文件损坏、被加密，或表头无法识别。
    """
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl 抛的异常类型很杂，统一转成业务错误
        raise ValueError("无法读取这个 xlsx，请确认文件没有加密也没有损坏") from exc

    try:
        sheet = workbook.active
        rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()

    header_index, mapping = _find_header(rows)

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CANONICAL_HEADER)

    for row in rows[header_index + 1:]:
        values = [_cell_text(cell) for cell in row]
        if not any(values):
            continue
        line = []
        for column in range(len(CANONICAL_HEADER)):
            source = mapping.get(column)
            line.append(values[source] if source is not None and source < len(values) else "")
        writer.writerow(line)

    return buffer.getvalue()
