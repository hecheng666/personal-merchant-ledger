"""命令行端到端测试：真正跑一遍 main()，而不是只测函数。

CLI 是用户唯一会碰到的入口，最容易出现「函数都对、串起来就坏」。
"""

from pathlib import Path

from app.cli import main

ROOT = Path(__file__).parent.parent
REAL = ROOT / "fixtures" / "wechat-sample-real.csv"


def run(tmp_path: Path, *args: str) -> int:
    return main(
        [
            "--store",
            str(tmp_path / "ledger.db"),
            "--memory",
            str(tmp_path / "memory.db"),
            *args,
        ]
    )


def test_import_and_report(tmp_path, capsys):
    html = tmp_path / "out" / "月报.html"
    code = run(
        tmp_path,
        "import",
        "--file",
        str(REAL),
        "--month",
        "2019-09",
        "--html",
        str(html),
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "成功 27" in output
    assert "待确认" in output
    assert html.exists()
    assert "经营月报" in html.read_text(encoding="utf-8")


def test_missing_file_is_friendly(tmp_path, capsys):
    code = run(tmp_path, "import", "--file", str(tmp_path / "不存在.csv"))
    assert code == 1
    assert "找不到文件" in capsys.readouterr().out


def test_clear_wipes_transactions_and_cash(tmp_path, capsys):
    assert run(tmp_path, "import", "--file", str(REAL), "--month", "2019-09") == 0
    assert run(tmp_path, "cash", "set", "2026-10-01", "380") == 0
    assert run(tmp_path, "clear", "--yes") == 0
    capsys.readouterr()
    # 清空后既没有交易月份，也没有现金月份
    assert run(tmp_path, "report", "--month", "2026-10") == 1
    assert "还没有导入任何账单" in capsys.readouterr().out


def test_cash_bad_input_is_friendly(tmp_path, capsys):
    code = run(tmp_path, "cash", "set", "2026-13-01", "100")
    assert code == 1
    assert "录入失败" in capsys.readouterr().out
