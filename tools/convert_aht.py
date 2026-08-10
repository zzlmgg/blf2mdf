"""复现 AHT 转换（与 GUI 绑定一致）：dbc_对应关系.txt 的通道-DBC 映射。"""
import sys
sys.path.insert(0, r"e:\projects\blf_dbc")

from core.converter import convert
from core.dbc_loader import load

DBC_DIR = r"e:\projects\blf_dbc\inputs\dbc_ccu3.0\AHT"
BLF = (r"e:\projects\blf_dbc\inputs\blf\AHT_ACFCANPUB_20260317_210430_59125089-"
       r"ACFCAN_20260317_210930_59125099.blf")
OUT = sys.argv[1] if len(sys.argv) > 1 else r"e:\projects\blf_dbc\outputs\AHT_ts_fix.mdf"

# dbc_对应关系.txt：PFCAN1→CAN1 CFCAN2→CAN3 CFCAN3→CAN6 ZFCANF→CAN8
# ZFCANL→CAN9 ZFCANR→CAN10 ZFCANT→CAN11 IFCAN→CAN12 CFCAN1→CAN13 PFCAN2→CAN15
MAPPING = {
    1: "PFCAN1.dbc",
    3: "CFCAN2.dbc",
    6: "CFCAN3.dbc",
    8: "ZFCANF.dbc",
    9: "ZFCANL.dbc",
    10: "ZFCANR.dbc",
    11: "ZFCANT.dbc",
    12: "IFCAN.dbc",
    13: "CFCAN1.dbc",
    15: "PFCAN2.dbc",
}

bindings = {ch: load(f"{DBC_DIR}/{name}") for ch, name in MAPPING.items()}
result = convert(BLF, bindings, OUT)
print(f"输出: {OUT}")
print(f"时长: {result.duration_seconds:.3f}s")
for s in result.summaries:
    if s.bound:
        print(f"  CAN{s.channel}: 解码 {s.decoded_frames} 帧, {s.signal_count} 信号, 未知 {s.unknown_frames}")
