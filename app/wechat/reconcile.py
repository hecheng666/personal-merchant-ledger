"""待确认交易的聚合。

这是产品能活下来的关键：3,191 笔待确认转账按对手方聚合后只有 54 项，
用户要点 54 次，不是 3191 次。逐笔确认没人受得了。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping

from app.wechat.parser import Category, Direction, ParseResult

ZERO = Decimal("0")


@dataclass(slots=True)
class PendingPeer:
    """一个待确认对手方的汇总。

    转账可能是收入（顾客付货款）也可能是支出（给供货商打款），
    所以收入、支出分开记。`total` 是两边相加，只用于排序，不能当「这个人往来多少钱」看。
    """

    peer: str
    count: int
    total: Decimal
    first_time: datetime
    known_category: str | None = None
    income_total: Decimal = ZERO
    expense_total: Decimal = ZERO

    @property
    def mixed(self) -> bool:
        """收入和支出都有的对手方，展示时必须分开写，否则金额没有意义。"""
        return self.income_total > ZERO and self.expense_total > ZERO

    def amount_text(self) -> str:
        """给人看的金额：只有一个方向就直接写，两个方向都有就分开写。"""
        if self.income_total == ZERO and self.expense_total == ZERO:
            return "—"
        if self.mixed:
            return f"收 {self.income_total:,.2f} / 支 {self.expense_total:,.2f}"
        if self.expense_total > ZERO:
            return f"支 {self.expense_total:,.2f}"
        return f"收 {self.income_total:,.2f}"


def aggregate_pending(
    result: ParseResult, known: Mapping[str, str] | None = None
) -> list[PendingPeer]:
    """按对手方聚合待确认交易，金额从大到小排序。

    只统计未剔除的 PENDING：退款 / 已关闭的转账不该再问用户。
    已在记忆库里的对手方仍然返回（`known_category` 有值），
    调用方可以据此把它们从「待你确认」里过滤掉。
    """
    known_map = dict(known or {})
    buckets: dict[str, PendingPeer] = {}

    for tx in result.transactions:
        if tx.category is not Category.PENDING or tx.excluded:
            continue
        bucket = buckets.get(tx.peer)
        if bucket is None:
            bucket = PendingPeer(
                peer=tx.peer,
                count=0,
                total=ZERO,
                first_time=tx.time,
                known_category=known_map.get(tx.peer),
            )
            buckets[tx.peer] = bucket

        bucket.count += 1
        bucket.total += tx.amount
        if tx.direction is Direction.INCOME:
            bucket.income_total += tx.amount
        elif tx.direction is Direction.EXPENSE:
            bucket.expense_total += tx.amount
        if tx.time < bucket.first_time:
            bucket.first_time = tx.time

    return sorted(buckets.values(), key=lambda item: (item.total, item.count), reverse=True)


def unresolved(pending: list[PendingPeer]) -> list[PendingPeer]:
    """过滤掉已确认过的对手方，只留还需要用户点的。"""
    return [item for item in pending if item.known_category is None]
