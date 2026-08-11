"""冻结包验证探针（临时）：以真实应用同款参数（parallel=True）做一次
A19G1 样例转换，验证打包后的多进程 finish 路径（spawn → worker → 合并）。

用法：与 blf2mdf.spec 同裁剪配置打成一个 console exe，exe 同目录需有
inputs/blf/A19G1_ACFCAN_00112_20260614_141114.blf 与 inputs/dbc/VDCCCU_CANFD1.dbc。
"""
import os
import sys
import time
from pathlib import Path

print(f"[PROBE] pid={os.getpid()} argv={sys.argv}", file=sys.stderr)

from core.converter import convert
from core.dbc_loader import load

root = Path(sys.executable).resolve().parent
blf = root / "inputs" / "blf" / "A19G1_ACFCAN_00112_20260614_141114.blf"
dbc = root / "inputs" / "dbc" / "VDCCCU_CANFD1.dbc"
out = root / "outputs" / "frozen_probe.mdf"
if not blf.is_file() or not dbc.is_file():
    print(f"PROBE_FAIL 缺少样例: {blf} / {dbc}")
    sys.exit(1)

t0 = time.time()
result = convert(str(blf), {1: load(dbc), 2: None}, str(out),
                 progress_cb=lambda s, p: None, raw_export=False, parallel=True)
dt = time.time() - t0
print(f"PROBE_OK dt={dt:.1f}s size={out.stat().st_size / 1024:.0f}KB out={out}")
print(f"summaries={len(result.summaries)} duration={result.duration_seconds:.1f}s")
