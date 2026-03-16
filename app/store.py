"""交易持久化。

本地用 SQLite，云托管配置 DB_HOST 后自动切 MySQL。
表里带 `owner` 字段（小程序里就是 openid），每个用户只能看到自己的数据。
金额以字符串存，读出来再转 Decimal，绝不落 float。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.db import Database, parse_time, using_mysql
from app.wechat.parser import (
    Category,
    Direction,
    ParseResult,
    Transaction,
)

DEFAULT_DB = Path("data/ledger.db")

# 统一到秒：账单本身没有微秒，带微秒的 datetime 存进去再读出来会解析失败
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _stamp(moment: datetime) -> str:
    return moment.strftime(_TIME_FORMAT)


SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS transactions (
    owner TEXT NOT NULL,
    id TEXT NOT NULL,
    tx_no TEXT NOT NULL,
    time TEXT NOT NULL,
    month TEXT NOT NULL,
    tx_type TEXT NOT NULL,
    peer TEXT NOT NULL,
    item TEXT NOT NULL,
    direction TEXT NOT NULL,
    amount TEXT NOT NULL,
    method TEXT NOT NULL,
    status TEXT NOT NULL,
    mch_no TEXT NOT NULL,
    note TEXT NOT NULL,
    category TEXT NOT NULL,
    excluded INTEGER NOT NULL,
    excluded_reason TEXT,
    fee TEXT,
    source TEXT NOT NULL,
    PRIMARY KEY (owner, id)
);
"""

SCHEMA_SQLITE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_transactions_owner_month ON transactions(owner, month);
CREATE INDEX IF NOT EXISTS idx_transactions_owner_txno ON transactions(owner, tx_no);
"""

SCHEMA_MYSQL = """
CREATE TABLE IF NOT EXISTS transactions (
    owner VARCHAR(64) NOT NULL,
    id CHAR(40) NOT NULL,
    tx_no VARCHAR(100) NOT NULL,
    time DATETIME NOT NULL,
    month CHAR(7) NOT NULL,
    tx_type VARCHAR(64) NOT NULL,
    peer VARCHAR(120) NOT NULL,
    item TEXT NOT NULL,
    direction VARCHAR(16) NOT NULL,
    amount VARCHAR(32) NOT NULL,
    method VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    mch_no VARCHAR(120) NOT NULL,
    note TEXT NOT NULL,
    category VARCHAR(32) NOT NULL,
    excluded TINYINT NOT NULL,
    excluded_reason VARCHAR(16) NULL,
    fee VARCHAR(32) NULL,
    source VARCHAR(200) NOT NULL,
    PRIMARY KEY (owner, id),
    KEY idx_transactions_owner_month (owner, month)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


@dataclass(slots=True)
class ImportSummary:
    total: int
    inserted: int
    updated: int
    months: list[str]


class TransactionStore:
    """交易表，按 `owner + 复合键` 去重。"""

    def __init__(self, path: str | Path = DEFAULT_DB) -> None:
        self._db = Database(path)
        self.is_mysql = self._db.is_mysql
        if self.is_mysql:
            self._db.executescript(SCHEMA_MYSQL)
        else:
            self._db.executescript(SCHEMA_SQLITE)
            self._migrate_sqlite()
            self._db.executescript(SCHEMA_SQLITE_INDEXES)
        self._db.commit()

    def _migrate_sqlite(self) -> None:
        """老版本本地库没有 owner 列，补上，避免升级后打不开。"""
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(transactions)")}
        if columns and "owner" not in columns:
            self._db.execute(
                "ALTER TABLE transactions ADD COLUMN owner TEXT NOT NULL DEFAULT 'local'"
            )

    @staticmethod
    def _key(tx: Transaction) -> str:
        """幂等键。

        不能只用交易单号：真实账单里存在多个不同交易共用同一单号的情况，
        只用单号去重会静默吃掉行。用「单号 + 时间 + 类型 + 对手方 + 金额 + 方向 + 商户单号」
        做复合键：同一份文件重复导入会命中同一键，不同交易不会互相覆盖。
        """
        parts = (
            tx.tx_no,
            _stamp(tx.time),
            tx.tx_type,
            tx.peer,
            str(tx.amount),
            tx.direction.value,
            tx.mch_no,
        )
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()

    def save(self, result: ParseResult, source: str, owner: str = "local") -> ImportSummary:
        """整份写入。已存在的记录覆盖，不会产生重复行。"""
        before = self.count(owner)
        rows = [
            (
                owner,
                self._key(tx),
                tx.tx_no,
                _stamp(tx.time),
                tx.time.strftime("%Y-%m"),
                tx.tx_type,
                tx.peer,
                tx.item,
                tx.direction.value,
                str(tx.amount),
                tx.method,
                tx.status,
                tx.mch_no,
                tx.note,
                tx.category.value,
                1 if tx.excluded else 0,
                tx.excluded_reason,
                str(tx.fee) if tx.fee is not None else None,
                source,
            )
            for tx in result.transactions
        ]
        prefix = "REPLACE INTO" if self.is_mysql else "INSERT OR REPLACE INTO"
        self._db.executemany(
            prefix
            + """
            transactions(
                owner, id, tx_no, time, month, tx_type, peer, item, direction, amount,
                method, status, mch_no, note, category, excluded,
                excluded_reason, fee, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self._db.commit()

        after = self.count(owner)
        inserted = max(after - before, 0)
        months = sorted({row[4] for row in rows})

        return ImportSummary(
            total=len(rows),
            inserted=inserted,
            updated=len(rows) - inserted,
            months=months,
        )

    def list_month(self, month: str, owner: str = "local") -> list[Transaction]:
        rows = self._db.execute(
            "SELECT time, tx_type, peer, item, direction, amount, method, status, "
            "tx_no, mch_no, note, category, excluded, excluded_reason, fee "
            "FROM transactions WHERE owner = ? AND month = ? ORDER BY time",
            (owner, month),
        ).fetchall()
        return [
            Transaction(
                time=parse_time(row[0]),
                tx_type=row[1],
                peer=row[2],
                item=row[3],
                direction=Direction(row[4]),
                amount=Decimal(row[5]),
                method=row[6],
                status=row[7],
                tx_no=row[8],
                mch_no=row[9],
                note=row[10],
                category=Category(row[11]),
                excluded=bool(row[12]),
                excluded_reason=row[13],
                fee=Decimal(row[14]) if row[14] is not None else None,
            )
            for row in rows
        ]

    def months(self, owner: str = "local") -> list[tuple[str, int]]:
        """返回 [(月份, 笔数)]，按月份升序。"""
        return [
            (row[0], row[1])
            for row in self._db.execute(
                "SELECT month, COUNT(*) FROM transactions WHERE owner = ? "
                "GROUP BY month ORDER BY month",
                (owner,),
            ).fetchall()
        ]

    def count(self, owner: str = "local") -> int:
        return self._db.execute(
            "SELECT COUNT(*) FROM transactions WHERE owner = ?", (owner,)
        ).fetchone()[0]

    def clear(self, owner: str = "local") -> None:
        """清空我的数据。"""
        self._db.execute("DELETE FROM transactions WHERE owner = ?", (owner,))
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "TransactionStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def schema_for(mysql: bool) -> str:
    """给测试用：确认两套 DDL 都包含关键结构。"""
    return SCHEMA_MYSQL if mysql else SCHEMA_SQLITE


def current_schema() -> str:
    return schema_for(using_mysql())
