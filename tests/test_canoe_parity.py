"""CANoe 参考质量校验（pytest 套件成员）：需求线硬门。

硬门 = 信号组全部信号值 + 时间戳逐位 + '1s' 统计组 t 轴逐位（判定路径复用
tools/verify_vs_canoe.py 的 compare_one，单一实现，不复制对拍逻辑）。

转换产物由 tools/verify_vs_canoe.py（不带 --skip-convert）预先产生于
outputs/verify_canoe/（用户按需管理）；产物缺失 → skip（先跑转换再跑本用例）。
"""
from pathlib import Path

import pytest

from tools.verify_vs_canoe import SAMPLES, artifact_filename, compare_one, latest_artifact

ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "outputs/verify_canoe"


@pytest.mark.parametrize("platform, project, sample, blf, canoe", SAMPLES)
def test_canoe_parity_hard_gate(platform, project, sample, blf, canoe):
    ours = latest_artifact(OUTDIR, platform, project, sample)
    if ours is None:
        expect = artifact_filename("<时间戳>", platform, project, sample)
        pytest.skip(f"{OUTDIR} 下无 {expect}，先运行 "
                    f"python tools/verify_vs_canoe.py 转换")
    label = f"{platform}/{project}/{sample}"
    _, val, stat_t, _ = compare_one(ours, canoe)
    assert not val, f"{label}: 信号值/时间戳 {len(val)} 处差异:\n" + "\n".join(val)
    assert not stat_t, f"{label}: 统计组 t 轴 {len(stat_t)} 处差异:\n" + "\n".join(stat_t)
