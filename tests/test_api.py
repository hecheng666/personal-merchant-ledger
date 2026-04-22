"""API 层测试：鉴权、数据隔离、导入预览。

API 是小程序唯一的入口，这里用 FastAPI 的 TestClient 真正走一遍 HTTP。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import app

ROOT = Path(__file__).parent.parent
REAL = ROOT / "fixtures" / "wechat-sample-real.csv"
CONTENT = REAL.read_text(encoding="utf-8-sig")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("MEMORY_DB", str(tmp_path / "memory.db"))
    monkeypatch.setenv("ALLOW_LOCAL_AUTH", "1")
    return TestClient(app)


def test_health_needs_no_auth(client):
    assert client.get("/api/health").json()["ok"] is True


def test_spoofed_identity_is_rejected(monkeypatch):
    """没开本地调试开关时，公网请求伪造 X-OPENID 必须被拒。"""
    monkeypatch.delenv("ALLOW_LOCAL_AUTH", raising=False)
    spoof = TestClient(app)
    response = spoof.post(
        "/api/import",
        json={"filename": "a.csv", "content": CONTENT},
        headers={"X-OPENID": "victim"},
    )
    assert response.status_code == 401


def test_import_requires_identity(monkeypatch, tmp_path):
    monkeypatch.delenv("ALLOW_LOCAL_AUTH", raising=False)
    monkeypatch.setenv("LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("MEMORY_DB", str(tmp_path / "memory.db"))
    anonymous = TestClient(app)
    response = anonymous.post("/api/import", json={"filename": "a.csv", "content": CONTENT})
    assert response.status_code == 401


def test_users_are_isolated(client):
    alice = {"X-OPENID": "alice"}
    bob = {"X-OPENID": "bob"}

    response = client.post(
        "/api/import", json={"filename": "a.csv", "content": CONTENT}, headers=alice
    )
    assert response.status_code == 200
    assert response.json()["saved"]["inserted"] == 27

    assert client.get("/api/report?month=2019-09", headers=alice).status_code == 200
    # bob 没导入过，看不到 alice 的数据
    assert client.get("/api/report?month=2019-09", headers=bob).status_code == 404
    assert client.get("/api/months", headers=bob).json()["months"] == []


def test_dry_run_does_not_save(client):
    headers = {"X-OPENID": "alice"}
    response = client.post(
        "/api/import",
        json={"filename": "a.csv", "content": CONTENT, "dry_run": True},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert client.get("/api/months", headers=headers).json()["months"] == []


def test_confirm_and_report(client):
    headers = {"X-OPENID": "alice"}
    client.post("/api/import", json={"filename": "a.csv", "content": CONTENT}, headers=headers)

    pending = client.get("/api/pending?month=2021-01", headers=headers).json()
    peer = pending["items"][0]["peer"]

    response = client.post(
        "/api/confirm", json={"peer": peer, "category": "private"}, headers=headers
    )
    assert response.status_code == 200
    assert client.get("/api/pending?month=2021-01", headers=headers).json()["total"] == 0


def test_bad_category_is_rejected(client):
    headers = {"X-OPENID": "alice"}
    response = client.post(
        "/api/confirm", json={"peer": "x", "category": "unknown"}, headers=headers
    )
    assert response.status_code == 400


def test_cash_and_clear(client):
    headers = {"X-OPENID": "alice"}
    assert client.post("/api/cash", json={"day": "2026-1-5", "amount": "380"}, headers=headers).json()["day"] == "2026-01-05"
    assert client.get("/api/cash?month=2026-01", headers=headers).json()["income"] == "380.00"
    assert client.delete("/api/data", headers=headers).status_code == 200
    assert client.get("/api/cash?month=2026-01", headers=headers).json()["items"] == []


def test_import_xlsx_via_base64(client):
    """小程序端把文件按 base64 传上来，后端要能直接吃 xlsx。"""
    import base64
    import io

    from openpyxl import Workbook

    from app.wechat.xlsx import CANONICAL_HEADER

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(CANONICAL_HEADER)
    sheet.append(["2026-11-02 09:15:22", "二维码收款", "王阿姨", "收款", "收入", "¥120.00", "/", "已存入零钱", "T1", "M1", "/"])
    sheet.append(["2026-11-05 08:05:41", "商户消费", "批发市场", "进货", "支出", "¥45.50", "零钱", "支付成功", "T2", "M2", "/"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    headers = {"X-OPENID": "alice"}
    response = client.post(
        "/api/import",
        json={
            "filename": "账单.xlsx",
            "content_base64": base64.b64encode(buffer.getvalue()).decode(),
        },
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["saved"]["inserted"] == 2
    assert client.get("/api/months", headers=headers).json()["months"] == ["2026-11"]


def test_import_without_content_is_rejected(client):
    response = client.post(
        "/api/import", json={"filename": "a.csv"}, headers={"X-OPENID": "alice"}
    )
    assert response.status_code == 400
