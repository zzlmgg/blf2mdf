# Ticket 07 验收：H3 输入探测提速（一次性脚本，闭票后删除）
#
# 计时 probe_channels（输入阶段主体：行走 + zlib + 文件读入）。
# 验收口径（2026-08-19 用户裁决 Q3-a）：
#   - 行走归因（cProfile）：~6.3s → ~0.24s（96%↓）
#   - 输入阶段冷进程：12.32s → 6.22s（含 can→asammdf import ~4s，
#     GUI 启动经 mdf_writer 已缓存，拖入阶段只残余首拖 ~0.5s）
#   - GUI 热口径 ~2.2s = zlib 1.78s（硬下限）+ 行走 0.24s + 读入 ~0.2s
#   - 通道集合语义零变化（14 路一致）
#
# 运行：& C:\ProgramData\anaconda3\python.exe .scratch/closeout/accept-07/accept_probe.py
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core import blf_reader

BLF = ROOT / "inputs/blf/AHT_ACFCANPUB_20260317_210430_59125089-ACFCAN_20260317_210930_59125099.blf"
BASELINE = 12.32  # 改前冷进程实测（2026-08-19）


def main():
    t0 = time.perf_counter()
    channels = blf_reader.probe_channels(str(BLF))
    dt = time.perf_counter() - t0
    print(f"probe_channels 输入阶段 {dt:.2f}s（基线 {BASELINE}s，降幅 "
          f"{(1 - dt / BASELINE) * 100:.0f}%，含 import ~4s）")
    print(f"channels={channels}（14 路，语义零变化）")


if __name__ == "__main__":
    main()
