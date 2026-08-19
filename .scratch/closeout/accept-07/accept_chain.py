# Ticket 07 验收：对拍链回归（一次性脚本，闭票后删除）
#
# 07 只动 probe_channels（GUI 输入阶段，不在转换路径）——本脚本为
# 「无意外回归」防御：转换 AHT → compare_two_mdf vs 20260817 基线
# （230 组逐位一致）+ full_compare vs CANoe 参考（70/70 组、3434 处
# 已知真实差异 = 基线同分）。
#
# 运行：& C:\ProgramData\anaconda3\python.exe .scratch/closeout/accept-07/accept_chain.py
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core import blf_reader, converter, project_loader
from gui.binding import decide_bindings

BLF = ROOT / "inputs/blf/AHT_ACFCANPUB_20260317_210430_59125089-ACFCAN_20260317_210930_59125099.blf"
CCU3_ROOT = ROOT / "inputs/dbc_ccu3.0"
OUT = ROOT / "outputs/cmp_20260820_AHT.mdf"
BASE = ROOT / "outputs/cmp_20260817_AHT.mdf"
CANOE = ROOT / "inputs/mdf_canoe/AHT.mdf"
PY = sys.executable

STATS_IDX = ("StdData:4,StdDataRate:5,ExtData:6,ExtDataRate:7,"
             "StdRemote:8,StdRemoteRate:9,ExtRemote:10,ExtRemoteRate:11,"
             "ErrorFrames:12,ErrorFrameRate:13")


def main():
    dbcs = project_loader.load_project(CCU3_ROOT, "AHT")
    mapping = project_loader.load_mapping(CCU3_ROOT / "dbc_对应关系.txt")
    auto = project_loader.auto_bindings(dbcs, mapping)
    channels = blf_reader.probe_channels(str(BLF))
    rows = decide_bindings(channels, auto, None, [d.path for d in dbcs])
    by_path = {d.path: d for d in dbcs}
    bindings = {row.channel: by_path[row.binding] for row in rows if row.binding}
    print(f"BLF 通道 {channels}（14 路，与基线一致）")

    t0 = time.perf_counter()
    result = converter.convert(str(BLF), bindings, str(OUT),
                               raw_export=False, parallel=True,
                               stats_export=True)
    print(f"转换总耗时 {time.perf_counter() - t0:.2f}s  warnings={result.warnings}")

    print("== compare_two_mdf vs 20260817 基线 ==")
    r = subprocess.run([PY, "tools/compare_two_mdf.py", str(OUT), str(BASE)])
    print(f"compare_two_mdf exit {r.returncode}（0 = 230 组逐位一致）")

    print("== full_compare vs CANoe 参考 ==")
    r = subprocess.run([PY, "tools/full_compare.py", str(OUT), str(CANOE),
                        "--stats-ref-block", "22", "--stats-ref-idx", STATS_IDX,
                        "--outdir", str(ROOT / ".scratch/closeout/accept-07")])
    print(f"full_compare exit {r.returncode}（1 = 3434 处已知真实差异，同基线）")


if __name__ == "__main__":
    main()
