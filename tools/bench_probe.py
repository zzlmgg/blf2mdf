r"""基准对拍：probe_channels vs list_channels（输入阶段轻量探测加速验证）。

用法：& C:\ProgramData\anaconda3\python.exe tools/bench_probe.py
输出：每文件两种方式的耗时、通道集合是否一致、加速比。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.blf_reader import list_channels, probe_channels

SAMPLES = [
    "inputs/blf/A19G1_ACFCAN_00112_20260614_141114.blf",
    "inputs/blf/ACFCANPUB_20260722_104000_59489600-ACFCANPUB_20260722_104930_59489619.blf",
    "inputs/blf/AHT_ACFCANPUB_20260317_210430_59125089-ACFCAN_20260317_210930_59125099.blf",
]

for rel in SAMPLES:
    p = Path(rel)
    if not p.exists():
        print(f"skip (缺失): {rel}")
        continue
    t0 = time.perf_counter()
    ch_probe = probe_channels(str(p))
    t_probe = time.perf_counter() - t0
    t0 = time.perf_counter()
    ch_list = list_channels(str(p))
    t_list = time.perf_counter() - t0
    same = ch_probe == ch_list
    print(f"{p.name} ({p.stat().st_size / 1e6:.0f}MB)")
    print(f"  probe   {t_probe:6.2f}s  通道={ch_probe}")
    print(f"  list    {t_list:6.2f}s  通道={ch_list}")
    print(f"  加速比  {t_list / t_probe:5.1f}x   通道集合一致={same}")
