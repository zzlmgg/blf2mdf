# H1 对拍 oracle 可导入性与测试保护 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以四项收尾（pytest 收集性修复、黄金套件按模块真实契约补全、STAT_NAMES 双副本相等性锁定、CLI 退出码进程级契约测试）使「oracle 出错不静默」成为有测试保证的事实，零生产代码改动。

**Architecture:** 全部改动限于 pytest 配置（1 行）、测试助手（抽取共享工厂）与测试用例（黄金套件 + 新 CLI 契约文件）。判定逻辑 [tools/mdf_compare.py](../../tools/mdf_compare.py) 双公开入口为唯一逻辑 seam，行为零变更；CLI 薄壳以 subprocess 穿（退出码本质是进程级契约）；[core/stats.py](../../core/stats.py)、gui/ 一律不动。测试数据全部合成 MDF（asammdf 构造），无样例依赖。

**Tech Stack:** Python 3.13.9（anaconda3）、pytest 8.4.2（console script 与模块同版本；`pythonpath` ini 选项要求 pytest ≥ 7，满足）、numpy 2.3.5、asammdf 8.8.23、标准库 subprocess/sys。

## Global Constraints

- **零生产代码改动**：core/、gui/ 及 tools/ 下三个被测脚本（mdf_compare.py / compare_two_mdf.py / full_compare.py）一律不修改。
- **零判定行为变更**：容差、dims 语义、对齐策略、报告逻辑全部保持现状（H2 已定）；本计划只增加测试保护。
- **只测外部行为**：逻辑层穿模块双入口（文件路径进、差异列表出）；进程层穿 CLI 子进程（参数进、返回码出）；不触碰 mdf_compare 内部函数、不断言实现文本。
- **测试数据合成、无样例依赖**：全部用例用 tests/mdf_factory.py 的 `_write_mdf` 构造（asammdf + 固定 start_time + `.mf4`）。
- **验收方式**：`python -m pytest tests/ -q` 与 `pytest tests/ -q`（控制台脚本）**两种调用方式均全绿**——D2 收集性契约的最终验收。
- **解释器**：所有命令用 `C:\ProgramData\anaconda3\python.exe`（PATH 上的 `python` 是 Windows 商店空壳，不可用）；控制台脚本 `pytest` 实测存在（anaconda Scripts 目录，版本 8.4.2）。
- **不执行 git commit**：用户指示「不要提交 git」，提交由用户执行（项目惯例）。每个任务以「改动范围核对（`git status --short`）」收尾，代替提交步骤。
- 提交信息（用户自行执行时）用中文。

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `pytest.ini` | 修改（+1 行） | `[pytest]` 段加 `pythonpath = .`：任意 pytest 调用方式下 `tools`、`core` 均可导入 |
| `tests/mdf_factory.py` | 新建 | 共享合成 MDF 构造助手：`_FIXED_START`、`_write_mdf`（扩展 `header=` / `chan_mut=` 参数）、`_simple`、`REF_LAYOUT` |
| `tests/test_mdf_compare.py` | 修改 | 删除本地助手副本改为 `from mdf_factory import ...`；新增 header 9 字段、通道元数据、values（NaN/整型/dtype/文本）、stats 逐点、dims 关闭、STAT_NAMES 锁定用例（15 → 41 项） |
| `tests/test_compare_cli.py` | 新建 | 两 CLI 薄壳子进程退出码契约（7 项断言场景） |

任务依赖：Task 1（pytest.ini，独立）→ Task 2（工厂抽取，供 Task 3-8 消费）→ Task 3/4/5/6/7（黄金套件补全，均依赖 Task 2，可并行）→ Task 8（CLI 契约，依赖 Task 2）→ Task 9（全量验证，依赖全部）。

## 构造可行性依据（2026-08-18 实测，anaconda 3.13.9 / asammdf 8.8.23）

以下事实决定了下述用例的具体构造方式，均已逐条实测通过（`%TEMP%\h1_oracle_probe*.py`）：

1. **header 字段**：MDF4 `HeaderBlock` 的 `start_time` 是属性（setter 写 abs_time/tz_offset/time_flags）；`abs_time` 是原始 int ns 字段（**必须写 int**，写 datetime 会在 save 时 `struct.error`）；`tz_offset`/`flags`/`author`/`department`/`project`/`subject` 均可 setattr 且落盘。`version` **无此属性**（`AttributeError`）→ 模块 `getattr(ha, f, None)` 两侧恒 None，**不可构造差异**。author/department/project/subject 落盘时写进 HD comment XML → 修改它们会**连带产生 header.comment 差异**（断言只查字段名存在性，不查互斥）。
2. **通道元数据**（读回 `m.groups[0].channels[1]`，channels[0] 是自动生成的 'time' 主通道）：data_type/bit_count 由采样 dtype 决定（float64→dt=4/64bit，float32→dt=4/32bit，int64→dt=2/64bit，int32→dt=2/32bit）；`unit` 默认 `''`，可用 `chan_mut` 就地 `setattr(ch, "unit", ...)` 且落盘；`flags` 同理可写；`conversion` 默认 `None`，`setattr(ch, "conversion", ChannelConversion(P1=2.0))` 落盘后读回为非 None 对象（str 与 'None' 不同）。**bit_resolution 无此属性**（读回 `<absent>`）→ 恒 None，不可构造差异。注意：data_type/bit_count 不可随意改值（不一致组合会在读回时 `TypeError: data type '<u3' not understood`），差异一律用 dtype 驱动构造。
3. **文本通道**：asammdf 8.8.23 append **不支持 U dtype 与 object dtype**（`MdfException: Unknown type`），S dtype 写入时**尾随 \x00 被剥除**（S8 `b"hi\x00\x00"` 读回 `b'hi'`）→ `_norm_bytes` 的 U→S 分支与 \x00 剥离分支**无法经文件往返构造**（spec D4 清单中该两项据此调整为「文本内容差异」用例，见 Task 5 说明）。
4. **NaN 语义**：identical 的 float 路径用 `np.nanmax`（**跳过 NaN**）：同位置 NaN 与单侧 NaN 均不报差异（契约现状，实测 diffs == []）；reference 用 `isclose(equal_nan=True)`：同位置 NaN 不报、单侧 NaN 报 `N 点不一致`。
5. **CLI 退出码**（实测）：`compare_two_mdf` 相等对→0、单点改值→1、`--skip-values` 后仅数值差异→0；`full_compare` 判定一致→0（报告写入）、单点改值→1（报告仍写入）、`--no-report` 不产生报告文件、缺 `--stats-ref-block`/`--stats-ref-idx`→argparse 退出码 2。
6. **收集红态**（实测）：`pytest tests/test_mdf_compare.py`（控制台脚本）→ `ModuleNotFoundError: No module named 'tools'`；`python -m pytest` → 15 passed。

---

### Task 1: pytest 收集性修复（`pythonpath = .`）

**Files:**
- Modify: `pytest.ini`（`[pytest]` 段加 1 行）

**Interfaces:**
- Consumes: 无（首个任务）。
- Produces: `pythonpath = .` ini 配置（相对 rootdir 解析 = 项目根）——Task 2-9 所有用例在**任意 pytest 调用方式**下可 `import tools.*` / `import core.*`；同时修复 test_blf_reader 等既有 core 系测试的同类问题（纯收益）。

- [ ] **Step 1: 确认红态（控制台脚本收集失败）**

```powershell
cd e:\projects\blf_dbc
pytest tests\test_mdf_compare.py -q
```

Expected: `ERROR tests/test_mdf_compare.py` + `ModuleNotFoundError: No module named 'tools'`（收集中断，1 error）。同时确认对照组绿态：

```powershell
python -m pytest tests\test_mdf_compare.py -q
```

Expected: `15 passed`。

- [ ] **Step 2: 修改 pytest.ini**

把 `[pytest]` 段改为（`pythonpath = .` 是 pytest ≥ 7.0 ini 选项，本环境 8.4.2）：

```ini
[pytest]
testpaths = tests
pythonpath = .
markers =
    golden: 金标准全量对比测试（较慢，读 35MB 样例）
```

- [ ] **Step 3: 验证两种调用方式均绿**

```powershell
pytest tests\test_mdf_compare.py -q
python -m pytest tests\test_mdf_compare.py -q
```

Expected: 两种方式均 `15 passed`。

- [ ] **Step 4: 改动范围核对（不提交）**

```powershell
git status --short
```

Expected: 仅 `M pytest.ini`。**不要 git commit**（用户指示，提交由用户执行）。

---

### Task 2: 抽取共享测试工厂 tests/mdf_factory.py

把 test_mdf_compare.py 的构造助手（`_FIXED_START` / `_write_mdf` / `_simple` / `REF_LAYOUT`）抽到独立模块，供黄金套件（Task 3-7）与 CLI 契约测试（Task 8）共用；`_write_mdf` 扩展 `header=` / `chan_mut=` 两个可选参数（Task 3/4 用例构造差异所需）。

**Files:**
- Create: `tests/mdf_factory.py`
- Modify: `tests/test_mdf_compare.py`（删本地定义、改 import；测试函数体一字不改）
- Test: 既有 15 用例（import 改写后必须全绿）

**Interfaces:**
- Consumes: 无（既有 15 用例的调用形态全部保持）。
- Produces:
  - `mdf_factory._FIXED_START: datetime`——固定文件头起始时间（`datetime(2026, 1, 1, tzinfo=timezone.utc)`）。
  - `mdf_factory._write_mdf(path, groups, comment="t", header=None, chan_mut=None)`——`groups: list[(acq, [(name, samples, dtype), ...])]`；t 通道自动加（arange 秒）；`header: dict[field, value]` 在 save 前逐一 setattr 到 `mdf.header`（**abs_time 必须传 int ns**，见可行性依据 1）；`chan_mut: callable(mdf)` 在 save 前对 mdf 就地改通道元数据（见 Task 4 用法）。
  - `mdf_factory._simple(comment="t") -> tuple[callable, list, str]`——既有三元组形态：`(_write_mdf, groups, comment)`，G1=[SigA float64, SigB int64]、G2=[SigC S8 文本]。
  - `mdf_factory.REF_LAYOUT: tuple[int, dict[str, int]]`——`(22, {"StdData": 4, "StdDataRate": 5, ..., "ErrorFrameRate": 13})`（参考统计布局，Task 8 由它生成 `--stats-ref-idx` 字符串）。

- [ ] **Step 1: 新建 tests/mdf_factory.py**

```python
"""合成 MDF 测试工厂：黄金套件（test_mdf_compare）与 CLI 契约测试（test_compare_cli）共享。

构造可行性依据见 docs/reviews/2026-08-18-h1-oracle-protection-plan.md 的
「构造可行性依据」一节（2026-08-18 实测）：
- header.abs_time 必须写 int ns（写 datetime 会在 save 时报 struct.error）；
- author/department/project/subject 落盘进 HD comment XML，会连带 header.comment 差异；
- S dtype 写入时尾随 \\x00 被 asammdf 剥除；U / object dtype 不支持 append；
- channels[0] 是自动生成的 'time' 主通道，数据通道从 channels[1] 起。
"""
from datetime import datetime, timezone

import numpy as np
from asammdf import MDF, Signal

# 固定文件头起始时间：asammdf 新建文件默认盖章写入时刻（两文件必然不同）；
# core/mdf_writer 的 start_time 来自转换源而非写入时刻，此处镜像该语义。
_FIXED_START = datetime(2026, 1, 1, tzinfo=timezone.utc)

# 参考文件统计布局（每通道 22 项，10 项统计名的组内偏移；CANoe 参考文件实测布局）
REF_LAYOUT = (22, {"StdData": 4, "StdDataRate": 5, "ExtData": 6, "ExtDataRate": 7,
                   "StdRemote": 8, "StdRemoteRate": 9, "ExtRemote": 10,
                   "ExtRemoteRate": 11, "ErrorFrames": 12, "ErrorFrameRate": 13})


def _write_mdf(path, groups, comment="t", header=None, chan_mut=None):
    """groups: list[(acq, [(name, samples, dtype), ...])]；t 通道自动加（arange 秒）。

    header: save 前逐一 setattr 到 mdf.header（abs_time 传 int ns）。
    chan_mut: save 前对 mdf 就地修改通道元数据（如 flags/conversion）。
    """
    with MDF(version="4.10") as mdf:
        for acq, chans in groups:
            n = len(chans[0][1])
            ts = np.arange(n, dtype=np.float64)
            t_idx = next((i for i, (nm, _, _) in enumerate(chans) if nm == "t"), None)
            if t_idx is not None:
                # 显式 t 通道承载组时间轴：其采样即该组 timestamps（组基准 = 首信号 timestamps）
                ts = np.asarray(chans[t_idx][1], dtype=np.float64)
            sigs = []
            for name, samples, dtype in chans:
                arr = np.asarray(samples, dtype=dtype)
                kwargs = {}
                if arr.dtype.kind in ("S", "O", "U"):
                    kwargs["encoding"] = "utf-8"  # asammdf 8.x 字符串通道需显式 encoding
                sigs.append(Signal(samples=arr, name=name, timestamps=ts, **kwargs))
            if t_idx is None:
                sigs.append(Signal(samples=ts, name="t", timestamps=ts))
            mdf.append(sigs, comment="", acq_name=acq)
        mdf.header.start_time = _FIXED_START
        mdf.header.comment = comment
        if header:
            for k, v in header.items():
                setattr(mdf.header, k, v)
        if chan_mut:
            chan_mut(mdf)
        mdf.save(path, overwrite=True)  # asammdf 8.x save 强制 .mf4 后缀 → 用例用 .mf4 路径


def _simple(comment="t"):
    groups = [
        ("G1", [("SigA", [1.0, 2.0, 3.0], np.float64),
                ("SigB", [10, 20, 30], np.int64)]),
        ("G2", [("SigC", [b"x", b"y", b"z"], "S8")]),
    ]
    return _write_mdf, groups, comment
```

- [ ] **Step 2: 改写 test_mdf_compare.py 的助手区**

删除文件头部到 `test_identical_equal_files` 之间的本地定义（`REF_LAYOUT` / `_FIXED_START` / `_write_mdf` / `_simple`），替换为：

```python
"""对拍共享模块黄金测试：合成 MDF，穿两个公开入口（唯一 seam）。"""
from datetime import datetime, timezone

import numpy as np
import pytest
from asammdf import MDF, Signal

from mdf_factory import _write_mdf, _simple, REF_LAYOUT
from tools.mdf_compare import (compare_files_identical, compare_files_reference,
                               STAT_NAMES, DEFAULT_DIMS)
```

（`from asammdf import MDF, Signal` 是否仍需保留取决于后续用例——Task 5 的 NaN/文本用例会直接构造 groups 列表，需 `np`；`MDF`/`Signal` 若没有新用例用到可从 import 中删掉，但删了也不影响正确性。保留原样最稳妥。）

- [ ] **Step 3: 运行既有 15 用例确认无行为变化**

```powershell
python -m pytest tests\test_mdf_compare.py -q
```

Expected: `15 passed`（收集性依赖 Task 1 已就绪）。

- [ ] **Step 4: 改动范围核对（不提交）**

```powershell
git status --short
```

Expected: `M pytest.ini`（Task 1 遗留）、`M tests/test_mdf_compare.py`、`?? tests/mdf_factory.py`。**不要 git commit**。

---

### Task 3: header 字段差异判定补全（8 字段构造 + version no-op）

在黄金套件补齐 spec D4 的 header 行：除已覆盖的 comment 外，其余 9 字段中 8 个可构造差异（见可行性依据 1），version 为 MDF4 无属性字段（模块两侧恒 None，不可构造）——用「真实差异对中 version 不出现」锁定其 no-op 契约。

**Files:**
- Modify: `tests/test_mdf_compare.py`（追加用例；`datetime`/`timezone` 已由 Task 2 的 import 保留）

**Interfaces:**
- Consumes: `mdf_factory._write_mdf`（`header=` 参数）、`mdf_factory._simple`、`tools.mdf_compare.compare_files_identical`。
- Produces: 无（纯测试；供后续任务复用的断言风格即本文件的 `any(f"header.{f}" in d for d in diffs)`）。

- [ ] **Step 1: 追加 8 字段参数化用例**

在 `test_identical_header_comment` 之后追加：

```python
@pytest.mark.parametrize("field, value", [
    ("start_time", datetime(2026, 1, 2, tzinfo=timezone.utc)),
    # abs_time 是 HD 原始 int ns 字段；start_time 由它派生 → 该用例会相伴出现 start_time 差异（不查互斥）
    ("abs_time", int(datetime(2026, 1, 3, tzinfo=timezone.utc).timestamp() * 10**9)),
    ("tz_offset", 480),
    ("flags", 3),
    # author 等四字段落盘进 HD comment XML → 会连带 header.comment 差异（不查互斥）
    ("author", "author2"),
    ("department", "dept2"),
    ("project", "proj2"),
    ("subject", "subj2"),
])
def test_identical_header_field_diff(tmp_path, field, value):
    """构造单字段头部差异 → 断言报出字段名。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups, header={field: value})
    diffs = compare_files_identical(p1, p2)
    assert any(f"header.{field}" in d for d in diffs)


def test_identical_header_version_noop(tmp_path):
    """MDF4 HeaderBlock 无 version 属性（实测 AttributeError）→ 模块 getattr 恒 None，永不报。

    以真实差异对（flags 差异）验证：差异确实存在但 version 不出现。
    """
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups, header={"flags": 3})
    diffs = compare_files_identical(p1, p2)
    assert diffs
    assert all("header.version" not in d for d in diffs)
```

- [ ] **Step 2: 运行新用例确认全绿（9 项）**

```powershell
python -m pytest tests\test_mdf_compare.py -q
```

Expected: `24 passed`（15 + 8 参数化 + 1 no-op）。

- [ ] **Step 3: 防空洞自检（一次性）——证明用例确实检测差异**

临时把参数化列表中的 `("author", "author2")` 改为 `("author", "")`（空串 = 默认值，不会产生差异）：

```python
    ("author", ""),
```

```powershell
python -m pytest tests\test_mdf_compare.py::test_identical_header_field_diff -q
```

Expected: `1 failed`（author 参数项——证明用例不是空洞断言）。还原该行后重跑：

```powershell
python -m pytest tests\test_mdf_compare.py::test_identical_header_field_diff -q
```

Expected: `8 passed`。

- [ ] **Step 4: 改动范围核对（不提交）**

```powershell
git status --short
```

Expected: 仅 `M tests/test_mdf_compare.py`（新增）。**不要 git commit**。

---

### Task 4: 通道元数据差异判定补全（unit / data_type / bit_count / flags / conversion + bit_resolution no-op）

补齐 spec D4 的 structure 行：5 个可构造字段逐一构造单字段差异（`dims=frozenset({"structure"})` 隔离结构维度）；bit_resolution 为 MDF4 无属性字段（恒 None，不可构造）——以真实差异对锁定 no-op 契约。构造方式见可行性依据 2：data_type/bit_count 用采样 dtype 驱动（float64→dt=4/64bit，int64→dt=2/64bit，float32→dt=4/32bit），unit/flags/conversion 用 `chan_mut` 就地改。

**Files:**
- Modify: `tests/test_mdf_compare.py`（追加用例）

**Interfaces:**
- Consumes: `mdf_factory._write_mdf`（`chan_mut=` 参数）、`mdf_factory._simple`、`tools.mdf_compare.compare_files_identical`；`asammdf.blocks.v4_blocks.ChannelConversion`（conversion 用例）。
- Produces: 无。

- [ ] **Step 1: 追加 5 个可构造字段用例 + bit_resolution no-op**

在 `test_identical_channel_order` 之后追加：

```python
# ---- identical：通道元数据（structure 维度隔离）----

def test_identical_structure_data_type(tmp_path):
    """SigA float64(dt=4) vs int64(dt=2)：data_type 单字段差异（bit_count 同为 64）。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, [("G1", [("SigA", [1, 2, 3], np.int64),
                            ("SigB", [10, 20, 30], np.int64)]),
                    ("G2", [("SigC", [b"x", b"y", b"z"], "S8")])])
    diffs = compare_files_identical(p1, p2, dims=frozenset({"structure"}))
    assert any("data_type" in d for d in diffs)


def test_identical_structure_bit_count(tmp_path):
    """SigA float64(64bit) vs float32(32bit)：bit_count 单字段差异（data_type 同为 4）。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, [("G1", [("SigA", [1.0, 2.0, 3.0], np.float32),
                            ("SigB", [10, 20, 30], np.int64)]),
                    ("G2", [("SigC", [b"x", b"y", b"z"], "S8")])])
    diffs = compare_files_identical(p1, p2, dims=frozenset({"structure"}))
    assert any("bit_count" in d for d in diffs)


def test_identical_structure_unit(tmp_path):
    """unit：chan_mut 就地改 SigA 的 unit（asammdf Channel.unit 可写且落盘）。

    channels[0] 是自动生成的 'time' 主通道，数据通道从 channels[1] 起（SigA）。
    """
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups,
               chan_mut=lambda m: setattr(m.groups[0].channels[1], "unit", "km/h"))
    diffs = compare_files_identical(p1, p2, dims=frozenset({"structure"}))
    assert any("unit" in d for d in diffs)


def test_identical_structure_flags(tmp_path):
    """flags：chan_mut 就地改 SigA 的 CN 块 flags 字段（可写且落盘）。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups,
               chan_mut=lambda m: setattr(m.groups[0].channels[1], "flags", 1))
    diffs = compare_files_identical(p1, p2, dims=frozenset({"structure"}))
    assert any("flags" in d for d in diffs)


def test_identical_structure_conversion(tmp_path):
    """conversion：默认 None vs 显式转换对象（str 不同即报）；对象读回后 str 含地址等字段。

    注意：读回的转换对象是"空"转换（val_param_nr=0，参数未落盘），不缩放采样值——
    但本用例仅开 structure 维度，不涉值比较。
    """
    from asammdf.blocks.v4_blocks import ChannelConversion
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups,
               chan_mut=lambda m: setattr(m.groups[0].channels[1], "conversion",
                                          ChannelConversion(P1=2.0)))
    diffs = compare_files_identical(p1, p2, dims=frozenset({"structure"}))
    assert any("conversion" in d for d in diffs)


def test_identical_structure_bit_resolution_noop(tmp_path):
    """MDF4 Channel 无 bit_resolution 属性（实测 <absent>）→ 模块 getattr 恒 None，永不报。

    以真实差异对（bit_count 差异）验证：差异确实存在但 bit_resolution 不出现。
    """
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, [("G1", [("SigA", [1.0, 2.0, 3.0], np.float32),
                            ("SigB", [10, 20, 30], np.int64)]),
                    ("G2", [("SigC", [b"x", b"y", b"z"], "S8")])])
    diffs = compare_files_identical(p1, p2, dims=frozenset({"structure"}))
    assert diffs
    assert all("bit_resolution" not in d for d in diffs)
```

- [ ] **Step 2: 运行确认全绿（+6 项）**

```powershell
python -m pytest tests\test_mdf_compare.py -q
```

Expected: `30 passed`（24 + 6）。

- [ ] **Step 3: 改动范围核对（不提交）**

```powershell
git status --short
```

Expected: 仅 `M tests/test_mdf_compare.py`（新增）。**不要 git commit**。

---

### Task 5: values 差异判定补全（NaN 语义两侧 / 整型 / dtype / 文本差异）

补齐 spec D4 的 values 行。**对 spec 清单的两处调整**（依据可行性依据 3/4，均经实测）：

- `identical 文本 \x00 归一化` / `U→S 归一化（_norm_bytes 两分支）` → **不可经文件往返构造**：asammdf 8.8.23 不支持 U/object dtype append，且 S dtype 写入时剥除尾随 \x00。改为等价的**可观测契约**用例：identical 文本内容差异报 `采样不一致`、reference 文本内容差异报 `文本不一致`（现有 test_reference_text_width_normalized 已覆盖宽度归一化；`_norm_bytes` 的 S 分支在两条文本路径中都被调用）。
- `identical NaN` 语义 = **不报**（float 路径用 `np.nanmax`，跳过 NaN）——锁为契约现状；`reference` 语义 = `isclose(equal_nan=True)`：同位置 NaN 不误报、单侧 NaN 不漏报。

**Files:**
- Modify: `tests/test_mdf_compare.py`（追加用例）

**Interfaces:**
- Consumes: `mdf_factory._write_mdf`、`mdf_factory._simple`、`mdf_factory.REF_LAYOUT`、`tools.mdf_compare.compare_files_identical` / `compare_files_reference`。
- Produces: 无。

- [ ] **Step 1: 追加 NaN / 整型 / dtype / 文本用例**

在 `test_identical_tolerance_boundary` 之后追加：

```python
# ---- identical / reference：NaN 语义（两侧）----

def test_identical_nan_semantics(tmp_path):
    """identical float 路径用 nanmax（跳过 NaN）：同位置 NaN 与单侧 NaN 均不报（契约现状）。

    sa - sb 含 NaN 时 nanmax 取非 NaN 最大值 → diff=0.0 ≤ atol → 不报。
    与 reference 的 equal_nan=True 语义区分（见下两用例）。
    """
    g_nan = [("G1", [("SigA", [1.0, np.nan, 3.0], np.float64),
                     ("SigB", [10, 20, 30], np.int64)]),
             ("G2", [("SigC", [b"x", b"y", b"z"], "S8")])]
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, g_nan)
    _write_mdf(p2, g_nan)  # 同位置 NaN
    assert compare_files_identical(p1, p2) == []
    _write_mdf(p1, g_nan)
    _write_mdf(p2, groups)  # 单侧 NaN
    assert compare_files_identical(p1, p2) == []


def test_reference_equal_nan_same_position(tmp_path):
    """reference isclose(equal_nan=True)：同位置 NaN 不误报（本应相等判为差异）。"""
    g_nan = [("G1", [("SigA", [1.0, np.nan, 3.0], np.float64),
                     ("SigB", [10, 20, 30], np.int64)]),
             ("G2", [("SigC", [b"x", b"y", b"z"], "S8")])]
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, g_nan)
    _write_mdf(p2, g_nan)
    assert compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT) == []


def test_reference_equal_nan_one_side(tmp_path):
    """reference：单侧 NaN → N 点不一致（不误合并为相等）。"""
    g_nan = [("G1", [("SigA", [1.0, np.nan, 3.0], np.float64),
                     ("SigB", [10, 20, 30], np.int64)]),
             ("G2", [("SigC", [b"x", b"y", b"z"], "S8")])]
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, g_nan)
    _write_mdf(p2, groups)
    diffs = compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT)
    assert any("1 点不一致" in d for d in diffs)


# ---- reference：整型 / identical：dtype / 两入口：文本内容差异 ----

def test_reference_integer_mismatch(tmp_path):
    """整型信号差异走 array_equal 整型路径 → 整型不一致（不落浮点容差路径被掩盖）。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups[0][1][1] = ("SigB", [10, 99, 30], np.int64)
    _write_mdf(p2, groups)
    diffs = compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT)
    assert any("整型不一致" in d for d in diffs)


def test_identical_dtype_mismatch(tmp_path):
    """identical：dtype 不匹配显式报出（dtype 漂移不静默）。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups[0][1][0] = ("SigA", [1.0, 2.0, 3.0], np.float32)
    _write_mdf(p2, groups)
    diffs = compare_files_identical(p1, p2)
    assert any("dtype" in d for d in diffs)


def test_identical_text_content_diff(tmp_path):
    """identical 文本内容差异 → 采样不一致（文本通道按归一化后内容判定）。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups[1][1][0] = ("SigC", [b"x", b"NO", b"z"], "S8")
    _write_mdf(p2, groups)
    diffs = compare_files_identical(p1, p2)
    assert any("采样不一致" in d for d in diffs)


def test_reference_text_content_diff(tmp_path):
    """reference 文本内容差异 → 文本不一致。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups[1][1][0] = ("SigC", [b"x", b"NO", b"z"], "S8")
    _write_mdf(p2, groups)
    diffs = compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT)
    assert any("文本不一致" in d for d in diffs)
```

- [ ] **Step 2: 运行确认全绿（+7 项）**

```powershell
python -m pytest tests\test_mdf_compare.py -q
```

Expected: `37 passed`（30 + 7）。

- [ ] **Step 3: 改动范围核对（不提交）**

```powershell
git status --short
```

Expected: 仅 `M tests/test_mdf_compare.py`（新增）。**不要 git commit**。

---

### Task 6: stats 逐点 + dims 关闭语义

补齐 spec D4 的 stats 行与 dims 行：identical 入口对 1s 组逐点数值差异的判定（default dims 下由 values 维度捕获）、两入口各一对 dims 关闭语义（关闭维度不报、其余维度仍报）。

**Files:**
- Modify: `tests/test_mdf_compare.py`（追加用例）

**Interfaces:**
- Consumes: `mdf_factory._write_mdf`、`mdf_factory._simple`、`mdf_factory.REF_LAYOUT`、`tools.mdf_compare.compare_files_identical` / `compare_files_reference`。
- Produces: 无。

- [ ] **Step 1: 追加 stats 逐点与 dims 用例**

在 `test_identical_stats_t_axis` 之后追加：

```python
def test_identical_stats_value_diff(tmp_path):
    """identical 1s 组逐点数值差异：default dims 下由 values 维度捕获（1s 组同样参与采样比较）。"""
    n = 20
    base = [("1s", [("StdData", np.full(n, 1.0, np.float64), np.float64)])]
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, base)
    vals = np.full(n, 1.0, np.float64)
    vals[5] = 2.0
    _write_mdf(p2, [("1s", [("StdData", vals, np.float64)])])
    diffs = compare_files_identical(p1, p2)
    assert any("采样不一致" in d for d in diffs)
```

在 `test_invalid_dims` 之前追加：

```python
# ---- dims 关闭语义（两入口各一对）----

def test_identical_dims_off(tmp_path):
    """dims 关闭：关闭维度不报、其余维度仍报（identical 入口）。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups[0][1][0] = ("SigA", [1.0, 2.0, 99.0], np.float64)
    _write_mdf(p2, groups, comment="diff-comment")
    diffs = compare_files_identical(p1, p2, dims=frozenset({"header"}))
    assert any("header.comment" in d for d in diffs)
    assert all("采样不一致" not in d for d in diffs)
    diffs = compare_files_identical(p1, p2, dims=frozenset({"values"}))
    assert any("采样不一致" in d for d in diffs)
    assert all("header.comment" not in d for d in diffs)


def test_reference_dims_off(tmp_path):
    """dims 关闭：关闭维度不报、其余维度仍报（reference 入口）。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups[0][1][0] = ("SigA", [1.0, 2.0, 99.0], np.float64)
    _write_mdf(p2, groups, comment="diff-comment")
    diffs = compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT,
                                    dims=frozenset({"header"}))
    assert any("header.comment" in d for d in diffs)
    assert all("不一致" not in d for d in diffs)
    diffs = compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT,
                                    dims=frozenset({"values"}))
    assert any("点不一致" in d for d in diffs)
    assert all("header.comment" not in d for d in diffs)
```

- [ ] **Step 2: 运行确认全绿（+3 项）**

```powershell
python -m pytest tests\test_mdf_compare.py -q
```

Expected: `40 passed`（37 + 3）。

- [ ] **Step 3: 改动范围核对（不提交）**

```powershell
git status --short
```

Expected: 仅 `M tests/test_mdf_compare.py`（新增）。**不要 git commit**。

---

### Task 7: STAT_NAMES 双副本相等性锁定

补齐 spec D3：黄金套件新增一个相等性断言，锁定生产端写出布局契约（core/stats.py）与 oracle 消费端假设（tools/mdf_compare.py）不漂移。不选 `from core.stats import STAT_NAMES` 的原因（spec D3 已定）：mdf_compare 引入 core 依赖会破坏 `python tools/compare_two_mdf.py` 独立运行（sys.path[0]=tools/ 无 core）的 §8 调用约定——副本 + 锁定测试是同等防护、零迁移的收敛方式。

**Files:**
- Modify: `tests/test_mdf_compare.py`（顶部 import + 末尾追加 1 用例）

**Interfaces:**
- Consumes: `tools.mdf_compare.STAT_NAMES`（已有 import）、`core.stats.STAT_NAMES`（新 import）。
- Produces: 无。

- [ ] **Step 1: 顶部 import 追加 core 侧 STAT_NAMES**

把 Task 2 改写的 import 块改为：

```python
from mdf_factory import _write_mdf, _simple, REF_LAYOUT
from tools.mdf_compare import (compare_files_identical, compare_files_reference,
                               STAT_NAMES, DEFAULT_DIMS)
from core.stats import STAT_NAMES as CORE_STAT_NAMES
```

- [ ] **Step 2: 文件末尾追加锁定用例**

```python
def test_stat_names_locked_to_core():
    """STAT_NAMES 双副本相等性锁定：生产写出布局（core.stats）与 oracle 消费端
    （tools.mdf_compare）任一侧漂移 → 本用例变红，对拍不再静默失真。"""
    assert STAT_NAMES == CORE_STAT_NAMES
```

- [ ] **Step 3: 运行确认全绿（+1 项）**

```powershell
python -m pytest tests\test_mdf_compare.py -q
```

Expected: `41 passed`（40 + 1）。

- [ ] **Step 4: 防空洞自检（一次性）——证明锁定有效**

临时把 `test_stat_names_locked_to_core` 的断言改为 `assert STAT_NAMES == ()`（空元组必不等）：

```python
    assert STAT_NAMES == ()
```

```powershell
python -m pytest tests\test_mdf_compare.py::test_stat_names_locked_to_core -q
```

Expected: `1 failed`。还原后重跑，Expected: `1 passed`。

- [ ] **Step 5: 改动范围核对（不提交）**

```powershell
git status --short
```

Expected: 仅 `M tests/test_mdf_compare.py`（新增）。**不要 git commit**。

---

### Task 8: CLI 退出码契约测试（subprocess）

补齐 spec D5：两个 CLI 薄壳的退出码本质是进程级契约（依赖 `sys.path[0]=tools/` 兄弟导入，模块导入路径不可用，实测 `import tools.compare_two_mdf` 报错），以 `[sys.executable, "tools/<cli>.py", ...]` 子进程断言 `returncode`——端到端覆盖 argparse 参数接线与 dims 映射。

**Files:**
- Create: `tests/test_compare_cli.py`
- Test: 新文件 3 个测试函数（compare_two_mdf 1 个场景组 + full_compare 1 个场景组 + 必填参数 1 个）

**Interfaces:**
- Consumes: `mdf_factory._write_mdf`、`mdf_factory._simple`、`mdf_factory.REF_LAYOUT`（生成 `--stats-ref-idx` 字符串）。
- Produces: 无（进程级契约由 returncode 表达）。

- [ ] **Step 1: 新建 tests/test_compare_cli.py**

```python
"""CLI 退出码契约测试：进程级（subprocess），合成 MDF，无样例依赖。

两 CLI 薄壳依赖 sys.path[0]=tools/ 的兄弟导入（实测 import tools.compare_two_mdf
报 ModuleNotFoundError: No module named 'mdf_compare'），模块导入路径不可用——
退出码是进程级契约，以子进程断言 returncode（端到端覆盖 argparse 接线与 dims 映射）。
"""
import subprocess
import sys
from pathlib import Path

import numpy as np

from mdf_factory import _write_mdf, _simple, REF_LAYOUT

ROOT = Path(__file__).resolve().parent.parent


def _run(args, timeout=60):
    return subprocess.run([sys.executable, *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=timeout)


def _ref_idx():
    """把 REF_LAYOUT 的偏移表转成 full_compare 的 --stats-ref-idx 字符串。"""
    return ",".join(f"{k}:{v}" for k, v in REF_LAYOUT[1].items())


def test_compare_two_mdf_exit_codes(tmp_path):
    """退出码契约：0 = 一致；非 0 = 存在差异；--skip-values 关闭数值维度后仅数值差异对 → 0。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups)
    r = _run(["tools/compare_two_mdf.py", str(p1), str(p2)])
    assert r.returncode == 0
    # 单点改值 → 1
    groups[0][1][0] = ("SigA", [1.0, 2.0, 99.0], np.float64)
    _write_mdf(p2, groups)
    r = _run(["tools/compare_two_mdf.py", str(p1), str(p2)])
    assert r.returncode == 1
    # --skip-values 后仅数值差异对 → 0（数值维度关闭生效）
    r = _run(["tools/compare_two_mdf.py", str(p1), str(p2), "--skip-values"])
    assert r.returncode == 0


def test_full_compare_exit_codes(tmp_path):
    """退出码契约：0 = 判定一致（报告写入）；1 = 存在判定差异（报告仍写入）；
    --no-report 不产生报告文件。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups)
    base = ["tools/full_compare.py", str(p1), str(p2),
            "--stats-ref-block", str(REF_LAYOUT[0]),
            "--stats-ref-idx", _ref_idx(), "--outdir", str(tmp_path)]
    r = _run(base)
    assert r.returncode == 0
    assert list(tmp_path.glob("compare_*.md"))  # 报告已写入
    # 单点改值 → 1，且报告仍写入（记录现有报告数再对比）
    groups[0][1][0] = ("SigA", [1.0, 2.0, 99.0], np.float64)
    _write_mdf(p2, groups)
    r = _run(base)
    assert r.returncode == 1
    assert len(list(tmp_path.glob("compare_*.md"))) == 2
    # --no-report → 判定照跑（差异 → 1）但不写报告文件
    r = _run(base + ["--no-report"])
    assert r.returncode == 1
    assert len(list(tmp_path.glob("compare_*.md"))) == 2


def test_full_compare_required_args(tmp_path):
    """--stats-ref-block / --stats-ref-idx 必填：缺省 → argparse 解析失败，非 0 退出。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups)
    r = _run(["tools/full_compare.py", str(p1), str(p2)])
    assert r.returncode != 0
    r = _run(["tools/full_compare.py", str(p1), str(p2),
              "--stats-ref-block", str(REF_LAYOUT[0])])
    assert r.returncode != 0
```

- [ ] **Step 2: 运行确认全绿（+3 项）**

```powershell
python -m pytest tests\test_compare_cli.py -q
```

Expected: `3 passed`。注意子进程真实执行两个 CLI（每次约 1-3s，全文件约 10s）。

- [ ] **Step 3: 防空洞自检（一次性）——证明 returncode 断言有效**

临时把 `test_compare_two_mdf_exit_codes` 中相等对的断言改为 `assert r.returncode == 1`（与实际 0 不符）：

```python
    assert r.returncode == 1
```

```powershell
python -m pytest tests\test_compare_cli.py::test_compare_two_mdf_exit_codes -q
```

Expected: `1 failed`。还原后重跑，Expected: `1 passed`。

- [ ] **Step 4: 改动范围核对（不提交）**

```powershell
git status --short
```

Expected: `M tests/test_mdf_compare.py`（前序任务新增）、`?? tests/test_compare_cli.py`。**不要 git commit**。

---

### Task 9: 全量验证（两种调用方式）与收尾

spec 验收方式：`python -m pytest tests/ -q` 与 `pytest tests/ -q`（控制台脚本）两种调用方式均全绿（覆盖 D2 收集性契约）；既有全量（约 203 通过 + 环境性跳过）新增约 26 项。

**Files:** 无改动（纯验证）。

**Interfaces:**
- Consumes: 全部前序任务产物。

- [ ] **Step 1: python -m 方式全量**

```powershell
python -m pytest tests\ -q
```

Expected: 全绿；`41 passed`（test_mdf_compare）+ `3 passed`（test_compare_cli）+ 其余既有用例（约 200，含环境性跳过）。若有个别失败：定位 → 最小修正 → 重验（失败不叠加猜测）。

- [ ] **Step 2: 控制台脚本方式全量（D2 收集性契约的最终验收）**

```powershell
pytest tests\ -q
```

Expected: 与 Step 1 相同全绿。此前该方式收集即报 `ModuleNotFoundError: No module named 'tools'`——本步证明收集性修复生效。

- [ ] **Step 3: 行为等价确认**

`git diff tests/ pytest.ini` 与 `git diff --stat` 核对：**生产代码（core/、gui/、tools/mdf_compare.py、tools/compare_two_mdf.py、tools/full_compare.py）零改动**——与 spec「本次收尾不改动任何判定行为」一致。

- [ ] **Step 4: 收尾汇报（不提交）**

在计划执行记录中汇总：新用例清单（41 + 3）、两种调用方式均全绿的实跑输出、防空洞自检结果（Task 3/7/8 各 1 次）、spec 偏差说明（values 行 `\x00`/U 分支不可构造的调整，依据可行性依据 3）。**不要 git commit**（提交由用户执行）。
