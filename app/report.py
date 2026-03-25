"""报表聚合与渲染。

口径（全项目唯一，别在别处另算一套）：

    微信营业收款 = BUSINESS_INCOME 且未剔除的金额合计
    微信净额     = 微信营业收款 − 退款冲销
    营业额       = 微信净额 + 现金收入
    结余         = 营业额 − 进货支出 − 现金支出

注意 `income_total` 不是营业收款：它包含红包、赞赏、待确认转账，
直接拿来当营业额会把私人款算进生意里。
"""

from __future__ import annotations

import calendar
import csv
import html
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.cash import CashEntry
from app.wechat.parser import Category, Direction, ParseResult, Transaction
from app.wechat.reconcile import PendingPeer, aggregate_pending, unresolved

ZERO = Decimal("0")

# 导出给人看时用中文，别把内部代码泄漏到表格里
_EXCLUDED_REASON_TEXT = {"refund": "退款", "closed": "已关闭"}


@dataclass(slots=True)
class DailyPoint:
    day: str            # YYYY-MM-DD
    wechat: Decimal     # 当日微信营业收款净额
    cash: Decimal       # 当日现金收入


@dataclass(slots=True)
class MonthReport:
    month: str
    total_tx: int
    gross_business: Decimal
    refund: Decimal
    wechat_net: Decimal
    purchase_expense: Decimal
    cash_income: Decimal
    cash_expense: Decimal
    revenue_total: Decimal
    balance: Decimal
    pending_count: int
    pending_amount: Decimal
    pending: list[PendingPeer] = field(default_factory=list)
    unknown_count: int = 0
    closed_count: int = 0
    private_count: int = 0
    daily: list[DailyPoint] = field(default_factory=list)


def _effective_category(tx: Transaction, known: dict[str, str]) -> Category:
    """待确认交易在人工确认过之后，按记忆库的口径参与统计。"""
    if tx.category is Category.PENDING:
        remembered = known.get(tx.peer)
        if remembered:
            return Category(remembered)
    return tx.category


def build_month_report(
    month: str,
    transactions: list[Transaction],
    cash_entries: list[CashEntry] | None = None,
    known: dict[str, str] | None = None,
) -> MonthReport:
    """把一个月（或多个月）的交易聚合成报表。"""
    known_map = dict(known or {})
    cash_entries = cash_entries or []

    gross_business = ZERO
    refund = ZERO
    purchase = ZERO
    unknown = 0
    closed = 0
    private = 0
    daily: dict[str, list[Decimal]] = {}

    for tx in transactions:
        category = _effective_category(tx, known_map)

        if tx.excluded:
            # 只有「营业收款」的退款才冲销营业额。
            # 私人消费（商户消费、红包）的退款与生意无关，不能拿来减营业额，
            # 否则买件衣服退款都能让当月营收变少。
            if tx.excluded_reason == "refund" and category is Category.BUSINESS_INCOME:
                refund += tx.amount
                bucket = daily.setdefault(tx.time.strftime("%Y-%m-%d"), [ZERO, ZERO])
                bucket[0] -= tx.amount
            elif tx.excluded_reason == "closed":
                closed += 1
            if category is Category.UNKNOWN:
                unknown += 1
            continue

        if category is Category.UNKNOWN:
            unknown += 1

        if category is Category.BUSINESS_INCOME and tx.direction is Direction.INCOME:
            gross_business += tx.amount
            bucket = daily.setdefault(tx.time.strftime("%Y-%m-%d"), [ZERO, ZERO])
            bucket[0] += tx.amount
        elif category is Category.BUSINESS_EXPENSE:
            # 只认收入/支出两个方向；中性交易不该进进货成本
            if tx.direction is Direction.EXPENSE:
                purchase += tx.amount
            elif tx.direction is Direction.INCOME:
                purchase -= tx.amount
        elif category is Category.PRIVATE:
            private += 1

    cash_income = ZERO
    cash_expense = ZERO
    for entry in cash_entries:
        bucket = daily.setdefault(entry.day, [ZERO, ZERO])
        if entry.direction is Direction.EXPENSE:
            cash_expense += entry.amount
            bucket[1] -= entry.amount
        else:
            cash_income += entry.amount
            bucket[1] += entry.amount

    wechat_net = gross_business - refund
    revenue_total = wechat_net + cash_income
    balance = revenue_total - purchase - cash_expense

    pending_all = aggregate_pending(ParseResult(transactions=list(transactions)), known_map)
    pending = unresolved(pending_all)

    return MonthReport(
        month=month,
        total_tx=len(transactions),
        gross_business=gross_business,
        refund=refund,
        wechat_net=wechat_net,
        purchase_expense=purchase,
        cash_income=cash_income,
        cash_expense=cash_expense,
        revenue_total=revenue_total,
        balance=balance,
        pending_count=sum(item.count for item in pending),
        pending_amount=sum((item.total for item in pending), ZERO),
        pending=pending,
        unknown_count=unknown,
        closed_count=closed,
        private_count=private,
        daily=_fill_days(month, daily),
    )


def _fill_days(month: str, daily: dict[str, list[Decimal]]) -> list[DailyPoint]:
    """补齐整月每一天，缺的天数按 0 画，图表才不会跳。"""
    year, mon = (int(part) for part in month.split("-"))
    days = calendar.monthrange(year, mon)[1]
    points: list[DailyPoint] = []
    for day in range(1, days + 1):
        key = f"{month}-{day:02d}"
        wechat, cash = daily.get(key, [ZERO, ZERO])
        points.append(DailyPoint(day=key, wechat=wechat, cash=cash))
    return points


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _md_cell(value: str) -> str:
    """表格单元格转义：对手方昵称里可能有 | 或换行，会把表格撑坏。"""
    return (value or "").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


# ------------------------------------------------------------------ 文本报表


def render_markdown(report: MonthReport) -> str:
    lines = [
        f"# {report.month} 经营月报",
        "",
        "| 指标 | 金额（元） |",
        "|---|---:|",
        f"| 微信营业收款 | {_money(report.gross_business)} |",
        f"| 退款冲销 | -{_money(report.refund)} |",
        f"| **微信净额** | **{_money(report.wechat_net)}** |",
        f"| 现金收入 | {_money(report.cash_income)} |",
        f"| **营业额合计** | **{_money(report.revenue_total)}** |",
        f"| 进货支出 | {_money(report.purchase_expense)} |",
        f"| 现金支出 | {_money(report.cash_expense)} |",
        f"| **本月结余** | **{_money(report.balance)}** |",
        "",
        f"- 交易笔数：{report.total_tx}",
        f"- 待确认：{report.pending_count} 笔 / {len(report.pending)} 个对手方 / 合计 {_money(report.pending_amount)} 元",
        f"- 未识别类型：{report.unknown_count} 笔",
        f"- 已关闭：{report.closed_count} 笔（钱没动过，不计入营业额）",
        f"- 私人往来：{report.private_count} 笔（不计入营业额）",
        "",
        "## 待确认清单（按金额排序，前 20）",
        "",
        "| 对手方 | 笔数 | 金额 | 首次出现 |",
        "|---|---:|---:|---|",
    ]
    for item in report.pending[:20]:
        lines.append(
            f"| {_md_cell(item.peer) or '（空）'} | {item.count} | {_md_cell(item.amount_text())} | "
            f"{item.first_time:%Y-%m-%d} |"
        )
    if not report.pending:
        lines.append("| 无 | 0 | 0.00 | — |")
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ HTML 报表

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#f4f6f9;color:#1f2733;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;padding:36px 24px 56px}
.wrap{max-width:1040px;margin:0 auto}
.head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:24px}
.head h1{font-size:26px;font-weight:700;letter-spacing:-.3px}
.head .sub{color:#7a8699;font-size:13px;margin-top:6px}
.badge{background:#e8f8ef;color:#07914a;border:1px solid #b9ebd0;border-radius:999px;padding:7px 14px;font-size:13px;font-weight:600;white-space:nowrap}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:14px}
.kpi{background:#fff;border-radius:14px;padding:18px 20px;box-shadow:0 1px 3px rgba(16,24,40,.06);border:1px solid #eef1f5}
.kpi .label{color:#7a8699;font-size:13px;margin-bottom:8px}
.kpi .value{font-size:25px;font-weight:700;letter-spacing:-.5px;font-variant-numeric:tabular-nums}
.kpi .hint{color:#98a2b3;font-size:12px;margin-top:6px}
.kpi.main{background:linear-gradient(135deg,#0dbf6a,#07a35a);border:none;color:#fff}
.kpi.main .label,.kpi.main .hint{color:rgba(255,255,255,.82)}
.minor{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:22px}
.minor .label{color:#7a8699;font-size:13px;margin-bottom:8px}
.minor .value{font-weight:700;letter-spacing:-.3px;font-variant-numeric:tabular-nums}
.card{background:#fff;border-radius:14px;padding:18px 20px;box-shadow:0 1px 3px rgba(16,24,40,.06);border:1px solid #eef1f5}
.card h2{font-size:15px;font-weight:600;margin-bottom:16px;display:flex;justify-content:space-between;align-items:center}
.card h2 span{font-size:12px;color:#98a2b3;font-weight:400}
.chart{margin-bottom:22px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:22px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{padding:9px 4px;text-align:left;border-bottom:1px solid #f0f2f6}
th{color:#7a8699;font-weight:500;font-size:12.5px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr.total td{font-weight:700;border-bottom:none}
tr.total td.num{color:#07914a}
.muted{color:#98a2b3}
.foot{margin-top:22px;color:#98a2b3;font-size:12px;line-height:1.9}
.tag{display:inline-block;background:#fff5e6;color:#b26a00;border-radius:6px;padding:2px 8px;font-size:12px}
.tag.red{background:#fdecec;color:#c0392b}
"""


def _svg_chart(report: MonthReport) -> str:
    """纯内联 SVG 柱状图：不依赖 CDN，离线也能看。"""
    points = report.daily
    width, height = 960.0, 250.0
    left, right, top, bottom = 56.0, 12.0, 16.0, 28.0
    plot_w = width - left - right
    plot_h = height - top - bottom
    # 退款集中在某一天时当日净额可能为负，柱子高度不能是负数
    values = [max(float(point.wechat + point.cash), 0.0) for point in points]
    peak = max(values) if values and max(values) > 0 else 1.0
    # 取一个好看的刻度上限
    magnitude = 10 ** max(len(str(int(peak))) - 1, 0)
    ceiling = (int(peak / magnitude) + 1) * magnitude if peak > 0 else 1

    slot = plot_w / max(len(points), 1)
    bar_w = max(slot * 0.55, 3.0)
    bars: list[str] = []
    for index, value in enumerate(values):
        bar_h = plot_h * value / ceiling
        x = left + index * slot + (slot - bar_w) / 2
        y = top + plot_h - bar_h
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" '
            'rx="3" fill="#0dbf6a" opacity=".9"/>'
        )

    grid: list[str] = []
    for ratio in (0, 0.25, 0.5, 0.75, 1.0):
        y = top + plot_h * (1 - ratio)
        grid.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" '
            'stroke="#eef1f5" stroke-width="1"/>'
            f'<text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="11" fill="#98a2b3">{_money(Decimal(str(ceiling * ratio)))}</text>'
        )

    labels: list[str] = []
    for index, point in enumerate(points):
        day = int(point.day[-2:])
        if day == 1 or day % 5 == 0:
            x = left + index * slot + slot / 2
            labels.append(
                f'<text x="{x:.1f}" y="{height - 8}" text-anchor="middle" '
                f'font-size="11" fill="#98a2b3">{day}</text>'
            )

    return (
        f'<svg viewBox="0 0 {width:.0f} {height:.0f}" width="100%" height="{height:.0f}" '
        'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="每日营业额">'
        + "".join(grid)
        + "".join(bars)
        + "".join(labels)
        + "</svg>"
    )


def render_html(report: MonthReport, source: str = "微信账单 CSV") -> str:
    """生成自包含的月报页面（无外部资源，可直接截图或离线打开）。"""
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = [
        ("微信营业收款", report.gross_business, ""),
        ("退款冲销", -report.refund, "muted"),
    ]
    status_parts = []
    if report.unknown_count:
        status_parts.append(f"{report.unknown_count} 笔未识别")
    if report.pending:
        status_parts.append(f"待确认 {len(report.pending)} 项")
    status_text = " · ".join(status_parts) or "账已对齐"

    pending_rows = "".join(
        f"<tr><td>{html.escape(item.peer or '（空）')}</td>"
        f'<td class="num">{item.count}</td>'
        f'<td class="num">{html.escape(item.amount_text())}</td>'
        f'<td class="num muted">{item.first_time:%m-%d}</td></tr>'
        for item in report.pending[:8]
    ) or '<tr><td colspan="4" class="muted">没有待确认的交易，账已经对齐</td></tr>'

    if len(report.pending) > 8:
        pending_rows += (
            f'<tr><td colspan="4" class="muted">另有 {len(report.pending) - 8} 个对手方未展示，'
            "完整清单见命令行 pending</td></tr>"
        )

    income_rows = "".join(
        f'<tr><td>{label}</td><td class="num {cls}">{_money(value)}</td></tr>'
        for label, value, cls in rows
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{report.month} 经营月报</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <div>
      <h1>{report.month} 经营月报</h1>
      <div class="sub">数据来源：{html.escape(source)} · 生成于 {generated}</div>
    </div>
    <div class="badge">{status_text}</div>
  </div>

  <div class="kpis">
    <div class="kpi main">
      <div class="label">营业额合计</div>
      <div class="value">¥{_money(report.revenue_total)}</div>
      <div class="hint">微信净额 + 现金收入</div>
    </div>
    <div class="kpi">
      <div class="label">微信净额</div>
      <div class="value">¥{_money(report.wechat_net)}</div>
      <div class="hint">已扣除退款 {_money(report.refund)}</div>
    </div>
    <div class="kpi">
      <div class="label">现金收入</div>
      <div class="value">¥{_money(report.cash_income)}</div>
      <div class="hint">支出 {_money(report.cash_expense)}</div>
    </div>
    <div class="kpi">
      <div class="label">本月结余</div>
      <div class="value">¥{_money(report.balance)}</div>
      <div class="hint">营业额 − 进货 − 现金支出</div>
    </div>
  </div>

  <div class="minor">
    <div class="card"><div class="label">交易笔数</div><div class="value" style="font-size:20px">{report.total_tx:,}</div></div>
    <div class="card"><div class="label">进货支出</div><div class="value" style="font-size:20px">¥{_money(report.purchase_expense)}</div></div>
    <div class="card"><div class="label">待确认</div><div class="value" style="font-size:20px">{report.pending_count} 笔</div><div class="hint muted">合计 ¥{_money(report.pending_amount)}</div></div>
    <div class="card"><div class="label">数据质量</div><div class="value" style="font-size:20px">未识别 {report.unknown_count} 笔</div><div class="hint muted">已关闭 {report.closed_count} 笔 · 私人往来 {report.private_count} 笔</div></div>
  </div>

  <div class="card chart">
    <h2>每日营业收款 <span>微信 + 现金 · 单位：元</span></h2>
    {_svg_chart(report)}
  </div>

  <div class="grid2">
    <div class="card">
      <h2>收入构成</h2>
      <table>
        <tr><th>项目</th><th class="num">金额（元）</th></tr>
        {income_rows}
        <tr class="total"><td>微信净额</td><td class="num">{_money(report.wechat_net)}</td></tr>
        <tr><td>现金收入</td><td class="num">{_money(report.cash_income)}</td></tr>
        <tr class="total"><td>营业额合计</td><td class="num">{_money(report.revenue_total)}</td></tr>
      </table>
    </div>
    <div class="card">
      <h2>支出与结余</h2>
      <table>
        <tr><th>项目</th><th class="num">金额（元）</th></tr>
        <tr><td>进货支出（已确认转账）</td><td class="num">{_money(report.purchase_expense)}</td></tr>
        <tr><td>现金支出</td><td class="num">{_money(report.cash_expense)}</td></tr>
        <tr><td>退款冲销</td><td class="num">{_money(report.refund)}</td></tr>
        <tr class="total"><td>本月结余</td><td class="num">{_money(report.balance)}</td></tr>
      </table>
      <div class="foot">待确认金额 ¥{_money(report.pending_amount)}，确认后会重新计入上表。</div>
    </div>
  </div>

  <div class="card">
    <h2>待确认清单 <span>按对手方聚合 · 一点归类，之后自动记住</span></h2>
    <table>
      <tr><th>对手方</th><th class="num">笔数</th><th class="num">金额（收 / 支）</th><th class="num">首次出现</th></tr>
      {pending_rows}
    </table>
  </div>

  <div class="foot">
    口径：营业额 = 微信营业收款 − 退款 + 现金收入；结余 = 营业额 − 进货支出 − 现金支出。<br>
    退款与已关闭交易只标记不删除，可在明细里查到；未识别类型不计入营业额，需人工确认。
  </div>
</div>
</body>
</html>
"""


# ------------------------------------------------------------------ 导出


def _csv_cell(value: str) -> str:
    """防 CSV 公式注入。

    对手方昵称是别人可以随便改的，`=cmd|...` 这类内容在 Excel 里会被当公式执行。
    以 = + - @ 开头的文本前面加一个单引号，Excel 会当纯文本显示。
    """
    text = (value or "").replace("\r", " ").replace("\n", " ")
    if text.startswith(("=", "+", "-", "@", "\t")):
        return "'" + text
    return text


def export_csv(
    report: MonthReport,
    transactions: list[Transaction],
    out_dir: str | Path,
    known: dict[str, str] | None = None,
) -> list[Path]:
    """导出五个 sheet 的 CSV（UTF-8 BOM，Excel 双击即可打开，金额是数字）。

    退款与已关闭单独出一张表，不是从账里删掉：用户要能看到「这笔被剔除了」。
    未识别的类型也单独一张，不混进私人往来。
    """
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    known_map = dict(known or {})

    buckets: dict[str, list[tuple[Transaction, str]]] = {
        "营业收款": [],
        "进货支出": [],
        "私人往来": [],
        "未识别": [],
        "退款与关闭": [],
    }
    for tx in transactions:
        category = _effective_category(tx, known_map)
        reason = ""
        if tx.excluded:
            name = "退款与关闭"
            code = tx.excluded_reason or ""
            reason = _EXCLUDED_REASON_TEXT.get(code, code)
        elif category is Category.BUSINESS_INCOME and tx.direction is Direction.INCOME:
            name = "营业收款"
        elif category is Category.BUSINESS_INCOME:
            # 营业类型却是支出、又没被标记退款：方向异常，交给用户看，不猜
            name = "未识别"
        elif category is Category.BUSINESS_EXPENSE:
            name = "进货支出"
        elif category in (Category.PENDING, Category.UNKNOWN):
            name = "未识别"
        else:
            name = "私人往来"
        buckets[name].append((tx, reason))

    paths: list[Path] = []
    for name, rows in buckets.items():
        path = target / f"{report.month}-{name}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["交易时间", "交易类型", "交易对方", "商品", "金额", "状态", "交易单号"]
                + (["剔除原因"] if name == "退款与关闭" else [])
            )
            for tx, reason in sorted(rows, key=lambda item: item[0].time):
                writer.writerow(
                    [
                        tx.time.strftime("%Y-%m-%d %H:%M:%S"),
                        _csv_cell(tx.tx_type),
                        _csv_cell(tx.peer),
                        _csv_cell(tx.item),
                        f"{tx.amount:.2f}",
                        _csv_cell(tx.status),
                        _csv_cell(tx.tx_no),
                    ]
                    + ([_csv_cell(reason)] if name == "退款与关闭" else [])
                )
        paths.append(path)
    return paths
