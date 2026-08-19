# PROTOTYPE — Proto E 落地验收：AHT 逐位不变对拍（一次性脚本，裁决后删除）
#
# 复刻 GUI 绑定链：probe_channels 通道探测 → ccu3.0 项目 DBC 加载 →
# dbc_对应关系.txt 映射 → auto_bindings → decide_bindings（prev=None）→
# bindings（含 NO_DATA 行，与 _start_convert 的 combo.currentData 一致）。
# 转换参数与 GUI 一致：parallel=True、raw_export=False、stats_export=True。
#
# 运行：& C:\ProgramData\anaconda3\python.exe .scratch/closeout/proto-e-adapter/accept_aht.py
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core import blf_reader, converter, project_loader
from gui.binding import decide_bindings

BLF = ROOT / "inputs/blf/AHT_ACFCANPUB_20260317_210430_59125089-ACFCAN_20260317_210930_59125099.blf"
CCU3_ROOT = ROOT / "inputs/dbc_ccu3.0"
OUT = ROOT / ".scratch/closeout/proto-e-adapter/AHT_20260819_e.mdf"


def main():
    dbcs = project_loader.load_project(CCU3_ROOT, "AHT")
    mapping = project_loader.load_mapping(CCU3_ROOT / "dbc_对应关系.txt")
    auto = project_loader.auto_bindings(dbcs, mapping)
    channels = blf_reader.probe_channels(str(BLF))
    rows = decide_bindings(channels, auto, None, [d.path for d in dbcs])
    by_path = {d.path: d for d in dbcs}
    bindings = {row.channel: by_path[row.binding] for row in rows if row.binding}
    print(f"BLF 通道 {channels}")
    print(f"绑定 {len(bindings)} 路: {sorted(bindings)}")

    result = converter.convert(str(BLF), bindings, str(OUT),
                               raw_export=False, parallel=True,
                               stats_export=True)
    print("转换完成:", result)


if __name__ == "__main__":
    main()
