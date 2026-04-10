"""小程序后端接口（FastAPI）。

部署在微信云托管里，复用 app/wechat/parser.py 的解析逻辑。
小程序用 wx.cloud.callContainer 调用，平台会自动注入 X-WX-OPENID，
所有数据按 openid 隔离，不同用户互不可见。

本地调试：
    set ALLOW_LOCAL_AUTH=1
    set LEDGER_DB=data/api.db
    set MEMORY_DB=data/api_memory.db
    uvicorn app.api:app --reload --port 8000
    curl -H "X-OPENID: local" http://127.0.0.1:8000/api/health
"""

from __future__ import annotations

import base64
import binascii
import io
import os
import zipfile
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app.cash import CashBook
from app.report import build_month_report
from app.store import TransactionStore
from app.wechat.memory import VALID_CATEGORIES, PeerMemory
from app.wechat.parser import Direction, ParseResult, parse_bill_text
from app.wechat.reconcile import aggregate_pending, unresolved
from app.wechat.xlsx import xlsx_to_csv_text

# 单次导入上限，防止有人传超大文件把容器打挂
MAX_CONTENT_BYTES = 8 * 1024 * 1024

app = FastAPI(title="个人商户记账工具 API", version="1.0.0")


def _ledger_db() -> Path:
    return Path(os.environ.get("LEDGER_DB", "data/ledger.db"))


def _memory_db() -> Path:
    return Path(os.environ.get("MEMORY_DB", "data/memory.db"))


def require_owner(
    x_wx_openid: str | None = Header(default=None, alias="X-WX-OPENID"),
    x_openid: str | None = Header(default=None, alias="X-OPENID"),
) -> str:
    """只信任云托管注入的 X-WX-OPENID。

    `X-OPENID` 仅用于本地调试，必须显式设置 ALLOW_LOCAL_AUTH=1 才生效，
    否则任何人拿到公网域名都能伪造身份读别人的账。
    """
    owner = x_wx_openid
    if not owner and os.environ.get("ALLOW_LOCAL_AUTH") == "1":
        owner = x_openid or "local"
    if not owner:
        raise HTTPException(status_code=401, detail="缺少用户身份，请通过小程序调用")
    return owner


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


class ImportRequest(BaseModel):
    filename: str = Field(default="bill.csv", max_length=200)
    content: str = ""
    content_base64: str | None = None
    dry_run: bool = False


def _decode_import(payload: ImportRequest) -> bytes:
    """支持两种入参：文本 content（CSV）或 base64 的 content_base64（xlsx/CSV）。"""
    if payload.content_base64:
        try:
            data = base64.b64decode(payload.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=400, detail="文件内容不是合法的 base64") from exc
    elif payload.content:
        data = payload.content.encode("utf-8")
    else:
        raise HTTPException(status_code=400, detail="没有收到文件内容")
    if len(data) > MAX_CONTENT_BYTES:
        raise HTTPException(status_code=413, detail="文件太大，请按月导入（上限 8MB）")
    return data


def _bill_text_from_bytes(data: bytes) -> str:
    """xlsx 转成标准 CSV 文本；CSV 兼容 utf-8 与 GBK。"""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if "xl/workbook.xml" in names:
                return xlsx_to_csv_text(data)
            csv_names = [name for name in names if name.lower().endswith(".csv")]
            if csv_names:
                return archive.read(csv_names[0]).decode("utf-8-sig")
        raise HTTPException(status_code=400, detail="这是压缩包，请先解压后再导入里面的账单文件")

    for encoding in ("utf-8-sig", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=400, detail="文件编码无法识别，请另存为 UTF-8 CSV 或 xlsx")


class ConfirmRequest(BaseModel):
    peer: str = Field(min_length=1, max_length=200)
    category: str


class CashRequest(BaseModel):
    day: str
    amount: str
    direction: str = "income"
    note: str = ""


@app.get("/")
def root() -> dict:
    """云托管默认探活可能打根路径，这里也返回 ok。"""
    return {"ok": True, "service": "ledger-api"}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "service": "ledger-api"}


@app.post("/api/import")
def import_bill(payload: ImportRequest, owner: str = Depends(require_owner)) -> dict:
    raw = _decode_import(payload)
    try:
        bill_text = _bill_text_from_bytes(raw)
        result: ParseResult = parse_bill_text(bill_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    stats = result.stats
    preview = {
        "total_data_lines": stats.total_data_lines,
        "parsed": stats.parsed,
        "skipped": stats.skipped,
        "unknown_type": stats.unknown_type,
        "excluded": stats.excluded,
        "income_total": _money(stats.income_total),
        "business_income_total": _money(stats.business_income_total),
        "refund_total": _money(stats.refund_total),
    }

    with PeerMemory(_memory_db()) as memory:
        pending = unresolved(aggregate_pending(result, memory.all(owner)))
        summary = {
            "pending_count": sum(item.count for item in pending),
            "pending_peers": len(pending),
        }
        if payload.dry_run:
            return {"dry_run": True, "stats": preview, "pending": summary}

        with TransactionStore(_ledger_db()) as store:
            saved = store.save(result, source=payload.filename, owner=owner)

    return {
        "dry_run": False,
        "stats": preview,
        "saved": {
            "total": saved.total,
            "inserted": saved.inserted,
            "updated": saved.updated,
            "months": saved.months,
        },
        "pending": summary,
    }


@app.get("/api/months")
def list_months(owner: str = Depends(require_owner)) -> dict:
    with TransactionStore(_ledger_db()) as store, CashBook(_ledger_db()) as cash:
        months = sorted({item[0] for item in store.months(owner)} | set(cash.months(owner)))
    return {"months": months}


@app.get("/api/pending")
def pending_list(month: str | None = None, limit: int = 50, owner: str = Depends(require_owner)) -> dict:
    with TransactionStore(_ledger_db()) as store, PeerMemory(_memory_db()) as memory:
        months = [month] if month else [item[0] for item in store.months(owner)]
        transactions = []
        for name in months:
            transactions.extend(store.list_month(name, owner))
        items = unresolved(
            aggregate_pending(ParseResult(transactions=transactions), memory.all(owner))
        )
    return {
        "total": len(items),
        "items": [
            {
                "peer": item.peer,
                "count": item.count,
                "total": _money(item.total),
                "income_total": _money(item.income_total),
                "expense_total": _money(item.expense_total),
                "first_time": item.first_time.strftime("%Y-%m-%d"),
            }
            for item in items[: max(1, min(limit, 200))]
        ],
    }


@app.post("/api/confirm")
def confirm(payload: ConfirmRequest, owner: str = Depends(require_owner)) -> dict:
    if payload.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"分类只能是 {VALID_CATEGORIES}")
    with PeerMemory(_memory_db()) as memory:
        memory.set(payload.peer, payload.category, owner)
        total = len(memory.all(owner))
    return {"ok": True, "peer": payload.peer, "category": payload.category, "remembered": total}


@app.get("/api/report")
def report(month: str | None = None, owner: str = Depends(require_owner)) -> dict:
    with TransactionStore(_ledger_db()) as store, CashBook(_ledger_db()) as cash, PeerMemory(
        _memory_db()
    ) as memory:
        months = sorted({item[0] for item in store.months(owner)} | set(cash.months(owner)))
        if not months:
            raise HTTPException(status_code=404, detail="还没有导入任何账单")
        target = month or months[-1]
        if target not in months:
            raise HTTPException(status_code=404, detail=f"{target} 没有数据")

        data = build_month_report(
            target,
            store.list_month(target, owner),
            cash.list_month(target, owner),
            memory.all(owner),
        )

    return {
        "month": data.month,
        "total_tx": data.total_tx,
        "gross_business": _money(data.gross_business),
        "refund": _money(data.refund),
        "wechat_net": _money(data.wechat_net),
        "purchase_expense": _money(data.purchase_expense),
        "cash_income": _money(data.cash_income),
        "cash_expense": _money(data.cash_expense),
        "revenue_total": _money(data.revenue_total),
        "balance": _money(data.balance),
        "pending_count": data.pending_count,
        "pending_peers": len(data.pending),
        "pending_amount": _money(data.pending_amount),
        "unknown_count": data.unknown_count,
        "closed_count": data.closed_count,
        "private_count": data.private_count,
        "daily": [
            {"day": point.day, "wechat": _money(point.wechat), "cash": _money(point.cash)}
            for point in data.daily
        ],
    }


@app.post("/api/cash")
def save_cash(payload: CashRequest, owner: str = Depends(require_owner)) -> dict:
    if payload.direction not in ("income", "expense"):
        raise HTTPException(status_code=400, detail="direction 只能是 income 或 expense")
    direction = Direction.EXPENSE if payload.direction == "expense" else Direction.INCOME
    try:
        with CashBook(_ledger_db()) as cash:
            entry = cash.set(payload.day, payload.amount, direction, payload.note, owner)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "day": entry.day, "direction": direction.value, "amount": _money(entry.amount)}


@app.get("/api/cash")
def cash_list(month: str, owner: str = Depends(require_owner)) -> dict:
    with CashBook(_ledger_db()) as cash:
        entries = cash.list_month(month, owner)
        income, expense = cash.totals(month, owner)
    return {
        "items": [
            {
                "day": entry.day,
                "direction": entry.direction.value,
                "amount": _money(entry.amount),
                "note": entry.note,
            }
            for entry in entries
        ],
        "income": _money(income),
        "expense": _money(expense),
    }


@app.delete("/api/data")
def clear_all(owner: str = Depends(require_owner)) -> dict:
    """清空当前用户的全部数据。"""
    with TransactionStore(_ledger_db()) as store, CashBook(_ledger_db()) as cash, PeerMemory(
        _memory_db()
    ) as memory:
        store.clear(owner)
        cash.clear(owner)
        memory.clear(owner)
    return {"ok": True}
