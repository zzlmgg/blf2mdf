"""CLI 退出码契约测试：进程级（subprocess），合成 MDF，无样例依赖。

两 CLI 薄壳依赖 sys.path[0]=tools/ 的兄弟导入（实测 import tools.compare_two_mdf
报 ModuleNotFoundError: No module named 'mdf_compare'），模块导入路径不可用——
退出码是进程级契约，以子进程断言 returncode（端到端覆盖 argparse 接线与 dims 映射）。
"""
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

from mdf_factory import _write_mdf, _simple, hole_stat_groups, REF_LAYOUT
from tools.mdf_compare import STAT_NAMES

ROOT = Path(__file__).resolve().parent.parent


def _run(args, timeout=60):
    """子进程运行：显式钉住子进程输出编码为 UTF-8 并按 UTF-8 解码——工具输出含中文，
    默认各随 locale 会在本测试进程解出 UnicodeDecodeError（读取线程报错、stdout 读空）。
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          timeout=timeout)


def _ref_idx():
    """把 REF_LAYOUT 的偏移表转成 full_compare 的 --stats-ref-idx 字符串。"""
    return ",".join(f"{k}:{v}" for k, v in REF_LAYOUT[1].items())


def test_compare_two_mdf_exit_codes(tmp_path):
    """退出码契约：0 = 一致；非 0 = 存在差异；--skip-values 关闭数值维度后仅数值差异对 → 0。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups)
    r = _run(["tools/compare_two_mdf.py", str(p1), str(p2)])
    assert r.returncode == 0
    # 单点改值 → 1
    groups[0][1][0] = ("SigA", [1.0, 2.0, 99.0], np.float64)
    _write_mdf(p2, groups)
    r = _run(["tools/compare_two_mdf.py", str(p1), str(p2)])
    assert r.returncode == 1
    # --skip-values 后仅数值差异对 → 0（数值维度关闭生效）
    r = _run(["tools/compare_two_mdf.py", str(p1), str(p2), "--skip-values"])
    assert r.returncode == 0


def test_full_compare_exit_codes(tmp_path):
    """退出码契约：0 = 判定一致（报告写入）；1 = 存在判定差异（报告仍写入）；
    --no-report 不产生报告文件。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups)
    base = ["tools/full_compare.py", str(p1), str(p2),
            "--stats-ref-block", str(REF_LAYOUT[0]),
            "--stats-ref-idx", _ref_idx(), "--outdir", str(tmp_path)]
    r = _run(base)
    assert r.returncode == 0
    reports = list(tmp_path.glob("compare_*.md"))
    assert reports  # 报告已写入
    reports[0].unlink()
    # 单点改值 → 1，且报告仍写入（清掉上一份后应恰好 1 份）
    groups[0][1][0] = ("SigA", [1.0, 2.0, 99.0], np.float64)
    _write_mdf(p2, groups)
    r = _run(base)
    assert r.returncode == 1
    reports = list(tmp_path.glob("compare_*.md"))
    assert len(reports) == 1
    reports[0].unlink()
    # --no-report → 判定照跑（差异 → 1）但不写报告文件
    r = _run(base + ["--no-report"])
    assert r.returncode == 1
    assert not list(tmp_path.glob("compare_*.md"))


def test_full_compare_required_args(tmp_path):
    """--stats-ref-block / --stats-ref-idx 必填：缺省 → argparse 解析失败，非 0 退出。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups)
    r = _run(["tools/full_compare.py", str(p1), str(p2)])
    assert r.returncode != 0
    r = _run(["tools/full_compare.py", str(p1), str(p2),
              "--stats-ref-block", str(REF_LAYOUT[0])])
    assert r.returncode != 0


def test_full_compare_known_diff_line_printed_not_counted(tmp_path):
    """记录空洞样例：统计 t 轴长度不等但契约满足 → 只落一条 KNOWN_DIFF_PREFIX
    说明行；统计组采样数差异（10 项 × 1 通道，真实差异）仍计入判定 → 退出码 1，
    说明行照常打印且单独计数。"""
    ours, ref = hole_stat_groups()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, ours)
    _write_mdf(p2, ref)
    r = _run(["tools/full_compare.py", str(p1), str(p2),
              "--stats-ref-block", str(REF_LAYOUT[0]),
              "--stats-ref-idx", _ref_idx(), "--no-report"])
    assert "已知差异: 统计 t 轴" in r.stdout
    assert f"{len(STAT_NAMES)} 处差异（另有 1 条已知差异说明，不计入）" in r.stdout
    assert r.returncode == 1
    # 关闭 stats 维度后，唯一残留的 t 轴说明行也随之消失 → 判定一致
    r = _run(["tools/full_compare.py", str(p1), str(p2),
              "--stats-ref-block", str(REF_LAYOUT[0]),
              "--stats-ref-idx", _ref_idx(), "--skip-stats", "--no-report"])
    assert r.returncode == 0
