"""测试前置：合成样本体积较大，不入库，缺失时自动生成。

真实样例（wechat-sample-real.csv）是入库的，因为它不可重建。
合成样本（3 万行、4MB）由 scripts/gen_synthetic.py 确定性生成，所以不提交。
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "fixtures"
REAL = FIXTURES / "wechat-sample-real.csv"
SYNTH = FIXTURES / "wechat-synthetic-30k.csv"


@pytest.fixture(scope="session", autouse=True)
def ensure_fixtures():
    """在收集测试之前把数据准备好，缺什么补什么。"""
    if not REAL.exists():
        pytest.exit(
            f"缺少真实样例 {REAL}。\n"
            "这个文件无法自动生成，必须从版本库里恢复：git checkout fixtures/wechat-sample-real.csv",
            returncode=1,
        )

    if not SYNTH.exists():
        FIXTURES.mkdir(exist_ok=True)
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "gen_synthetic.py"),
             "--rows", "30000", "--out", str(SYNTH)],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
