"""生成合成微信账单，用于大数据量与性能测试。

格式严格对齐 docs/bill-format.md 中记录的真实样例：
- 16 行说明文字 + 1 行分隔线 + 表头
- 金额带 ¥ 前缀
- 交易单号/商户单号尾部带 TAB
- 含退款行、已关闭行、中性交易、重复对手方

用法：
    python scripts/gen_synthetic.py --rows 30000 --out fixtures/wechat-synthetic-30k.csv
"""

import argparse
import csv
import random
from datetime import datetime, timedelta

HEADER = [
    "交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)",
    "支付方式", "当前状态", "交易单号", "商户单号", "备注",
]

CUSTOMERS = [f"顾客{n:03d}" for n in range(1, 201)]          # 200 个重复出现的顾客
SUPPLIERS = [f"供货商{n:02d}" for n in range(1, 21)]          # 20 个供货商
PRIVATE = ["家人", "老同学", "小区团购", "房东"]

# 只有少数顾客会加好友转账（现实比例约 15%），这样「按对手方聚合」才有意义：
# 3000+ 笔转账会被压缩到几十个待确认项
TRANSFER_PEERS = CUSTOMERS[:30] + PRIVATE + SUPPLIERS

# 零售客单价的真实分布：多数小额，少数大额
AMOUNT_BUCKETS = [
    (0.55, 3, 20),      # 55% 概率落在 3-20 元
    (0.85, 20, 60),     # 30% 落在 20-60 元
    (0.97, 60, 200),    # 12% 落在 60-200 元
    (1.00, 200, 800),   # 3% 落在 200-800 元
]


def pick_amount() -> float:
    r = random.random()
    for threshold, low, high in AMOUNT_BUCKETS:
        if r <= threshold:
            return round(random.uniform(low, high), 2)
    return round(random.uniform(3, 20), 2)


def make_rows(rows: int, start: datetime):
    out = []
    total_minutes = 30 * 24 * 60  # 覆盖 30 天

    for _ in range(rows):
        ts = start + timedelta(minutes=random.randint(0, total_minutes))
        time_s = ts.strftime("%Y-%m-%d %H:%M:%S")
        tx_no = f"{random.randint(10**17, 10**18)}\t"
        mch_no = f"{random.randint(10**11, 10**12)}\t"
        r = random.random()

        if r < 0.85:
            # 柜台收款码 —— 营业收入的绝对主力
            row = [time_s, "二维码收款", random.choice(CUSTOMERS),
                   "收款方备注:二维码收款", "收入", f"¥{pick_amount():.2f}",
                   random.choice(["零钱", "零钱通"]), "已收钱", tx_no, mch_no, "/"]
        elif r < 0.93:
            # 加好友后转账 —— 无法自动判定，进待确认
            peer = random.choice(TRANSFER_PEERS)
            row = [time_s, "转账", peer, "转账备注:微信转账", "收入",
                   f"¥{pick_amount():.2f}", "零钱", "已收钱", tx_no, mch_no, "/"]
        elif r < 0.96:
            # 进货支出
            row = [time_s, "转账", random.choice(SUPPLIERS), "转账备注:微信转账", "支出",
                   f"¥{random.uniform(200, 5000):.2f}", "零钱通", "朋友已收钱",
                   tx_no, mch_no, "/"]
        elif r < 0.975:
            # 红包 —— 私人往来
            row = [time_s, "微信红包", random.choice(PRIVATE), "/", "支出",
                   f"¥{random.choice([6.66, 8.88, 20, 50, 100]):.2f}", "零钱",
                   "支付成功", tx_no, mch_no, "/"]
        elif r < 0.985:
            # 提现 —— 中性交易，手续费在备注里
            amt = round(random.uniform(100, 3000), 2)
            fee = 0.1
            row = [time_s, "零钱提现", "招商银行()", "/", "/", f"¥{amt + fee:.2f}",
                   "零钱", "提现已到账", tx_no, mch_no, f"服务费¥{fee:.2f}"]
        elif r < 0.995:
            # 退款行 —— 必须不计入营业额
            row = [time_s, "二维码收款", random.choice(CUSTOMERS), "收款方备注:二维码收款",
                   "支出", f"¥{pick_amount():.2f}", "零钱", "已全额退款",
                   tx_no, mch_no, "已全额退款"]
        else:
            # 已关闭 —— 同样必须剔除
            row = [time_s, "商户消费", random.choice(SUPPLIERS), "总共消费", "支出",
                   f"¥{pick_amount():.2f}", "零钱", "已关闭", tx_no, mch_no, "/"]

        out.append(row)

    out.sort(key=lambda x: x[0])
    return out


def write_file(path: str, rows):
    preamble = [
        ["微信支付账单明细"] + [""] * 10,
        ["微信昵称：[合成测试数据]"] + [""] * 10,
        [f"起始时间：[{rows[0][0][:10]} 00:00:00] 终止时间：[{rows[-1][0][:10]} 23:59:59]"] + [""] * 10,
        ["导出类型：[全部]"] + [""] * 10,
        ["导出时间：[2026-11-15 16:00:00]"] + [""] * 10,
        [""] * 11,
        [f"共{len(rows)}笔记录"] + [""] * 10,
        ["收入：—"] + [""] * 10,
        ["支出：—"] + [""] * 10,
        ["中性交易：—"] + [""] * 10,
        ["注："] + [""] * 10,
        ["1. 充值/提现/理财通购买/零钱通存取/信用卡还款等交易，将计入中性交易"] + [""] * 10,
        ["2. 本明细仅展示当前账单中的交易，不包括已删除的记录"] + [""] * 10,
        ["3. 本明细仅供个人对账使用"] + [""] * 10,
        [""] * 11,
        ["----------------------微信支付账单明细列表--------------------"] + [""] * 10,
    ]

    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerows(preamble)
        w.writerow(HEADER)
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=30000)
    ap.add_argument("--out", default="fixtures/wechat-synthetic-30k.csv")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    random.seed(args.seed)
    rows = make_rows(args.rows, datetime(2026, 10, 1))
    write_file(args.out, rows)
    print(f"已生成 {len(rows)} 行 -> {args.out}")


if __name__ == "__main__":
    main()
