# Ticket 04 落地验收：AHT 逐位不变对拍（一次性脚本，裁决后删除）
#
# 复刻 GUI 绑定链（与 proto-e-adapter/accept_aht.py 同形）：
# probe_channels 通道探测 → ccu3.0 项目 DBC 加载 → dbc_对应关系.txt 映射 →
# auto_bindings → decide_bindings（prev=None）→ bindings。
# 转换参数与 GUI 一致：parallel=True、raw_export=False、stats_export=True。
# 验收：compare_two_mdf vs outputs/cmp_20260817_AHT.mdf（230 组逐位一致）
# + full_compare vs inputs/mdf_canoe/AHT.mdf（3434 处已知真实差异 = 基线同分）
# + 总耗时复测（Q1-Q6 均为行为中性重构，不应有可观测性能变化）。
#
# 运行：& C:\ProgramData\anaconda3\python.exe .scratch/closeout/accept-04/accept_aht.py
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

    t0 = time.perf_counter()
    result = converter.convert(str(BLF), bindings, str(OUT),
                               raw_export=False, parallel=True,
                               stats_export=True)
    print(f"总耗时 {time.perf_counter() - t0:.2f}s  warnings={result.warnings}")
    print("转换完成:", result)


if __name__ == "__main__":
    main()
