# PROTOTYPE — Proto E 等价性预验证（一次性脚本，裁决后删除）
#
# 验证：adapter 形状（payload_block / payload，glen 恒存在归一）与现消费面
# （converter._bucket_block 双分支 / 测试 _payload 双分支）逐字节等价。
# 手工构造覆盖语义角落 + 真实样例文件全容器两路径抽检。
#
# 运行：python .scratch/closeout/proto-e-adapter/check.py
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core import blf_vector
from core.blf_vector import ContainerFrames, iter_container_frames
from core.converter import _bucket_block

# ── 被替换的旧消费实现（参考副本，与 tests/test_blf_vector._payload 同语义）──
def _payload_old(cf, i):
    o = int(cf.data_off[i]); n = int(cf.data_len[i])
    if cf.scattered:
        g = int(cf.glen[i])
        return bytes(cf.data8[o:o + g]) + b"\x00" * (n - g)
    return bytes(cf.data8[o:o + n])

# ── 原型 adapter（拟落入 ContainerFrames 的方法，模块函数形式）──
def payload_block_proto(cf, sel):
    lens = cf.data_len[sel]
    n = len(lens)
    if n == 0:
        return np.zeros((0, 0), dtype=np.uint8)
    L = int(lens.max())
    idt = np.int32 if cf.data8.size < 2 ** 31 else np.int64
    src2 = cf.data_off[sel].astype(idt)[:, None] + np.arange(L, dtype=idt)
    block = np.take(cf.data8, src2, mode="clip")
    if not bool(np.all(cf.glen[sel] == L)):
        block[np.arange(L)[None, :] >= cf.glen[sel][:, None]] = 0
    return block

def payload_proto(cf, i):
    o = int(cf.data_off[i]); n = int(cf.data_len[i]); g = int(cf.glen[i])
    return bytes(cf.data8[o:o + g]) + b"\x00" * (n - g)

# ── 手工构造：语义角落 ──
def mk(offs, lens, glens, raw, scattered):
    n = len(offs)
    return ContainerFrames(
        channel=np.arange(n, dtype=np.int64), ts=np.arange(n, dtype=np.float64),
        arb=np.zeros(n, np.uint32), is_ext=np.zeros(n, bool),
        is_remote=np.zeros(n, bool), is_error=np.zeros(n, bool),
        is_fd=np.zeros(n, bool), dlc=np.zeros(n, np.uint8),
        data8=np.frombuffer(raw, np.uint8), data_off=np.array(offs, np.int64),
        data_len=np.array(lens, np.int64), scattered=scattered,
        # 归一假设（§3.1）：glen 恒存在；packed 路径生产者已设 glen = data_len
        glen=np.array(lens, np.int64) if glens is None else np.array(glens, np.int64))

CASES = [
    # (名字, cf, 关注的 sel)
    ("scattered 全满桶（skip 掩码优化路径）", mk([8, 16], [4, 4], [4, 4],
       b"XX" + b"ABCD" + b"EFGH" + b"tail", True), None),
    ("scattered FD64 截断（glen<data_len，容器尾裁切）", mk([8, 24], [60, 60], [30, 30],
       b"P" * 8 + b"Q" * 60 + b"R" * 60 + b"ZZ", True), None),
    ("scattered 越界 clip（data_off+L 过容器尾，垃圾由掩码清零）", mk([40], [60], [30],
       b"X" * 40 + b"Y" * 30, True), None),
    ("scattered glen=0（说谎 header_size → 全零行）", mk([8], [60], [0],
       b"X" * 68, True), None),
    ("scattered 混合 glen（部分满行）", mk([4, 12, 20], [8, 8, 8], [8, 3, 5],
       b"aa" + b"12345678" + b"456" + b"78901" + b"zz", True), None),
    ("packed 全满桶（skip 优化路径）", mk([0, 8, 16], [8, 8, 8], None,
       b"12345678" + b"abcdefgh" + b"ABCDEFGH", False), None),
    ("packed 混合行长（掩码路径）", mk([0, 8, 10], [8, 2, 8], None,
       b"12345678" + b"ab" + b"ABCDEFGH", False), None),
    ("packed 单帧", mk([0], [8], None, b"12345678", False), None),
    ("空 sel（n==0 早退）", mk([0], [8], None, b"12345678", False), np.array([], np.int64)),
]

fails = 0

def cmp_case(name, cf, sel):
    global fails
    if sel is None:
        sel = np.arange(len(cf.channel))
    old = _bucket_block(cf, sel)
    block = payload_block_proto(cf, sel)
    same_block = np.array_equal(old[2], block)
    same_ts = np.array_equal(old[0], cf.ts[sel])
    same_lens = np.array_equal(old[1], cf.data_len[sel])
    # 单帧抽查（前 3 帧）：payload 等价
    ok_payload = all(payload_proto(cf, int(i)) == _payload_old(cf, int(i))
                     for i in sel[:3])
    good = same_block and same_ts and same_lens and ok_payload
    print(f"  [{'PASS' if good else 'FAIL'}] {name}"
          f"  block={old[2].shape} ts_lens={'OK' if same_ts and same_lens else 'BAD'}"
          f" payload={'OK' if ok_payload else 'BAD'}")
    fails += 0 if good else 1

print("== 手工构造语义角落 ==")
for name, cf, sel in CASES:
    cmp_case(name, cf, sel)

print("\n== 真实样例文件（快/回退两路径，逐容器抽检）==")
for blf in sorted(Path(ROOT, "inputs/blf").glob("*.blf")):
    for force in (False, True):
        tag = "回退(packed)" if force else "快(scattered)"
        n_cf = n_sel = 0
        for cf in iter_container_frames(str(blf), _force_fallback=force):
            if cf.glen is None:
                cf.glen = cf.data_len   # 归一假设：packed 生产者设 glen = data_len
            n = len(cf.channel)
            if n == 0:
                continue
            sels = [np.arange(min(n, 5000))]
            if n > 1:
                sels.append(np.arange(0, min(n, 30000), 7))
            for s in sels:
                old = _bucket_block(cf, s)
                block = payload_block_proto(cf, s)
                assert np.array_equal(old[2], block), (blf.name, force, "block")
                for i in s[:3]:
                    assert payload_proto(cf, int(i)) == _payload_old(cf, int(i))
                n_sel += 1
            n_cf += 1
        print(f"  [PASS] {blf.name}  {tag}  容器 {n_cf} 个 / 抽检 {n_sel} 块，逐位一致")

print("\n结果：", "ALL PASS" if fails == 0 else f"{fails} 处 FAIL")
sys.exit(1 if fails else 0)
