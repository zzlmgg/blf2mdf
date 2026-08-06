# 方案C 解码向量化 + 0x7DF 溢出截断修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 [core/decoder.py](core/decoder.py) 的逐帧 cantools 解码替换为 numpy 向量化位提取（解码阶段 ~74s → 预期个位数到十几秒），并顺带修复 512 位信号值超出 uint64 时转换崩溃的潜伏 bug（截断存储，与向量化路径一致）。

**Architecture:** 保留 `ChannelDecoder.feed()/finish()` 接口（converter 零改动）——feed 只按 (通道, 报文 ID) 分桶收集原始字节（未知 ID 键查即预检，方案 D 顺带完成），finish 把桶内帧构造成 `(N, L)` uint8 数组后按信号向量化提取（start_bit/length/字节序/符号/浮点/scale/offset/choices 全部 numpy 化），mux 信号按切换值分帧提取；无法向量化的报文（选择信号带变换/choices 的 mux）回退逐帧 cantools 解码。测试侧保留一份逐帧参考实现，属性测试对拍零差异。

**Tech Stack:** Python 3.11+ / numpy / cantools 42.0.3（仅测试参考）/ 现有 pytest 套件

## Global Constraints

- cantools 版本锁定 42.0.3（向量化复制了 bitstruct 位序语义，升级需重跑对拍）；requirements.txt 不变
- 输出语义与逐帧参考实现**逐点一致**（含 nan 语义、dtype、未知帧计数、unknown_ids 集合）；验收 = 属性测试 + 全量 pytest + golden + full_compare
- 真实 DBC 特征（已实测）：8744 信号 100% big_endian、无符号、无浮点；5507 有 choices；无 mux 报文；15 个 >64 位信号（512 位 Fun_Diag_Request ×12、128 位 ×2，样例 BLF 无对应帧）——向量化必须同时支持大端/小端（测试 DBC 用小端），符号/浮点在测试面覆盖
- 分桶键 = `arb | (0x80000000 if is_extended else 0)`（`_normalize_id` 约定）；未知帧计数沿用 `fr.arbitration_id`（原始 id，非归一化）
- 每次 commit 一条，消息用仓库既有风格（如 `方案C: 解码向量化-位提取核心`）
- 工作目录 `e:\projects\blf_dbc`；Python 解释器 = conda env `blfmdf`（`/c/ProgramData/anaconda3/envs/blfmdf/python.exe`），命令统一 `$PY` 前缀
- golden 标记测试较慢（读 35MB 样例），仅在验收 Task 6 全量跑

---

### Task 1: dbc_loader SignalDef 扩展（byte_order / mux 元数据）

**Files:**
- Modify: `core/dbc_loader.py:8-18`（SignalDef 字段）、`core/dbc_loader.py:69-83`（load() 构造）
- Test: `tests/test_dbc_loader.py`（末尾追加）

**Interfaces:**
- Consumes: cantools Signal 属性 `byte_order`（"little_endian"/"big_endian"）、`is_multiplexer`（bool）、`multiplexer_ids`（list[int] | None）
- Produces: `SignalDef` 新增三个字段——`byte_order: str = "big_endian"`、`is_multiplexer: bool = False`、`multiplexer_ids: list[int] | None = None`（None = 常活跃信号）。全仓库仅 `dbc_loader.load()` 构造 SignalDef（已 grep 确认无其他构造点），带默认值不破坏现有代码

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_dbc_loader.py`）

```python
def test_signal_metadata_fields(tmp_path):
    """SignalDef 扩展字段：byte_order/多路复用信息（方案C向量化解码需要）。"""
    dbc_txt = '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 100 M1: 8 ECU
 SG_ Sel M : 0|8@1+ (1,0) [0|255] "" ECU
 SG_ A m0 : 8|8@1+ (1,0) [0|255] "" ECU
 SG_ B m1 : 8|8@0+ (1,0) [0|255] "" ECU

BO_ 200 M2: 8 ECU
 SG_ C : 16|16@0+ (1,0) [0|65535] "" ECU

VAL_ 200 C 0 "off" 1 "on" ;
'''
    p = tmp_path / "meta.dbc"
    p.write_text(dbc_txt, encoding="utf-8")
    dbc = load(str(p))
    m1 = dbc.messages[100]
    sel, a, b = m1.signals
    assert sel.is_multiplexer and sel.multiplexer_ids is None
    assert a.multiplexer_ids == [0] and a.byte_order == "little_endian"
    assert b.multiplexer_ids == [1] and b.byte_order == "big_endian"
    m2 = dbc.messages[200]
    assert m2.signals[0].byte_order == "big_endian"
    assert m2.signals[0].choices == {0: "off", 1: "on"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `$PY -m pytest tests/test_dbc_loader.py::test_signal_metadata_fields -v`
Expected: FAIL，`AttributeError: 'SignalDef' object has no attribute 'byte_order'`

- [ ] **Step 3: 实现字段扩展**

在 `core/dbc_loader.py` 的 SignalDef 末尾追加三个字段：

```python
    is_float: bool = False
    choices: dict | None = None     # {原始值: 文本}（DBC value table，修复项 3）
    byte_order: str = "big_endian"  # cantools: "little_endian"/"big_endian"（方案C 位提取）
    is_multiplexer: bool = False    # 是否为 mux 选择信号（方案C）
    multiplexer_ids: list[int] | None = None   # 子信号激活的 mux 值集合；None=常活跃（方案C）
```

`load()` 中 SignalDef 构造追加：

```python
                SignalDef(
                    name=s.name,
                    start_bit=s.start,
                    length=s.length,
                    scale=float(s.scale or 1.0),
                    offset=float(s.offset or 0.0),
                    unit=s.unit or "",
                    is_signed=bool(s.is_signed),
                    is_float=bool(s.is_float),
                    choices=({k: str(v) for k, v in s.choices.items()}
                             if s.choices else None),
                    byte_order=str(s.byte_order),
                    is_multiplexer=bool(s.is_multiplexer),
                    multiplexer_ids=(sorted(s.multiplexer_ids)
                                     if s.multiplexer_ids is not None else None),
                )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `$PY -m pytest tests/test_dbc_loader.py -v`
Expected: PASS（4 项：原 3 项 + 新增 1 项）

- [ ] **Step 5: 提交**

```bash
git add core/dbc_loader.py tests/test_dbc_loader.py
git commit -m "方案C: SignalDef 扩展 byte_order/mux 元数据"
```

---

### Task 2: 向量化位提取核心（_extract_bits / _sign_extend / _extract_signal）

**Files:**
- Modify: `core/decoder.py`（文件头部新增向量化工具函数区，位于 `_signal_kind` 之前）
- Test: 新建 `tests/test_decoder_vectorized.py`

**Interfaces:**
- Consumes: `SignalDef`（Task 1 的 byte_order/is_multiplexer/multiplexer_ids）、`_int_dtype`
- Produces（后续任务依赖的精确签名）:
  - `_extract_bits(data64: np.ndarray(N,W) uint64, pos: int, length: int, byte_order: str) -> np.ndarray(N,) uint64`——位流位置 pos 起提取 length（≤64）位
  - `_sign_extend(v: np.ndarray uint64, length: int) -> np.ndarray int64`
  - `_extract_signal(data64, sd: SignalDef) -> np.ndarray`——uint64（无符号）/ int64（有符号）/ float32|float64（浮点）；>64 位信号取低 64 位
  - `_pad_to_64(data: np.ndarray(N,L) uint8) -> np.ndarray(N,Lp) uint8`（Lp 为 8 的倍数）

**位序约定（与 bitstruct/cantools 逐位等价，已对照源码验证）：**
- 大端（Motorola `@0`）：DBC start bit s 的位流位置 `p = 8*(s//8) + (7 - s%8)`；位流 MSB-first，位 p = 字节 p//8 的 MSB 起第 p%8 位。uint64 小端字视图下：位 p = 字 (p//64) 的位 (63 - p%64)
- 小端（Intel `@1`）：位流位置 = start bit 本身；位 i = 字节 i//8 的 LSB 起第 i%8 位。uint64 小端字视图下：位 i = 字 (i//64) 的位 (i%64)
- 跨界（length 跨 64 位字边界）：大端 `((hi << k) | (lo >> (64-k))) & mask`，k = o+length-64；小端 `((hi >> o) | (lo << (64-o))) & mask`
- 字界末越界：缺字补零
- >64 位信号：取**低 64 位**（`pos += length-64; length = 64` 后再提取）——与 Task 5 存储截断语义一致

- [ ] **Step 1: 写失败测试**（新建 `tests/test_decoder_vectorized.py`）

```python
"""方案C 向量化解码测试：位提取单元 + 与逐帧参考实现对拍。"""
import struct

import numpy as np
import pytest

from core.decoder import _extract_bits, _extract_signal, _pad_to_64
from core.dbc_loader import SignalDef


def _ref_bits(data: bytes, pos: int, length: int, byte_order: str) -> int:
    """位级参考（独立于实现）：bit 0 = 首字节 MSB 的大端位流语义。"""
    big = int.from_bytes(data, "big")
    n = len(data) * 8
    if byte_order == "big_endian":
        p = pos
    else:
        # 小端位 i = 字节 i//8 的 LSB 起第 i%8 位 → 大端位置 8*(i//8)+(7-i%8)
        p = 8 * (pos // 8) + (7 - pos % 8)
    return (big >> (n - p - length)) & ((1 << length) - 1)


def _frame_data(n_frames: int, n_bytes: int, rng) -> np.ndarray:
    return rng.integers(0, 256, size=(n_frames, n_bytes), dtype=np.uint8)


@pytest.mark.parametrize("byte_order", ["big_endian", "little_endian"])
def test_extract_bits_random_matches_bit_reference(byte_order):
    """随机 (数据, 位置, 长度) 逐位对拍位级参考（含跨界、>64 位截断）。"""
    rng = np.random.default_rng(20260806)
    for n_bytes in (1, 2, 8, 9, 24, 64):
        for _ in range(200):
            n = int(rng.integers(1, 6))
            data = _frame_data(n, n_bytes, rng)
            total = n_bytes * 8
            pos = int(rng.integers(0, total))
            length = int(rng.integers(1, min(129, total - pos + 1)))
            data64 = _pad_to_64(data).view("<u8")
            got = _extract_bits(data64, pos, min(length, 64), byte_order)
            ref = np.array([
                _ref_bits(bytes(row), pos, length, byte_order) & ((1 << 64) - 1)
                for row in data], dtype=np.uint64)
            assert np.array_equal(got, ref), \
                f"{byte_order} n_bytes={n_bytes} pos={pos} len={length}"


def test_extract_signal_signed_and_float():
    """符号扩展与浮点位模式（int64/float32/float64 view）。"""
    rng = np.random.default_rng(7)
    data = _frame_data(4, 8, rng)
    data64 = _pad_to_64(data).view("<u8")
    for sd in [
        SignalDef(name="s", start_bit=0, length=12, scale=1.0, offset=0.0,
                  unit="", is_signed=True, byte_order="big_endian"),
        SignalDef(name="f", start_bit=16, length=32, scale=1.0, offset=0.0,
                  unit="", is_float=True, byte_order="little_endian"),
    ]:
        raw = _extract_signal(data64, sd)
        if sd.is_signed:
            ref = []
            for row in data:
                v = _ref_bits(bytes(row), 0, 12, "big_endian")
                if v & (1 << 11):
                    v -= 1 << 12
                ref.append(v)
            assert raw.dtype == np.int64
            assert raw.tolist() == ref
        else:
            ref = [struct.unpack("<f", bytes(row[2:6]))[0] for row in data]
            assert raw.dtype == np.float32
            assert np.allclose(raw, ref)


def test_extract_signal_truncates_over_64_bits():
    """>64 位信号：取低 64 位（Task 5 截断语义）。"""
    sd = SignalDef(name="big", start_bit=7, length=512, scale=1.0, offset=0.0,
                   unit="", byte_order="big_endian")  # 512 位整帧
    data = np.array([list(range(8)) + [0] * 56] * 2, dtype=np.uint8)
    data64 = _pad_to_64(data).view("<u8")
    got = _extract_signal(data64, sd)
    low = int.from_bytes(bytes(range(8))[::-1], "little")  # 末字（低 64 位）
    assert got.dtype == np.uint64
    assert got.tolist() == [low, low]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `$PY -m pytest tests/test_decoder_vectorized.py -v`
Expected: FAIL，`ImportError: cannot import name '_extract_bits' from 'core.decoder'`

- [ ] **Step 3: 实现向量化提取函数**（`core/decoder.py`，`_signal_kind` 之前插入）

```python
# ── 方案C：向量化位提取（与 bitstruct/cantools 逐位等价，测试对拍）──

def _pad_to_64(data: np.ndarray) -> np.ndarray:
    """(N, L) uint8 → (N, Lp) uint8，Lp 为 8 的倍数（尾部补零，供 uint64 视图）。"""
    n, l = data.shape
    if l % 8 == 0:
        return data
    pad = np.zeros((n, 8 - l % 8), dtype=np.uint8)
    return np.concatenate([data, pad], axis=1)


def _extract_bits(data64: np.ndarray, pos: int, length: int,
                  byte_order: str) -> np.ndarray:
    """从 (N, W) uint64 小端字视图的位流 pos 起提取 length（≤64）位 → uint64。

    大端: pos 为 MSB-first 位流位置（位 p = 字节 p//8 的 MSB 起第 p%8 位）；
    小端: pos = DBC start bit（位 i = 字节 i//8 的 LSB 起第 i%8 位）。
    跨界（跨 64 位字）两字拼接；末字越界补零。
    """
    w, o = divmod(pos, 64)
    hi = data64[:, w]
    mask = (np.uint64(1) << length) - 1
    if byte_order == "big_endian":
        if o + length <= 64:
            return (hi >> (64 - o - length)) & mask
        k = o + length - 64
        lo = (data64[:, w + 1] if w + 1 < data64.shape[1]
              else np.zeros(data64.shape[0], dtype=np.uint64))
        return (((hi << k) | (lo >> (64 - k))) & mask)
    else:
        if o + length <= 64:
            return (hi >> o) & mask
        k = 64 - o
        lo = (data64[:, w + 1] if w + 1 < data64.shape[1]
              else np.zeros(data64.shape[0], dtype=np.uint64))
        return ((hi >> o) | (lo << k)) & mask


def _sign_extend(v: np.ndarray, length: int) -> np.ndarray:
    """length 位无符号 → 补码符号扩展 int64（length=64 直接 view）。"""
    if length == 64:
        return v.view(np.int64)
    mask = (np.uint64(1) << length) - 1
    sign = np.uint64(1) << (length - 1)
    return np.where(v & sign, (v | ~mask).view(np.int64), v.astype(np.int64))


def _extract_signal(data64: np.ndarray, sd: SignalDef) -> np.ndarray:
    """单信号向量化提取 → uint64（无符号）/ int64（有符号）/ float32|64（浮点）。

    >64 位信号取低 64 位（与 Task 5 存储截断语义一致）。
    """
    length = sd.length
    if sd.byte_order == "big_endian":
        pos = 8 * (sd.start_bit // 8) + (7 - sd.start_bit % 8)
    else:
        pos = sd.start_bit
    if length > 64:
        pos += length - 64
        length = 64
    v = _extract_bits(data64, pos, length, sd.byte_order)
    if sd.is_float:
        bits = v.astype(np.uint32 if length == 32 else np.uint64)
        return bits.view(np.float32 if length == 32 else np.float64)
    if sd.is_signed:
        return _sign_extend(v, length)
    return v
```

- [ ] **Step 4: 运行测试确认通过**

Run: `$PY -m pytest tests/test_decoder_vectorized.py -v`
Expected: PASS（4 项参数化展开 ≈ 2×200×6 + 3 项 ≈ 2400+ 条断言）

- [ ] **Step 5: 提交**

```bash
git add core/decoder.py tests/test_decoder_vectorized.py
git commit -m "方案C: 向量化位提取核心(大端/小端/跨界/符号/浮点/截断)"
```

---

### Task 3: ChannelDecoder 向量化 finish（无 mux 报文）+ 参考回退

**Files:**
- Modify: `core/decoder.py`（ChannelDecoder.feed/finish 重写；新增 `_bucket_data_array`、`_choices_lookup`、`_signal_kind_vec`、`_text_array`、`_int_array`、`_float_array`、`_physical`、`_msg_vectorizable`、`_decode_bucket_reference`）
- Test: `tests/test_decoder_vectorized.py`（追加参考实现 + 属性对拍 + 真实 DBC 对拍）；`tests/test_decoder.py` 9 项不改，作为回归网

**Interfaces:**
- Consumes: Task 2 的 `_extract_signal`/`_pad_to_64`、Task 1 的 SignalDef 新字段、既有 `_signal_kind`/`_int_dtype`/`DecodeStats`/`SignalSeries`
- Produces: `ChannelDecoder.feed(fr)` / `finish()` 接口签名不变（converter 零改动）；`decode_channel` 不变。finish 返回与旧实现完全等价的 series/stats（属性测试锁定）

**关键语义（必须与逐帧参考逐点一致）：**
- 有效帧 = `len(data) >= md.frame_length`（短帧 cantools 抛 DecodeError → 未知帧；长帧裁剪后解码）
- 未知帧计数/unknown_ids：feed 对未知 ID 键查失败直接计数（方案 D 并入）；finish 对短帧/mux 无效帧按桶补计（unknown_ids 只加一次桶 raw id，旧实现 set 去重后等价）
- mux 报文（选择信号带变换/choices）回退逐帧参考解码；无 mux 报文走向量化

- [ ] **Step 1: 写参考实现与失败测试**（`tests/test_decoder_vectorized.py` 追加）

```python
"""逐帧 cantools 参考实现（= 方案C前的 ChannelDecoder 语义，对拍基准）。"""
from cantools.database.errors import DecodeError
from cantools.database.namedsignalvalue import NamedSignalValue

from core.blf_reader import Frame
from core.decoder import (DecodeStats, SignalSeries, _int_dtype,
                          _signal_kind, _clamped_int_array)
from core.dbc_loader import DbcDef, load


def reference_decode(frames, dbc: DbcDef, channel: int):
    stats = DecodeStats()
    buckets = {}
    for fr in frames:
        stats.total_frames += 1
        arb = fr.arbitration_id | (0x80000000 if fr.is_extended else 0)
        try:
            decoded = dbc.db.decode_message(arb, fr.data)
        except (KeyError, DecodeError):
            stats.unknown_frames += 1
            stats.unknown_ids.add(fr.arbitration_id)
            continue
        md = dbc.messages.get(arb)
        if md is None:
            stats.unknown_frames += 1
            stats.unknown_ids.add(fr.arbitration_id)
            continue
        b = buckets.setdefault(arb, {"ts": [], "values": {s.name: [] for s in md.signals}})
        b["ts"].append(fr.ts_seconds)
        for s in md.signals:
            b["values"][s.name].append(decoded.get(s.name, float("nan")))
    series = []
    for arb, b in buckets.items():
        md = dbc.messages[arb]
        values = {}
        for s in md.signals:
            vals = b["values"][s.name]
            kind = _signal_kind(s, vals)
            if kind == "text":
                values[s.name] = np.asarray([
                    str(v).encode("utf-8") if isinstance(v, NamedSignalValue) else b""
                    for v in vals])
            elif kind == "int":
                values[s.name] = _clamped_int_array(vals, _int_dtype(s.length, s.is_signed))
            else:
                values[s.name] = np.asarray(
                    [float("nan") if isinstance(v, NamedSignalValue) else v
                     for v in vals], dtype=np.float64)
        series.append(SignalSeries(
            channel=channel, message_name=md.name, node=md.sender_node,
            signal_names=[s.name for s in md.signals],
            timestamps=np.asarray(b["ts"], dtype=np.float64),
            values=values, units={s.name: s.unit for s in md.signals}))
    return series, stats


def _assert_series_equal(sa, sb):
    assert len(sa) == len(sb), f"系列数 {len(sa)} vs {len(sb)}"
    for a, b in zip(sa, sb):
        assert a.message_name == b.message_name
        assert a.signal_names == b.signal_names
        assert a.units == b.units
        assert np.array_equal(a.timestamps, b.timestamps)
        for name in a.signal_names:
            va, vb = a.values[name], b.values[name]
            assert va.dtype == vb.dtype, f"{name}: {va.dtype} vs {vb.dtype}"
            if va.dtype.kind == "S":
                assert va.tolist() == vb.tolist(), name
            elif va.dtype.kind == "f":
                eq = np.isnan(va) & np.isnan(vb)
                np.testing.assert_allclose(va[~eq], vb[~eq], rtol=0, atol=0), name
            else:
                assert np.array_equal(va, vb), name


def _random_frames(rng, n, known_ids=()):
    ids = list(known_ids) + [int(rng.integers(300, 400)) for _ in range(max(0, n - len(known_ids)))]
    out = []
    for i in range(n):
        if rng.random() < 0.2:
            # 短帧/超长帧混合
            dlc = int(rng.integers(0, 5))
            data = bytes(rng.integers(0, 256, size=dlc))
        else:
            data = bytes(rng.integers(0, 256, size=8))
        out.append(Frame(channel=1, ts_seconds=float(i), arbitration_id=ids[i % len(ids)],
                         is_extended=bool(rng.random() < 0.1), is_fd=False, dlc=len(data),
                         data=data))
    return out
```

**失败测试（无 mux 随机等价）：**

```python
def _random_signal_dbc(rng, frame_len=8, n_signals=6):
    """单字节内不重叠的随机信号 DBC（大端/小端/符号/变换/choices 混合）。
    单字节域内大端与小端占据相同位集合（顺序相反），按 LSB-first 位集合标记占用即可保证不重叠。"""
    occ = np.zeros(frame_len * 8, dtype=bool)
    lines = [f'BO_ 100 RND: {frame_len} ECU']
    sgs, vals = [], []
    for i in range(n_signals):
        free = np.flatnonzero(~occ)
        if not free:
            break
        start = int(rng.choice(free))
        len_max = min(8 - start % 8, int(np.flatnonzero(~occ[start:])[0]) if start < frame_len * 8 else 1)
        length = int(rng.integers(1, len_max + 1))
        occ[start:start + length] = True
        bo = "@1" if rng.random() < 0.5 else "@0"
        signed = "+" if rng.random() < 0.3 else "-"
        scale = float(rng.choice([1.0, 1.0, 1.0, 2.0, 10.0, 0.5]))
        offset = float(rng.choice([0.0, 0.0, 0.0, -40.0, 5.0]))
        sgs.append(f" SG_ S{i} : {start}|{length}{bo}{signed} ({scale},{offset}) [0|255] \"\" ECU")
        if rng.random() < 0.3:
            keys = sorted(rng.choice(1 << length, size=min(3, 1 << length), replace=False).tolist())
            vals.append(f"VAL_ 100 S{i} " + " ".join(f"{k} \"v{k}\"" for k in keys) + " ;")
    return "\n".join(['VERSION ""', "", "NS_ :", "", "BS_:", "", "BU_: ECU", ""]
                     + lines + sgs + vals)


def test_vectorized_matches_reference_random(tmp_path):
    """随机信号 DBC × 随机帧：向量化 vs 逐帧参考全量对拍（dtype/值/nan/统计）。"""
    from core.decoder import decode_channel
    rng = np.random.default_rng(20260807)
    for trial in range(30):
        frame_len = int(rng.choice([8, 8, 16, 32]))
        dbc_txt = _random_signal_dbc(rng, frame_len)
        p = tmp_path / f"r{trial}.dbc"
        p.write_text(dbc_txt, encoding="utf-8")
        dbc = load(str(p))
        frames = _random_frames(rng, int(rng.integers(10, 120)), known_ids=[100])
        sr, st = reference_decode(frames, dbc, 1)
        vr, vt = decode_channel(iter(frames), dbc, 1)
        assert vt.total_frames == st.total_frames
        assert vt.unknown_frames == st.unknown_frames
        assert vt.unknown_ids == st.unknown_ids
        _assert_series_equal(vr, sr)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `$PY -m pytest tests/test_decoder_vectorized.py::test_vectorized_matches_reference_random -v`
Expected: FAIL（参考实现 import `_clamped_int_array` 尚不存在，先报 ImportError；该 helper 由 Task 5 落地，本 Task 在 decoder.py 内先提供**同样语义的最小实现**——见 Step 3，Task 5 再统一）

- [ ] **Step 3: 重写 ChannelDecoder（feed 分桶 + finish 向量化 + 参考回退）**

`core/decoder.py` 的 ChannelDecoder 整体替换为：

```python
class ChannelDecoder:
    """增量式通道解码器：单遍扫描管线按帧 feed，收齐后 finish 产出系列。

    方案C：feed 只按 (通道, 报文 ID) 分桶收集原始字节（未知 ID 键查即预检，
    不再逐帧 cantools 解码），finish 用 numpy 批量位提取（与逐帧参考实现
    逐点等价，属性测试锁定）；无法向量化的报文回退逐帧解码。
    """

    def __init__(self, dbc: DbcDef, channel: int):
        self.dbc = dbc
        self.channel = channel
        self.stats = DecodeStats()
        self.buckets = {}  # arb -> {"ts": [], "data": [], "raw_id": int, "md": MessageDef}

    def feed(self, fr: Frame) -> None:
        stats = self.stats
        stats.total_frames += 1
        arb = fr.arbitration_id | (0x80000000 if fr.is_extended else 0)
        md = self.dbc.messages.get(arb)
        if md is None:
            # cantools 对 >0x7FF 的 id 无条件置 EFF 位：畸形标准帧可能成功解码到
            # 扩展报文，但 loader 键（含 EFF 位）不含此键 → 计未知帧（方案D 预检并入）。
            stats.unknown_frames += 1
            stats.unknown_ids.add(fr.arbitration_id)
            return
        bucket = self.buckets.setdefault(arb, {
            "ts": [], "data": [], "raw_id": fr.arbitration_id, "md": md,
        })
        bucket["ts"].append(fr.ts_seconds)
        bucket["data"].append(fr.data)

    def finish(self) -> tuple[list[SignalSeries], DecodeStats]:
        series = []
        for arb, b in self.buckets.items():
            md = b["md"]
            if not _msg_vectorizable(md):
                series.extend(_decode_bucket_reference(self.dbc, self.channel, b, self.stats))
                continue
            s = _finish_bucket_vectorized(self.dbc, self.channel, b, self.stats)
            series.extend(s)
        return series, self.stats
```

新增模块级函数（`_signal_kind` 之后）：

```python
def _bucket_data_array(data_bytes: list[bytes], max_len: int) -> np.ndarray:
    """bytes 列表 → (N, L) uint8（L = 桶内最大帧长，补零；参考 converter._collect_raw）。"""
    n = len(data_bytes)
    arr = np.zeros((n, max_len), dtype=np.uint8)
    for i, d in enumerate(data_bytes):
        if d:
            arr[i, : len(d)] = np.frombuffer(d, dtype=np.uint8)
    return arr


def _choices_lookup(sd: SignalDef, raw: np.ndarray) -> tuple[np.ndarray | None, np.ndarray | None]:
    """choices 键查询 → (排序键数组, in_table 掩码)；无 choices → (None, None)。

    键匹配语义同 Python dict（31.0 == 31）：浮点信号键按 float64 比较，整型按 uint64。
    """
    if not sd.choices:
        return None, None
    keys = np.asarray(sorted(sd.choices),
                      dtype=np.float64 if sd.is_float else np.uint64)
    idx = np.searchsorted(keys, raw, side="left")
    ok = idx < len(keys)
    in_table = ok & (keys[np.where(ok, idx, 0)] == raw)
    return keys, in_table


def _signal_kind_vec(sd: SignalDef, in_table: np.ndarray | None) -> str:
    """向量化存储类型判定（= _signal_kind 语义，观察值全在表内判定向量化）。
    in_table 为 None（无 choices）或掩码（mux 非活跃帧为 False，同 nan 语义）。"""
    if sd.is_float or sd.scale != 1.0 or sd.offset != 0.0:
        if sd.choices and bool(np.all(in_table)):
            return "text"
        return "float"
    if sd.choices:
        return "text"
    return "int"


def _physical(sd: SignalDef, raw: np.ndarray) -> np.ndarray:
    """scale/offset 物理变换 → float64（与参考实现逐位一致）。

    参考路径对整型 scale/offset 做 Python 精确整数运算后再一次转 float64；
    仅当 |raw*scale+offset| 可能 ≥2^53（float64 无法精确表示）时走逐帧精确路径，
    否则 float64 快路径（raw<2^53 且乘积<2^53 时浮点运算精确，二者逐位相同）。
    """
    scale, offset = float(sd.scale), float(sd.offset)
    if not (scale.is_integer() and offset.is_integer() and not sd.is_float):
        return raw.astype(np.float64) * scale + offset   # 与参考同序 float64 运算
    s, o = int(scale), int(offset)
    if raw.size == 0 or (float(raw.max()) * abs(s) + abs(o)) < 2**53:
        return raw.astype(np.float64) * float(s) + float(o)
    return np.fromiter((int(r) * s + o for r in raw), dtype=np.float64, count=raw.size)


def _text_array(sd: SignalDef, raw: np.ndarray, keys: np.ndarray,
                in_table: np.ndarray) -> np.ndarray:
    """文本存储：表内值 → 定宽 UTF-8 bytes（= 最长观察值），表外/非活跃 → b''。"""
    n = len(raw)
    if not in_table.any():
        return np.zeros(n, dtype="|S1")
    texts = np.array([sd.choices[k] for k in keys.tolist()], dtype=object)
    ai = np.where(in_table)[0]
    matched = texts[np.searchsorted(keys, raw[ai], side="left")]
    w = max((len(t) for t in matched), default=1)
    out = np.zeros(n, dtype=f"|S{w}")
    out[ai] = np.asarray([t.encode("utf-8") for t in matched.tolist()], dtype=out.dtype)
    return out


def _float_array(sd: SignalDef, raw: np.ndarray, in_table: np.ndarray | None) -> np.ndarray:
    """float64 物理值：无 choices 全量；变换+choices 且存在表外值时表内值存 nan。"""
    out = np.full(len(raw), np.nan, dtype=np.float64)
    phys = _physical(sd, raw)
    if in_table is None:
        out[:] = phys
    else:
        out[~in_table] = phys[~in_table]
    return out


def _int_array(sd: SignalDef, raw: np.ndarray) -> np.ndarray:
    """原始整型（最小 dtype，mux 非活跃/溢出截断由调用侧 _store_signal 处理）。"""
    return raw.astype(_int_dtype(sd.length, sd.is_signed))


def _store_signal(sd: SignalDef, raw: np.ndarray, active: np.ndarray,
                  keys, in_table) -> np.ndarray:
    """按存储类型落盘：int 非活跃→0；float 非活跃→nan；text 非活跃/表外→b''。"""
    n = len(raw)
    if _signal_kind_vec(sd, in_table) == "text":
        return _text_array(sd, raw, keys, in_table)
    if _signal_kind_vec(sd, in_table) == "int":
        out = np.zeros(n, dtype=_int_dtype(sd.length, sd.is_signed))
        out[active] = raw[active].astype(out.dtype)
        return out
    out = np.full(n, np.nan, dtype=np.float64)
    phys = _physical(sd, raw)
    if in_table is None:
        out[active] = phys[active]
    else:
        out[active & ~in_table] = phys[active & ~in_table]
    return out


def _msg_vectorizable(md: MessageDef) -> bool:
    """向量化支持面：无 mux，或选择信号为 (1,0) 无 choices 的单级 mux。
    选择信号带变换/choices 的 mux（cantools 按缩放值路由）回退逐帧参考。"""
    selector = next((s for s in md.signals if s.is_multiplexer), None)
    if selector is None:
        return True
    return selector.scale == 1.0 and selector.offset == 0.0 and not selector.choices


def _finish_bucket_vectorized(dbc, channel, b, stats) -> list[SignalSeries]:
    """单桶向量化解码（无 mux 或单级 mux），与逐帧参考实现逐点等价。"""
    md = b["md"]
    n = len(b["ts"])
    data_bytes = b["data"]
    lens = np.fromiter((len(d) for d in data_bytes), dtype=np.intp, count=n)
    valid = lens >= md.frame_length      # 短帧 DecodeError → 未知
    decodable = valid.copy()
    plan = _mux_plan(md)
    if plan is not None:
        raw_data = _bucket_data_array(data_bytes, int(lens.max()))
        data64 = _pad_to_64(raw_data).view("<u8")
        selector, children = plan
        sel_raw = _extract_signal(data64, selector)
        known_arr = np.array(sorted(children), dtype=sel_raw.dtype)
        bad = ~np.isin(sel_raw, known_arr)      # 无子组的 mux 值 → DecodeError → 未知
        decodable[valid] = ~bad
        n_bad = int(bad.sum())
        if n_bad:
            stats.unknown_frames += n_bad
            stats.unknown_ids.add(b["raw_id"])
    if not decodable.any():
        if not valid.any():
            stats.unknown_frames += n
            stats.unknown_ids.add(b["raw_id"])
        return []
    if plan is None:
        raw_data = _bucket_data_array(data_bytes, int(lens.max()))
        data64 = _pad_to_64(raw_data).view("<u8")
    d64 = data64[decodable]
    timestamps = np.asarray(b["ts"], dtype=np.float64)[decodable]
    n_ok = int(decodable.sum())
    values = {}
    for s in md.signals:
        if plan is not None and s.multiplexer_ids is not None:
            active = np.isin(d64[:, 0], np.array([], dtype=np.uint64))  # 占位，Task 4 实现
            raise NotImplementedError("mux 向量化在 Task 4 落地")
        else:
            active = np.ones(n_ok, dtype=bool)
        raw = _extract_signal(d64, s)
        keys, in_table = _choices_lookup(s, raw)
        if in_table is not None:
            in_table &= active
        values[s.name] = _store_signal(s, raw, active, keys, in_table)
    md_ = md
    return [SignalSeries(
        channel=channel, message_name=md_.name, node=md_.sender_node,
        signal_names=[s.name for s in md_.signals],
        timestamps=timestamps, values=values,
        units={s.name: s.unit for s in md_.signals})]
```

> 注：`_finish_bucket_vectorized` 中 mux 分支为 Task 4 占位（`NotImplementedError`）——本任务内 `_msg_vectorizable` 只放行无 mux 报文，mux 报文走参考回退，故不会触发。Task 4 替换该分支并删除占位。

新增参考回退（`_finish_bucket_vectorized` 之后）：

```python
def _decode_bucket_reference(dbc, channel, b, stats) -> list[SignalSeries]:
    """非向量化报文（选择信号带变换/choices 的 mux）回退：逐帧 cantools 解码。

    逐帧语义与方案C前完全一致（decode_message/未知帧分类/值累积/收尾类型判定）。
    """
    from cantools.database.errors import DecodeError
    from cantools.database.namedsignalvalue import NamedSignalValue

    md = b["md"]
    vals_by_sig = {s.name: [] for s in md.signals}
    ts_ok = []
    for ts, data in zip(b["ts"], b["data"]):
        try:
            decoded = dbc.db.decode_message(b["raw_id"] | (0x80000000 if False else 0), data)
        except (KeyError, DecodeError):
            stats.unknown_frames += 1
            stats.unknown_ids.add(b["raw_id"])
            continue
        # 归一化键：原始 id 须还原 EFF 位（参考实现与 feed 的键契约一致）
        md_cur = None
        for k in dbc.messages:
            pass
        md_cur = md
        ts_ok.append(ts)
        for s in md.signals:
            vals_by_sig[s.name].append(decoded.get(s.name, float("nan")))
    if not ts_ok:
        return []
    values = {}
    for s in md.signals:
        vals = vals_by_sig[s.name]
        kind = _signal_kind(s, vals)
        if kind == "text":
            values[s.name] = np.asarray([
                str(v).encode("utf-8") if isinstance(v, NamedSignalValue) else b""
                for v in vals])
        elif kind == "int":
            values[s.name] = _clamped_int_array(vals, _int_dtype(s.length, s.is_signed))
        else:
            values[s.name] = np.asarray(
                [float("nan") if isinstance(v, NamedSignalValue) else v
                 for v in vals], dtype=np.float64)
    return [SignalSeries(
        channel=channel, message_name=md.name, node=md.sender_node,
        signal_names=[s.name for s in md.signals],
        timestamps=np.asarray(ts_ok, dtype=np.float64),
        values=values, units={s.name: s.unit for s in md.signals})]
```

同时新增 `_clamped_int_array`（Task 5 完整语义的**最小可用版**，Task 5 统一补测试）：

```python
def _clamped_int_array(vals: list, dtype: np.dtype) -> np.ndarray:
    """Python 值列表 → 最小整型 dtype；非整数（mux 非活跃 nan）→ 0；
    超出 dtype 范围的值按模截断（0x7DF 512 位信号修复，语义见 Task 5）。"""
    if not vals:
        return np.asarray(vals, dtype=dtype)
    info = np.iinfo(dtype)
    if all(isinstance(v, (int, np.integer)) and info.min <= int(v) <= info.max
           for v in vals):
        return np.asarray(vals, dtype=dtype)
    bits = dtype.itemsize * 8
    mod = 1 << bits
    clean = [0 if not isinstance(v, (int, np.integer)) else int(v) % mod
             for v in vals]
    u = np.asarray(clean, dtype=np.uint64 if bits >= 32
                   else np.uint32 if bits >= 16 else np.uint8)
    if dtype.kind == "u":
        return u.astype(dtype)
    half = 1 << (bits - 1)
    return np.where(u.astype(object) >= half,
                    u.astype(object) - (1 << bits), u.astype(object)).astype(dtype)
```

> 注：上述参考回退中 `decode_message` 的键问题——`b["raw_id"]` 是去掉 EFF 位的原始 id，直接调用 `decode_message(raw_id, data)` 由 cantools 内部按 `>0x7FF` 置位，等价于原 feed 的 `arb = raw_id | EFF`（含 EFF 的帧 raw_id 即原 arb）。若实现中发现与参考对拍差异，以 `_decode_bucket_reference` 的输出为准修正（该路径仅 mux-变换报文触发，真实 DBC 无 mux）。

- [ ] **Step 4: 运行测试确认通过**

Run: `$PY -m pytest tests/test_decoder_vectorized.py::test_vectorized_matches_reference_random tests/test_decoder.py -v`
Expected: PASS（随机对拍 30 轮 + 原 test_decoder 9 项全过）

- [ ] **Step 5: 真实 DBC 对拍测试**（`tests/test_decoder_vectorized.py` 追加）

```python
@pytest.mark.parametrize("dbc_name", sorted(p.name for p in DBC_DIR.glob("*.dbc")))
def test_vectorized_matches_reference_real_dbc(tmp_path, dbc_name):
    """真实 DBC（含 512 位信号/64 字节帧/choices）随机帧全量对拍。"""
    from core.decoder import decode_channel
    rng = np.random.default_rng(hash(dbc_name) & 0xFFFFFFFF)
    dbc = load(str(DBC_DIR / dbc_name))
    for trial in range(5):
        msg_ids = list(dbc.messages)
        frames = []
        for i in range(60):
            arb = int(rng.choice(msg_ids + [int(rng.integers(2000, 5000))]))
            md = dbc.messages.get(arb)
            max_len = 8 if md is None else int(md.frame_length) + int(rng.integers(0, 9))
            length = int(rng.integers(0, max_len + 1)) if rng.random() < 0.3 else max_len
            frames.append(Frame(channel=1, ts_seconds=float(i), arbitration_id=arb & 0x7FFFFFFF,
                                is_extended=bool(arb & 0x80000000), is_fd=length > 8,
                                dlc=length, data=bytes(rng.integers(0, 256, size=length))))
        sr, st = reference_decode(frames, dbc, 1)
        vr, vt = decode_channel(iter(frames), dbc, 1)
        assert vt.total_frames == st.total_frames
        assert vt.unknown_frames == st.unknown_frames
        assert vt.unknown_ids == st.unknown_ids
        _assert_series_equal(vr, sr)
```

> 注：`DBC_DIR` 需 import（`from conftest import DBC_DIR`；conftest 在 tests/ 下，pytest 自动可见——test_decoder_vectorized.py 顶部加 `from conftest import DBC_DIR`）。

- [ ] **Step 6: 运行真实 DBC 对拍 + 全量测试**

Run: `$PY -m pytest tests/test_decoder_vectorized.py tests/test_decoder.py tests/test_converter.py -v`
Expected: PASS（13 个真实 DBC × 5 轮对拍全过；test_converter 14 项确认 converter 零改动不回归）

- [ ] **Step 7: 提交**

```bash
git add core/decoder.py tests/test_decoder_vectorized.py
git commit -m "方案C: ChannelDecoder 向量化 finish(无 mux 报文)+参考回退"
```

---

### Task 4: mux 单级向量化

**Files:**
- Modify: `core/decoder.py`（新增 `_mux_plan`；`_finish_bucket_vectorized` 中替换 Task 3 的 mux 占位分支）
- Test: `tests/test_decoder_vectorized.py`（追加 mux 随机对拍）；`tests/test_decoder.py::test_mux_inactive_signal_defaults_to_zero` 原样回归

**Interfaces:**
- Consumes: Task 3 的 `_finish_bucket_vectorized` 骨架、Task 1 的 is_multiplexer/multiplexer_ids
- Produces: `_mux_plan(md) -> tuple[SignalDef, dict[int, list[SignalDef]]] | None`（None = 无 mux；多级 mux 在 DBC 格式中不存在，选择信号带变换/choices 已由 `_msg_vectorizable` 挡回参考回退）

**语义（与 cantools 逐点一致）：**
- 先向量化提取选择信号 raw 值；无子组对应的 mux 值 → 整帧 DecodeError → 未知帧（该帧对**所有**信号不产出值，时间戳同步剔除）
- 子信号只在所属 mux 值活跃帧上取值；非活跃帧 int→0 / float→nan / text→b''（即参考实现的 nan 语义）
- 观察值全在表内判定含非活跃帧（nan 非 NamedSignalValue → 变换+choices 信号只要有非活跃帧即 float 类型）——`in_table &= active` 实现

- [ ] **Step 1: 写失败测试**

```python
MUX_DBC = '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 200 MUX: 8 ECU
 SG_ Mux M : 0|8@1+ (1,0) [0|255] "" ECU
 SG_ SigA m0 : 8|8@1+ (1,0) [0|255] "" ECU
 SG_ SigB m1 : 8|8@1+ (1,0) [0|255] "" ECU
 SG_ Always : 16|8@1+ (1,0) [0|255] "" ECU
'''


def test_vectorized_mux_matches_reference_random(tmp_path):
    """mux 随机帧（含无子组 mux 值/短帧/未知 ID）对拍参考实现。"""
    from core.decoder import decode_channel
    rng = np.random.default_rng(4242)
    p = tmp_path / "mux.dbc"
    p.write_text(MUX_DBC, encoding="utf-8")
    dbc = load(str(p))
    for trial in range(40):
        frames = []
        for i in range(80):
            arb = int(rng.choice([200, 200, 200, 300]))
            if arb == 300:
                data = bytes(rng.integers(0, 256, size=8))
            elif rng.random() < 0.15:
                data = bytes(rng.integers(0, 256, size=int(rng.integers(0, 8))))
            else:
                mux = int(rng.choice([0, 1, 2, 3]))   # 2/3 无子组 → 未知帧
                data = bytes([mux]) + bytes(rng.integers(0, 256, size=7))
            frames.append(Frame(channel=1, ts_seconds=float(i), arbitration_id=arb,
                                is_extended=False, is_fd=False, dlc=len(data), data=data))
        sr, st = reference_decode(frames, dbc, 1)
        vr, vt = decode_channel(iter(frames), dbc, 1)
        assert vt.unknown_frames == st.unknown_frames and vt.unknown_ids == st.unknown_ids
        _assert_series_equal(vr, sr)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `$PY -m pytest tests/test_decoder_vectorized.py::test_vectorized_mux_matches_reference_random -v`
Expected: FAIL，`NotImplementedError: mux 向量化在 Task 4 落地`

- [ ] **Step 3: 实现 `_mux_plan` 并替换占位分支**

新增模块级函数：

```python
def _mux_plan(md: MessageDef) -> tuple[SignalDef, dict[int, list[SignalDef]]] | None:
    """单级 mux 分解 → (选择信号, {mux 值: 子信号列表})；无 mux → None。

    前提（_msg_vectorizable 已保证）：选择信号为 (1,0) 无 choices；DBC 格式
    无多级 mux（cantools 的多级支持仅 ARXML 有）。
    """
    selector = next((s for s in md.signals if s.is_multiplexer), None)
    if selector is None:
        return None
    children: dict[int, list[SignalDef]] = defaultdict(list)
    for s in md.signals:
        if s.multiplexer_ids is not None:
            for mv in s.multiplexer_ids:
                children[mv].append(s)
    return selector, dict(children)
```

`_finish_bucket_vectorized` 的 mux 占位分支替换为：

```python
    for s in md.signals:
        if plan is not None and s.multiplexer_ids is not None:
            active = np.isin(sel_raw[decodable],
                             np.array(sorted(s.multiplexer_ids), dtype=sel_raw.dtype))
        else:
            active = np.ones(n_ok, dtype=bool)
        raw = _extract_signal(d64, s)
        keys, in_table = _choices_lookup(s, raw)
        if in_table is not None:
            in_table &= active
        values[s.name] = _store_signal(s, raw, active, keys, in_table)
```

并在模块顶部补 `from collections import defaultdict`。

- [ ] **Step 4: 运行测试确认通过**

Run: `$PY -m pytest tests/test_decoder_vectorized.py::test_vectorized_mux_matches_reference_random tests/test_decoder.py -v`
Expected: PASS（mux 随机对拍 40 轮 + test_decoder 9 项全过，含 `test_mux_inactive_signal_defaults_to_zero`）

- [ ] **Step 5: 提交**

```bash
git add core/decoder.py tests/test_decoder_vectorized.py
git commit -m "方案C: mux 单级向量化(按切换值分帧提取)"
```

---

### Task 5: 0x7DF 溢出截断修复（顺带修复）

**Files:**
- Modify: `core/decoder.py`（`_store_signal` int 分支走截断转换；Task 3 的 `_clamped_int_array` 保持）
- Test: `tests/test_decoder_vectorized.py`（追加溢出回归）；`tests/test_decoder.py` 追加一条

**根因（已实证）：** 512 位信号（`Fun_Diag_Request`）值 > 2^64−1 时，旧 `np.asarray([...], dtype=uint64)` 抛 `OverflowError` 崩溃（样例 BLF 无 0x7DF 帧，潜伏）。**决策（用户已批准）：顺带修复为按模截断存储**——MDF 整型通道最大 64 位，截断与向量化路径提取低 64 位语义一致；有符号按补码回绕。两条路径（向量化/参考）输出必须一致。

- [ ] **Step 1: 写失败测试**

```python
BIG_DBC = '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 2015 DIAG: 64 ECU
 SG_ Payload : 0|512@0+ (1,0) [0|0] "" ECU
 SG_ Cnt : 0|8@0+ (1,0) [0|255] "" ECU

BO_ 2016 SIGNED: 8 ECU
 SG_ BigS : 0|64@0- (1,0) [0|0] "" ECU
'''


def test_overflow_512bit_signal_truncates(tmp_path):
    """0x7DF 修复：512 位信号值超 uint64 → 按模截断低 64 位，不崩溃；
    向量化与参考实现截断结果一致。"""
    from core.decoder import decode_channel
    p = tmp_path / "big.dbc"
    p.write_text(BIG_DBC, encoding="utf-8")
    dbc = load(str(p))
    # 高位置位：整帧 0xFF... 的最高 64 位 → 低 64 位应为 0x00...00（第 57-64 字节）
    data = bytes([0xFF] * 56 + [0xAB] * 8)
    frames = [Frame(channel=1, ts_seconds=1.0, arbitration_id=2015,
                    is_extended=False, is_fd=True, dlc=64, data=data)]
    sr, st = reference_decode(frames, dbc, 1)
    vr, vt = decode_channel(iter(frames), dbc, 1)
    assert vt.unknown_frames == st.unknown_frames == 0
    payload = vr[0].values["Payload"]
    assert payload.dtype == np.uint64
    assert payload.tolist() == [int.from_bytes(data[56:64], "big")]  # 低 64 位 = 末 8 字节
    _assert_series_equal(vr, sr)


def test_overflow_signed_64bit_wraps(tmp_path):
    """有符号 64 位信号全范围回绕：int64 view（补码）。"""
    from core.decoder import decode_channel
    p = tmp_path / "s64.dbc"
    p.write_text(BIG_DBC, encoding="utf-8")
    dbc = load(str(p))
    data = (2**64 - 1).to_bytes(8, "big")  # 0xFFFF... = int64 -1
    frames = [Frame(channel=1, ts_seconds=1.0, arbitration_id=2016,
                    is_extended=False, is_fd=False, dlc=8, data=data)]
    sr, st = reference_decode(frames, dbc, 1)
    vr, _ = decode_channel(iter(frames), dbc, 1)
    assert vr[0].values["BigS"].dtype == np.int64
    assert vr[0].values["BigS"].tolist() == [-1]
    _assert_series_equal(vr, sr)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `$PY -m pytest tests/test_decoder_vectorized.py::test_overflow_512bit_signal_truncates tests/test_decoder_vectorized.py::test_overflow_signed_64bit_wraps -v`
Expected: 第 1 条 PASS（向量化路径天然截断，参考走 `_clamped_int_array` 已截断）；第 2 条 FAIL——`_extract_signal` 对 length=64 有符号返回 `v.view(int64)`，但参考路径 `_clamped_int_array` 处理 `0xFFFF...`（= 2^64−1，超出 int64 max）→ 期望回绕为 −1，若实现不符则断言失败。以实际失败信息为准修正 `_clamped_int_array` 或 `_sign_extend` 至对拍一致。

- [ ] **Step 3: 实现（对齐两路径）**

检查并修正 `_store_signal` int 分支与 `_clamped_int_array`，保证两条路径对超范围值输出一致：

```python
    if _signal_kind_vec(sd, in_table) == "int":
        out = np.zeros(n, dtype=_int_dtype(sd.length, sd.is_signed))
        out[active] = _clamped_int_array(
            [int(v) for v in raw[active].tolist()], out.dtype)
        return out
```

> 说明：向量化路径 raw 已限 64 位且位宽匹配 dtype（length≤8→uint8 等），`_clamped_int_array` 的快速路径（全在范围内）直接 `np.asarray`，零开销；仅当有符号 64 位信号在 `raw.view(int64)` 与参考 Python int 路径出现符号位差异时，以测试驱动逐项对齐。**验收标准 = 两条溢出测试 + 随机对拍全过**，不对实现做预判式微调。

- [ ] **Step 4: 运行测试确认通过**

Run: `$PY -m pytest tests/test_decoder_vectorized.py tests/test_decoder.py -v`
Expected: PASS（溢出 2 项 + 全部对拍 + 9 项回归全过）

- [ ] **Step 5: 提交**

```bash
git add core/decoder.py tests/test_decoder_vectorized.py
git commit -m "修复: 0x7DF 512位信号溢出截断(两路径一致)"
```

---

### Task 6: 全量验收 + 文档更新

**Files:**
- Verify: 全量 pytest、golden、full_compare、性能测量
- Modify: `docs/2026-08-05-blf-mdf-conversion-performance.md`（§4 方案C 状态、§5 表、§6 执行记录）

**Interfaces:** 无新接口；验收方案C的全部输出路径与性能结论。

- [ ] **Step 1: 全量 pytest**

Run: `$PY -m pytest -v`（不含 golden 的常规套件 + 全量含 golden 各跑一次）
Expected: PASS（基线 59 + 新增 ≈ 10+ 项；含 13 个真实 DBC 参数化对拍）

- [ ] **Step 2: golden 全量转换与 CANoe 参考对拍**

Run:
```bash
$PY -m pytest tests/test_golden.py -v
$PY tools/full_compare.py outputs/golden.mdf inputs/mdf/_T058.mdf --outdir outputs/mdf_compare --no-report
```
Expected: golden 2 项 PASS；full_compare 报告 160 组 × 601 点统计全部一致、组结构/顺序/值一致（与方案B 验收口径相同）。

> 注：golden 测试自产 `outputs/golden.mdf`；full_compare 以该文件对比 CANoe 参考 `inputs/mdf/_T058.mdf`。若 first_compare 失败，回到 Task 3/4 属性测试定位差异（属性对拍已锁定逐帧等价，golden 失败几乎只能来自测试外的路径——converter 未动，可排除）。

- [ ] **Step 3: 性能测量**

```bash
$PY - <<'EOF'
import time
from pathlib import Path
from core.converter import convert
from core.dbc_loader import load
from tests.test_golden import BINDING  # 复用 golden 绑定
blf = sorted(Path("inputs/blf").glob("*.blf"))[0]
bindings = {}
for ch, dbc in BINDING.items():
    p = Path("inputs/dbc") / dbc
    if p.exists():
        bindings[ch] = load(str(p))
t0 = time.perf_counter()
convert(str(blf), bindings, "outputs/perf_c.mdf")
print(f"总耗时: {time.perf_counter() - t0:.1f}s")
EOF
```
Expected: 总耗时 < 130.2s（方案A 基线）；记录解码阶段耗时与结论。**验收标准 = 解码阶段 ≥5× 加速（74s → ≤15s），不承诺文档原先的 "<1s"**（每信号 numpy 调用开销 × ~3000 信号使个位数秒为现实下限）。

- [ ] **Step 4: 更新性能文档**

`docs/2026-08-05-blf-mdf-conversion-performance.md`：
- §4 表格 方案C 状态 ⬜ → ✅，填写实测数据；方案D 标注"已并入方案C（键查预检）"
- §5 预期收益表加一行实测（A+B+C 组合实际耗时）
- §6 追加「方案C：解码向量化 + 0x7DF 溢出截断」执行记录（涉及文件、实测耗时、质量验证、新增测试清单、0x7DF 修复说明）

- [ ] **Step 5: 提交**

```bash
git add docs/2026-08-05-blf-mdf-conversion-performance.md
git commit -m "方案C: 解码向量化完成(对拍零差异+性能实测)"
```

---

## Self-Review

**Spec 覆盖核对（对照方案C设计 + 用户决策）：**
- ✅ 分桶收集 → Task 3（feed 重写）；(N, L) uint8 数组 → `_bucket_data_array`
- ✅ numpy 位提取（start_bit/length/字节序）→ Task 2（大端 sawtooth + 小端 + 跨界 + 截断）
- ✅ scale/offset/choices 全向量化 → Task 3（`_physical` 舍入顺序门控 + `_choices_lookup`）
- ✅ mux 按切换值分桶 → Task 4（`_mux_plan` + 无子组值 → 未知帧）
- ✅ 符号扩展/浮点 → Task 2（测试面覆盖；真实 DBC 无）
- ✅ 0x7DF 溢出边界「顺带修复」→ Task 5（按模截断，两路径一致）
- ✅ 对拍验收 → Task 3/4 属性对拍 + Task 6 golden/full_compare
- ✅ 参考实现（test_stats 先例）→ Task 3 的 `reference_decode` 常驻测试

**占位符扫描：** 无 TBD/TODO；Task 3 的 mux 占位分支是显式两阶段落地点（Task 4 替换并删除），非占位符。

**类型一致性：** `_extract_bits(data64, pos, length, byte_order)` / `_extract_signal(data64, sd)` / `_choices_lookup(sd, raw) → (keys|None, in_table|None)` / `_store_signal(sd, raw, active, keys, in_table)` / `_clamped_int_array(vals, dtype)` 在 Task 2-5 中签名一致；`_signal_kind_vec(sd, in_table)` 在 Task 3 定义、Task 3/4 复用。`_finish_bucket_vectorized` 的 `sel_raw` 在 Task 4 分支使用（Task 3 时 plan=None 不可达，变量作用域安全）。

**遗留提示（记录而非隐藏）：**
- `_decode_bucket_reference` 的键归一化细节在实现时以参考对拍为准（见 Task 3 注）
- 性能验收从 "<1s" 下调为 "≥5×"（见 Task 6 Step 3 理由）
