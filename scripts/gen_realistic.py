"""生成接近真实小店的微信账单模拟数据。

与 `gen_synthetic.py` 的区别：
- `gen_synthetic.py` 只造 5 种交易类型，目的是压测性能与验证 54 个对手方的聚合；
- 本脚本按真实店铺的节奏造数据：覆盖微信近 20 种交易类型、抹零习惯、
  部分退款、提现手续费、私人往来，用来做功能验收和演示。

金额全程用「分」的整数计算，不经过 float。
生成结果不含任何真实个人信息，可以随便转发。

用法：
    python scripts/gen_realistic.py --months 3 --per-month 900 --out samples
"""

from __future__ import annotations

import argparse
import calendar
import csv
import random
from datetime import datetime
from pathlib import Path

HEADER = [
    "交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)",
    "支付方式", "当前状态", "交易单号", "商户单号", "备注",
]

# 顾客昵称：真实微信昵称长什么样，这里就长什么样（带符号、带店铺前缀、带数字）
CUSTOMERS = [
    "王**", "李**", "张女士", "陈先生", "小刘", "阿香", "老赵", "周姐",
    "A0建材王哥", "微信用户_8f3a", "微笑向暖", "陌上花开", "大海", "随缘",
    "知足常乐", "平安是福", "吉祥三宝", "顺其自然", "幸福一家人", "清风徐来",
    "晨晨妈妈", "楼下张阿姨", "3栋1002", "隔壁老李", "外卖小哥-小杨",
]

SUPPLIERS = [
    "老周粮油批发", "城南蔬菜批发", "旺旺食品商行", "纸箱包装厂",
    "冻品配送_李", "闽南干货批发", "鲜果源", "日化用品-小吴",
]

PRIVATE_PEERS = ["老婆", "妈妈", "房东王姐", "大学同学老陈", "小区团购群", "表弟"]

MERCHANTS = [
    "美团平台商户", "拼多多平台商户", "饿了么", "肯德基", "中国石化",
    "国网电力", "中国移动", "菜鸟驿站", "理发店-阿强", "五金店",
]

PAY_METHODS = ["零钱", "零钱通", "工商银行(6217)", "建设银行(8899)", "招商银行(3344)"]

# 一天里什么时候最忙：早市、午间、傍晚三个高峰
HOUR_WEIGHTS = [
    (6, 1), (7, 3), (8, 6), (9, 5), (10, 4), (11, 6), (12, 8),
    (13, 5), (14, 3), (15, 3), (16, 4), (17, 7), (18, 9), (19, 7),
    (20, 5), (21, 3), (22, 1),
]
HOURS = [hour for hour, _ in HOUR_WEIGHTS]
HOUR_W = [weight for _, weight in HOUR_WEIGHTS]

KINDS = [
    "qr", "group", "transfer_in", "transfer_out", "merchant", "scan_pay",
    "redpacket_out", "redpacket_in", "withdraw", "recharge", "into_change",
    "out_change", "wealth", "creditcard", "donate", "family_card", "reward",
    "other", "unknown", "refund", "closed", "transfer_refund",
]
WEIGHTS = [
    700, 12, 55, 30, 40, 18,
    12, 4, 10, 4, 5, 4,
    1, 2, 2, 2, 3,
    2, 1, 8, 4, 2,
]


def money(cents: int) -> str:
    return f"¥{cents // 100}.{cents % 100:02d}"


def plain(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def rounded_cents(rng: random.Random, low: int, high: int) -> int:
    """模拟抹零：整元、五毛、八毛、九毛九都比随机小数常见。"""
    value = rng.randint(low, high)
    tail = rng.choices([0, 50, 80, 90, 99, rng.randint(1, 99)], weights=[35, 15, 12, 8, 10, 20])[0]
    return (value // 100) * 100 + tail


def supplier_cents(rng: random.Random) -> int:
    """进货是大额、整十/整百：500、1200、2200 元这种。"""
    return rng.choice([500, 800, 1200, 1500, 1800, 2200, 2600, 3000, 3500, 4200]) * 100


def _row(
    moment: datetime,
    tx_type: str,
    peer: str,
    item: str,
    direction: str,
    cents: int,
    method: str,
    status: str,
    tx_no: str,
    mch_no: str,
    note: str = "/",
) -> list[str]:
    return [
        moment.strftime("%Y-%m-%d %H:%M:%S"),
        tx_type,
        peer,
        item,
        direction,
        money(cents),
        method,
        status,
        f"{tx_no}\t",
        f"{mch_no}\t",
        note,
    ]


def make_row(
    kind: str,
    at: datetime,
    tx_no: str,
    mch_no: str,
    rng: random.Random,
    method: str,
) -> list[str]:
    """按交易类型造一行，字段组合与真实账单一致。"""
    if kind == "qr":
        return _row(at, "二维码收款", rng.choice(CUSTOMERS), "收款方备注:二维码收款",
                    "收入", rounded_cents(rng, 300, 12000),
                    rng.choice(["零钱", "零钱通"]), "已收钱", tx_no, mch_no)
    if kind == "group":
        return _row(at, "群收款", "小区团购群", "收款方备注:群收款", "收入",
                    rounded_cents(rng, 500, 6000), "零钱", "已收钱", tx_no, mch_no)
    if kind == "transfer_in":
        # 老顾客加好友后转账，和二维码收款一样是营业款，但需要人工确认一次
        return _row(at, "转账", rng.choice(CUSTOMERS), "转账备注:货款/微信转账", "收入",
                    rounded_cents(rng, 1000, 50000), "零钱",
                    rng.choice(["已收钱", "朋友已收钱"]), tx_no, mch_no)
    if kind == "transfer_out":
        return _row(at, "转账", rng.choice(SUPPLIERS), "转账备注:微信转账", "支出",
                    supplier_cents(rng), "零钱通", "朋友已收钱", tx_no, mch_no)
    if kind == "merchant":
        cents = rounded_cents(rng, 500, 20000)
        return _row(at, "商户消费", rng.choice(MERCHANTS), f"总共消费:{plain(cents)}",
                    "支出", cents, method, "支付成功", tx_no, mch_no)
    if kind == "scan_pay":
        return _row(at, "扫二维码付款", rng.choice(MERCHANTS), "付款方备注:扫二维码付款",
                    "支出", rounded_cents(rng, 200, 10000), method,
                    rng.choice(["支付成功", "已转账"]), tx_no, "/")
    if kind == "redpacket_out":
        return _row(at, "微信红包", rng.choice(PRIVATE_PEERS), "/", "支出",
                    rng.choice([666, 888, 2000, 5000, 10000, 20000]),
                    "零钱", "支付成功", tx_no, mch_no)
    if kind == "redpacket_in":
        return _row(at, "微信红包", rng.choice(PRIVATE_PEERS), "/", "收入",
                    rng.choice([666, 888, 2000, 5000, 10000]),
                    "/", "已存入零钱", tx_no, mch_no)
    if kind == "withdraw":
        cents = rng.choice([10000, 20000, 50000, 100000, 200000])
        fee = max(10, cents // 1000)          # 提现手续费 0.1%
        return _row(at, "零钱提现", "招商银行()", "/", "/", cents + fee,
                    "零钱", "提现已到账", tx_no, mch_no, f"服务费{money(fee)}")
    if kind == "recharge":
        return _row(at, "零钱充值", "工商银行(6217)", "/", "/",
                    rng.choice([50000, 100000, 200000]), "工商银行(6217)",
                    "充值完成", tx_no, mch_no)
    if kind == "into_change":
        return _row(at, "转入零钱通-来自工商银行(6217)", "/", "/", "/",
                    rng.choice([20000, 50000, 100000]), "工商银行(6217)",
                    "支付成功", tx_no, mch_no)
    if kind == "out_change":
        return _row(at, "零钱通转出-到零钱", "/", "/", "/",
                    rng.choice([10000, 30000, 50000]), "零钱通", "支付成功", tx_no, mch_no)
    if kind == "wealth":
        return _row(at, "购买理财通", "理财通", "中欧医疗健康混合C", "/",
                    rng.choice([100000, 300000, 500000]), "零钱", "支付成功", tx_no, mch_no)
    if kind == "creditcard":
        return _row(at, "信用卡还款", "建设银行信用卡还款", "/", "/",
                    rng.choice([100000, 300000, 500000]), "零钱", "支付成功", tx_no, mch_no)
    if kind == "donate":
        return _row(at, "分分捐", "腾讯公益慈善基金会", "/", "支出",
                    rng.choice([1, 2, 10, 100]), "零钱", "支付成功", tx_no, mch_no)
    if kind == "family_card":
        return _row(at, "亲属卡交易", rng.choice(PRIVATE_PEERS), "亲属卡", "支出",
                    rounded_cents(rng, 1000, 50000), "零钱通", "支付成功", tx_no, mch_no)
    if kind == "reward":
        return _row(at, "赞赏码", rng.choice(CUSTOMERS), "赞赏码", "收入",
                    rounded_cents(rng, 100, 5000), "零钱", "已到账", tx_no, mch_no)
    if kind == "other":
        return _row(at, "其他", "微信支付有优惠", "/", "收入",
                    rng.choice([1, 10, 50, 100, 500]), "零钱", "已到账", tx_no, mch_no)
    if kind == "unknown":
        return _row(at, "微信支付有优惠", "微信支付", "/", "支出",
                    rng.choice([1, 10, 100]), "零钱", "支付成功", tx_no, mch_no)
    if kind == "refund":
        # 退款是单独一行：类型仍是二维码收款，方向却是支出
        return _row(at, "二维码收款", rng.choice(CUSTOMERS), "收款方备注:二维码收款",
                    "支出", rounded_cents(rng, 300, 8000),
                    "零钱", "已全额退款", tx_no, mch_no, "已全额退款")
    if kind == "closed":
        cents = rounded_cents(rng, 500, 10000)
        return _row(at, "商户消费", rng.choice(MERCHANTS), f"总共消费:{plain(cents)}",
                    "支出", cents, "零钱", "已关闭", tx_no, mch_no)
    # transfer_refund
    return _row(at, "转账", rng.choice(CUSTOMERS), "转账备注:微信转账", "支出",
                rounded_cents(rng, 1000, 30000), "零钱", "对方已退还", tx_no, mch_no)


# 每个月至少保证出现一条，否则报表的「未识别 / 退款 / 已关闭 / 手续费」四条路径测不到
COVERAGE = {
    "unknown": lambda item: item[1] == "微信支付有优惠",
    "refund": lambda item: item[7] == "已全额退款",
    "closed": lambda item: item[7] == "已关闭",
    "withdraw": lambda item: item[1] == "零钱提现",
}


def build_month(month_start: datetime, count: int, rng: random.Random) -> list[list[str]]:
    days = calendar.monthrange(month_start.year, month_start.month)[1]

    def moment() -> datetime:
        return datetime(
            month_start.year, month_start.month, rng.randint(1, days),
            rng.choices(HOURS, HOUR_W)[0], rng.randint(0, 59), rng.randint(0, 59),
        )

    def serial() -> tuple[str, str]:
        return str(rng.randint(10**17, 10**18)), str(rng.randint(10**11, 10**12))

    rows: list[list[str]] = []
    for kind in rng.choices(KINDS, WEIGHTS, k=count):
        tx_no, mch_no = serial()
        rows.append(make_row(kind, moment(), tx_no, mch_no, rng, rng.choice(PAY_METHODS)))

    for kind, matched in COVERAGE.items():
        if not any(matched(item) for item in rows):
            tx_no, mch_no = serial()
            rows.append(make_row(kind, moment(), tx_no, mch_no, rng, rng.choice(PAY_METHODS)))

    rows.sort(key=lambda item: item[0])
    return rows


def write_month(path: Path, rows: list[list[str]], nickname: str) -> None:
    income = [item for item in rows if item[4] == "收入"]
    expense = [item for item in rows if item[4] == "支出"]
    neutral = [item for item in rows if item[4] not in ("收入", "支出")]

    def total(items: list[list[str]]) -> str:
        cents = 0
        for item in items:
            yuan, fen = item[5].lstrip("¥").split(".")
            cents += int(yuan) * 100 + int(fen)
        return plain(cents)

    preamble = [
        ["微信支付账单明细"] + [""] * 10,
        [f"微信昵称：[{nickname}]"] + [""] * 10,
        [f"起始时间：[{rows[0][0][:10]} 00:00:00] 终止时间：[{rows[-1][0][:10]} 23:59:59]"]
        + [""] * 10,
        ["导出类型：[全部]"] + [""] * 10,
        # 用最后一笔交易时间当导出时间，保证同 seed 生成的文件逐字节一致
        [f"导出时间：[{rows[-1][0]}]"] + [""] * 10,
        [""] * 11,
        [f"共{len(rows)}笔记录"] + [""] * 10,
        [f"收入：{len(income)}笔 {total(income)}元"] + [""] * 10,
        [f"支出：{len(expense)}笔 {total(expense)}元"] + [""] * 10,
        [f"中性交易：{len(neutral)}笔 {total(neutral)}元"] + [""] * 10,
        ["注："] + [""] * 10,
        ["1. 充值/提现/理财通购买/零钱通存取/信用卡还款等交易，将计入中性交易"] + [""] * 10,
        ["2. 本明细仅展示当前账单中的交易，不包括已删除的记录"] + [""] * 10,
        ["3. 本明细仅供个人对账使用"] + [""] * 10,
        [""] * 11,
        ["----------------------微信支付账单明细列表--------------------"] + [""] * 10,
    ]

    # 真实导出带 BOM，这里保持一致
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(preamble)
        writer.writerow(HEADER)
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成真实节奏的微信账单模拟数据")
    parser.add_argument("--months", type=int, default=3, help="生成几个月，从 --start 起连续")
    parser.add_argument("--per-month", type=int, default=900, help="每月大约多少笔")
    parser.add_argument("--start", default="2026-08", help="起始月份 YYYY-MM")
    parser.add_argument("--out", default="samples", help="输出目录")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--nickname", default="小林便利店")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    target = Path(args.out)
    target.mkdir(parents=True, exist_ok=True)

    year, month = (int(part) for part in args.start.split("-"))
    for index in range(args.months):
        offset = month - 1 + index
        y = year + offset // 12
        m = offset % 12 + 1
        start = datetime(y, m, 1)
        # 每月笔数上下浮动 10%，更像真实店铺
        count = max(50, int(args.per_month * rng.uniform(0.9, 1.1)))
        rows = build_month(start, count, rng)
        path = target / f"微信支付账单_{y}-{m:02d}.csv"
        write_month(path, rows, args.nickname)
        print(f"{path}  {len(rows)} 行")


if __name__ == "__main__":
    main()
