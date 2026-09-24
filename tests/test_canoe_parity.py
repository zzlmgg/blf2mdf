"""CANoe 金样本守卫（pytest -m golden）。

收集 canoe_golden 的全部样本：用 verify_vs_canoe.convert_one 把 source.blf
转成 mdf，再与同目录 canoe.mdf 做现有硬门对比（compare_one）。
整段墙钟上限 = 登记墙钟 + min(2s, 登记墙钟的 10%)。
产物写到 outputs/verify_canoe/，断言输出后删除，不在该目录堆积。
"""
import time
from datetime import datetime
from pathlib import Path

import pytest

from tools.verify_vs_canoe import SAMPLES, artifact_filename, compare_one, convert_one

ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "outputs/verify_canoe"

# 2026-09-24 本机 convert_one 整段墙钟（含加载 DBC 与通道探测），单位秒。
_WALL_S = {
    ("ccu3.0", "A19G1", "A19G1_ACFCAN_00112_20260614_141114"): 5.01,
    ("ccu3.0", "A02Y",
     "A02Y_ACFCANPUB_20260917_221900_59655158-ACFCANPUB_20260917_222500_59655170"): 5.64,
    ("ccu3.0", "A66T",
     "A66T_ACFCANPUB_20260917_151500_59654310-ACFCANPUB_20260917_154000_59654360"): 13.76,
    ("ccu3.0", "AHT",
     "AHT_ACFCANPUB_20260317_210430_59125089-ACFCAN_20260317_210930_59125099"): 15.15,
}


def _time_limit(platform, project, sample):
    wall = _WALL_S[(platform, project, sample)]
    return wall + min(2.0, wall * 0.10)


@pytest.mark.golden
@pytest.mark.parametrize("platform, project, sample, blf, canoe", SAMPLES)
def test_canoe_parity_hard_gate(platform, project, sample, blf, canoe):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ours = OUTDIR / artifact_filename(ts, platform, project, sample)
    label = f"{platform}/{project}/{sample}"
    try:
        t0 = time.perf_counter()
        convert_one(platform, project, blf, ours)
        elapsed = time.perf_counter() - t0
        _, val, stat_t, _ = compare_one(ours, canoe)
        assert not val, f"{label}: 信号值/时间戳 {len(val)} 处差异:\n" + "\n".join(val)
        assert not stat_t, f"{label}: 统计组 t 轴 {len(stat_t)} 处差异:\n" + "\n".join(stat_t)
        limit = _time_limit(platform, project, sample)
        assert elapsed <= limit, (
            f"{label}: 转换整段墙钟 {elapsed:.2f}s 超过上限 {limit:.2f}s")
    finally:
        ours.unlink(missing_ok=True)
