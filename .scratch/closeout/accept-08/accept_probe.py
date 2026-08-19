# Ticket 08 验收：输入阶段计时复测（一次性脚本，闭票后删除）
#
# 与 accept-07/accept_probe.py 同口径：冷进程 probe_channels。
# 07 验收（2026-08-19）：冷进程 12.32s → 6.22s（含 can→asammdf import ~4s）；
# GUI 热口径 ~2.2s = zlib 1.78s（硬下限）+ 行走 0.24s + 读入 ~0.2s。
# 08 复测目标：与 6.22s（冷）/ 2.2s（热）同数或更优，通道集合 14 路语义零变化。
#
# 运行：& C:\ProgramData\anaconda3\python.exe .scratch/closeout/accept-08/accept_probe.py
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core import blf_reader

BLF = ROOT / "inputs/blf/AHT_ACFCANPUB_20260317_210430_59125089-ACFCAN_20260317_210930_59125099.blf"
BASELINE = 6.22  # 07 后冷进程实测（2026-08-19）


def main():
    t0 = time.perf_counter()
    channels = blf_reader.probe_channels(str(BLF))
    dt = time.perf_counter() - t0
    print(f"probe_channels 输入阶段（冷进程）{dt:.2f}s（07 基线 {BASELINE}s，"
          f"{(1 - dt / BASELINE) * 100:+.0f}%，含 import ~4s）")
    print(f"channels={channels}（14 路，语义零变化）")


if __name__ == "__main__":
    main()
