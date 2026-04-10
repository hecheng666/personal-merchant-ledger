"""数据库连接适配层。

本地 CLI 和测试用 SQLite；云托管里配置了 DB_HOST 就自动切 MySQL。
业务代码只写一套 SQL，占位符统一用 `?`，MySQL 下自动转成 `%s`。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path


def mysql_config() -> dict | None:
    """读取云托管 MySQL 的连接配置；没配 DB_HOST 就返回 None。"""
    host = (os.environ.get("DB_HOST") or "").strip()
    if not host:
        return None
    return {
        "host": host,
        "port": int(os.environ.get("DB_PORT") or 3306),
        "user": os.environ.get("DB_USER") or "",
        "password": os.environ.get("DB_PASSWORD") or "",
        "database": os.environ.get("DB_NAME") or "",
        "charset": "utf8mb4",
        "autocommit": False,
    }


def using_mysql() -> bool:
    return mysql_config() is not None


class Database:
    """把 sqlite3 和 pymysql 的差异收在这一层。"""

    def __init__(self, path: str | Path) -> None:
        config = mysql_config()
        self.is_mysql = config is not None
        if config is not None:
            import pymysql  # 只有云端才需要这个依赖

            self._conn = pymysql.connect(**config)
        else:
            file_path = Path(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(file_path)
        self._cursor = self._conn.cursor()

    def _query(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.is_mysql else sql

    def execute(self, sql: str, params: tuple = ()):
        self._cursor.execute(self._query(sql), params)
        return self._cursor

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        self._cursor.executemany(self._query(sql), rows)

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._cursor.close()
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def parse_time(value) -> datetime:
    """SQLite 返回字符串，MySQL 返回 datetime，统一成 datetime。"""
    if isinstance(value, datetime):
        return value
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
