"""CANoe 参考质量校验（pytest 套件成员）：需求线硬门。

硬门 = 信号组全部信号值 + 时间戳逐位 + '1s' 统计组 t 轴逐位（判定路径复用
tools/verify_vs_canoe.py 的 compare_one，单一实现，不复制对拍逻辑）。

转换产物由 tools/verify_vs_canoe.py（不带 --skip-convert）预先产生于
outputs/verify_canoe/（用户按需管理）；产物缺失 → skip（先跑转换再跑本用例）。
"""
from pathlib import Path

import pytest

from tools.verify_vs_canoe import SAMPLES, compare_one

ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "outputs/verify_canoe"


def _latest(project):
    hits = sorted(OUTDIR.glob(f"cmp_*_{project}.mdf"), key=lambda p: p.stat().st_mtime)
    return hits[-1] if hits else None


@pytest.mark.parametrize("project, blf, canoe", SAMPLES)
def test_canoe_parity_hard_gate(project, blf, canoe):
    ours = _latest(project)
    if ours is None:
        pytest.skip(f"{OUTDIR} 下无 cmp_*_{project}.mdf，先运行 "
                    f"python tools/verify_vs_canoe.py 转换")
    _, val, stat_t, _ = compare_one(ours, canoe)
    assert not val, f"{project}: 信号值/时间戳 {len(val)} 处差异:\n" + "\n".join(val)
    assert not stat_t, f"{project}: 统计组 t 轴 {len(stat_t)} 处差异:\n" + "\n".join(stat_t)
