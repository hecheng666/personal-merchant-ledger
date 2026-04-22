"""数据库适配层测试：本地 SQLite、云端 MySQL 两套 DDL 都要对。"""

from datetime import datetime

from app import cash, db, store
from app.wechat import memory


def test_default_is_sqlite(monkeypatch):
    monkeypatch.delenv("DB_HOST", raising=False)
    assert db.mysql_config() is None
    assert db.using_mysql() is False


def test_mysql_config_from_env(monkeypatch):
    monkeypatch.setenv("DB_HOST", "10.12.107.105")
    monkeypatch.setenv("DB_PORT", "3306")
    monkeypatch.setenv("DB_USER", "ledger")
    monkeypatch.setenv("DB_PASSWORD", "secret")
    monkeypatch.setenv("DB_NAME", "ledger")
    config = db.mysql_config()
    assert config["host"] == "10.12.107.105"
    assert config["port"] == 3306
    assert config["charset"] == "utf8mb4"
    assert db.using_mysql() is True


def test_mysql_ddl_uses_innodb():
    for module in (store, cash, memory):
        ddl = module.schema_for(True)
        assert "ENGINE=InnoDB" in ddl
        assert "utf8mb4" in ddl
        # MySQL 不允许 TEXT 直接做主键，主键列必须是带长度的 VARCHAR/CHAR
        assert "PRIMARY KEY" in ddl
        assert "TEXT NOT NULL,\n    PRIMARY KEY" not in ddl


def test_sqlite_ddl_stays_simple():
    for module in (store, cash, memory):
        ddl = module.schema_for(False)
        assert "ENGINE=InnoDB" not in ddl
        assert "PRIMARY KEY" in ddl


def test_parse_time_accepts_both_types():
    moment = datetime(2026, 10, 1, 12, 30, 0)
    assert db.parse_time("2026-10-01 12:30:00") == moment
    assert db.parse_time(moment) == moment
