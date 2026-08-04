# BLF → MDF 转换工具（blf2mdf）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个桌面 GUI 工具，导入 1 个 BLF + 多个 DBC，为各通道绑定 DBC 后一键转换输出单个 MDF 4.10 文件（绑定通道输出解码信号，未绑定通道输出原始帧）。

**Architecture:** GUI（PySide6）与转换内核（core/，不依赖 Qt）分离。内核四个模块：blf_reader（流式读 BLF）、dbc_loader（单文件 DBC 解析）、decoder（帧→物理值）、mdf_writer（asammdf 写 MDF 4.10），加一个 converter 编排模块（多通道聚合、进度回调、半成品清理）。

**Tech Stack:** Python 3.12（conda 环境 `blfmdf`）、cantools、python-can（BLF 读取，备选 `blf` 包）、asammdf、PySide6、numpy、pytest、PyInstaller。

## Global Constraints

- 所有 Python 命令通过 `conda run -n blfmdf python ...` 执行（PATH 里的 WindowsApps 占位符不可用）。
- core/ 目录下的代码**禁止 import PySide6/Qt**（GUI 与内核分离）。
- DBC **不合并**；一个 BLF 通道 ↔ 一个 DBC（或"不绑定"）。
- 所有通道**全部导出**到同一个 MDF：绑定 → 信号组 `Signal::<发送节点>`；未绑定 → 原始组 `Raw::CAN<n>`。
- 输出 MDF 4.10；时间轴为 float64 秒；信号通道 float64 带单位。
- mux 信号非激活帧记 NaN，保证组内各通道等长。
- 未知报文 ID 跳过并计数，转换完成后摘要报告。
- 转换中途失败必须删除半成品 MDF 文件。
- 通道显示名统一为 `CAN<n>`（BLF 内部只有数字通道号）。
- 数据文件（blf/、mdf/、dbc/、outputs/、raw_inputs.7z）不入库（.gitignore 已配置）。
- 转换产生的临时/结果文件一律放 `outputs/` 目录。

---

### Task 1: 项目骨架与环境验证

**Files:**
- Create: `requirements.txt`
- Create: `pytest.ini`
- Create: `tests/conftest.py`
- Create: `core/__init__.py`
- Create: `gui/__init__.py`

**Interfaces:**
- Consumes: 无（首个任务）
- Produces: 测试路径工具函数 `sample_blf()`、`all_dbc_files()`（后续所有测试任务使用）

- [ ] **Step 1: 创建依赖与配置文件**

`requirements.txt`：
```
cantools
python-can
asammdf
PySide6
numpy
pytest
```

`pytest.ini`：
```ini
[pytest]
testpaths = tests
markers =
    golden: 金标准全量对比测试（较慢，读 35MB 样例）
```

`tests/conftest.py`：
```python
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BLF_DIR = PROJECT_ROOT / "blf"
DBC_DIR = PROJECT_ROOT / "dbc"
MDF_DIR = PROJECT_ROOT / "mdf"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)


def sample_blf() -> Path | None:
    """返回样例 BLF 路径；无样例时返回 None。"""
    files = sorted(BLF_DIR.glob("*.blf"))
    return files[0] if files else None


def all_dbc_files() -> list[Path]:
    """返回 dbc/ 目录下全部 .dbc 文件（排序）。"""
    return sorted(DBC_DIR.glob("*.dbc"))
```

`core/__init__.py`、`gui/__init__.py`：空文件。

- [ ] **Step 2: 验证环境与空测试通过**

```bash
conda run -n blfmdf python -c "import cantools, can, asammdf, PySide6, numpy; print('env OK')"
conda run -n blfmdf python -m pytest
```

Expected: 输出 `env OK`；pytest 无测试收集，退出码 0。

- [ ] **Step 3: Commit**

```bash
git add requirements.txt pytest.ini tests/conftest.py core/__init__.py gui/__init__.py
git commit -m "chore: 项目骨架与测试基础设施"
```

---

### Task 2: blf_reader — BLF 读取（含样例文件探测）

**Files:**
- Create: `core/blf_reader.py`
- Test: `tests/test_blf_reader.py`

**Interfaces:**
- Consumes: `conftest.sample_blf()`
- Produces: 后续 Task 4/6 依赖：
  - `Frame` 数据类：`channel: int, ts_seconds: float, arbitration_id: int, is_extended: bool, is_fd: bool, dlc: int, data: bytes`
  - `list_channels(path: str) -> list[int]`（升序）
  - `iter_messages(path: str, channel: int) -> Iterator[Frame]`（只产该通道报文，时间戳秒）

- [ ] **Step 1: 写失败测试**

`tests/test_blf_reader.py`：
```python
import itertools

import pytest

from core.blf_reader import Frame, iter_messages, list_channels
from conftest import sample_blf


def test_synthetic_blf_roundtrip(tmp_path):
    """不依赖样例文件：python-can 写的小 BLF 能枚举通道并过滤。"""
    import can

    p = tmp_path / "tiny.blf"
    with can.BLFWriter(str(p)) as w:
        w.on_message(can.Message(arbitration_id=0x123, data=b"\x01\x02",
                                 channel=1, timestamp=1.0))
        w.on_message(can.Message(arbitration_id=0x456, data=b"\x03",
                                 channel=2, timestamp=2.0))
    assert list_channels(str(p)) == [1, 2]
    frames = list(iter_messages(str(p), 1))
    assert len(frames) == 1
    assert frames[0].arbitration_id == 0x123
    assert frames[0].data == b"\x01\x02"
    assert frames[0].ts_seconds == 1.0
    assert isinstance(frames[0], Frame)


def test_list_channels_on_sample():
    blf = sample_blf()
    if blf is None:
        pytest.skip("无样例 BLF 文件")
    channels = list_channels(str(blf))
    assert channels, "样例 BLF 应至少有一个通道"
    print("样例 BLF 通道:", channels)


def test_iter_messages_fields_on_sample():
    blf = sample_blf()
    if blf is None:
        pytest.skip("无样例 BLF 文件")
    chans = list_channels(str(blf))
    frames = list(itertools.islice(iter_messages(str(blf), chans[0]), 200))
    assert frames, "第一个通道应产出帧"
    assert all(f.channel == chans[0] for f in frames)
    ts = [f.ts_seconds for f in frames]
    assert ts == sorted(ts), "时间戳应非递减"
    assert all(f.ts_seconds >= 0 for f in frames)
    assert all(0 <= f.dlc <= 64 and len(f.data) == f.dlc for f in frames)
    assert any(f.is_fd for f in frames) or True  # 打印 FD 帧占比，不强制
    fd_count = sum(1 for f in frames if f.is_fd)
    print(f"前 200 帧中 CANFD: {fd_count}")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
conda run -n blfmdf python -m pytest tests/test_blf_reader.py -v
```

Expected: FAIL（`ModuleNotFoundError: core.blf_reader`）。

- [ ] **Step 3: 探测样例 BLF，确认 python-can 的 BLF 读取能力**

先写一个一次性探测脚本 `outputs/probe_blf.py`（不入库，outputs/ 已 gitignore）：

```python
import can
from pathlib import Path

path = str(sorted(Path("blf").glob("*.blf"))[0])  # 在项目根目录运行
types, channels, fd = {}, set(), 0
n = 0
try:
    with can.BLFReader(path) as reader:
        for msg in reader:
            n += 1
            channels.add(msg.channel)
            if getattr(msg, "is_fd", False):
                fd += 1
            if n <= 5:
                print("sample:", msg)
    print(f"总帧数(前扫): {n}")
    print("通道:", sorted(channels))
    print("CANFD 帧数:", fd)
except Exception as e:
    print("BLF 读取失败:", type(e).__name__, e)
```

运行：
```bash
cd e:/projects/blf_dbc && conda run -n blfmdf python outputs/probe_blf.py
```

Expected: 打印通道列表、总帧数、CANFD 帧数（样例来自 CANFD 总线，大概率含 FD 帧）。
**决策点**：若成功（不抛异常且通道数 ≥ 1）→ 用 python-can，继续 Step 4。
若失败（如 CAN FD 对象不支持、抛异常）→ 安装备选库并改用之：
```bash
conda run -n blfmdf pip install blf
```
`blf` 包映射：`from blf.parser import BLFParser`；`obj.channel`、`obj.timestamp`（秒）、`obj.arbitration_id`、`obj.flags`（扩展位/错误位按包文档）、`obj.dlc`、`bytes(obj.data)`；CanFdMessage / CanFdMessage64 均为可读对象。Step 4 的 `_decode` 实现相应改为该包的对象映射，其余接口不变。

- [ ] **Step 4: 实现最小可用代码**

`core/blf_reader.py`：
```python
"""BLF 读取：通道枚举 + 按通道流式产帧。"""
from dataclasses import dataclass
from typing import Iterator


@dataclass
class Frame:
    channel: int
    ts_seconds: float
    arbitration_id: int
    is_extended: bool
    is_fd: bool
    dlc: int
    data: bytes


def _decode(msg) -> Frame:
    """python-can Message → Frame（若探测后改用 blf 包，仅需改写本函数）。"""
    return Frame(
        channel=int(msg.channel),
        ts_seconds=float(msg.timestamp),
        arbitration_id=int(msg.arbitration_id),
        is_extended=bool(msg.is_extended_id),
        is_fd=bool(getattr(msg, "is_fd", False)),
        dlc=int(msg.dlc),
        data=bytes(msg.data),
    )


def list_channels(path: str) -> list[int]:
    import can

    channels = set()
    with can.BLFReader(path) as reader:
        for msg in reader:
            channels.add(int(msg.channel))
    return sorted(channels)


def iter_messages(path: str, channel: int) -> Iterator[Frame]:
    import can

    with can.BLFReader(path) as reader:
        for msg in reader:
            if int(msg.channel) != channel:
                continue
            yield _decode(msg)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
conda run -n blfmdf python -m pytest tests/test_blf_reader.py -v
```

Expected: 3 个测试 PASS（样例测试打印通道数与 FD 占比，目测合理即可）。

- [ ] **Step 6: Commit**

```bash
git add core/blf_reader.py tests/test_blf_reader.py
git commit -m "feat: blf_reader 通道枚举与按通道流式读取"
```

---

### Task 3: dbc_loader — 单文件 DBC 解析

**Files:**
- Create: `core/dbc_loader.py`
- Test: `tests/test_dbc_loader.py`

**Interfaces:**
- Consumes: `conftest.all_dbc_files()`
- Produces: 后续 Task 4/6/8 依赖：
  - `SignalDef(name, start_bit, length, scale, offset, unit)`
  - `MessageDef(name, sender_node, signals: list[SignalDef], is_extended, frame_length)`
  - `DbcDef(path, db, messages: dict[int, MessageDef])` — `db` 为 cantools Database，供解码
  - `load(path: str) -> DbcDef`（解析失败抛异常，错误信息含文件路径）

- [ ] **Step 1: 写失败测试**

`tests/test_dbc_loader.py`：
```python
import pytest

from core.dbc_loader import load
from conftest import all_dbc_files

INLINE_DBC = '''VERSION ""

NS_ :
    NS_DESC_
    CM_
    BA_DEF_
    BA_
    VAL_
    CAT_DEF_
    CAT_
    FILTER
    BA_DEF_DEF_
    EV_DATA_
    ENVVAR_DATA_
    SGTYPE_
    SGTYPE_VAL_
    BA_DEF_SGTYPE_
    BA_SGTYPE_
    SIG_TYPE_REF_
    VAL_TABLE_
    SIG_GROUP_
    SIG_VALTYPE_
    SIGTYPE_VALTYPE_
    BO_TX_BU_
    BA_DEF_REL_
    BA_REL_
    BA_DEF_DEF_REL_
    BU_SG_REL_
    BU_EV_REL_
    BU_BO_REL_
    SG_MUL_VAL_

BS_:

BU_: ECU

BO_ 100 ECU: 8 ABC
 SG_ Speed : 0|16@1+ (0.01,0) [0|655.35] "km/h" ECU
 SG_ Temp : 16|8@1+ (1,-40) [-40|215] "degC" ECU
'''


def test_load_inline_dbc(tmp_path):
    p = tmp_path / "t.dbc"
    p.write_text(INLINE_DBC, encoding="utf-8")
    dbc = load(str(p))
    assert 100 in dbc.messages
    md = dbc.messages[100]
    assert md.name == "ABC"
    assert md.sender_node == "ECU"
    assert md.is_extended is False
    assert md.frame_length == 8
    names = [s.name for s in md.signals]
    assert names == ["Speed", "Temp"]
    speed = md.signals[0]
    assert speed.scale == 0.01 and speed.unit == "km/h"
    assert speed.start_bit == 0 and speed.length == 16
    assert md.signals[1].offset == -40


@pytest.mark.parametrize("dbc_path", all_dbc_files(), ids=lambda p: p.name)
def test_all_real_dbc_parse(dbc_path):
    dbc = load(str(dbc_path))
    assert dbc.messages, f"{dbc_path.name} 解析后为空"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
conda run -n blfmdf python -m pytest tests/test_dbc_loader.py -v
```

Expected: FAIL（`ModuleNotFoundError: core.dbc_loader`）。

- [ ] **Step 3: 实现**

`core/dbc_loader.py`：
```python
"""DBC 解析：一次一个文件，不合并。"""
from dataclasses import dataclass, field


@dataclass
class SignalDef:
    name: str
    start_bit: int
    length: int
    scale: float
    offset: float
    unit: str


@dataclass
class MessageDef:
    name: str
    sender_node: str
    signals: list[SignalDef] = field(default_factory=list)
    is_extended: bool = False
    frame_length: int = 8


@dataclass
class DbcDef:
    path: str
    db: object  # cantools Database，保留给 decoder 用
    messages: dict[int, MessageDef] = field(default_factory=dict)


def load(path: str) -> DbcDef:
    import cantools

    db = cantools.database.load_file(path)
    messages = {}
    for msg in db.messages:
        messages[msg.frame_id] = MessageDef(
            name=msg.name,
            sender_node=msg.senders[0] if msg.senders else "Unknown",
            signals=[
                SignalDef(
                    name=s.name,
                    start_bit=s.start,
                    length=s.length,
                    scale=float(s.scale or 1.0),
                    offset=float(s.offset or 0.0),
                    unit=s.unit or "",
                )
                for s in msg.signals
            ],
            is_extended=bool(msg.is_extended_frame),
            frame_length=int(msg.length),
        )
    return DbcDef(path=path, db=db, messages=messages)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
conda run -n blfmdf python -m pytest tests/test_dbc_loader.py -v
```

Expected: inline 测试 PASS + 13 个真实 DBC 全部解析 PASS。

- [ ] **Step 5: Commit**

```bash
git add core/dbc_loader.py tests/test_dbc_loader.py
git commit -m "feat: dbc_loader 单文件 DBC 解析"
```

---

### Task 4: decoder — 帧 → 信号物理值

**Files:**
- Create: `core/decoder.py`
- Test: `tests/test_decoder.py`

**Interfaces:**
- Consumes: `Frame`（Task 2）、`DbcDef`/`SignalDef`（Task 3）
- Produces: 后续 Task 5/6/8 依赖：
  - `SignalSeries(channel: int, message_name: str, node: str, signal_names: list[str], timestamps: np.ndarray(float64), values: dict[str, np.ndarray(float64)], units: dict[str, str])`
  - `DecodeStats(total_frames: int, unknown_frames: int, unknown_ids: set[int])`
  - `decode_channel(frames: Iterator[Frame], dbc: DbcDef, channel: int) -> tuple[list[SignalSeries], DecodeStats]`

- [ ] **Step 1: 写失败测试**

`tests/test_decoder.py`：
```python
import math

import pytest

from core.decoder import SignalSeries, decode_channel
from core.dbc_loader import load

INLINE_DBC = '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 100 ECU: 8 ABC
 SG_ Speed : 0|16@1+ (0.01,0) [0|655.35] "km/h" ECU
 SG_ Temp : 16|8@1+ (1,-40) [-40|215] "degC" ECU

BO_ 200 ECU: 8 ABC
 SG_ Mux m0 : 0|8@1+ (1,0) [0|255] "" ECU
 SG_ SigA m0 : 8|8@1+ (1,0) [0|255] "" ECU
 SG_ SigB m1 : 8|8@1+ (1,0) [0|255] "" ECU
'''

DBC_TXT = "\n".join(INLINE_DBC.splitlines())


def _frames():
    """构造 3 帧：Speed=0x03E8(→10.0 km/h) Temp=0x50(→40.0°C)；一个 mux 帧；一个未知 ID。"""
    from core.blf_reader import Frame

    f1 = Frame(channel=1, ts_seconds=1.0, arbitration_id=100,
               is_extended=False, is_fd=False, dlc=8,
               data=bytes([0xE8, 0x03, 0x50, 0, 0, 0, 0, 0]))
    f2 = Frame(channel=1, ts_seconds=2.0, arbitration_id=200,
               is_extended=False, is_fd=False, dlc=8,
               data=bytes([0x00, 0x05, 0x07, 0, 0, 0, 0, 0]))
    f3 = Frame(channel=1, ts_seconds=3.0, arbitration_id=999,
               is_extended=False, is_fd=False, dlc=8,
               data=bytes(8))
    return [f1, f2, f3]


def test_decode_physical_values(tmp_path):
    p = tmp_path / "t.dbc"
    p.write_text(DBC_TXT, encoding="utf-8")
    dbc = load(str(p))
    series, stats = decode_channel(iter(_frames()), dbc, channel=1)
    assert stats.total_frames == 3
    assert stats.unknown_frames == 1
    assert stats.unknown_ids == {999}
    assert len(series) == 2
    by_name = {s.message_name: s for s in series}
    abc100 = by_name["ABC"]
    assert abc100.channel == 1 and abc100.node == "ECU"
    assert abc100.signal_names == ["Speed", "Temp"]
    assert abc100.timestamps.tolist() == [1.0]
    assert abc100.values["Speed"][0] == pytest.approx(10.0)
    assert abc100.values["Temp"][0] == pytest.approx(40.0)
    assert abc100.units["Speed"] == "km/h"
    assert abc100.units["Temp"] == "degC"


def test_mux_inactive_signal_is_nan(tmp_path):
    p = tmp_path / "t.dbc"
    p.write_text(DBC_TXT, encoding="utf-8")
    dbc = load(str(p))
    series, _ = decode_channel(iter(_frames()), dbc, channel=1)
    abc200 = next(s for s in series if s.message_name == "ABC"
                  and s.signal_names == ["Mux", "SigA", "SigB"])
    assert abc200.values["SigA"][0] == pytest.approx(5.0)
    assert math.isnan(abc200.values["SigB"][0])
    assert abc200.values["Mux"][0] == pytest.approx(0.0)
    assert len(abc200.signal_names) == len(abc200.values)
    n = len(abc200.timestamps)
    assert all(len(v) == n for v in abc200.values.values()), "组内等长"


def test_no_matching_frames(tmp_path):
    p = tmp_path / "t.dbc"
    p.write_text(DBC_TXT, encoding="utf-8")
    dbc = load(str(p))
    series, stats = decode_channel(iter([]), dbc, channel=1)
    assert series == [] and stats.total_frames == 0
```

- [ ] **Step 2: 运行测试确认失败**

```bash
conda run -n blfmdf python -m pytest tests/test_decoder.py -v
```

Expected: FAIL（`ModuleNotFoundError: core.decoder`）。

- [ ] **Step 3: 实现**

`core/decoder.py`：
```python
"""帧流 → 按报文聚合的信号物理值。"""
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

from core.blf_reader import Frame
from core.dbc_loader import DbcDef


@dataclass
class SignalSeries:
    channel: int
    message_name: str
    node: str
    signal_names: list[str]
    timestamps: np.ndarray          # float64 (N,)
    values: dict[str, np.ndarray] = field(default_factory=dict)  # float64 (N,)
    units: dict[str, str] = field(default_factory=dict)


@dataclass
class DecodeStats:
    total_frames: int = 0
    unknown_frames: int = 0
    unknown_ids: set[int] = field(default_factory=set)


def decode_channel(frames: Iterator[Frame], dbc: DbcDef, channel: int):
    stats = DecodeStats()
    buckets = {}  # msg_id -> {"ts": [], "values": {name: []}}
    for fr in frames:
        stats.total_frames += 1
        try:
            decoded = dbc.db.decode_message(fr.arbitration_id, fr.data)
        except KeyError:
            stats.unknown_frames += 1
            stats.unknown_ids.add(fr.arbitration_id)
            continue
        md = dbc.messages[fr.arbitration_id]
        bucket = buckets.setdefault(
            fr.arbitration_id,
            {"ts": [], "values": {s.name: [] for s in md.signals}},
        )
        bucket["ts"].append(fr.ts_seconds)
        for s in md.signals:
            bucket["values"][s.name].append(
                decoded.get(s.name, float("nan"))
            )

    series = []
    for msg_id, b in buckets.items():
        md = dbc.messages[msg_id]
        timestamps = np.asarray(b["ts"], dtype=np.float64)
        values = {k: np.asarray(v, dtype=np.float64) for k, v in b["values"].items()}
        units = {s.name: s.unit for s in md.signals}
        series.append(
            SignalSeries(
                channel=channel,
                message_name=md.name,
                node=md.sender_node,
                signal_names=[s.name for s in md.signals],
                timestamps=timestamps,
                values=values,
                units=units,
            )
        )
    return series, stats
```

- [ ] **Step 4: 运行测试确认通过**

```bash
conda run -n blfmdf python -m pytest tests/test_decoder.py -v
```

Expected: 3 个测试 PASS。

- [ ] **Step 5: Commit**

```bash
git add core/decoder.py tests/test_decoder.py
git commit -m "feat: decoder 帧到信号物理值聚合（含 mux NaN、未知 ID 计数）"
```

---

### Task 5: mdf_writer — MDF 4.10 写出

**Files:**
- Create: `core/mdf_writer.py`
- Test: `tests/test_mdf_writer.py`

**Interfaces:**
- Consumes: `SignalSeries`（Task 4）
- Produces: 后续 Task 6/8 依赖：
  - `RawGroup(channel: int, timestamps: np.ndarray, ids: np.ndarray, dlcs: np.ndarray, data_array: np.ndarray, is_extended: np.ndarray, is_fd: np.ndarray)`
  - `write_mdf(signal_series_list: list[SignalSeries], raw_groups: list[RawGroup], out_path: str) -> None`（MDF 4.10；同名 `Signal::<节点>` 组冲突时第二个加 `CAN<n>::` 前缀；写失败异常上抛）

- [ ] **Step 1: 写失败测试**

`tests/test_mdf_writer.py`：
```python
import numpy as np
import pytest
from asammdf import MDF

from core.decoder import SignalSeries
from core.mdf_writer import RawGroup, write_mdf


def _series(channel, node, msg_name, names_units, ts, vals):
    return SignalSeries(
        channel=channel, message_name=msg_name, node=node,
        signal_names=[n for n, _ in names_units],
        timestamps=np.array(ts, dtype=np.float64),
        values={n: np.array(v, dtype=np.float64) for n, v in vals.items()},
        units=dict(names_units),
    )


def test_write_and_readback_signal_groups(tmp_path):
    s1 = _series(1, "ECU1", "MsgA", [("Speed", "km/h"), ("Temp", "degC")],
                 [1.0, 2.0], {"Speed": [10.0, 20.0], "Temp": [25.0, 26.0]})
    s2 = _series(1, "ECU2", "MsgB", [("Volt", "V")],
                 [0.5], {"Volt": [12.6]})
    out = tmp_path / "out.mdf"
    write_mdf([s1, s2], [], str(out))

    m = MDF(str(out))
    group_names = {g.name for g in m.groups}
    assert group_names == {"Signal::ECU1", "Signal::ECU2"}
    speed = m.get("Speed")
    assert speed is not None and speed.unit == "km/h"
    assert np.allclose(speed.samples, [10.0, 20.0])
    assert np.allclose(speed.timestamps, [1.0, 2.0])
    assert np.allclose(m.get("Temp").samples, [25.0, 26.0])
    assert np.allclose(m.get("Volt").samples, [12.6])


def test_write_raw_group(tmp_path):
    n = 2
    rg = RawGroup(
        channel=2,
        timestamps=np.array([1.0, 2.0]),
        ids=np.array([0x123, 0x456], dtype=np.uint32),
        dlcs=np.array([2, 1], dtype=np.uint8),
        data_array=np.array([[0x01, 0x02], [0x03, 0x00]], dtype=np.uint8),
        is_extended=np.array([False, True]),
        is_fd=np.array([False, False]),
    )
    out = tmp_path / "raw.mdf"
    write_mdf([], [rg], str(out))

    m = MDF(str(out))
    assert {g.name for g in m.groups} == {"Raw::CAN2"}
    ids = m.get("ID")
    assert ids is not None and np.issubdtype(ids.samples.dtype, np.integer)
    assert ids.samples.tolist() == [0x123, 0x456]
    assert m.get("DLC").samples.tolist() == [2, 1]
    assert m.get("Data").samples.tolist() == [[1, 2], [3, 0]]
    assert np.allclose(m.get("Time").samples, [1.0, 2.0])
    assert m.get("IsExtended").samples.tolist() == [0, 1]
    assert m.get("IsFD").samples.tolist() == [0, 0]


def test_same_node_across_channels_dedupe(tmp_path):
    s1 = _series(1, "ECU1", "MsgA", [("Speed", "km/h")], [1.0], {"Speed": [10.0]})
    s2 = _series(5, "ECU1", "MsgC", [("Accel", "m/s2")], [2.0], {"Accel": [3.0]})
    out = tmp_path / "dedupe.mdf"
    write_mdf([s1, s2], [], str(out))
    m = MDF(str(out))
    names = sorted(g.name for g in m.groups)
    assert names == ["CAN5::Signal::ECU1", "Signal::ECU1"]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
conda run -n blfmdf python -m pytest tests/test_mdf_writer.py -v
```

Expected: FAIL（`ModuleNotFoundError: core.mdf_writer`）。

- [ ] **Step 3: 实现**

`core/mdf_writer.py`：
```python
"""MDF 4.10 写出：信号组 + 原始帧组。"""
from dataclasses import dataclass

import numpy as np
from asammdf import MDF, Signal

from core.decoder import SignalSeries


@dataclass
class RawGroup:
    channel: int
    timestamps: np.ndarray   # float64 (N,)
    ids: np.ndarray          # uint32 (N,)
    dlcs: np.ndarray         # uint8 (N,)
    data_array: np.ndarray   # uint8 (N, L)，L = 该通道最大 DLC
    is_extended: np.ndarray  # bool (N,)
    is_fd: np.ndarray        # bool (N,)


def write_mdf(signal_series_list: list[SignalSeries],
              raw_groups: list[RawGroup], out_path: str) -> None:
    mdf = MDF(version="4.10")
    used_groups = set()

    for s in signal_series_list:
        group = f"Signal::{s.node}"
        if group in used_groups:
            group = f"CAN{s.channel}::Signal::{s.node}"
        used_groups.add(group)
        for name in s.signal_names:
            mdf.append(
                Signal(
                    samples=s.values[name].astype(np.float64),
                    timestamps=s.timestamps,
                    name=name,
                    unit=s.units.get(name, ""),
                ),
                group_name=group,
            )

    for rg in raw_groups:
        group = f"Raw::CAN{rg.channel}"
        ts = rg.timestamps.astype(np.float64)
        mdf.append(Signal(ts, ts, "Time", unit="s"), group_name=group)
        mdf.append(Signal(rg.ids.astype(np.uint32), ts, "ID"), group_name=group)
        mdf.append(Signal(rg.dlcs.astype(np.uint8), ts, "DLC"), group_name=group)
        mdf.append(Signal(rg.data_array, ts, "Data"), group_name=group)
        mdf.append(Signal(rg.is_extended.astype(np.uint8), ts, "IsExtended"),
                   group_name=group)
        mdf.append(Signal(rg.is_fd.astype(np.uint8), ts, "IsFD"),
                   group_name=group)

    mdf.save(out_path, overwrite=True)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
conda run -n blfmdf python -m pytest tests/test_mdf_writer.py -v
```

Expected: 3 个测试 PASS（若 `m.get("Data")` 数组形状断言失败，说明 asammdf 对 2D uint8 通道的读回形状不同，按实际形状调整断言——值必须一致）。

- [ ] **Step 5: Commit**

```bash
git add core/mdf_writer.py tests/test_mdf_writer.py
git commit -m "feat: mdf_writer 写出 MDF4.10（信号组/原始组/重名消歧）"
```

---

### Task 6: converter — 多通道编排与端到端

**Files:**
- Create: `core/converter.py`
- Test: `tests/test_converter.py`

**Interfaces:**
- Consumes: `list_channels`/`iter_messages`（Task 2）、`load`/`DbcDef`（Task 3）、`decode_channel`（Task 4）、`write_mdf`/`RawGroup`（Task 5）
- Produces: GUI（Task 7）与金标准测试（Task 8）依赖：
  - `ChannelSummary(channel, bound, decoded_frames, signal_count, unknown_frames, unknown_ids, raw_frames, warning)`
  - `ConversionResult(summaries: list[ChannelSummary], duration_seconds: float)`
  - `convert(blf_path: str, bindings: dict[int, DbcDef | None], out_path: str, progress_cb=None) -> ConversionResult`
    - `progress_cb(stage: str, percent: float)` 可选回调
    - 绑定通道无匹配帧 → summary.warning 置"该通道无匹配帧"，跳过该组
    - 写 MDF 失败 → 删除半成品文件后重新抛出异常

- [ ] **Step 1: 写失败测试**

`tests/test_converter.py`：
```python
import numpy as np
import pytest
from asammdf import MDF

from core.converter import convert
from core.dbc_loader import load

INLINE_DBC = '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 100 ECU: 8 ABC
 SG_ Speed : 0|16@1+ (0.01,0) [0|655.35] "km/h" ECU
'''


@pytest.fixture()
def blf_and_dbc(tmp_path):
    import can

    blf = tmp_path / "two_ch.blf"
    with can.BLFWriter(str(blf)) as w:
        # 通道 1：匹配 DBC 的帧
        w.on_message(can.Message(arbitration_id=100, data=bytes([0xE8, 0x03, 0, 0, 0, 0, 0, 0]),
                                 channel=1, timestamp=1.0))
        w.on_message(can.Message(arbitration_id=100, data=bytes([0xF4, 0x01, 0, 0, 0, 0, 0, 0]),
                                 channel=1, timestamp=2.0))
        # 通道 1：未知 ID
        w.on_message(can.Message(arbitration_id=999, data=bytes(8), channel=1, timestamp=3.0))
        # 通道 2：原始帧
        w.on_message(can.Message(arbitration_id=0x456, data=b"\xAA\xBB",
                                 channel=2, timestamp=4.0))
    dbc = tmp_path / "t.dbc"
    dbc.write_text(INLINE_DBC, encoding="utf-8")
    return str(blf), str(dbc)


def test_convert_mixed_channels(tmp_path, blf_and_dbc):
    blf, dbc_path = blf_and_dbc
    out = tmp_path / "out.mdf"
    result = convert(blf, {1: load(dbc_path), 2: None}, str(out))

    assert result.duration_seconds == pytest.approx(3.0)  # 1.0s → 4.0s
    by_ch = {s.channel: s for s in result.summaries}
    s1 = by_ch[1]
    assert s1.bound and s1.decoded_frames == 2
    assert s1.signal_count == 1
    assert s1.unknown_frames == 1 and s1.unknown_ids == 1
    assert s1.warning == ""
    s2 = by_ch[2]
    assert not s2.bound and s2.raw_frames == 1

    m = MDF(str(out))
    speed = m.get("Speed")
    assert np.allclose(speed.samples, [10.0, 50.0])   # 0x03E8→10.0, 0x01F4→50.0
    assert {g.name for g in m.groups} == {"Signal::ECU", "Raw::CAN2"}
    data = m.get("Data")
    assert data.samples.tolist() == [[0xAA, 0xBB]]


def test_convert_progress_callback(tmp_path, blf_and_dbc):
    blf, dbc_path = blf_and_dbc
    calls = []
    convert(blf, {1: load(dbc_path), 2: None}, str(tmp_path / "p.mdf"),
            progress_cb=lambda stage, pct: calls.append((stage, pct)))
    assert calls, "应至少有一次回调"
    assert calls[-1][0] == "完成"
    assert "写 MDF" in [s for s, _ in calls]


def test_convert_no_matching_frames_warns(tmp_path, blf_and_dbc):
    blf, dbc_path = blf_and_dbc
    out = tmp_path / "empty.mdf"
    result = convert(blf, {1: None, 3: load(dbc_path)}, str(out))
    s3 = next(s for s in result.summaries if s.channel == 3)
    assert s3.warning == "该通道无匹配帧"
    m = MDF(str(out))
    assert {g.name for g in m.groups} == {"Raw::CAN1"}  # 通道 3 空组不写入


def test_convert_no_channels_raises(tmp_path, blf_and_dbc):
    blf, _ = blf_and_dbc
    with pytest.raises(ValueError):
        convert(blf, {}, str(tmp_path / "x.mdf"))
```

- [ ] **Step 2: 运行测试确认失败**

```bash
conda run -n blfmdf python -m pytest tests/test_converter.py -v
```

Expected: FAIL（`ModuleNotFoundError: core.converter`）。

- [ ] **Step 3: 实现**

`core/converter.py`：
```python
"""转换编排：多通道聚合 → 单个 MDF。"""
import os
from dataclasses import dataclass

import numpy as np

from core import blf_reader, mdf_writer
from core.decoder import decode_channel
from core.dbc_loader import DbcDef


@dataclass
class ChannelSummary:
    channel: int
    bound: bool
    decoded_frames: int = 0
    signal_count: int = 0
    unknown_frames: int = 0
    unknown_ids: int = 0
    raw_frames: int = 0
    warning: str = ""


@dataclass
class ConversionResult:
    summaries: list[ChannelSummary]
    duration_seconds: float


def _collect_raw(frames, channel: int) -> mdf_writer.RawGroup:
    ts, ids, dlcs, datas, is_ext, is_fd = [], [], [], [], [], []
    max_dlc = 0
    for fr in frames:
        ts.append(fr.ts_seconds)
        ids.append(fr.arbitration_id)
        dlcs.append(fr.dlc)
        datas.append(fr.data)
        is_ext.append(bool(fr.is_extended))
        is_fd.append(bool(fr.is_fd))
        max_dlc = max(max_dlc, fr.dlc)
    n = len(ts)
    data_array = np.zeros((n, max_dlc), dtype=np.uint8) if n else np.zeros((0, 1), dtype=np.uint8)
    for i, d in enumerate(datas):
        data_array[i, : len(d)] = np.frombuffer(d, dtype=np.uint8)
    return mdf_writer.RawGroup(
        channel=channel,
        timestamps=np.asarray(ts, dtype=np.float64),
        ids=np.asarray(ids, dtype=np.uint32),
        dlcs=np.asarray(dlcs, dtype=np.uint8),
        data_array=data_array,
        is_extended=np.asarray(is_ext, dtype=bool),
        is_fd=np.asarray(is_fd, dtype=bool),
    )


def convert(blf_path: str, bindings: dict[int, DbcDef | None], out_path: str,
            progress_cb=None) -> ConversionResult:
    channels = sorted(bindings)
    if not channels:
        raise ValueError("未选择任何通道")
    total = len(channels)
    all_series, raw_groups, summaries = [], [], []
    min_ts, max_ts = float("inf"), float("-inf")

    def note_range(ts_arr):
        nonlocal min_ts, max_ts
        if len(ts_arr):
            min_ts = min(min_ts, float(ts_arr[0]))
            max_ts = max(max_ts, float(ts_arr[-1]))

    for i, ch in enumerate(channels):
        if progress_cb:
            progress_cb(f"处理 CAN{ch}", 10 + 80 * i / total)
        dbc = bindings.get(ch)
        frames = blf_reader.iter_messages(blf_path, ch)
        if dbc is not None:
            series, stats = decode_channel(frames, dbc, ch)
            for s in series:
                note_range(s.timestamps)
            summary = ChannelSummary(
                channel=ch, bound=True,
                decoded_frames=stats.total_frames - stats.unknown_frames,
                signal_count=sum(len(s.signal_names) for s in series),
                unknown_frames=stats.unknown_frames,
                unknown_ids=len(stats.unknown_ids),
            )
            if not series:
                summary.warning = "该通道无匹配帧"
            all_series.extend(series)
        else:
            raw = _collect_raw(frames, ch)
            note_range(raw.timestamps)
            raw_groups.append(raw)
            summary = ChannelSummary(channel=ch, bound=False, raw_frames=len(raw.timestamps))
        summaries.append(summary)

    if progress_cb:
        progress_cb("写 MDF", 95)
    try:
        mdf_writer.write_mdf(all_series, raw_groups, out_path)
    except Exception:
        if os.path.exists(out_path):
            os.remove(out_path)  # 删除半成品
        raise
    if progress_cb:
        progress_cb("完成", 100)
    return ConversionResult(
        summaries=summaries,
        duration_seconds=(max_ts - min_ts) if min_ts <= max_ts else 0.0,
    )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
conda run -n blfmdf python -m pytest tests/test_converter.py -v
```

Expected: 4 个测试 PASS。

- [ ] **Step 5: Commit**

```bash
git add core/converter.py tests/test_converter.py
git commit -m "feat: converter 多通道编排（进度回调/半成品清理/摘要）"
```

---

### Task 7: GUI（PySide6 主窗口）与程序入口

**Files:**
- Create: `gui/main_window.py`
- Modify: `main.py`（现为空文件，整体覆写）

**Interfaces:**
- Consumes: `list_channels`（Task 2）、`load`（Task 3）、`convert`/`ConversionResult`/`ChannelSummary`（Task 6）
- Produces: 可运行程序入口（`conda run -n blfmdf python main.py`）

**说明**：通道表**没有勾选框**——按设计所有通道全部导出（绑定→信号，未绑定→原始）。界面仅在通道行状态列显示"已绑定 / 原始"。

- [ ] **Step 1: 实现主窗口**

`gui/main_window.py`：
```python
"""主窗口：BLF/DBC 选择 → 通道绑定 → 转换 → 摘要。"""
from pathlib import Path

from PySide6.QtCore import QThread, QObject, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core import blf_reader
from core.converter import ConversionResult, convert
from core.dbc_loader import DbcDef, load

UNBOUND = "不绑定（导出原始帧）"


class ConvertWorker(QObject):
    progress = Signal(str, float)
    done = Signal(object)
    error = Signal(str)

    def __init__(self, blf_path, bindings, out_path):
        super().__init__()
        self.blf_path = blf_path
        self.bindings = bindings
        self.out_path = out_path

    @Slot()
    def run(self):
        try:
            result = convert(self.blf_path, self.bindings, self.out_path,
                             progress_cb=lambda s, p: self.progress.emit(s, p))
            self.done.emit(result)
        except Exception as e:  # noqa: BLE001 — 界面层兜底
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BLF → MDF 转换")
        self.resize(760, 640)
        self.blf_path = None
        self.dbc_list: list[DbcDef] = []
        self.worker_thread: QThread | None = None

        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)

        # BLF 文件
        row = QHBoxLayout()
        row.addWidget(QLabel("BLF 文件"))
        self.blf_edit = QLineEdit()
        self.blf_edit.setReadOnly(True)
        row.addWidget(self.blf_edit, 1)
        btn_blf = QPushButton("浏览…")
        btn_blf.clicked.connect(self._pick_blf)
        row.addWidget(btn_blf)
        v.addLayout(row)

        # DBC 列表
        v.addWidget(QLabel("DBC 矩阵文件"))
        self.dbc_list_widget = QListWidget()
        v.addWidget(self.dbc_list_widget, 1)
        dbc_btns = QHBoxLayout()
        btn_add = QPushButton("添加 DBC…")
        btn_add.clicked.connect(self._add_dbc)
        btn_rm = QPushButton("移除选中")
        btn_rm.clicked.connect(self._remove_dbc)
        dbc_btns.addWidget(btn_add)
        dbc_btns.addWidget(btn_rm)
        dbc_btns.addStretch(1)
        v.addLayout(dbc_btns)

        # 通道表
        v.addWidget(QLabel("通道"))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["通道", "DBC 矩阵", "状态"])
        self.table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.table, 1)

        # 输出路径
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("输出文件"))
        self.out_edit = QLineEdit()
        out_row.addWidget(self.out_edit, 1)
        btn_out = QPushButton("浏览…")
        btn_out.clicked.connect(self._pick_out)
        out_row.addWidget(btn_out)
        v.addLayout(out_row)

        # 转换按钮 + 进度
        self.convert_btn = QPushButton("转 换")
        self.convert_btn.setEnabled(False)
        self.convert_btn.clicked.connect(self._start_convert)
        v.addWidget(self.convert_btn)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        v.addWidget(self.progress)
        self.stage_label = QLabel("")
        v.addWidget(self.stage_label)

        # 摘要
        v.addWidget(QLabel("结果摘要"))
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        v.addWidget(self.summary, 1)

    # ---- 文件选择 ----
    def _pick_blf(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 BLF 文件", "",
                                              "BLF 文件 (*.blf)")
        if not path:
            return
        try:
            channels = blf_reader.list_channels(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "BLF 读取失败", f"{path}\n{e}")
            return
        if not channels:
            QMessageBox.warning(self, "提示", "文件中未找到有效报文数据")
            return
        self.blf_path = path
        self.blf_edit.setText(path)
        self._rebuild_channel_table(channels)
        default_out = Path(path).with_name(Path(path).stem + "_conv.mdf")
        self.out_edit.setText(str(default_out))
        self.convert_btn.setEnabled(True)

    def _rebuild_channel_table(self, channels: list[int]):
        # 记录现有绑定选择，重建后恢复（添加/移除 DBC 时不丢失用户选择）
        prev = {}
        for r in range(self.table.rowCount()):
            combo = self.table.cellWidget(r, 1)
            if combo is not None and self.table.item(r, 0) is not None:
                prev[self.table.item(r, 0).text()] = combo.currentText()
        self.table.setRowCount(0)
        for ch in channels:
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = f"CAN{ch}"
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 2, QTableWidgetItem("原始"))
            combo = QComboBox()
            combo.addItem(UNBOUND)
            for dbc in self.dbc_list:
                combo.addItem(Path(dbc.path).name)
            if name in prev:
                valid = [combo.itemText(i) for i in range(combo.count())]
                if prev[name] in valid:
                    combo.setCurrentText(prev[name])
            self.table.setCellWidget(row, 1, combo)
            combo.currentIndexChanged.connect(
                lambda _idx, r=row: self._update_status(r)
            )
            self._update_status(row)

    def _update_status(self, row: int):
        combo = self.table.cellWidget(row, 1)
        status = "已绑定" if combo.currentText() != UNBOUND else "原始"
        self.table.item(row, 2).setText(status)

    def _add_dbc(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择 DBC 文件", "",
                                                "DBC 文件 (*.dbc)")
        for p in paths:
            try:
                self.dbc_list.append(load(p))
                self.dbc_list_widget.addItem(f"{Path(p).name}    {Path(p).parent}")
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, "DBC 解析失败", f"{p}\n{e}")
        # 刷新所有下拉框
        channels = [int(self.table.item(r, 0).text()[3:])
                    for r in range(self.table.rowCount())]
        self._rebuild_channel_table(channels)

    def _remove_dbc(self):
        row = self.dbc_list_widget.currentRow()
        if row < 0:
            return
        self.dbc_list.pop(row)
        self.dbc_list_widget.takeItem(row)
        channels = [int(self.table.item(r, 0).text()[3:])
                    for r in range(self.table.rowCount())]
        self._rebuild_channel_table(channels)

    def _pick_out(self):
        path, _ = QFileDialog.getSaveFileName(self, "选择输出文件", "",
                                              "MDF 文件 (*.mdf)")
        if path:
            self.out_edit.setText(path)

    # ---- 转换 ----
    def _start_convert(self):
        if not self.blf_path:
            return
        out = self.out_edit.text().strip()
        if not out:
            QMessageBox.warning(self, "提示", "请指定输出文件路径")
            return
        if Path(out).exists():
            ans = QMessageBox.question(
                self, "覆盖确认", f"输出文件已存在：\n{out}\n\n是否覆盖？")
            if ans != QMessageBox.StandardButton.Yes:
                return
        bindings = {}
        for r in range(self.table.rowCount()):
            ch = int(self.table.item(r, 0).text()[3:])
            text = self.table.cellWidget(r, 1).currentText()
            if text == UNBOUND:
                bindings[ch] = None
            else:
                bindings[ch] = next(d for d in self.dbc_list
                                    if Path(d.path).name == text)
        self._set_busy(True)
        self.progress.setValue(0)
        self.summary.clear()
        self.worker_thread = QThread()
        self.worker = ConvertWorker(self.blf_path, bindings, out)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.worker.error.connect(self._on_error)
        self.worker_thread.start()

    def _set_busy(self, busy: bool):
        self.convert_btn.setEnabled(not busy)

    @Slot(str, float)
    def _on_progress(self, stage: str, percent: float):
        self.stage_label.setText(stage)
        self.progress.setValue(int(percent))

    @Slot(object)
    def _on_done(self, result: ConversionResult):
        self._finish()
        lines = [f"总时长: {result.duration_seconds:.1f} s"]
        for s in result.summaries:
            if s.bound:
                lines.append(
                    f"CAN{s.channel} 已绑定: 解码 {s.decoded_frames} 帧 · "
                    f"{s.signal_count} 个信号 · 未知 {s.unknown_frames} 帧 "
                    f"({s.unknown_ids} 个 ID)"
                )
                if s.warning:
                    lines.append(f"  警告: {s.warning}")
            else:
                lines.append(f"CAN{s.channel} 未绑定: 原始帧 {s.raw_frames} 帧")
        self.summary.setPlainText("\n".join(lines))

    @Slot(str)
    def _on_error(self, msg: str):
        self._finish()
        QMessageBox.critical(self, "转换失败", msg)

    def _finish(self):
        self.worker_thread.quit()
        self.worker_thread.wait()
        self.worker_thread = None
        self._set_busy(False)
        self.progress.setValue(0)
        self.stage_label.setText("")
```

`main.py`（覆写空文件）：
```python
import sys

from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 语法与导入检查**

```bash
conda run -n blfmdf python -c "import main; from gui.main_window import MainWindow; print('GUI OK')"
```

Expected: 打印 `GUI OK`（无 Qt 平台错误；Windows 下若报 `could not load platform plugin` 说明 PySide6 安装不完整，先 `conda run -n blfmdf pip install --force-reinstall PySide6`）。

- [ ] **Step 3: GUI 冒烟（手动）**

```bash
conda run -n blfmdf python main.py
```

手动清单：
1. 浏览选 `blf/ACFCANPUB_20260722_*.blf`（扫描约数秒）→ 通道表出现（通道名 CANn）。
2. "添加 DBC…" 选 `dbc/VDCPublic_CANFD1.dbc` → 出现在列表；通道行下拉框出现该 DBC。
3. 为第一个通道选该 DBC → 状态列变"已绑定"；其余保持"原始"。
4. 输出路径改为 `outputs/manual_smoke.mdf`，点"转换" → 进度条走完 → 摘要显示各通道统计。
5. 用 asammdf 验证产物：`conda run -n blfmdf python -c "from asammdf import MDF; m=MDF('outputs/manual_smoke.mdf'); print(len(m.groups), [g.name for g in m.groups][:5])"`。
6. 再次转换（覆盖确认弹窗出现）→ 点"是"正常继续。
7. 故意把 BLF 路径改错（浏览选一个非 BLF 文件）→ 弹"BLF 读取失败"。

- [ ] **Step 4: Commit**

```bash
git add gui/main_window.py main.py
git commit -m "feat: PySide6 主窗口与程序入口"
```

---

### Task 8: 金标准对比测试（样例 BLF vs _T058.mdf）

**Files:**
- Create: `tests/test_golden.py`

**Interfaces:**
- Consumes: `sample_blf`/`MDF_DIR`/`OUTPUTS_DIR`（conftest）、`list_channels`（Task 2）、`load`（Task 3）、`convert`（Task 6）

- [ ] **Step 1: 写测试**

`tests/test_golden.py`：
```python
"""金标准：样例 BLF 转换结果与 CANoe 生成的 _T058.mdf 对比。"""
import numpy as np
import pytest
from asammdf import MDF

from conftest import DBC_DIR, MDF_DIR, OUTPUTS_DIR, sample_blf
from core.blf_reader import list_channels
from core.converter import convert
from core.dbc_loader import load

REF = MDF_DIR / "_T058.mdf"


def _signal_names(mdf: MDF) -> set[str]:
    names = set()
    for g in mdf.groups:
        if g.name and g.name.startswith("Signal::"):
            for ch in g.channels:
                names.add(ch.name)
    return names


@pytest.mark.golden
def test_golden_signal_coverage_and_values():
    blf = sample_blf()
    if blf is None or not REF.exists():
        pytest.skip("缺少样例 BLF 或参考 MDF")
    channels = list_channels(str(blf))
    assert channels, "样例 BLF 无通道"
    main_ch = channels[0]

    ref = MDF(str(REF))
    ref_names = _signal_names(ref)

    # 候选 DBC 逐一尝试，取信号覆盖率最大者
    candidates = ("VDCPublic_CANFD1.dbc", "VDCPublic_CANFD2.dbc")
    best = (0.0, None, None)
    for name in candidates:
        p = DBC_DIR / name
        if not p.exists():
            continue
        out = OUTPUTS_DIR / f"golden_{name}.mdf"
        convert(str(blf), {main_ch: load(str(p))}, str(out))
        m = MDF(str(out))
        ours = _signal_names(m)
        overlap = len(ours & ref_names) / max(len(ref_names), 1)
        print(f"{name}: 信号覆盖 {len(ours & ref_names)}/{len(ref_names)} = {overlap:.2%}")
        if overlap > best[0]:
            best = (overlap, m, out)
    assert best[1] is not None, "候选 DBC 均不可用"
    assert best[0] >= 0.9, f"信号覆盖率不足: {best[0]:.2%}（参考 {len(ref_names)} 个信号）"

    # 抽样数值对比：3 个共同信号，每个取参考前 100 个采样点
    m, _out = best[1], best[2]
    common = sorted(_signal_names(m) & ref_names)
    checked = 0
    for name in common[:3]:
        ours = m.get(name)
        theirs = ref.get(name)
        if ours is None or theirs is None or len(theirs.samples) == 0:
            continue
        t_ref = np.asarray(theirs.timestamps)
        v_ref = np.asarray(theirs.samples)
        t_our = np.asarray(ours.timestamps)
        v_our = np.asarray(ours.samples)
        for i in range(min(100, len(t_ref))):
            idx = int(np.argmin(np.abs(t_our - t_ref[i])))
            if abs(t_our[idx] - t_ref[i]) > 1e-3:
                continue  # 时间无对应采样点则跳过
            assert abs(float(v_our[idx]) - float(v_ref[i])) <= max(1e-6, 1e-6 * abs(float(v_ref[i]))), \
                f"{name} t={t_ref[i]:.3f}: 期望 {v_ref[i]} 实际 {v_our[idx]}"
            checked += 1
    assert checked > 0, "未能找到可对比的采样点"


@pytest.mark.golden
def test_golden_duration():
    blf = sample_blf()
    if blf is None:
        pytest.skip("无样例 BLF")
    channels = list_channels(str(blf))
    p = DBC_DIR / "VDCPublic_CANFD1.dbc"
    if not p.exists():
        pytest.skip("缺 VDCPublic_CANFD1.dbc")
    out = OUTPUTS_DIR / "golden_dur.mdf"
    result = convert(str(blf), {channels[0]: load(str(p))}, str(out))
    assert 580 <= result.duration_seconds <= 620, \
        f"时长异常: {result.duration_seconds:.1f}s（参考文件为 10 分钟）"
```

- [ ] **Step 2: 运行金标准测试**

```bash
conda run -n blfmdf python -m pytest tests/test_golden.py -v -m golden
```

Expected:
- `test_golden_duration` PASS（时长 ≈ 600s）。
- `test_golden_signal_coverage_and_values` PASS 且打印覆盖率（预期 ≥90%）。
- 若覆盖率 <90%：打印结果会显示两个候选 DBC 各自的覆盖率，据实调整 `candidates` 元组（例如改用 `VDCPublic_CANFD2.dbc` 优先、或补充其他 DBC 文件）；若数值对比失败，优先检查参考 MDF 时间基（CANoe 输出的时间可能不是自 0 起）——若参考与我们的时间戳存在固定偏移，把对比逻辑改为"窗口内最近邻"即可，值断言本身不变。

- [ ] **Step 3: Commit**

```bash
git add tests/test_golden.py
git commit -m "test: 金标准对比（样例 BLF vs _T058.mdf）"
```

---

### Task 9: 打包（PyInstaller 单文件 exe）与 README

**Files:**
- Create: `README.md`

- [ ] **Step 1: 打包**

```bash
cd e:/projects/blf_dbc
conda run -n blfmdf pip install pyinstaller
conda run -n blfmdf pyinstaller --noconfirm --onefile --windowed --name blf2mdf --collect-all PySide6 main.py
```

Expected: `dist/blf2mdf.exe` 生成（体积约 80-100MB）。

- [ ] **Step 2: 验证 exe 可启动**

双击 `dist/blf2mdf.exe`，窗口正常打开；走一遍冒烟清单（Task 7 Step 3 的 1-5 步）。

- [ ] **Step 3: 写 README**

`README.md`：
```markdown
# blf2mdf

把车上采集的 Vector .blf 日志结合 .dbc 信号矩阵转换为 .mdf（MDF 4.10）测量文件。

## 使用

双击 `blf2mdf.exe`（或 `conda run -n blfmdf python main.py`）：

1. 选择 .blf 文件（自动列出其中记录的总线通道）
2. "添加 DBC…" 导入 .dbc 矩阵
3. 为每个通道选择一个 DBC；不选则导出原始帧
4. 点"转换"，输出单个 .mdf（所有通道数据）

## 开发

依赖：`pip install -r requirements.txt`（Python 3.12，conda 环境 blfmdf）
测试：`conda run -n blfmdf python -m pytest`
金标准对比：`conda run -n blfmdf python -m pytest -m golden`

## 打包

```bash
conda run -n blfmdf pyinstaller --noconfirm --onefile --windowed --name blf2mdf --collect-all PySide6 main.py
```
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README 使用说明与打包命令"
```

---

## Self-Review 记录

**Spec 覆盖核对**（写入本计划后逐条对照设计文档）：
- blf_reader 通道枚举/按通道流式 → Task 2
- DBC 单文件不合并 → Task 3（`load` 单文件，无合并逻辑）
- 1 通道 ↔ 1 DBC 绑定、多通道输出单 MDF → Task 6/7
- 解码物理信号组 `Signal::<节点>`、通道=信号名带单位 → Task 4/5
- 原始组 `Raw::CAN<n>`（时间/ID/DLC/Data 数组/IsExtended/IsFD）→ Task 5/6
- 同名组跨通道前缀消歧 → Task 5（`CAN5::Signal::ECU1` 测试）
- mux 非激活 NaN、未知 ID 计数 → Task 4
- 无匹配帧通道警告并跳过 → Task 6
- 写失败删除半成品 → Task 6（convert 内 try/except + remove）
- 覆盖确认、错误弹窗、转换中禁用、进度/阶段、按通道摘要 → Task 7
- MDF 4.10、时间 float64 秒 → Task 5
- 金标准对比（覆盖率、抽样数值、时长）→ Task 8
- PyInstaller exe → Task 9

**类型一致性核对**：`Frame`（Task 2）→ `decode_channel(frames, dbc, channel)`（Task 4）→ `SignalSeries`（Task 4，含 channel/units）→ `write_mdf(series, raw_groups, out)`（Task 5）→ `convert(blf, bindings, out, progress_cb)`（Task 6）→ GUI（Task 7）与金标准（Task 8）按同名签名调用，无跨任务改名。

**已知偏差（相对 spec 的 GUI 示意图）**：spec 中通道表示意图带勾选框，但确认后的导出模型是"所有通道全部导出"，故 GUI 无勾选框，状态列只显示"已绑定/原始"。已在 Task 7 开头注明。
