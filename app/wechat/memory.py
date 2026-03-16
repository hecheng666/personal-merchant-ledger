"""对手方记忆库。

本地 SQLite，云托管自动切 MySQL。带 `owner`（openid），不同用户互不影响。
产品原则：不确定就让人定，然后记住。一个陌生昵称只问一次。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.db import Database

DEFAULT_DB = Path("data/memory.db")

# 人工确认只允许这三个落点：营业收款 / 进货支出 / 私人往来
VALID_CATEGORIES = ("business_income", "business_expense", "private")

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS peer_category (
    owner TEXT NOT NULL,
    peer TEXT NOT NULL,
    category TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (owner, peer)
);
"""

SCHEMA_MYSQL = """
CREATE TABLE IF NOT EXISTS peer_category (
    owner VARCHAR(64) NOT NULL,
    peer VARCHAR(120) NOT NULL,
    category VARCHAR(32) NOT NULL,
    updated_at VARCHAR(32) NOT NULL,
    PRIMARY KEY (owner, peer)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


class PeerMemory:
    """对手方 → 分类的持久化映射。"""

    def __init__(self, path: str | Path = DEFAULT_DB) -> None:
        self._db = Database(path)
        self.is_mysql = self._db.is_mysql
        self._db.executescript(SCHEMA_MYSQL if self.is_mysql else SCHEMA_SQLITE)
        if not self.is_mysql:
            columns = {
                row[1] for row in self._db.execute("PRAGMA table_info(peer_category)")
            }
            if columns and "owner" not in columns:
                self._db.execute(
                    "ALTER TABLE peer_category ADD COLUMN owner TEXT NOT NULL DEFAULT 'local'"
                )
        self._db.commit()

    def get(self, peer: str, owner: str = "local") -> str | None:
        row = self._db.execute(
            "SELECT category FROM peer_category WHERE owner = ? AND peer = ?",
            (owner, peer),
        ).fetchone()
        return row[0] if row else None

    def set(self, peer: str, category: str, owner: str = "local") -> None:
        """写入或覆盖。同一个对手方重复确认是覆盖，不是报错。"""
        if category not in VALID_CATEGORIES:
            raise ValueError(f"不支持的分类: {category!r}，可选 {VALID_CATEGORIES}")
        if not peer:
            raise ValueError("对手方不能为空")
        prefix = "REPLACE INTO" if self.is_mysql else "INSERT OR REPLACE INTO"
        self._db.execute(
            prefix
            + " peer_category(owner, peer, category, updated_at) VALUES (?, ?, ?, ?)",
            (owner, peer, category, datetime.now().isoformat(timespec="seconds")),
        )
        self._db.commit()

    def all(self, owner: str = "local") -> dict[str, str]:
        rows = self._db.execute(
            "SELECT peer, category FROM peer_category WHERE owner = ?", (owner,)
        ).fetchall()
        return dict(rows)

    def clear(self, owner: str = "local") -> None:
        self._db.execute("DELETE FROM peer_category WHERE owner = ?", (owner,))
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "PeerMemory":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def schema_for(mysql: bool) -> str:
    return SCHEMA_MYSQL if mysql else SCHEMA_SQLITE
