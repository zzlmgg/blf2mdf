# A 桶契约类型化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把解码桶从三态隐式多态 dict 收口为 decoder.py 的显式状态单类 `Bucket`（三相位互斥字段组 + 转换方法），分类规则唯一实现落 dbc_loader（`classify` / `classify_batch` / `message_table`），两条建桶路由由新增路由等价测试直接对拍，finish 防御性 length 掩码与 converter 死残留删除，零行为漂移。

**Architecture:** 桶是唯一跨模块契约对象（decoder 生产+消费、converter 生产、mp_finish 消费）——字段按 spec Q11 草案原样（arb/raw_id/md/feed_ts/feed_data/blocks/ts_blocks/lens_blocks/ts/lens/data），相位互斥由工厂方法保证，`to_array()` 幂等收敛到数组相位（H2a 性能注释随迁）；分类规则按 M2 双入口模板同构收口 dbc_loader（标量 classify + 批量 classify_batch + 长度表 message_table 相邻定义），feed 与向量化路由各自调用；`finish()` 入口 `assert arb == b.arb` 防字典键与字段漂移；isinstance 形状分派三处全部消失。

**Tech Stack:** Python 3.12、numpy（向量化）、dataclass、pytest（含 can.BLFWriter 合成 BLF 双路由对拍）、tools/mdf_compare.py 逐位对拍链。

**Spec:** [2026-08-18-a-bucket-typing-spec.md](./2026-08-18-a-bucket-typing-spec.md)（grilling 决策 Q1-Q16 全部 settle，本计划是它的落地；三处实施澄清已在文中标注）

## Global Constraints

- **零行为漂移（硬前提）**：全量 pytest（269 基线 + 新增）与逐位对拍链（`tools/mdf_compare.compare_files_identical` 对 AHT 并/串产物，样例在场时执行）全绿；类型化不改变任何 numpy 运算语义（`to_array` 与现 `_assemble_bucket` 是同一份操作）。
- **黄金套件零改动**：test_decoder（11 处 `decode_channel`）、test_decoder_vectorized（5 处 fuzz 对拍）、test_parallel_decode（2 处 feed 循环）的断言一行不动；唯一既有测试文件改动 = test_decoder_vectorized 的 `reference_decode` oracle 换调生产 `classify`（断言零变化）。feed/decode_channel 保留为被测对象本体（Q5）。
- **分类唯一实现**：`classify`（标量）+ `classify_batch`（批量）+ `message_table`（长度表）相邻定义于 dbc_loader；feed 与向量化路由都调它；批量与标量逐元素等价由属性测试锁定；oracle 不复制键规则（解码面保持 cantools 独立）。
- **依赖方向零变化**：converter → decoder → dbc_loader 单向保持；dbc_loader 不得 import decoder/converter。
- **字段名按 spec Q11 草案原样**（arb/raw_id/md/feed_ts/feed_data/blocks/ts_blocks/lens_blocks/ts/lens/data）；Bucket docstring 按 spec A1 定稿逐字；实施中任何字段名微调必须在 plan 修订中说明理由。
- **不新增 seam/抽象**：断言落在既有测试面（`decode_channel` / `convert` / `finish_all` / `classify*` / `Bucket` / `_read_vectorized` 私有直调——test_converter / test_decoder_vectorized 直调私有函数是既有先例）；不新增任何「管理器/适配器」类抽象。
- **性能前提**：H2a 性能注释（一次末态连接、每字节只写一次、禁散点索引）随 `to_array` 迁入；不重跑 AHT 基准（若本机方便可顺手记数，不作通过条件）。
- **提交**：由用户执行（项目惯例），每个任务末尾是交付点，不自动 git commit。
- **范围外（spec Out of Scope）**：feed 列表形删除/测试收敛（Q5 反转回保留）；stats_bufs L4 缺省元组形状；raw_chunks / 原始帧路径；L6 `_read_vectorized` 参数面；L10 容器流衔接骨架；M8 绑定表；2 个可删 bench 脚本（bench_parallel_finish / bench_bucket_dist 的 `b["ts"]` dict 访问为 M8 残留，删除候选，本轮不维护、不修）。

---

### Task 1: dbc_loader 分类收口（classify / message_table / classify_batch）

**Files:**
- Modify: `core/dbc_loader.py`（`normalize_ids` 之后、`SignalDef` 之前插入三个函数）
- Test: `tests/test_dbc_loader.py`（追加，M2 风格）

> **实施澄清 ②（2026-08-19 执行时标注）**：三个函数插入于 `SignalDef` 之前，签名注解引用本模块后定义的 `DbcDef`/`MessageDef`——按计划原文放置即前向引用，需惰性注解，故模块顶部加 `from __future__ import annotations`（已核验 core/ 内无 `get_type_hints`/`__annotations__` 反射使用，语义零影响）。

**Interfaces:**
- Consumes: `DbcDef` / `MessageDef`（本模块定义）、`normalize_id`/`normalize_ids`（本模块已有）。
- Produces（Task 3 消费）:
  - `classify(dbc: DbcDef, arb: int, data_len: int) -> MessageDef | None` — 归一化键空间查找 + `data_len >= md.frame_length` 校验，**分类规则唯一实现**。`arb` 必须为归一化键（`normalize_id` 产物）。
  - `message_table(dbc: DbcDef) -> tuple[np.ndarray, np.ndarray, list[MessageDef]]` — `(keys uint32 排序, lens int64, mds list)`，单通道长度表（现 converter `_prep_decode_info` 的逐通道构造语义迁入；`_read_vectorized` 保留 `{ch: message_table(dec.dbc)}` 包装）。
  - `classify_batch(table, norm: np.ndarray, data_len: np.ndarray) -> tuple[np.ndarray, np.ndarray]` — `(found, valid)` bool 掩码；found = 归一化键在表内，valid = found 且 `data_len >= frame_length`；与 `classify` 逐元素等价（属性测试锁定）。

- [x] **Step 1: 写失败测试**（追加到 `tests/test_dbc_loader.py` 末尾）

```python
from core.dbc_loader import classify, classify_batch, load, message_table, \
    normalize_id


def _classify_dbc(tmp_path):
    """标准报文 BO_ 100（frame_length 8）+ 扩展报文 BO_ 2147483904（0x80000100，8B）。"""
    p = tmp_path / "c.dbc"
    p.write_text('''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 100 ABC: 8 ECU
 SG_ Speed : 0|16@1+ (0.01,0) [0|655.35] "km/h" ECU

BO_ 2147483904 M2: 8 ECU
 SG_ B : 0|8@1+ (1,0) [0|255] "" ECU
''', encoding="utf-8")
    return load(str(p))


@pytest.mark.parametrize("arb,data_len,is_known", [
    (100, 8, True),            # 已知 ID 标准帧
    (0x80000100, 8, True),     # 已知扩展帧（EFF 键空间）
    (100, 9, True),            # 超长帧（FD 变长载荷）有效——只拒绝更短
    (100, 7, False),           # 已知 ID 短帧 → None
    (0x80000100, 0, False),    # 扩展帧空载荷 → None
    (999, 8, False),           # 未知 ID → None
    (0x100, 8, False),         # 标准帧同 raw id（无 EFF 位）→ 键不存在
])
def test_classify_boundaries(tmp_path, arb, data_len, is_known):
    """分类规则 = 归一化键查找 + 帧长校验（未知 ID/短帧 → None）。"""
    dbc = _classify_dbc(tmp_path)
    md = classify(dbc, arb, data_len)
    if is_known:
        assert md is dbc.messages[arb]
    else:
        assert md is None


def test_message_table_contract(tmp_path):
    """长度表：键 uint32 排序（searchsorted 前提）、lens int64、mds 与键对齐。"""
    dbc = _classify_dbc(tmp_path)
    keys, lens, mds = message_table(dbc)
    assert keys.dtype == np.uint32 and lens.dtype == np.int64
    assert np.all(np.diff(keys) > 0), "键必须严格排序（searchsorted 前提）"
    assert keys.tolist() == sorted(dbc.messages)
    assert lens.tolist() == [dbc.messages[k].frame_length for k in keys]
    assert [m.name for m in mds] == [dbc.messages[k].name for k in keys]


def test_classify_batch_matches_scalar_random(tmp_path):
    """批量与标量逐元素等价（属性测试）：随机帧 on 随机信号 DBC。

    复用 test_decoder_vectorized 的生成器（spec A4：同 A2 测试节）。
    每行断言：found = 键在表内；valid = 键在表内且帧长足够（= classify 非 None）。"""
    from test_decoder_vectorized import _random_frames, _random_signal_dbc
    rng = np.random.default_rng(20260819)
    for trial in range(20):
        frame_len = int(rng.choice([8, 8, 16]))
        p = tmp_path / f"r{trial}.dbc"
        p.write_text(_random_signal_dbc(rng, frame_len), encoding="utf-8")
        dbc = load(str(p))
        table = message_table(dbc)
        frames = _random_frames(rng, int(rng.integers(10, 80)), known_ids=[100])
        arb = np.array([normalize_id(f.arbitration_id, f.is_extended)
                        for f in frames], dtype=np.uint32)
        data_len = np.array([len(f.data) for f in frames], dtype=np.int64)
        found, valid = classify_batch(table, arb, data_len)
        assert found.dtype == np.bool_ and valid.dtype == np.bool_
        assert len(found) == len(frames) == len(valid)
        for i, fr in enumerate(frames):
            assert bool(found[i]) == (int(arb[i]) in dbc.messages), i
            md = classify(dbc, int(arb[i]), int(data_len[i]))
            assert bool(valid[i]) == (md is not None), i


def test_classify_batch_empty_table(tmp_path):
    """空 DBC：classify None；classify_batch 全 False（调用方按 ~valid 全计未知）。"""
    p = tmp_path / "e.dbc"
    p.write_text('''VERSION ""

NS_ :

BS_:

BU_: ECU
''', encoding="utf-8")
    dbc = load(str(p))
    assert classify(dbc, 1, 8) is None
    table = message_table(dbc)
    assert table[0].size == 0 and table[1].size == 0 and table[2] == []
    found, valid = classify_batch(table, np.array([1, 2], dtype=np.uint32),
                                  np.array([8, 8], dtype=np.int64))
    assert not found.any() and not valid.any()
```

- [x] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_dbc_loader.py -q`
Expected: 4 处 FAIL，`ImportError: cannot import name 'classify' from 'core.dbc_loader'`（新函数未定义）。

- [x] **Step 3: 实现三个函数**（插入 `core/dbc_loader.py` 的 `normalize_ids`（第 24 行）之后）

```python
def classify(dbc: DbcDef, arb: int, data_len: int) -> MessageDef | None:
    """报文分类（唯一实现）：归一化键查找 + 帧长校验 → MessageDef 或 None。

    未知 ID，或 data_len < md.frame_length（短帧，cantools DecodeError）→ None。
    feed 逐帧路由与向量化路由（classify_batch）共用本规则；arb 必须为归一化
    键（normalize_id 产物）。等价性由属性测试锁定（test_dbc_loader）。"""
    md = dbc.messages.get(arb)
    if md is None or data_len < md.frame_length:
        return None
    return md


def message_table(dbc: DbcDef) -> tuple[np.ndarray, np.ndarray, list[MessageDef]]:
    """单通道长度表：(键 uint32 排序, 帧长 int64, MessageDef 表)。

    键 = 归一化键（规则见 normalize_ids），searchsorted 前提 = 严格排序；
    逐通道构造（原 converter._prep_decode_info 语义迁入，调用方包装
    {ch: message_table(dec.dbc)}）。"""
    keys = sorted(dbc.messages)
    return (
        np.asarray(keys, dtype=np.uint32),
        np.asarray([dbc.messages[k].frame_length for k in keys], dtype=np.int64),
        [dbc.messages[k] for k in keys],
    )


def classify_batch(table: tuple[np.ndarray, np.ndarray, list[MessageDef]],
                   norm: np.ndarray, data_len: np.ndarray) \
        -> tuple[np.ndarray, np.ndarray]:
    """批量分类 twin（与 classify 逐元素等价，属性测试锁定）。

    found = 归一化键在表内；valid = found 且 data_len >= frame_length
    （短帧排除）。空表 → 全 False（调用方按 ~valid 全计未知，与逐帧等价）。
    """
    keys, lens_tab, _ = table
    if len(keys) == 0:
        return (np.zeros(len(norm), dtype=bool),
                np.zeros(len(norm), dtype=bool))
    pos = np.searchsorted(keys, norm)
    ok = pos < len(keys)
    safe = np.where(ok, pos, 0)
    found = ok & (keys[safe] == norm)
    fl = np.where(ok, lens_tab[safe], np.int64(0))
    valid = found & (data_len >= fl)
    return found, valid
```

- [x] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_dbc_loader.py -q`
Expected: 全 PASS（既有 6 用例 + 新增 4 用例）。

- [x] **Step 5: 全量回归确认分类未接线不影响既有行为**

Run: `python -m pytest tests/ -q`
Expected: 269 passed / 0 failed（新函数尚无调用方，纯新增）。

- [x] **Step 6: 交付点（提交由用户执行）**

---

### Task 2: decoder.py Bucket 类型（三相位显式状态单类）

> **实施澄清 ③（2026-08-19 执行时标注）**：Step 1 测试与 Step 3 实现正文有两处冲突，以测试（可执行契约）为准定稿：
> 1. `add_block` 参数序 = `(block, ts, lens)`（block 在前）——与 `from_blocks` 及全部测试调用一致（工厂 7 处、测试 3 处均为 block 在前；Step 3 正文签名与 Task 3 Step 5c 调用为笔误，5c 已修订）。docstring 已注明参数序。
> 2. `to_array` 单块桶直接复用入桶数组（零拷贝、对象同一性保持）——`test_to_array_array_phase_noop` 断言 `b.ts is ts`（同一性），`np.concatenate` 恒拷贝无法满足；多块路径不变（H2a 一次末态连接保留，含窄块补零）。
> 全量回归实测（Task 2 Step 5）：**287 passed / 0 failed**（anaconda3 Python 3.13.9；269 基线 + Task 1 的 4 + Task 2 的 8 + 既有新增）。

**Files:**
- Modify: `core/decoder.py`（`SignalSeries` 之后、`_pad_to_64` 之前插入 `Bucket`）
- Create: `tests/test_bucket.py`

**Interfaces:**
- Consumes: `MessageDef`（dbc_loader 已有 import）、numpy。
- Produces（Task 3 消费）:
  - `Bucket` dataclass，字段与 docstring 按 spec A1 定稿逐字（字段名 = 现 dict 键名，15 处 `b["…"]` 改属性访问零改名成本）。
  - `Bucket.from_feed(arb: int, raw_id: int, md: MessageDef) -> Bucket` — feed 相位建桶（现 decoder.feed setdefault 语义）。
  - `Bucket.from_blocks(arb: int, raw_id: int, md: MessageDef, block: np.ndarray, ts: np.ndarray, lens: np.ndarray) -> Bucket` — blocks 相位建桶（现 converter._read_vectorized 语义）。
  - `add_frame(ts_seconds: float, data: bytes) -> None` / `add_block(block, ts, lens) -> None` — 相位内追加（参数序见澄清 ③）。
  - `to_array() -> None` — 幂等：feed/blocks 相位 → 数组相位；数组相位 no-op。**H2a 性能注释随迁**。
  - `@property n_frames -> int` — mp_finish 任务/进度用（现 `len(b["ts"])`）。
  - `memory_estimate() -> int` — 三相位（feed：`len(ts)*32 + Σlen(data)`；blocks/数组：nbytes 求和）。
- 本任务不改动任何既有函数；`_normalize_bucket` / `_bucket_data_array` 保持原样（Task 3 删除）。

- [x] **Step 1: 写失败测试**（新建 `tests/test_bucket.py`）

```python
"""Bucket 解码桶相位契约测试（spec A：桶契约类型化）。

相位互斥（由工厂方法保证）：feed 列表 / blocks 块 / 数组三相位；
to_array 幂等收敛到数组相位；n_frames / memory_estimate 覆盖三相位。
"""
import numpy as np
import pytest

from core.decoder import Bucket
from core.dbc_loader import MessageDef


def _md(frame_length=8):
    return MessageDef(name="T", sender_node="ECU", frame_length=frame_length)


def test_from_feed_initializes_feed_phase():
    b = Bucket.from_feed(100, 100, _md())
    assert b.arb == 100 and b.raw_id == 100
    assert b.feed_ts == [] and b.feed_data == []
    assert b.ts is None and b.blocks is None


def test_from_blocks_initializes_blocks_phase():
    """raw_id = 首帧原始 id（调用方传入，两工厂均捕获）。"""
    ts = np.array([1.0, 2.0], dtype=np.float64)
    lens = np.array([2, 2], dtype=np.int64)
    block = np.zeros((2, 2), dtype=np.uint8)
    b = Bucket.from_blocks(100, 0x64, _md(), block, ts, lens)
    assert b.raw_id == 0x64
    assert b.blocks == [block] and b.ts_blocks == [ts] and b.lens_blocks == [lens]
    assert b.ts is None and b.feed_ts is None


def test_to_array_feed_phase_converts_once():
    """feed 相位 → 数组：lens 从 data 字节长推导、data 列宽 = max lens 补零；幂等。"""
    b = Bucket.from_feed(100, 100, _md())
    for t, d in zip([0.0, 1.5, 2.0], [b"\x01", b"\x02\x03", b""]):
        b.add_frame(t, d)
    b.to_array()
    assert b.ts.dtype == np.float64 and b.lens.dtype == np.int64 \
        and b.data.dtype == np.uint8
    assert np.array_equal(b.ts, [0.0, 1.5, 2.0])
    assert np.array_equal(b.lens, [1, 2, 0])
    assert b.data.shape == (3, 2)          # 列宽 = max lens
    assert b.data.tolist() == [[1, 0], [2, 3], [0, 0]]
    assert b.feed_ts is None and b.feed_data is None
    b.to_array()                            # 幂等：结果不变
    assert np.array_equal(b.ts, [0.0, 1.5, 2.0])
    assert np.array_equal(b.lens, [1, 2, 0])


def test_to_array_blocks_phase_matches_feed_phase():
    """同帧序列：feed 逐帧 vs 单块整体入桶，to_array 后逐元素等价（双转换合一）。"""
    frames = [b"\x01", b"\x02\x03", b"\x04"]
    ts = [0.0, 1.5, 2.25]
    bf = Bucket.from_feed(100, 100, _md())
    for t, d in zip(ts, frames):
        bf.add_frame(t, d)
    bf.to_array()
    rows = np.array([list(f) + [0] * (2 - len(f)) for f in frames], dtype=np.uint8)
    bb = Bucket.from_blocks(100, 100, _md(), rows,
                            np.asarray(ts, dtype=np.float64),
                            np.asarray([len(f) for f in frames], dtype=np.int64))
    bb.to_array()
    assert np.array_equal(bf.ts, bb.ts)
    assert np.array_equal(bf.lens, bb.lens)
    assert np.array_equal(bf.data, bb.data)


def test_to_array_blocks_multi_block_pad():
    """多块拼接（H2a 一次末态连接语义）：窄块行按末态列宽补零，源相位置 None。"""
    b = Bucket.from_blocks(100, 100, _md(),
                           np.array([[1, 2]], dtype=np.uint8),
                           np.array([0.0], dtype=np.float64),
                           np.array([2], dtype=np.int64))
    b.add_block(np.array([[3, 4, 5], [6, 7, 8]], dtype=np.uint8),
                np.array([1.0, 2.0], dtype=np.float64),
                np.array([3, 3], dtype=np.int64))
    b.to_array()
    assert b.ts.tolist() == [0.0, 1.0, 2.0]
    assert b.lens.tolist() == [2, 3, 3]
    assert b.data.tolist() == [[1, 2, 0], [3, 4, 5], [6, 7, 8]]
    assert b.blocks is None and b.ts_blocks is None and b.lens_blocks is None


def test_to_array_array_phase_noop():
    """数组相位 no-op：不重建、不重赋值（对象同一性保持）。"""
    ts = np.array([0.0], dtype=np.float64)
    lens = np.array([1], dtype=np.int64)
    data = np.array([[1]], dtype=np.uint8)
    b = Bucket.from_blocks(100, 100, _md(), data, ts, lens)
    b.to_array()
    b.to_array()
    assert b.ts is ts and b.lens is lens and b.data is data


def test_n_frames_three_phases():
    feed = Bucket.from_feed(1, 1, _md())
    for t in (0.0, 1.0, 2.0):
        feed.add_frame(t, b"\x01")
    assert feed.n_frames == 3
    feed.to_array()
    assert feed.n_frames == 3
    blocks = Bucket.from_blocks(1, 1, _md(),
                                np.zeros((2, 2), dtype=np.uint8),
                                np.array([0.0, 1.0], dtype=np.float64),
                                np.array([2, 2], dtype=np.int64))
    blocks.add_block(np.zeros((1, 2), dtype=np.uint8),
                     np.array([2.0], dtype=np.float64),
                     np.array([2], dtype=np.int64))
    assert blocks.n_frames == 3


def test_memory_estimate_three_phases():
    feed = Bucket.from_feed(1, 1, _md())
    feed.add_frame(0.0, b"\x01\x02")
    feed.add_frame(1.0, b"\x03")
    assert feed.memory_estimate() == 2 * 32 + 3      # feed：len(ts)*32 + Σlen(data)
    feed.to_array()
    assert feed.memory_estimate() == int(feed.ts.nbytes) + int(feed.lens.nbytes) \
        + int(feed.data.nbytes)
    blocks = Bucket.from_blocks(1, 1, _md(),
                                np.zeros((2, 2), dtype=np.uint8),
                                np.array([0.0, 1.0], dtype=np.float64),
                                np.array([2, 2], dtype=np.int64))
    blocks.add_block(np.zeros((1, 2), dtype=np.uint8),
                     np.array([2.0], dtype=np.float64),
                     np.array([2], dtype=np.int64))
    exp = sum(t.nbytes for t in blocks.ts_blocks) \
        + sum(l.nbytes for l in blocks.lens_blocks) \
        + sum(blk.nbytes for blk in blocks.blocks)
    assert blocks.memory_estimate() == exp
```

- [x] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_bucket.py -q`
Expected: 8 处 FAIL，`ImportError: cannot import name 'Bucket' from 'core.decoder'`。

- [x] **Step 3: 实现 Bucket**（插入 `core/decoder.py` 的 `SignalSeries`（第 24 行）之后；docstring 按 spec A1 定稿逐字）

```python
@dataclass
class Bucket:
    """解码桶（跨模块契约，词条见 CONTEXT.md「解码桶」）：
    arb=归一化键（= buckets dict 键，finish 入口断言）；raw_id=首帧原始 id；
    桶内帧均已分类（构造即预检，finish 不再防御）；插入序=系列序（dict 插入序）。
    相位互斥（由工厂方法保证）：
      feed 相位（feed_ts: list[float], feed_data: list[bytes]）→ 数组相位（to_array）
      blocks 相位（blocks/ts_blocks/lens_blocks: list[ndarray]）→ 数组相位（to_array）
    数组相位 = ts (N,) float64 / lens (N,) int64 / data (N, L) uint8（finish 消费）。"""
    arb: int
    raw_id: int
    md: MessageDef
    feed_ts: list[float] | None = None
    feed_data: list[bytes] | None = None
    blocks: list[np.ndarray] | None = None
    ts_blocks: list[np.ndarray] | None = None
    lens_blocks: list[np.ndarray] | None = None
    ts: np.ndarray | None = None
    lens: np.ndarray | None = None
    data: np.ndarray | None = None

    @classmethod
    def from_feed(cls, arb: int, raw_id: int, md: MessageDef) -> "Bucket":
        """feed 路由建桶（decoder.feed setdefault 语义）：raw_id = 首帧原始 id。"""
        return cls(arb=arb, raw_id=raw_id, md=md, feed_ts=[], feed_data=[])

    @classmethod
    def from_blocks(cls, arb: int, raw_id: int, md: MessageDef,
                    block: np.ndarray, ts: np.ndarray, lens: np.ndarray) -> "Bucket":
        """向量化路由建桶（converter._read_vectorized 语义）：raw_id = 首帧原始 id。"""
        return cls(arb=arb, raw_id=raw_id, md=md, blocks=[block],
                   ts_blocks=[ts], lens_blocks=[lens])

    def add_frame(self, ts_seconds: float, data: bytes) -> None:
        """feed 追加一帧（仅 feed 相位）。"""
        self.feed_ts.append(ts_seconds)
        self.feed_data.append(data)

    def add_block(self, ts: np.ndarray, lens: np.ndarray, block: np.ndarray) -> None:
        """向量化追加一块（仅 blocks 相位）。"""
        self.ts_blocks.append(ts)
        self.lens_blocks.append(lens)
        self.blocks.append(block)

    def to_array(self) -> None:
        """三相位 → 数组相位（幂等：数组相位 no-op）。

        feed 相位 → 数组：lens 从 data 字节长推导（原 _normalize_bucket +
        _bucket_data_array 语义内化）；blocks 相位 → 数组：一次末态连接（原
        _assemble_bucket 语义内化）。转换后源相位字段置 None（互斥不变式）。
        """
        if self.ts is not None:
            return
        if self.feed_ts is not None:
            lens = np.fromiter((len(d) for d in self.feed_data), dtype=np.intp,
                               count=len(self.feed_ts))
            n = len(self.feed_data)
            max_len = int(lens.max()) if n else 0
            data = np.zeros((n, max_len), dtype=np.uint8)
            for i, d in enumerate(self.feed_data):
                if d:
                    data[i, : len(d)] = np.frombuffer(d, dtype=np.uint8)
            self.lens = lens
            self.data = data
            self.ts = np.asarray(self.feed_ts, dtype=np.float64)
            self.feed_ts = None
            self.feed_data = None
            return
        # blocks 相位 → 数组（H2a：一次末态连接替代逐容器 np.concatenate
        # 重分配——实测 3774 次追加拷贝 4.83GB、其中 4.67GB 可避免、函数内
        # 1.65s → 块切片批量拷贝，每字节只写一次；不用 row/col 散点——桶规模
        # 1.6 亿位置 × int64 索引数组的 fancy indexing 实测 7.6s，比块拷贝
        # 慢一个数量级）
        ts = np.concatenate(self.ts_blocks)
        lens = np.concatenate(self.lens_blocks)
        n = len(ts)
        L = max(int(blk.shape[1]) for blk in self.blocks) if self.blocks else 0
        data = np.zeros((n, L), dtype=np.uint8)
        start = 0
        for blk, bl in zip(self.blocks, self.lens_blocks):
            m = len(bl)
            if m:
                if blk.shape[1] < L:
                    blk = np.pad(blk, ((0, 0), (0, L - blk.shape[1])))
                data[start:start + m] = blk
            start += m
        self.ts = ts
        self.lens = lens
        self.data = data
        self.blocks = None
        self.ts_blocks = None
        self.lens_blocks = None

    @property
    def n_frames(self) -> int:
        """桶内帧数（三相位）：mp_finish 任务/进度用（现 len(b["ts"])）。"""
        if self.ts is not None:
            return len(self.ts)
        if self.feed_ts is not None:
            return len(self.feed_ts)
        if self.ts_blocks is not None:
            return sum(len(t) for t in self.ts_blocks)
        return 0

    def memory_estimate(self) -> int:
        """桶内存估算（三相位）：feed 按帧 ts ~32B + data 字节；blocks/数组按
        nbytes 求和。bucket_bytes 用（isinstance 分派收进类内）。"""
        if self.ts is not None:
            return int(self.ts.nbytes) + int(self.lens.nbytes) + int(self.data.nbytes)
        if self.feed_ts is not None:
            return len(self.feed_ts) * 32 + sum(len(d) for d in self.feed_data)
        if self.ts_blocks is not None:
            return (sum(t.nbytes for t in self.ts_blocks)
                    + sum(l.nbytes for l in self.lens_blocks)
                    + sum(blk.nbytes for blk in self.blocks))
        return 0
```

- [x] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_bucket.py -q`
Expected: 8 用例全 PASS。

- [x] **Step 5: 全量回归确认新类型未接线不影响既有行为**

Run: `python -m pytest tests/ -q`
Expected: 269 passed / 0 failed（Bucket 尚无调用方，纯新增）。

- [x] **Step 6: 交付点（提交由用户执行）**

---

### Task 3: 消费端改造（decoder + converter + mp_finish 一次契约切换）

> **实施澄清 ④（2026-08-19 执行时标注）**：接线完成，全部按正文落地，无新增冲突——三条链路（feed / `_read_vectorized` / finish+worker）一次切换后中间态红预期兑现（Step 2：AttributeError，dict 桶不支持属性赋值）；消费端门禁（Step 7）**50 passed / 0 failed**（test_bucket 9 + test_decoder 11 + test_decoder_vectorized 5 + test_converter + test_parallel_decode 2，黄金套件断言零改动）；全量 pytest **288 passed / 0 failed**（anaconda3 Python 3.13.9，85.8s；287 基线 + 本任务新增 1 个 finish 断言用例）。15 处 `b["…"]` 字典访问全部收敛为属性访问，isinstance 形状分派 2 处（decoder/mp_finish）+ 死残留 1 处（converter stats_bufs）消失。

**Files:**
- Modify: `core/decoder.py`（feed / finish / `_finish_bucket_vectorized` / `_decode_bucket_reference`；删 `_normalize_bucket` / `_bucket_data_array`；Q12 防御掩码删除；`classify` import）
- Modify: `core/converter.py`（`_read_vectorized` 改 `classify_batch` + `message_table` + `Bucket.from_blocks` / `add_block` + 装配循环改 `to_array()`；删 `_prep_decode_info` / `_assemble_bucket`；:389 死残留）
- Modify: `core/mp_finish.py`（worker 签名 typed Bucket + `to_array()`；`bucket_bytes` 改 `memory_estimate`；tasks / `_report` 改 `n_frames`；import 更新）
- Test: `tests/test_bucket.py`（追加 finish 断言用例）

**Interfaces:**
- Consumes: Task 1 的 `classify` / `message_table` / `classify_batch`；Task 2 的 `Bucket`。
- Produces: 无新接口——15 处 `b["…"]` 字典访问点全部收敛为属性访问，isinstance 形状分派 2 处（decoder.py:156、mp_finish.py:91）+ 死残留 1 处（converter.py:389）消失。
- **为什么是一个任务（而非按模块拆三个）**：三条链路（feed 生产 / `_read_vectorized` 生产 / finish+worker 消费）互相耦合——feed 先改则 mp_finish worker 对 Bucket 做 `_normalize_bucket` dict 操作 TypeError；converter 先改则 finish 对 Bucket 做 `_normalize_bucket` TypeError。任何中间态都红 test_parallel_decode 的 finish_all 路径。契约切换是单一可审查单元（reviewer 无法只批准半个契约）。
- **实施澄清 ①（mp_finish worker）**：spec 说「`_normalize_bucket` 调用（:41）删除」——`_normalize_bucket` 整体删除后，其职责内化进 `Bucket.to_array`；worker 改为 `bucket.to_array()`（幂等，数组相位 no-op）。归一化留在 worker 侧，与现状「归一化在 worker 内」语义一致（test_parallel_decode 的 feed 相位桶经 pickle 到 worker 后转换，行为不变）。

- [x] **Step 1: 写失败测试**（追加到 `tests/test_bucket.py` 末尾；此时 finish 尚无断言，测试必红）

```python
def test_finish_asserts_key_matches_arb():
    """finish 入口断言：buckets 键与 bucket.arb 漂移 → AssertionError（防键/字段分叉）。"""
    from core.blf_reader import Frame
    from core.decoder import ChannelDecoder
    from core.dbc_loader import DbcDef

    md = MessageDef(name="T", sender_node="ECU", frame_length=1)
    dbc = DbcDef(path="", db=None, messages={100: md})
    dec = ChannelDecoder(dbc, 1)
    dec.feed(Frame(channel=1, ts_seconds=0.0, arbitration_id=100,
                   is_extended=False, is_fd=False, dlc=1, data=b"\x01"))
    dec.buckets[100].arb = 999              # 制造键/字段漂移
    with pytest.raises(AssertionError):
        dec.finish()
```

- [x] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_bucket.py::test_finish_asserts_key_matches_arb -q`
Expected: FAIL——此时 feed 仍产出 dict 桶，`dec.buckets[100].arb = 999` 抛 AttributeError（dict 不支持属性赋值；断言尚未接线，测试红为预期）。实测：FAIL（AttributeError: 'dict' object has no attribute 'arb'，与预期一致）。

- [x] **Step 3: decoder.py 接线**（五处改动）

3a. Import 增加 classify：

```python
from core.dbc_loader import DbcDef, MessageDef, SignalDef, classify, normalize_id
```

3b. `feed()`（现 :414-433）整体替换——改 `classify` + `from_feed` + `add_frame`；分类注释保留短帧不入桶的系列序理由：

```python
    def feed(self, fr: Frame) -> None:
        stats = self.stats
        stats.total_frames += 1
        arb = normalize_id(fr.arbitration_id, fr.is_extended)
        md = classify(self.dbc, arb, len(fr.data))
        if md is None:
            # 未知 ID，或已知 ID 短帧（cantools DecodeError）→ 未知帧。
            # 桶只收已通过预检的帧：短帧入桶会使系列位置前移，与参考系列
            # 顺序不符（_assert_series_equal 按顺序 zip）。分类规则唯一实现
            # 在 dbc_loader.classify（A2：feed 与向量化路由共用）。
            stats.unknown_frames += 1
            stats.unknown_ids.add(fr.arbitration_id)
            return
        bucket = self.buckets.setdefault(
            arb, Bucket.from_feed(arb, fr.arbitration_id, md))
        bucket.add_frame(fr.ts_seconds, fr.data)
```

3c. `finish()`（现 :435-445）整体替换——`to_array()` 循环 + 入口断言：

```python
    def finish(self) -> tuple[list[SignalSeries], DecodeStats]:
        series = []
        for arb, b in self.buckets.items():
            assert arb == b.arb, f"桶键与字段漂移: {arb} != {b.arb}"
            b.to_array()
            md = b.md
            if not _msg_vectorizable(md):
                series.extend(_decode_bucket_reference(self.dbc, self.channel, b, self.stats))
                continue
            s = _finish_bucket_vectorized(self.dbc, self.channel, b, self.stats)
            series.extend(s)
        return series, self.stats
```

3d. `self.buckets` 注释（:412）更新：

```python
        self.buckets = {}  # arb -> Bucket（三相位互斥，见 Bucket docstring）
```

3e. `_finish_bucket_vectorized`（:295-308 前半）——属性访问 + **Q12 删除防御 length 掩码**：

```python
    md = b.md
    n = len(b.ts)
    # 桶内帧均已分类（构造即预检，见 Bucket docstring）——不再防御短帧
    decodable = np.ones(n, dtype=bool)
    plan = _mux_plan(md)
    if plan is not None:
        data64 = _pad_to_64(b.data).view("<u8")
        selector, children = plan
        sel_raw = _extract_signal(data64, selector)
        known_arr = np.array(sorted(children), dtype=sel_raw.dtype)
        bad = ~np.isin(sel_raw, known_arr)      # 无子组的 mux 值 → DecodeError → 未知
        decodable &= ~bad
    n_unknown = n - int(decodable.sum())
    if n_unknown:
        stats.unknown_frames += n_unknown
        stats.unknown_ids.add(b.raw_id)
    if not decodable.any():
        return []
    if plan is None:
        data64 = _pad_to_64(b.data).view("<u8")
    d64 = data64[decodable]
    timestamps = np.asarray(b.ts, dtype=np.float64)[decodable]
```

（删除 `lens = b["lens"]`、`valid = lens >= md.frame_length`、`decodable = valid.copy()` 三行——`lens` 局部变量唯一消费者是 valid 掩码，全函数无其他引用；:307「短帧已由 valid 排除」括号注释随掩码删除。`decodable` 起点改为全真，mux 无子组计数（:306）与 unknown 记账原样保留。:302 的 `data64 = _pad_to_64(b["data"])...` 已在 mux 块内改为 `b.data` 属性访问。）

3f. 确认函数余部无字典访问：:308-335 已在 3e 块内覆盖（`n_unknown` 计数、`b.raw_id` 记账、`data64`/`d64`/`timestamps` 局部与 `b.data`/`b.ts` 属性访问、SignalSeries 装配），无遗漏的 `b["…"]` 访问点。

3g. `_finish_bucket_vectorized` docstring（:291-293）更新：

```python
    """单桶向量化解码（无 mux 或单级 mux），与逐帧参考实现逐点等价。

    A：桶为数组相位 Bucket（to_array 收敛：ts float64 (N,)、lens int64
    (N,)、data (N, L) uint8——feed/向量化路由统一经 to_array 到数组相位）。
    """
```

3h. `_decode_bucket_reference` 属性访问（:347-356）：

```python
    md = b.md
    vals_by_sig = {s.name: [] for s in md.signals}
    ts_ok = []
    for ts, ln, row in zip(b.ts, b.lens, b.data):
        try:
            # 归一化键（含 EFF 位）与 feed/参考实现对拍一致（rulings 修正 2）
            decoded = dbc.db.decode_message(b.arb, row[:ln].tobytes())
        except (KeyError, DecodeError):
            stats.unknown_frames += 1
            stats.unknown_ids.add(b.raw_id)
            continue
```
（docstring :342-343「H1 Step 2：数组表示」改为「数组相位（to_array 收敛）」）

3i. 删除 `_normalize_bucket`（:150-161）与 `_bucket_data_array`（:140-147）整函数——语义已内化进 `Bucket.to_array`（Task 2 Step 3）。

- [x] **Step 4: 运行 decoder 侧门禁**

Run: `python -m pytest tests/test_bucket.py tests/test_decoder.py tests/test_decoder_vectorized.py -q`
Expected: 全 PASS（test_decoder / test_decoder_vectorized 断言零改动原样通过）。此时 test_parallel_decode 仍红（mp_finish 尚未接线，见任务说明）——**预期中间态，继续 Step 5**。实测：26 passed / 0 failed。

- [x] **Step 5: converter.py 接线**（五处改动）

5a. Import 更新：

```python
from core.decoder import Bucket, ChannelDecoder
from core.dbc_loader import DbcDef, classify_batch, message_table, normalize_ids
```

5b. 删除 `_prep_decode_info`（:25-39）整函数；`_read_vectorized` 首行（:114）改为：

```python
    info = {ch: message_table(dec.dbc) for ch, dec in decoders.items()}
```

5c. `_read_vectorized` 解码桶循环（:139-176）——分类改 `classify_batch`（空表早退内化：全 False 掩码 → `~valid` 全计未知，与旧语义逐位一致），建桶改 `Bucket.from_blocks` / `add_block`：

```python
            dec.stats.total_frames += cnt
            norm = arb_norm[idx_m]
            found, valid = classify_batch(info[ch], norm, cf.data_len[idx_m])
            bad = cnt - int(valid.sum())
            if bad:
                dec.stats.unknown_frames += bad
                dec.stats.unknown_ids.update(cf.arb[idx_m][~valid].tolist())
            if not valid.any():
                continue
            keys, _, mds = info[ch]
            # 桶分组：首现序 = 桶插入序（feed setdefault 首次出现序）
            vkeys = norm[valid]
            uniq, first_i = np.unique(vkeys, return_index=True)
            for k in uniq[np.argsort(first_i, kind="stable")]:
                grp = valid & (norm == k)
                sel = idx_m[grp]
                arb = int(k)
                md = mds[int(np.searchsorted(keys, k))]
                ts, lens, block = _bucket_block(cf, sel)
                b = dec.buckets.get(arb)
                if b is None:
                    dec.buckets[arb] = Bucket.from_blocks(
                        arb, int(cf.arb[sel][0]), md, block, ts, lens)
                else:
                    b.add_block(block, ts, lens)      # 参数序按澄清 ③ 修订（block 在前）
```

（替换自原 `keys, lens_tab, mds = info[ch]` 起至块追加结束；`if len(keys) == 0` 早退分支删除——classify_batch 空表返回全 False，语义不变。）

5d. 删除 `_assemble_bucket`（:79-103）整函数；装配循环（:196-199）改为：

```python
    # 桶装配：blocks 相位 → 数组相位（读入后一次成型，下游 finish/阈值计按数组消费）
    for dec in decoders.values():
        for b in dec.buckets.values():
            b.to_array()
```

5e. 死残留（:388-389）——`isinstance(t[0], np.ndarray)` 删除（stats_bufs 块收集产物恒为 ndarray；M1 死残留）：

```python
        for ch, (t, e, r, er) in list(stats_bufs.items()):
            if not t:
                continue
```

- [x] **Step 6: mp_finish.py 接线**（四处改动）

6a. Import 更新：

```python
from core.decoder import (Bucket, DecodeStats, _decode_bucket_reference,
                          _finish_bucket_vectorized, _msg_vectorizable)
```

6b. `_finish_bucket_worker`（:31-49）——签名 typed `Bucket`、`_normalize_bucket` 调用改 `bucket.to_array()`（实施澄清 ①）：

```python
def _finish_bucket_worker(channel: int, arb: int, bucket: Bucket, dbc):
    """子进程入口：单桶 finish（= ChannelDecoder.finish() 内同一函数）。

    返回 (channel, arb, series 列表, unknown_frames, sorted(unknown_ids),
    桶解码耗时秒)。耗时在 worker 内实测（perf_counter 包住解码本身，
    不含 pickle/传输）——timings 语义 = 每通道累计工作量，而非调度跨度。
    stats 从空开始（feed 期计数在父进程，见 finish_all 合并规则）。
    """
    t0 = time.perf_counter()
    stats = DecodeStats()
    bucket.to_array()   # feed/blocks 相位 → 数组相位（幂等；原 _normalize_bucket 职责内化）
    md = bucket.md
    if not _msg_vectorizable(md):
        # 回退路径需要 cantools Database（_decode_bucket_reference 用 dbc.db）
        series = _decode_bucket_reference(dbc, channel, bucket, stats)
    else:
        series = _finish_bucket_vectorized(dbc, channel, bucket, stats)
    return channel, arb, series, stats.unknown_frames, sorted(stats.unknown_ids), \
        time.perf_counter() - t0
```

6c. `bucket_bytes`（:82-96）整体替换——isinstance 分派收进 `memory_estimate`：

```python
def bucket_bytes(decoders, channels: list[int]) -> int:
    """桶内存估算（阈值回退用，见 §5.7）：Σ Bucket.memory_estimate()（三相位）。"""
    total = 0
    for ch in channels:
        for b in decoders[ch].buckets.values():
            total += b.memory_estimate()
    return total
```

6d. tasks（:165-170）与 `_report`（:188-194）——`len(b["ts"])` / `len(bucket["ts"])` 改 `n_frames`；`bins` / `failed` 注解同步：

```python
    tasks: list[tuple[int, int, Bucket, int]] = []                # (ch, arb, 桶, 帧数)
    for ch in channels:
        for arb in decoders[ch].buckets:
            b = decoders[ch].buckets[arb]
            order[ch].append(arb)
            tasks.append((ch, arb, b, b.n_frames))
```
```python
    def _report(ch: int, bucket: Bucket) -> None:
        """每桶完成上报：percent = 10 + 80 × 累计帧数 / 总帧数（单调）。"""
        nonlocal done_frames
        done_frames += bucket.n_frames
```

（`bins: list[list[tuple[int, int, dict, int]]]` → `list[list[tuple[int, int, Bucket, int]]]`；`failed: list[tuple[int, int, dict]]` → `list[tuple[int, int, Bucket]]`。模块 docstring :16「报文定义在 bucket["md"]」→「bucket.md」。）

- [x] **Step 7: 运行消费端门禁**

Run: `python -m pytest tests/test_bucket.py tests/test_decoder.py tests/test_decoder_vectorized.py tests/test_converter.py tests/test_parallel_decode.py -q`
Expected: 全 PASS（黄金套件原样通过；并行/串行等价、fuzz 对拍、分类语义、convert 契约全部绿）。实测：**50 passed / 0 failed**（20.3s）；随后全量 `python -m pytest tests/ -q` 实测 **288 passed / 0 failed**（85.8s，287 基线 + 本任务新增 1 用例）。

- [x] **Step 8: 交付点（提交由用户执行）**

---

### Task 4: 测试新增（路由等价对拍 + reference_decode 换调 classify）

> **实施记录（2026-08-19 执行时标注）**：按正文全量落地，无新增冲突。Step 2 回归锁实测 **1 passed**（3.3s，诞生即绿——Task 3 接线无漂移）；Step 4 实测 **14 passed / 0 failed**（13.4s，test_decoder_vectorized 9 + test_parallel_decode 3 + 路由等价 1 + 模块对拍 1，fuzz 对拍断言零改动）；Step 5 全量 pytest 实测 **289 passed / 0 failed**（anaconda3 Python 3.13.9，76.5s；288 基线 + 本任务新增 1 用例）。真解释器为 `C:/ProgramData/anaconda3/python.exe`（PATH 上 python 是商店空壳）。

**Files:**
- Modify: `tests/test_parallel_decode.py`（追加路由等价测试）
- Modify: `tests/test_decoder_vectorized.py`（`reference_decode` oracle 换调 `classify`）

**Interfaces:**
- Consumes: Task 1 的 `classify`；Task 2/3 的 `Bucket`（属性访问）、`_read_vectorized` 装配后数组相位。
- Produces: 无生产接口；`_assert_buckets_equal` 为测试内私有 helper。

- [x] **Step 1: 写路由等价测试**（追加到 `tests/test_parallel_decode.py` 末尾；import 更新两行）

`from core import blf_reader, mp_finish` 替换现 :13；`from core.converter import convert, _read_vectorized` 替换现 :14：

```python
def test_route_equivalence_buckets(tmp_path):
    """两条建桶路由（feed 逐帧 / _read_vectorized 向量化）桶级逐元素对拍。

    M1 最脆弱等价点收口：np.unique 首现序 vs setdefault 插入序、未知 ID/短帧
    分类此前只靠注释声明 + 端到端对拍间接兜底；本测试直接对拍键集、插入序、
    ts/lens/data、raw_id 与 unknown 记账。feed 侧输入 = 同一文件的标量读回
    （blf_reader.iter_messages）——与向量化侧同源整数 ns，ts 逐位可比。"""
    import can

    from test_decoder_vectorized import (MUX_DBC, _random_frames,
                                         _random_signal_dbc)

    rng = np.random.default_rng(20260819)
    for trial in range(10):
        if trial % 2:
            dbc_txt, known = _random_signal_dbc(rng, 8), [100]
        else:
            dbc_txt, known = MUX_DBC, [200]      # mux 报文混入建桶输入面
        p = tmp_path / f"r{trial}.dbc"
        p.write_text(dbc_txt, encoding="utf-8")
        dbc = load(str(p))
        frames = _random_frames(rng, int(rng.integers(10, 120)), known_ids=known)
        blf = tmp_path / f"r{trial}.blf"
        with can.BLFWriter(str(blf), channel=4) as w:
            for fr in frames:
                w.on_message_received(can.Message(
                    timestamp=1784716800.0 + fr.ts_seconds,
                    arbitration_id=fr.arbitration_id,
                    is_extended_id=fr.is_extended,
                    dlc=fr.dlc, data=fr.data, channel=fr.channel))
                # 与 test_parallel_decode:90-97 先例同形（不写 is_fd：
                # _random_frames 恒 is_fd=False，BLF 往返标志无关紧要）
        # feed 路由（to_array 后；输入 = 同一文件标量读回帧）
        dec_feed = ChannelDecoder(dbc, 1)
        for fr in blf_reader.iter_messages(str(blf), 1):
            dec_feed.feed(fr)
        for b in dec_feed.buckets.values():
            b.to_array()
        # 向量化路由（_read_vectorized 装配后 = 数组相位）
        dec_vec = ChannelDecoder(dbc, 1)
        # 签名 = (blf_path, decoders, raw_chs, stats_export, stats_bufs,
        #         raw_chunks, known_keys, read_cb, cancel_cb)——raw_chs=[] 时
        # 原始帧/known_keys 分支不执行，stats_export=False 时 stats_bufs 不触碰
        _read_vectorized(str(blf), {1: dec_vec}, [], False, {}, None,
                         None, None, None)
        # 桶级逐元素对拍：键集 + 插入序（最脆弱等价点）
        assert list(dec_vec.buckets) == list(dec_feed.buckets), \
            f"键集/插入序: {list(dec_vec.buckets)} vs {list(dec_feed.buckets)}"
        for k in dec_vec.buckets:
            bv, bf = dec_vec.buckets[k], dec_feed.buckets[k]
            assert (bv.raw_id, bv.md.name) == (bf.raw_id, bf.md.name), k
            assert bv.ts.dtype == np.float64 and bf.ts.dtype == np.float64
            assert bv.lens.dtype == np.int64 and bf.lens.dtype == np.int64
            assert bv.data.dtype == np.uint8 and bf.data.dtype == np.uint8
            assert np.array_equal(bv.ts, bf.ts), f"{k} ts"
            assert np.array_equal(bv.lens, bf.lens), f"{k} lens"
            assert np.array_equal(bv.data, bf.data), f"{k} data"
        assert dec_vec.stats == dec_feed.stats, "unknown 记账逐位一致"
```

- [x] **Step 2: 运行确认通过（回归锁）**

Run: `python -m pytest tests/test_parallel_decode.py::test_route_equivalence_buckets -q`
Expected: PASS（两条路由已等价——本测试是结构性收口锁，诞生即绿；若红则说明 Task 3 接线引入漂移，先修再继续）。

- [x] **Step 3: reference_decode 换调 classify**（`tests/test_decoder_vectorized.py`）

3a. Import 更新（:14）：

```python
from core.dbc_loader import DbcDef, SignalDef, classify, load, normalize_id
```

3b. `reference_decode`（:98-114 前半）——键查找 + 帧长校验改用生产 `classify`，解码仍走 cantools `decode_message`（独立 oracle 的解码面不动）；三路径已推演一致（已知 ID 短帧 / 未知 ID / mux 无子组）：

```python
def reference_decode(frames, dbc: DbcDef, channel: int):
    """逐帧 cantools 参考（oracle）：键空间契约（归一化键 + 帧长校验）换调
    生产 classify（A2 收口，oracle 不复制键规则，M2 normalize_id 先例同型）；
    解码面保持 cantools decode_message 独立（oracle 语义）。"""
    stats = DecodeStats()
    buckets = {}
    for fr in frames:
        stats.total_frames += 1
        arb = normalize_id(fr.arbitration_id, fr.is_extended)
        md = classify(dbc, arb, len(fr.data))
        if md is None:
            # 未知 ID / 已知 ID 短帧 → 未知帧（mux 无子组在解码面仍 DecodeError）
            stats.unknown_frames += 1
            stats.unknown_ids.add(fr.arbitration_id)
            continue
        try:
            decoded = dbc.db.decode_message(arb, fr.data)
        except (KeyError, DecodeError):
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
```

（原 :102-114 的 `decode_message` 先行 + `md = dbc.messages.get(arb)` 后置的两段，替换为先 `classify` 后 `decode_message`——每条帧的统计记账逐帧等价，见函数 docstring。）

- [x] **Step 4: 运行确认等价性无漂移**

Run: `python -m pytest tests/test_decoder_vectorized.py tests/test_parallel_decode.py -q`
Expected: 全 PASS（5 处 fuzz 对拍断言零改动；oracle 换调后逐帧行为等价）。

- [x] **Step 5: 交付点（提交由用户执行）**

---

### Task 5: 全量 pytest + 逐位对拍链

> **实施记录（2026-08-19 执行时标注）**：
> - Step 1 实测 **289 passed / 0 failed（83.97s，anaconda3 Python 3.13.9）**，与 Task 4 基线一致。两种调用方式在本机不可并行验证：PATH 上 `python` 是商店空壳（WindowsApps stub，直跑 exit 49 无输出）、`pytest` 不在 PATH——唯一可用调用 = `C:/ProgramData/anaconda3/python.exe -m pytest tests/ -q`（6 条 warnings 为 test_compare_cli 既有 GBK 解码环境问题，与 A 无关）。
> - Step 2 发现计划正文 stdin heredoc 脚本在 Windows spawn 下的缺陷并修正后复跑：① 首跑（按正文 heredoc）worker spawn 全灭（`__mp_main__` 重导入 `<stdin>` → OSError）→ mp_finish BrokenProcessPool → 串行兜底（§5.7），「并行产物」实为串行产物，`对拍差异: []` 是串行对串行的假阳性；② 改真实脚本但漏 `__main__` 守卫 → worker 重执行脚本体 → 转换级联 + 输出文件互抢（WinError 32，残留 .mf4）；③ 加 `if __name__ == "__main__":` 守卫后干净复现：**串行 20.1s / 并行 26.2s / `对拍差异: []`**（exit 0，无 traceback）。并行真实执行证据：进程采样并行窗口内 9 个 python 进程（父 + 8 worker = min(cpu 8, 10 通道)，池关闭后回落 1）+ timings「解码墙钟（并行） 2.17s」vs 逐通道累计工作量 7.09s（CPU 工作量 > 墙钟 = 真并行特征）。临时脚本已删；**后续对拍改用真实脚本文件 + `__main__` 守卫，勿用 stdin heredoc**。
> - Step 3 随手记：串行 20.1s（读入 8.62 + 解码 2.57 + 聚合 0.39 + 写 MDF 8.44）；并行 26.2s（读入 8.28 + 解码墙钟并行 2.17 + 聚合 0.40 + 写 8.75）。AHT 82MB 上并行反慢 ~6s——解码工作量仅 ~2.5s（读入/写占大头），池 spawn + pickle 开销大于并行收益，符合「并行只作用于解码阶段」的设计；与本机 2026-08-15 基线（29.8s）相比串行更快（机器波动/后续优化，非通过条件）。

**Files:**
- 无代码改动；验证任务。

- [x] **Step 1: 全量 pytest（两种调用方式，H1 后 `pythonpath = .` 保证）**

Run: `python -m pytest tests/ -q`
Expected: 全 PASS / 0 failed（269 基线 + 新增：Task 1 的 4 + Task 2 的 8 + Task 3 的 1 + Task 4 的 1 ≈ 283；以实际输出为准并记录）。

Run: `pytest tests/ -q`
Expected: 同上全绿。

- [x] **Step 2: AHT 并/串产物逐位对拍**（样例在场时执行；`inputs/blf/` 无 AHT 文件则跳过并记录）

Run（Git Bash；约 1 分钟）：

```bash
cd /e/projects/blf_dbc
python - <<'EOF'
import sys, time
sys.path.insert(0, ".")
from core.converter import convert
from core.dbc_loader import load
from tools.mdf_compare import compare_files_identical
DBC_DIR = r"inputs/dbc_ccu3.0/AHT"
BLF = (r"inputs/blf/AHT_ACFCANPUB_20260317_210430_59125089-"
       r"ACFCAN_20260317_210930_59125099.blf")
MAPPING = {1: "PFCAN1.dbc", 3: "CFCAN2.dbc", 6: "CFCAN3.dbc", 8: "ZFCANF.dbc",
           9: "ZFCANL.dbc", 10: "ZFCANR.dbc", 11: "ZFCANT.dbc", 12: "IFCAN.dbc",
           13: "CFCAN1.dbc", 15: "PFCAN2.dbc"}
bindings = {ch: load(f"{DBC_DIR}/{name}") for ch, name in MAPPING.items()}
for par, out in ((False, "outputs/A_ser.mdf"), (True, "outputs/A_par.mdf")):
    t = time.perf_counter()
    r = convert(BLF, bindings, out, parallel=par)
    print(out, f"{time.perf_counter() - t:.1f}s", f"时长 {r.duration_seconds:.3f}s")
diffs = compare_files_identical("outputs/A_ser.mdf", "outputs/A_par.mdf")
print("对拍差异:", diffs)
assert diffs == []
EOF
```

Expected: 两行转换输出 + `对拍差异: []`（并行/串行产物四维逐位一致）。

- [x] **Step 3: 性能随手记（不作通过条件）**

Step 2 的输出已含并/串各自耗时——记录到任务日志；不另行重跑 AHT 基准（spec：类型化只换容器，性能前提由 numpy 运算语义零变化 + H2a 注释随迁保障）。

- [x] **Step 4: 交付点（提交由用户执行）**

---

### Task 6: 双轴 code-review（receiving-code-review 流程）

> **实施记录（2026-08-19 执行时标注）**：
> - Step 1 双轴并行子代理完成：Standards 轴硬性契约全部核验通过（Q11 字段名逐字 / 依赖方向零变化 / 黄金套件断言零改动 / 分类唯一实现 / Bucket 三相位互斥 / finish 断言在位 / isinstance 分派三处消失 / 289 全绿），仅 5 项 nitpick；Spec 轴 **0 findings**（A1-A4 全对位、Out of Scope 零触碰、三处实施澄清均落地且有修订记录）。审查报告存 [2026-08-19-a-bucket-typing-review.md](./2026-08-19-a-bucket-typing-review.md)（docs/reviews/a/，B/D/G 先例同布局）。
> - Step 2 findings 逐条处置（receiving-code-review：先核验再动作）：S1（plan:517 数字与 spec 269 基线跨时点比对不符）**误报关闭**——plan 数字链自洽（287→288→289 逐任务时点实测）；「N passed」占位为 Task 7 模板，Task 7 执行时填充。S2（spec:63 `add_block` 参数序未同步）**已修订**——spec 签名改为 block 在前并标注 plan 澄清 ③ 理由（spec/文档漂移类随修订关闭，B 先例）。S3（test_dbc_loader.py:137 模块中部重复 import）**已修订**——classify/classify_batch/message_table 并入顶部 import、中部块删除（纯卫生，行为零变化）。S4（oracle KeyError 防御分支不可达）**保留**——oracle 防御无害（mux 无子组仍走 DecodeError），改动属无谓（标准 ⑥）。S5（`_bucket_block` 返回序 vs 入参序双序并存）**保留**——既有函数（A 范围外），调用点解包重排显式且局部（judgement）。
> - Step 3 全量 pytest 复跑实测 **289 passed / 0 failed（75.97s）**，与处置前一致（处置零行为变化）；0 实质 findings 收尾（blocking 0 / substantive 0）。

**Files:**
- 生产代码零改动；测试侧唯一改动 = tests/test_dbc_loader.py import 合并（S3 处置）；文档侧 = spec :63 签名同步（S2 处置）+ 审查报告新增。

- [x] **Step 1: 触发双轴 review**

按项目惯例走 receiving-code-review：Standards 轴（架构干净 / 调用链扁平 / 契约明确 / 严格收口 / 易维护 / 不新增无谓抽象）+ Spec 轴（对照 2026-08-18-a-bucket-typing-spec.md 逐条核验 Q1-Q16 决策落地）。审查报告存 `docs/reviews/`（与 B/D/G 先例同布局）。

- [x] **Step 2: 处理 findings**

修复实质问题并补测试（B 先例：Spec 轴曾捕获 1 个实质问题）；spec/文档漂移类 finding 随修订关闭。

- [x] **Step 3: 0 findings 收尾**

Expected: 双轴 code-review 0 findings；全量 pytest 复跑全绿。

---

### Task 7: 审查文档更新（§3 M1 / §4 A / §5 首要建议 / §2.1 行数）+ CONTEXT.md 核对

> **实施记录（2026-08-19 执行时标注）**：
> - Step 1 CONTEXT.md「解码桶」词条核对通过：arb=归一化键 / raw_id=首帧原始 id / 插入序=系列序 / 只收已通过预检的帧 四句与 spec A1 一致，未改动。
> - Step 2-6 审查文档（docs/reviews/2026-08-18-architecture-review.md）更新：头部 A 更新块（N=289，269 基线 + 新增 20：test_bucket 9 + test_dbc_loader 10 + test_parallel_decode 1）；§3 M1 状态标注（✅ 已完成，2026-08-19）；§4 候选 A 行改「✅ 已落地」；§5 首要建议首段与理由 2 更新（无剩余 Strong 候选，下一步 M8/L9 bench/probe 清理等）；§2.1 行数刷新（decoder 453→551 / converter 530→472 / mp_finish 279→271 / dbc_loader 126→175，dbc_loader 公开接口列补 classify 族；tests 20→21 文件）。另有超出正文 Step 6 字面、但属同一目标（消除已删函数名漂移）的两处同步：§2.2 主链路图 `_assemble_bucket`/`_normalize_bucket` 改 `Bucket.to_array` 链路；§7「下一步」段收尾（五个 Strong 候选全落地）。
> - Step 7 全量 pytest 终验实测 **289 passed / 0 failed（71.53s，exit 0）**。

**Files:**
- Modify: `docs/reviews/2026-08-18-architecture-review.md`
- Verify: `CONTEXT.md`（「解码桶」词条——spec 阶段已加（Q15 定稿措辞），仅核对无需改动）

- [x] **Step 1: 核对 CONTEXT.md 词条**

Read `CONTEXT.md` 第 23-24 行「解码桶」：确认 arb=归一化键 / raw_id=首帧原始 id / 插入序=系列序 / 桶只收已通过预检的帧 四句与 spec A1 一致。已一致则不改动。

- [x] **Step 2: 文档头部加 A 落地更新块**（文首 2026-08-18 更新列表之后追加；N = Task 5 实测用例数）

```markdown
> **2026-08-19 更新（A 实施完成后）**：§3 M1 标 ✅ 完成；§4 候选 A 已落地；§5 首要建议更新（无剩余 Strong 候选）；§2.1 行数刷新（decoder / converter / mp_finish / dbc_loader 按实施后行数、tests 20→21 文件新增 test_bucket.py）；CONTEXT.md「解码桶」词条（spec 阶段已加）；全量 pytest 现状：**N passed / 0 失败**（anaconda3 实测，2026-08-19，269 基线 + 新增）。A 实施按项目惯例未 commit（由用户执行）。
```

- [x] **Step 3: §3 M1 状态标注**（M1 条目内追加状态行，M2/M4 先例同型；N = Task 5 实测用例数）

```markdown
- **状态：✅ 已完成**（2026-08-19，实施 A，[spec](./a/2026-08-18-a-bucket-typing-spec.md)）——桶结构收口为 decoder.py 显式状态单类 `Bucket`（三相位互斥字段组 + `from_feed`/`from_blocks`/`add_frame`/`add_block`/`to_array`/`n_frames`/`memory_estimate`，isinstance 分派从 decoder/mp_finish/converter 三处收进类内，H2a 性能注释随迁）；分类规则收口 dbc_loader（`classify`/`classify_batch`/`message_table` 相邻定义，批量与标量等价由属性测试锁定，M2 双入口模板同构）；两条建桶路由（feed/向量化）桶级等价由新增路由等价测试直接对拍（np.unique 首现序 vs setdefault 插入序收口）；finish 防御 length 掩码删除（构造即预检，mux 无子组计数保留）；converter.py:389 死残留清除；oracle（reference_decode）换调生产 classify（解码面保持 cantools 独立）；CONTEXT.md 新增「解码桶」词条。双轴 code-review 0 findings。全量 pytest N passed。
```

- [x] **Step 4: §4 候选 A 行更新**（替换现 A 行）

```markdown
| A. 桶记录类型化 + owner 集中（M1） | ✅ 已落地（2026-08-19） | 桶结构收口 decoder.py `Bucket` 显式状态单类（三相位互斥 + 转换方法收敛）；分类规则唯一实现 dbc_loader（classify/classify_batch/message_table）；路由等价测试直接对拍两条建桶路由；finish 防御掩码删除；死残留清除；reference_decode 换调 classify；N passed。详见 [A spec](./a/2026-08-18-a-bucket-typing-spec.md) |
```

- [x] **Step 5: §5 首要建议更新**（替换现首段与理由 2；保留其余结构）

```markdown
**F（H1/H2 对拍收口）已落地（2026-08-18）；D（write_mdf 失败无残留归 writer）已实施完毕（2026-08-18，253 passed）；C（归一化键单一来源）已实施完毕（2026-08-18，259 passed）；G（ChannelStats 死字段）已实施完毕（2026-08-18，259 passed）；B（绑定决策抽无 Qt 纯函数）已实施完毕（2026-08-18，269 passed）；A（桶契约类型化）已实施完毕（2026-08-19，N passed）。五个 Strong 候选（A/B/F/H1/H2）全部落地，无剩余 Strong 候选。下一步：M8/L9 bench/probe 脚本清理（8 个可删脚本）等 Worth exploring 条目。**
```

理由 2 替换为：

```markdown
2. **A（桶契约类型化）已落地**：解码桶收口为 `Bucket` 显式状态单类，分类规则唯一实现落 dbc_loader，两条建桶路由由路由等价测试直接对拍——M1 的「等价性靠注释声明」结构性收口完成，单遍扫描性能前提（numpy 运算语义零变化 + H2a 注释随迁）未触碰。
```

- [x] **Step 6: §2.1 行数刷新**

按 Task 5 后各文件实际行数更新模块地图（decoder / converter / mp_finish / dbc_loader 四行；tests 行 `20 文件` → `21 文件`（新增 test_bucket.py））。

- [x] **Step 7: 全量 pytest 终验 + 交付点（提交由用户执行）**

Run: `python -m pytest tests/ -q`
Expected: 全 PASS（文档改动不涉代码，终验确认）。

---

## 自检记录（spec 覆盖核验）

- **A1 Bucket 类型** → Task 2（类 + 三相位测试）+ Task 3（消费端）；`key == arb` 断言 → Task 3 Step 1/3c；H2a 注释随迁 → Task 2 to_array。
- **A2 分类收口** → Task 1（classify / classify_batch / message_table + 属性测试）+ Task 3（feed 与向量化路由改调）。
- **A3 消费端** → Task 3（decoder feed/finish/桶函数属性访问、Q12 掩码删除、converter `_prep_decode_info`/`_assemble_bucket`/死残留删除、mp_finish typed worker/`bucket_bytes`/`n_frames`）。
- **A4 测试** → Task 1 Step 1（参数化单测 + 批量/标量属性测试 + dtype 契约）、Task 2 Step 1（相位契约）、Task 3 Step 1（断言触发）、Task 4（路由等价对拍 + reference_decode 换调）。
- **Testing Decisions** → 不新增 seam：断言面 = classify*/Bucket/decode_channel/convert/finish_all/_read_vectorized 直调；零重写黄金套件（Task 3 Step 7 门禁）；验收（Task 5）：双调用方式 pytest + AHT 并/串对拍 + 性能随手记不作通过条件；双轴 code-review 0 findings（Task 6）。
- **Out of Scope** → Global Constraints 逐条列入，无任务触碰（bench 脚本 dict 访问不修、stats_bufs 形状不动、feed 保留）。
- **CONTEXT.md 词条** → Task 7 Step 1 核对（spec 阶段已加）。
