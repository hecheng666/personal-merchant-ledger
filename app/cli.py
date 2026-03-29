"""命令行入口。

    python -m app.cli import --file 账单.csv --html 月报.html
    python -m app.cli pending
    python -m app.cli confirm --peer 张三 --as business_expense
    python -m app.cli cash set 2026-10-31 380
    python -m app.cli cash set 2026-10-31 120 --expense --note 批发市场
    python -m app.cli report --month 2026-10 --html 月报.html
    python -m app.cli export --month 2026-10 --dir out
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

from app.cash import CashBook
from app.report import build_month_report, export_csv, render_html, render_markdown
from app.store import TransactionStore
from app.wechat.memory import VALID_CATEGORIES, PeerMemory
from app.wechat.parser import Direction, ParseResult, parse_bill
from app.wechat.reconcile import aggregate_pending, unresolved

DEFAULT_STORE = "data/ledger.db"
DEFAULT_MEMORY = "data/memory.db"
ZERO = Decimal("0")


def _setup_console() -> None:
    """Windows 控制台默认不是 UTF-8，中文输出会炸，这里强制一次。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _pick_month(transactions: list) -> str:
    counts = Counter(tx.time.strftime("%Y-%m") for tx in transactions)
    return counts.most_common(1)[0][0] if counts else ""


def _print_stats(result) -> None:
    stats = result.stats
    print("解析统计")
    print(f"  数据行 {stats.total_data_lines} | 成功 {stats.parsed} | 跳过 {stats.skipped}")
    print(
        f"  收入合计 {_money(stats.income_total)} | 其中营业收款 {_money(stats.business_income_total)}"
    )
    print(
        f"  退款 {_money(stats.refund_total)} | 已关闭 {_money(stats.closed_total)} | 支出 {_money(stats.expense_total)}"
    )
    print(f"  未识别类型 {stats.unknown_type} 笔 | 剔除 {stats.excluded} 笔 | 服务费 {_money(stats.fee_total)}")


def _print_pending(items, limit: int = 20) -> None:
    print(f"\n待确认（按对手方聚合，共 {len(items)} 项）")
    if not items:
        print("  没有待确认的转账")
        return
    print(f"  {'对手方':<22}{'笔数':>4}  {'金额':<28}首次出现")
    for item in items[:limit]:
        peer = item.peer if len(item.peer) <= 20 else item.peer[:19] + "…"
        print(
            f"  {peer:<22}{item.count:>4}  {item.amount_text():<28}"
            f"{item.first_time:%Y-%m-%d}"
        )
    if len(items) > limit:
        print(f"  …… 还有 {len(items) - limit} 项")


def _build_report(store: TransactionStore, cash: CashBook, memory: PeerMemory, month: str):
    transactions = store.list_month(month)
    entries = cash.list_month(month)
    return build_month_report(month, transactions, entries, memory.all()), transactions


def _write_outputs(report, args) -> None:
    if getattr(args, "md", None):
        target = Path(args.md)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_markdown(report), encoding="utf-8")
        print(f"  月报已写入 {args.md}")
    if getattr(args, "html", None):
        target = Path(args.html)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_html(report), encoding="utf-8")
        print(f"  网页版月报已写入 {args.html}")


def cmd_import(args: argparse.Namespace) -> int:
    try:
        result = parse_bill(args.file)
    except FileNotFoundError:
        print(f"找不到文件：{args.file}")
        return 1
    except IsADirectoryError:
        print(f"这是一个目录，不是账单文件：{args.file}")
        return 1
    except (ValueError, csv.Error) as exc:
        print(f"解析失败：{exc}")
        return 1
    _print_stats(result)

    with TransactionStore(args.store) as store, PeerMemory(args.memory) as memory, CashBook(
        args.store
    ) as cash:
        summary = store.save(result, source=str(Path(args.file).name))
        print(
            f"\n入库 {summary.total} 笔（新增 {summary.inserted} / 覆盖 {summary.updated}）"
            f" | 涉及月份 {', '.join(summary.months)}"
        )
        known = memory.all()
        pending = unresolved(aggregate_pending(result, known))
        _print_pending(pending)

        month = args.month or _pick_month(result.transactions)
        if (args.md or args.html) and month and month not in summary.months:
            print(f"  提示：{month} 不在本次导入的数据里，未生成月报")
            month = ""
        if month and (args.md or args.html):
            report, _ = _build_report(store, cash, memory, month)
            _write_outputs(report, args)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    with TransactionStore(args.store) as store, PeerMemory(args.memory) as memory, CashBook(
        args.store
    ) as cash:
        # 只记了现金、没导入账单的月份也应该能出报表
        months = sorted({item[0] for item in store.months()} | set(cash.months()))
        if not months:
            print("还没有导入任何账单，先跑 import")
            return 1
        month = args.month or months[-1]
        if month not in months:
            print(f"{month} 没有数据，可选：{', '.join(months)}")
            return 1
        report, _ = _build_report(store, cash, memory, month)
        print(render_markdown(report))
        _write_outputs(report, args)
    return 0


def cmd_pending(args: argparse.Namespace) -> int:
    with TransactionStore(args.store) as store, PeerMemory(args.memory) as memory:
        months = [item[0] for item in store.months()]
        if not months:
            print("还没有导入任何账单")
            return 1
        scope = store.list_month(args.month) if args.month else []
        if not args.month:
            for month in months:
                scope.extend(store.list_month(month))
        pending = unresolved(aggregate_pending(ParseResult(transactions=scope), memory.all()))
        _print_pending(pending, limit=args.limit)
    return 0


def cmd_confirm(args: argparse.Namespace) -> int:
    with PeerMemory(args.memory) as memory:
        memory.set(args.peer, args.category)
        print(f"已记住：{args.peer} → {args.category}")
        print(f"记忆库现有 {len(memory.all())} 个对手方")
    return 0


def cmd_cash_set(args: argparse.Namespace) -> int:
    with CashBook(args.store) as cash:
        direction = Direction.EXPENSE if args.expense else Direction.INCOME
        try:
            entry = cash.set(args.day, args.amount, direction, args.note or "")
        except ValueError as exc:
            print(f"录入失败：{exc}")
            return 1
        print(f"已记录 {entry.day} 现金{direction.value} {_money(entry.amount)}")
    return 0


def cmd_cash_list(args: argparse.Namespace) -> int:
    with CashBook(args.store) as cash:
        entries = cash.list_month(args.month)
        if not entries:
            print(f"{args.month} 没有现金记录")
            return 0
        income = ZERO
        expense = ZERO
        for entry in entries:
            label = "支出" if entry.direction is Direction.EXPENSE else "收入"
            print(f"  {entry.day}  {label}  {_money(entry.amount):>10}  {entry.note}")
            if entry.direction is Direction.EXPENSE:
                expense += entry.amount
            else:
                income += entry.amount
        print(f"合计：收入 {_money(income)} | 支出 {_money(expense)}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    with TransactionStore(args.store) as store, PeerMemory(args.memory) as memory, CashBook(
        args.store
    ) as cash:
        transactions = store.list_month(args.month)
        if not transactions:
            print(f"{args.month} 没有数据")
            return 1
        report = build_month_report(
            args.month, transactions, cash.list_month(args.month), memory.all()
        )
        paths = export_csv(report, transactions, args.dir, memory.all())
        for path in paths:
            print(f"  已导出 {path}")
    return 0


def cmd_clear(args: argparse.Namespace) -> int:
    if not args.yes:
        print("这会删除所有已导入的账单、现金记录和对手方记忆，确认请加 --yes")
        return 1
    with TransactionStore(args.store) as store, CashBook(args.store) as cash:
        store.clear()
        cash.clear()
    Path(args.memory).unlink(missing_ok=True)
    print("已清空全部本地数据（账单、现金、对手方记忆）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="个人商户记账工具")
    parser.add_argument("--store", default=DEFAULT_STORE, help="交易与现金的 SQLite 路径")
    parser.add_argument("--memory", default=DEFAULT_MEMORY, help="对手方记忆库路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="导入微信账单 CSV")
    p_import.add_argument("--file", required=True)
    p_import.add_argument("--month", help="生成月报的月份，默认取数据量最大的月份")
    p_import.add_argument("--md", help="月报输出到 markdown")
    p_import.add_argument("--html", help="月报输出到网页")
    p_import.set_defaults(func=cmd_import)

    p_report = sub.add_parser("report", help="生成月报")
    p_report.add_argument("--month")
    p_report.add_argument("--md")
    p_report.add_argument("--html")
    p_report.set_defaults(func=cmd_report)

    p_pending = sub.add_parser("pending", help="查看待确认清单")
    p_pending.add_argument("--month")
    p_pending.add_argument("--limit", type=int, default=20)
    p_pending.set_defaults(func=cmd_pending)

    p_confirm = sub.add_parser("confirm", help="确认一个对手方的归属")
    p_confirm.add_argument("--peer", required=True)
    p_confirm.add_argument("--as", dest="category", required=True, choices=VALID_CATEGORIES)
    p_confirm.set_defaults(func=cmd_confirm)

    p_cash = sub.add_parser("cash", help="现金补录")
    cash_sub = p_cash.add_subparsers(dest="cash_command", required=True)
    p_set = cash_sub.add_parser("set", help="录入某天的现金")
    p_set.add_argument("day")
    p_set.add_argument("amount")
    p_set.add_argument("--expense", action="store_true", help="记为支出，默认是收入")
    p_set.add_argument("--note")
    p_set.set_defaults(func=cmd_cash_set)
    p_list = cash_sub.add_parser("list", help="查看某月现金")
    p_list.add_argument("--month", required=True)
    p_list.set_defaults(func=cmd_cash_list)

    p_export = sub.add_parser("export", help="导出三个分类的 CSV")
    p_export.add_argument("--month", required=True)
    p_export.add_argument("--dir", default="out")
    p_export.set_defaults(func=cmd_export)

    p_clear = sub.add_parser("clear", help="清空本地数据")
    p_clear.add_argument("--yes", action="store_true")
    p_clear.set_defaults(func=cmd_clear)

    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
