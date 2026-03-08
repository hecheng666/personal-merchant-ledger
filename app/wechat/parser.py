"""微信账单解析器。

把微信支付导出的 CSV 账单变成结构化交易 + 可直接展示的解析统计。
格式事实见 docs/bill-format.md，验收标准见 tests/test_parse_wechat.py。

设计要点：
- 金额全程 Decimal，绝不经过 float；
- 分类解决「这是什么」，剔除解决「算不算数」，两件事分开；
- 解析失败的行计数后报告，不静默丢弃。
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path


class Direction(str, Enum):
    """收/支方向。注意微信有第三态：中性交易。"""

    INCOME = "income"
    EXPENSE = "expense"
    NEUTRAL = "neutral"


class Category(str, Enum):
    """三态分类 + 两个兜底桶。

    BUSINESS_EXPENSE 不由规则产出，只由「对手方记忆库」在人工确认后写入。
    """

    BUSINESS_INCOME = "business_income"    # 二维码收款 / 群收款 / 收入方向的赞赏码
    BUSINESS_EXPENSE = "business_expense"  # 人工确认后的进货转账
    PRIVATE = "private"                    # 红包 / 亲属卡 / 提现 / 理财
    PENDING = "pending"                    # 无法判定，需人工确认（主要是「转账」）
    UNKNOWN = "unknown"                    # 未知交易类型，不可丢弃也不可猜测


@dataclass(slots=True)
class Transaction:
    time: datetime
    tx_type: str
    peer: str
    item: str
    direction: Direction
    amount: Decimal          # 必须用 Decimal，不能用 float
    method: str
    status: str
    tx_no: str               # 已 strip，不含 \t
    mch_no: str
    note: str
    category: Category
    excluded: bool = False           # 退款 / 已关闭 → 不计入营业额
    excluded_reason: str | None = None
    fee: Decimal | None = None       # 从备注里抠出的「服务费¥x.xx」


@dataclass(slots=True)
class ParseStats:
    """解析统计。这份统计要直接展示给用户，他才会信任账目。"""

    total_data_lines: int = 0     # 表头之后的所有非空行
    parsed: int = 0               # 成功解析
    skipped: int = 0              # 列数不足 / 时间或金额无法解析，被跳过
    unknown_type: int = 0         # 未知交易类型条数
    excluded: int = 0             # 退款或已关闭条数
    income_total: Decimal = Decimal("0")      # 收入方向、未剔除的金额合计
    business_income_total: Decimal = Decimal("0")  # 营业收款（真正能进营业额的那部分）
    refund_total: Decimal = Decimal("0")      # 退款金额合计（方向多为支出）
    closed_total: Decimal = Decimal("0")      # 已关闭交易金额合计（钱没动，单独统计）
    expense_total: Decimal = Decimal("0")
    fee_total: Decimal = Decimal("0")

    @property
    def net_revenue(self) -> Decimal:
        """全部收入净额 = 收入 − 退款。

        注意：这不是营业额。它的基数包含红包、赞赏等私人收入，
        经营指标要用 `business_income_total - refund_total`。
        """
        return self.income_total - self.refund_total

    @property
    def business_net(self) -> Decimal:
        """微信渠道营业净额 = 营业收款 − 退款，可直接进营业额。"""
        return self.business_income_total - self.refund_total


@dataclass(slots=True)
class ParseResult:
    transactions: list[Transaction] = field(default_factory=list)
    stats: ParseStats = field(default_factory=ParseStats)


# 模块级常量：正则只编译一次，3 万行解析不能每行重编译
_FEE_RE = re.compile(r"服务费\s*[¥￥]\s*([\d.]+)")

# 规则 4：无论方向如何都是私人往来
_PRIVATE_PREFIXES = (
    "零钱提现",
    "零钱充值",
    "转入零钱通",
    "零钱通转出",
    "购买理财通",
    "信用卡还款",
    "分分捐",
    "亲属卡交易",
    "微信红包",
)

# 规则 5：支付类类型，任意方向都归私人
_PAYMENT_PREFIXES = ("商户消费", "扫二维码付款")

_REQUIRED_COLUMNS = 11
_HEADER_SCAN_LIMIT = 30
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm"}


def clean_amount(raw: str) -> Decimal:
    """清洗金额字符串。

    处理 '¥28.16'、'¥50.0'、'¥1,234.56'、全角￥、前后空白与 TAB。
    返回 Decimal，不是 float；空串或无法解析抛 ValueError（不能返回 0，
    否则错误会静默流入统计）。
    """
    text = (raw or "").replace("\t", "").strip()
    for symbol in ("¥", "￥", ",", "，"):
        text = text.replace(symbol, "")
    text = text.strip()
    if not text:
        raise ValueError(f"金额为空: {raw!r}")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"无法解析金额: {raw!r}") from exc
    if not value.is_finite():
        raise ValueError(f"金额不是有限数: {raw!r}")
    return value


def parse_direction(raw: str) -> Direction:
    """'收入' -> INCOME，'支出' -> EXPENSE，其余（多为 '/'）-> NEUTRAL。

    第三分支不抛异常：'/' 表示中性交易，是正常数据。
    """
    text = (raw or "").strip()
    if text == "收入":
        return Direction.INCOME
    if text == "支出":
        return Direction.EXPENSE
    return Direction.NEUTRAL


def find_header_row(rows: list[list[str]]) -> int:
    """返回表头所在行的 0-based 索引。

    规则：扫描前 30 行，找到「第一列 strip 后等于 '交易时间'」且列数 ≥ 6 的行。
    不要硬编码 16 —— 微信调整过说明文字的行数。

    Raises:
        ValueError: 前 30 行内找不到表头（不是微信账单，或格式已变更）。
    """
    for index, row in enumerate(rows[:_HEADER_SCAN_LIMIT]):
        if len(row) >= 6 and row[0].strip() == "交易时间":
            return index
    raise ValueError(
        f"前 {_HEADER_SCAN_LIMIT} 行内未找到表头（首列为「交易时间」），"
        "请确认这是微信支付导出的账单 CSV"
    )


def classify(tx_type: str, direction: Direction, note: str = "", item: str = "") -> Category:
    """把交易类型映射到 Category，前缀匹配，命中即返回。

    判定顺序（与 docs/impl-spec.md 第 5 节一致）：
    1. 二维码收款 / 群收款（任意方向）→ 营业收款。退款行也是这个类型，
       由 excluded 冲销，不能掉进 UNKNOWN。
    2. 收入方向的赞赏码 → 营业收款；支出方向是打赏别人。
    3. 转账（任意方向）→ 待确认，可能是顾客、亲友、退货返款。
    4. 提现 / 充值 / 理财 / 红包等 → 私人。
    5. 商户消费 / 扫二维码付款（任意方向）→ 私人；赞赏码（非收入）→ 私人。
    6. 「其他」→ 待确认，微信的兜底分类可能藏着营业款。
    7. 其余 → UNKNOWN，绝不猜测。

    note / item 暂未参与判定，保留参数是为了后续规则扩展时不必改调用方。
    """
    kind = (tx_type or "").strip()

    if kind.startswith(("二维码收款", "群收款")):
        return Category.BUSINESS_INCOME
    if direction is Direction.INCOME and kind.startswith("赞赏码"):
        return Category.BUSINESS_INCOME
    if kind.startswith("转账"):
        return Category.PENDING
    if kind.startswith(_PRIVATE_PREFIXES):
        return Category.PRIVATE
    if kind.startswith(_PAYMENT_PREFIXES):
        return Category.PRIVATE
    if kind.startswith("赞赏码"):
        return Category.PRIVATE
    if kind == "其他":
        return Category.PENDING
    return Category.UNKNOWN


def extract_fee(note: str) -> Decimal | None:
    """从备注里抠出服务费，如 '服务费¥0.10' -> Decimal('0.10')。没有则 None。"""
    match = _FEE_RE.search(note or "")
    if not match:
        return None
    try:
        return Decimal(match.group(1))
    except InvalidOperation:
        return None


def _clean_field(raw: str) -> str:
    """字段清洗：去首尾空白与内嵌 TAB（实测 33 个字段尾部带 TAB）。"""
    return raw.strip().replace("\t", "")


def _exclusion_reason(status: str) -> str | None:
    """退款 / 已关闭的判定。剔除是标记不是删除。"""
    if "退款" in status or "退还" in status:
        return "refund"
    if "关闭" in status:
        return "closed"
    return None


def _row_is_usable(row: list[str]) -> bool:
    """列数必须正好 11，或者多出来的列全是空（有些导出会在末尾补空列）。

    列数多于 11 且非空，说明这一行被多余的逗号撑开了，后面所有字段都会错位。
    宁可按「跳过」计数，也不能把错位的数据当成账。
    """
    if len(row) < _REQUIRED_COLUMNS:
        return False
    if len(row) > _REQUIRED_COLUMNS:
        return all(not cell.strip() for cell in row[_REQUIRED_COLUMNS:])
    return True


def parse_bill(path: str | Path) -> ParseResult:
    """解析一份微信账单 CSV。

    步骤：utf-8-sig 读文件 → 扫描定位表头 → 逐行清洗构造 → 汇总统计。
    列数不足、时间或金额无法解析的行计入 stats.skipped 并继续，
    退款 / 已关闭只置 excluded=True，不从列表里删除。
    """
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix in _EXCEL_SUFFIXES:
        if suffix == ".xls":
            raise ValueError(f"不支持旧版 .xls，请另存为 .xlsx 或 CSV 后再试: {file_path.name}")
        from app.wechat.xlsx import xlsx_to_csv_text

        return parse_bill_text(xlsx_to_csv_text(file_path.read_bytes()))

    return parse_bill_text(file_path.read_text(encoding="utf-8-sig"))


def parse_bill_text(text: str) -> ParseResult:
    """解析账单文本。小程序上传的是文件内容而不是磁盘路径，走这个入口。"""
    rows = list(csv.reader(io.StringIO(text)))
    header_index = find_header_row(rows)

    result = ParseResult()
    stats = result.stats
    transactions = result.transactions

    for row in rows[header_index + 1:]:
        if not row or not row[0].strip():
            continue  # 空行不算数据行，也不计入 total_data_lines
        stats.total_data_lines += 1

        if not _row_is_usable(row):
            stats.skipped += 1
            continue

        fields = [_clean_field(value) for value in row[:_REQUIRED_COLUMNS]]
        try:
            moment = datetime.strptime(fields[0], _TIME_FORMAT)
        except ValueError:
            stats.skipped += 1
            continue
        try:
            amount = clean_amount(fields[5])
        except ValueError:
            stats.skipped += 1
            continue

        tx_type = fields[1]
        note = fields[10]
        direction = parse_direction(fields[4])
        category = classify(tx_type, direction, note, fields[3])
        reason = _exclusion_reason(fields[7])

        transactions.append(
            Transaction(
                time=moment,
                tx_type=tx_type,
                peer=fields[2],
                item=fields[3],
                direction=direction,
                amount=amount,
                method=fields[6],
                status=fields[7],
                tx_no=fields[8],
                mch_no=fields[9],
                note=note,
                category=category,
                excluded=reason is not None,
                excluded_reason=reason,
                fee=extract_fee(note),
            )
        )

        stats.parsed += 1
        if category is Category.UNKNOWN:
            stats.unknown_type += 1
        if reason == "refund":
            stats.excluded += 1
            stats.refund_total += amount
            continue
        if reason == "closed":
            stats.excluded += 1
            stats.closed_total += amount
            continue

        if direction is Direction.INCOME:
            stats.income_total += amount
            if category is Category.BUSINESS_INCOME:
                stats.business_income_total += amount
        elif direction is Direction.EXPENSE:
            stats.expense_total += amount

    for transaction in transactions:
        if transaction.fee is not None:
            stats.fee_total += transaction.fee

    return result
