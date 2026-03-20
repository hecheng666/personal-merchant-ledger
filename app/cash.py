"""现金补录。

本地 SQLite，云托管自动切 MySQL。金额存「分」（整数），不存元。
带 `owner`（小程序里是 openid），不同用户的现金互不影响。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.db import Database
from app.wechat.parser import Direction

DEFAULT_DB = Path("data/ledger.db")

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS cash_entry (
    owner TEXT NOT NULL,
    day TEXT NOT NULL,
    direction TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (owner, day, direction)
);
"""

SCHEMA_MYSQL = """
CREATE TABLE IF NOT EXISTS cash_entry (
    owner VARCHAR(64) NOT NULL,
    day CHAR(10) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    amount_cents BIGINT NOT NULL,
    note VARCHAR(512) NOT NULL DEFAULT '',
    PRIMARY KEY (owner, day, direction)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


@dataclass(slots=True)
class CashEntry:
    day: str            # YYYY-MM-DD
    direction: Direction
    amount_cents: int
    note: str = ""

    @property
    def amount(self) -> Decimal:
        """元，两位小数。"""
        return (Decimal(self.amount_cents) / Decimal(100)).quantize(Decimal("0.01"))


def to_cents(amount: str | int | Decimal) -> int:
    """元 → 分。超过两位小数直接报错，不静默四舍五入。"""
    try:
        value = Decimal(str(amount).replace(",", "").strip())
    except InvalidOperation as exc:
        raise ValueError(f"无法解析金额: {amount!r}") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"现金金额必须为正数: {amount!r}")
    cents = value * 100
    if cents != cents.to_integral_value():
        raise ValueError(f"现金金额最多两位小数: {amount!r}")
    return int(cents)


def normalize_day(day: str) -> str:
    """统一成 YYYY-MM-DD。

    `2026-1-1` 这种不补零的写法 strptime 也认，但直接存会让按月查询漏掉，
    所以入库前必须归一化。
    """
    try:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"日期格式必须是 YYYY-MM-DD: {day!r}") from exc


class CashBook:
    """按「用户 + 日期 + 方向」唯一，同一天重复录入是覆盖。"""

    def __init__(self, path: str | Path = DEFAULT_DB) -> None:
        self._db = Database(path)
        self.is_mysql = self._db.is_mysql
        self._db.executescript(SCHEMA_MYSQL if self.is_mysql else SCHEMA_SQLITE)
        if not self.is_mysql:
            columns = {
                row[1] for row in self._db.execute("PRAGMA table_info(cash_entry)")
            }
            if columns and "owner" not in columns:
                self._db.execute(
                    "ALTER TABLE cash_entry ADD COLUMN owner TEXT NOT NULL DEFAULT 'local'"
                )
        self._db.commit()

    def set(
        self,
        day: str,
        amount: str | int | Decimal,
        direction: Direction = Direction.INCOME,
        note: str = "",
        owner: str = "local",
    ) -> CashEntry:
        day = normalize_day(day)
        if direction is Direction.NEUTRAL:
            raise ValueError("现金补录只有收入或支出两个方向")
        entry = CashEntry(day=day, direction=direction, amount_cents=to_cents(amount), note=note)
        prefix = "REPLACE INTO" if self.is_mysql else "INSERT OR REPLACE INTO"
        self._db.execute(
            prefix
            + " cash_entry(owner, day, direction, amount_cents, note) VALUES (?, ?, ?, ?, ?)",
            (owner, entry.day, entry.direction.value, entry.amount_cents, entry.note),
        )
        self._db.commit()
        return entry

    def get(
        self, day: str, direction: Direction = Direction.INCOME, owner: str = "local"
    ) -> CashEntry | None:
        day = normalize_day(day)
        row = self._db.execute(
            "SELECT amount_cents, note FROM cash_entry "
            "WHERE owner = ? AND day = ? AND direction = ?",
            (owner, day, direction.value),
        ).fetchone()
        if row is None:
            return None
        return CashEntry(day=day, direction=direction, amount_cents=row[0], note=row[1])

    def list_month(self, month: str, owner: str = "local") -> list[CashEntry]:
        rows = self._db.execute(
            "SELECT day, direction, amount_cents, note FROM cash_entry "
            "WHERE owner = ? AND day LIKE ? ORDER BY day",
            (owner, f"{month}-%"),
        ).fetchall()
        return [
            CashEntry(day=row[0], direction=Direction(row[1]), amount_cents=row[2], note=row[3])
            for row in rows
        ]

    def totals(self, month: str, owner: str = "local") -> tuple[Decimal, Decimal]:
        """返回 (现金收入, 现金支出)，单位元。"""
        income = Decimal("0")
        expense = Decimal("0")
        for entry in self.list_month(month, owner):
            if entry.direction is Direction.EXPENSE:
                expense += entry.amount
            else:
                income += entry.amount
        return income, expense

    def clear(self, owner: str = "local") -> None:
        """清空现金记录。「清空我的数据」必须连现金一起清掉。"""
        self._db.execute("DELETE FROM cash_entry WHERE owner = ?", (owner,))
        self._db.commit()

    def months(self, owner: str = "local") -> list[str]:
        """有现金记录的月份，升序。用于「只记了现金、没导入账单」的月份也能出报表。"""
        return [
            row[0]
            for row in self._db.execute(
                "SELECT DISTINCT substr(day, 1, 7) FROM cash_entry "
                "WHERE owner = ? ORDER BY 1",
                (owner,),
            ).fetchall()
        ]

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "CashBook":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def schema_for(mysql: bool) -> str:
    return SCHEMA_MYSQL if mysql else SCHEMA_SQLITE
