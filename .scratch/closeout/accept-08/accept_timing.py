# Ticket 08 验收：转换总耗时复测（干净负载，一次性脚本，闭票后删除）
#
# 与 accept_chain.py 同流程但只计时（含 warm probe ≈ GUI 热口径）：
# 转换总耗时 vs 本机基线 29.8s（H1+H2a 后，master plan §5.2；
# H3 不在转换路径，预期维持同数，允许机器漂移）。
#
# 运行：& C:\ProgramData\anaconda3\python.exe .scratch/closeout/accept-08/accept_timing.py
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core import blf_reader, converter, project_loader
from gui.binding import decide_bindings

BLF = ROOT / "inputs/blf/AHT_ACFCANPUB_20260317_210430_59125089-ACFCAN_20260317_210930_59125099.blf"
CCU3_ROOT = ROOT / "inputs/dbc_ccu3.0"
OUT = ROOT / "outputs/cmp_20260819_AHT.mdf"
BASELINE = 29.8  # 本机 H1+H2a 后（master plan §5.2）


def main():
    dbcs = project_loader.load_project(CCU3_ROOT, "AHT")
    mapping = project_loader.load_mapping(CCU3_ROOT / "dbc_对应关系.txt")
    auto = project_loader.auto_bindings(dbcs, mapping)

    t0 = time.perf_counter()
    channels = blf_reader.probe_channels(str(BLF))
    t_probe = time.perf_counter() - t0
    print(f"probe_channels（warm，≈GUI 热口径）{t_probe:.2f}s（07 热口径 ~2.2s）")

    rows = decide_bindings(channels, auto, None, [d.path for d in dbcs])
    by_path = {d.path: d for d in dbcs}
    bindings = {row.channel: by_path[row.binding] for row in rows if row.binding}

    t0 = time.perf_counter()
    result = converter.convert(str(BLF), bindings, str(OUT),
                               raw_export=False, parallel=True,
                               stats_export=True)
    dt = time.perf_counter() - t0
    print(f"转换总耗时 {dt:.2f}s（本机基线 {BASELINE}s，"
          f"{(1 - dt / BASELINE) * 100:+.0f}%） warnings={result.warnings}")


if __name__ == "__main__":
    main()
