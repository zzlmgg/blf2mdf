# 自产 MDF 与 CANoe 输出差异修复计划

日期：2026-08-05
状态：执行中（修复项 1、2、3+8、5、6、7 已完成并验收，见 §11 执行记录）
前置文档：[2026-08-05-mdf-canoe-comparison.md](2026-08-05-mdf-canoe-comparison.md)（差异调查报告）

## 1. 目标与范围

目标：自产 MDF 与 CANoe 参考输出（`inputs/mdf/_T058.mdf`）逐项对齐。

调查报告中列出的 7 类差异 + 调查期间新发现的第 8 类差异（信号存储精度），共 **8 个修复项**。数据内容已证明一致（103/103 组信号/采样数全等、抽样数值全等），本计划只对齐**结构、表示与元数据**，不改变解码数值。

| # | 修复项 | 优先级 | 工作量 | 决策点 |
|---|---|---|---|---|
| 1 | 组命名：节点名 → 报文名 ✅ 已完成 | P0 | 小 | 无 |
| 2 | 时间基准：绝对 → 相对 ✅ 已完成 | P1 | 小 | 有（见 §5.2） |
| 3 | 枚举信号：数值 → 文本 ✅ 已完成 | P0 | 中 | 无 |
| 4 | 总线统计 352 组 | P2 | 大 | 有（见 §5.4） |
| 5 | 原始帧组选项化 ✅ 已完成 | P1 | 小 | 有（见 §5.5） |
| 6 | 时间通道名 time → t ✅ 已完成 | P0 | 极小 | 无 |
| 7 | 文件体积：开启压缩 ✅ 已完成 | P0 | 极小 | 无 |
| 8 | 信号存储精度：float64 → 最小类型 ✅ 已完成 | P0 | 中 | 无 |

实施顺序：6 → 7 → 1 → 3+8（共享 SignalDef 扩展）→ 2 → 5 → 4。每项独立可验收、可回退。进度：6、7、1、3+8、2、5 已完成；剩余 4。

---

## 2. 修复项 1：组命名（P0）✅ 已完成（2026-08-05）

### 现状与根因
`core/mdf_writer.py:30-34` 用 `s.node`（DBC 发送节点名）命名组：`Signal::ADC_VIU`、`CAN1::Signal::ADC_VIU`。CANoe 用**报文名**：`ADC_4`、`VCU_2`。后果：同一节点多报文组名重复、丢失报文信息、首个组不带通道前缀（跨通道歧义）。

### 方案
`mdf_writer.write_mdf()` 中组名改为报文名，去重时加通道前缀：

```python
# 现状
group = f"Signal::{s.node}"
if group in used_groups:
    group = f"CAN{s.channel}::Signal::{s.node}"
# 改为
group = s.message_name                      # 与 CANoe 一致（如 ADC_4）
if group in used_groups:
    group = f"CAN{s.channel}::{s.message_name}"   # 跨通道同名报文兜底
```

`SignalSeries.message_name` 已存在（`core/decoder.py:72`），无需改动 decoder。

### 涉及文件
- `core/mdf_writer.py:30-34`（组名构造）

### 验收
- 组名与参考逐组一致：103 组全部等于参考报文名（无前缀）；
- `tests/test_golden.py::test_golden_signal_coverage_and_values` 通过（`_signal_names` 的过滤逻辑 `acq != "1s"`、`not startswith("Raw::")` 不受影响，仅需更新注释）；
- 金标准测试中 `_signal_names` 自产侧与参考侧可简化为同一过滤规则（均为报文名）。

### 风险
- 跨通道同名报文（如 VDCPublic_CANFD1/CANFD2 均含 `VCU_2`）：加 `CAN<ch>::` 前缀兜底，行为可预期；当前样例无此冲突（参考文件仅 ch1 有 `VCU_2` 数据）。
- 无其他调用方依赖 `Signal::` 前缀（GUI 摘要/进度不解析组名）。

---

## 3. 修复项 6：时间通道名（P0）✅ 已完成（2026-08-05）

### 现状与根因
`core/mdf_writer.py:53` 原始帧组时间通道命名为 `time`；解码组的时间通道由 asammdf 自动生成（实测 append 同时间戳信号列表时自动建 `time` 主通道）。CANoe 命名为 `t`。

### 方案
解码组与原始组的时间通道统一命名为 `t`：
- 原始帧组：`core/mdf_writer.py:53` `name="Time"` → 检查，现为 `"Time"`（大写），统一为 `"t"`；
- 解码组：asammdf 自动生成的时间通道名。验证 asammdf 是否允许自定义主时间通道名——若 `append` 不支持，可在写完后用 `mdf.rename_channel`（asammdf 8.8 支持）批量改名为 `t`。

### 涉及文件
- `core/mdf_writer.py:53`（原始组时间通道）
- `core/mdf_writer.py` 写完后（如需要）`rename_channel("time", "t")` 遍历全部组

### 验收
- 两组文件中全部时间通道名为 `t`（自产含原始组）；
- 现有测试全绿（`_signal_names` 已排除 `t`/`time`）。

### 风险
- asammdf `rename_channel` 对主时间通道的重命名行为需实测（低风险，改名不影响数据）。
- 组内位置差异（CANoe `t` 在末位，asammdf 主时间通道在首位）由 asammdf 决定，不强行对齐。

---

## 4. 修复项 7：文件体积（P0）✅ 已完成（2026-08-05）

### 现状与根因
`core/mdf_writer.py:65` `mdf.save(out_path, overwrite=True)` 未指定压缩 → asammdf 默认 `compression=0`（不压缩）。实测（asammdf 8.8.22）：

| 文件 | 原大小 | asammdf 重存 compression=0 | compression=2 |
|---|---|---|---|
| `_T058.mdf`（参考） | 5.7 MB | 62.2 MB | 4.4 MB |
| `golden_dur.mdf`（自产） | 15.9 MB | 15.9 MB | 0.1 MB |

参考文件由 CANoe 以约 11 倍压缩写出；我们完全不压缩 → 326 MB。仅开启压缩即可解决体积问题（不依赖修复项 8）。

### 方案
```python
mdf.save(out_path, overwrite=True, compression=2)   # zlib，已验证
```

### 涉及文件
- `core/mdf_writer.py:65`

### 验收
- 全量样例转换后文件 < 10 MB（当前 326 MB）；
- 数据读回一致：组/信号/采样数/抽样数值对比全部通过（压缩透明，理论零风险）；
- 金标准测试通过。

### 风险
- 极小。压缩仅影响存储层，asammdf 读回自动解压。
- `compression=3`（实测与 2 同体积）不采用，`2` 即足够。

---

## 5. 修复项 3+8：信号存储精度与枚举表示（P0，共享基础）✅ 已完成（2026-08-05）

### 5.1 现状与根因
`core/decoder.py:57-61` 将所有解码值转为 float64 存入 `SignalSeries.values`；`core/mdf_writer.py:36-46` 以 float64 写出。CANoe 按 DBC 定义选择最小存储类型（实测规则）：

| DBC 定义 | CANoe 存储 | 示例（参考文件实测） |
|---|---|---|
| `factor==1 && offset==0 && 无 choices && 非 float` | 原始整型（按位宽/符号：uint8/16/32、int8/16/32） | `ADC_4_CRC` uint16、`ADC_4_Counter` uint8 |
| `offset≠0` 或 `factor≠1`（物理变换） | float64 物理值 | `ADC_ActTrqReq`(offset=-5000) float64、`VCU_VehElcConspInst`(factor=0.1) float64 |
| 有 choices（枚举） | 文本（DBC value table 文本，|Sn） | `ADC_VLCSt` |S15、`ADC_FlowReq` |S12 |

当前 float64 全量存储是 326 MB 的次要成因（主要成因见修复项 7），且枚举信号丢失文本语义（存 0.0 而非 'VLC not working'）。

### 5.2 前置：SignalDef 扩展
`core/dbc_loader.py:8-16` 的 `SignalDef` 缺少 choices / is_signed / is_float，需扩展：


```python
@dataclass
class SignalDef:
    name: str
    start_bit: int
    length: int
    scale: float
    offset: float
    unit: str
    is_signed: bool = False
    is_float: bool = False
    choices: dict | None = None     # {原始值: 文本}
```

`core/dbc_loader.py:53-63` 从 cantools 的 `s.is_signed`、`s.is_float`、`s.choices` 填充。

### 5.3 decoder 改动
`core/decoder.py`：

1. `decode_channel` 中按 DBC 定义决定每个信号的解码结果类型：
   - 枚举（有 choices）：保留**文本**——`str(NamedSignalValue)`（值为 choices 文本）；未知值（cantools 返回裸数值）存 `str(值)`，行为待与 CANoe 抽查对齐；
   - 整型原值（factor==1 && offset==0 && !is_float）：存整型数组（dtype 按位宽/符号）；
   - 其余：float64 物理值（现状不变）。
2. `SignalSeries` 增加信号级 dtype 元数据（如 `dtypes: dict[str, np.dtype]`），`values` 支持混合 dtype（整型数组/float64/bytes 文本）。
3. 原始帧组（`RawGroup`）的 ID/DLC/Data/IsExtended/IsFD 保持现状（已是 uint32/uint8/uint8/uint8/uint8，与 CANoe 无对应物，不在对齐范围）。

### 5.4 mdf_writer 改动
`core/mdf_writer.py:36-46`：按 `SignalSeries` 的每信号 dtype 构造 `Signal(samples=…)`：
- 整型：直接整型数组（不再 `.astype(np.float64)`）；
- 枚举：bytes 数组（`np.array([b'VLC not working', …], dtype=object)` 或 asammdf 支持的可变长度 bytes）；
- 浮点：float64（现状）。

### 涉及文件
- `core/dbc_loader.py:8-16, 53-63`（SignalDef 扩展）
- `core/decoder.py:25-31, 57-68`（解码类型选择、SignalSeries 扩展）
- `core/mdf_writer.py:36-46`（按 dtype 写出）

### 验收
- 逐信号 dtype 与参考一致：随机抽查 ≥20 个信号（覆盖整型/浮点/枚举三类），dtype 与参考相同；
- 枚举文本与参考一致：`ADC_VLCSt == b'VLC not working'`、`ADC_FlowReq == b'0      L/min'`（含尾部空格）等抽查一致；
- 数值一致性不回归：金标准测试 + `tools/full_compare.py` 抽样对比全通过（整型/浮点数值与参考相同，枚举由测试跳过改为文本比对）；
- 文件体积进一步下降（整型 1-2 字节/样本）。

### 风险
- **枚举未知值**：CANoe 对未在 choices 中的值的存储形式未验证，需抽查（实现前用参考文件确认；若无法确认，先存 `str(值)` 并在测试中标注）；
- **文本长度**：CANoe 固定 |Sn（n=最长文本），我们可用 object 数组/可变长度 bytes，读回时 asammdf 会适配，需实测确认下游（asammdf 读回 dtype）与参考一致；
- **负值整型**：有符号信号（is_signed）需 int8/16/32，本样例少见，仍按规则实现并抽查；
- 金标准测试 `test_golden.py:93-95` 的"枚举跳过"注释需更新为文本比对。

---

## 6. 修复项 2：时间基准（P1）✅ 已完成（2026-08-05）

### 现状与根因
`core/blf_reader.py` 的 `ts_seconds` 为 BLF 绝对时间戳（实测起始 1784716800.005 = 2026-07-22 10:40:00 UTC）；CANoe 以测量开始为 0（0.005s 起）。两者恒定偏移 1784716800 s。

### 方案
- `core/converter.convert()` 计算全局起始时间 `min_ts`（首个通道首帧），所有信号组与原始帧组时间戳统一减去 `min_ts`；
- 绝对时间信息不丢失：在输出文件 metadata/注释中记录绝对起始时间（asammdf 支持组 comment 或文件 history）；
- 可选：MDF 头部 start time 对齐测量开始（asammdf 是否支持需实测，见风险）；
- 金标准测试 `test_golden.py:96-98` 移除 offset 对齐逻辑，直接以相对时间对比。

### 决策点（默认：对齐 CANoe，做相对时间）
| 选项 | 说明 |
|---|---|
| A. 默认相对时间（推荐） | 与 CANoe/参考文件一致，金标准对比简化；绝对时间写入元数据 |
| B. 默认绝对时间 | 保留现状，仅加配置项；与参考文件对比需偏移 |

### 涉及文件
- `core/converter.py`（全局偏移计算与扣除）
- `core/mdf_writer.py`（元数据/注释写入）
- `tests/test_golden.py:96-98`（移除偏移对齐）
- `README.md`（行为说明）

### 验收
- 自产文件时间轴与参考一致：起始 0.005、终止 599.991（对齐后逐点数值对比通过）；
- 元数据中含绝对起始时间；
- 金标准测试通过（简化后）。

### 风险
- MDF 头部 start time 若无法通过 asammdf 设置（实测前不确定），仅对齐信号时间戳 + 元数据记录，验收标准相应下调；
- 多通道时基一致性：各通道帧时间来自同一 BLF 时钟，统一减 `min_ts` 无时基错位风险。

---

## 7. 修复项 5：原始帧组选项化（P1）✅ 已完成（2026-08-05）

### 现状与根因
未绑定 DBC 的通道导出原始帧组（`README.md` 设计如此），CANoe 完全不导出原始帧 → 自产多出 `Raw::CAN2`。这是功能范围差异，不是缺陷。

### 方案
- `core/converter.convert()` 增加参数 `raw_export: bool = True`（保持现有默认行为）；
- GUI（`gui/main_window.py`）增加复选框「导出未绑定通道的原始帧」，默认勾选；
- 关闭时跳过 `_collect_raw` 收集，输出与 CANoe 一致（仅解码信号 + 可选统计）。

### 决策点（默认：保留现有行为 + 加选项）
| 选项 | 说明 |
|---|---|
| A. 选项化，默认开（推荐） | 不破坏现有功能，用户可对齐 CANoe |
| B. 选项化，默认关 | 与 CANoe 默认一致，但丢失 README 承诺的原始帧能力 |
| C. 删除原始帧导出 | 不建议，违背设计文档 |

### 涉及文件
- `core/converter.py:78, 118-121`（raw_export 参数、_collect_raw 调用）
- `gui/main_window.py`（复选框）
- `README.md`（文档更新）

### 验收
- `raw_export=False` 时输出组数 = 解码组数（无 `Raw::` 组）；
- `raw_export=True` 时行为与现状一致（回归）；
- GUI 复选框状态传递正确。

### 风险
- 无（纯新增选项，默认行为不变）。

---

## 8. 修复项 4：总线统计 352 组（P2，需决策）

### 现状与根因
CANoe 输出 352 个 `1s` 统计组 = 22 种统计量 × 16 通道（Busload/BusloadAvg/BusloadMin/BusloadMax、StdData/StdDataRate、ExtData/ExtDataRate、StdRemote/StdRemoteRate、ExtRemote/ExtRemoteRate、ErrorFrames/ErrorFrameRate、ChipState/ChipStateTxErr/ChipStateRxErr、MinSendDist、BurstTime、FramesPerBurst、TransceiverErrors、Bursts）。本工具无统计导出。属功能范围差异。

### 方案（分级）
| 阶段 | 统计项 | 可行性 |
|---|---|---|
| 1 | StdData/ExtData/StdRemote/ExtRemote/ErrorFrames + Rate（帧计数类） | 可行：从现有帧流按秒聚合，无额外信息 |
| 2 | Busload/BusloadAvg/Min/Max | 需波特率（BLF 不含）；需 GUI 输入或配置 |
| 3 | ChipState*/MinSendDist/BurstTime/FramesPerBurst/TransceiverErrors/Bursts | 信息源存疑（依赖总线控制器内部状态，BLF 需评估是否含事件记录） |

输出结构对齐 CANoe：`1s` 组 × 22 统计 × 16 通道，每组 2 通道（统计信号 + `t`），周期 1s（600 点/10 分钟）。

### 决策点（默认：P2 立项但先做阶段 1）
| 选项 | 说明 |
|---|---|
| A. 仅阶段 1（推荐起步） | 帧计数类统计，无外部依赖 |
| B. 阶段 1+2 | 需为波特率增加配置入口 |
| C. 全部 22 项 | 需先调研 BLF 事件类型，工作量最大 |
| D. 不做 | 维持现状（工具定位为信号转换，非总线分析） |

### 涉及文件（如立项）
- 新增 `core/stats.py`（逐秒聚合统计）
- `core/converter.py`（统计收集）
- `core/mdf_writer.py`（`1s` 组写出，复用共享时间通道模式）
- `gui/main_window.py`（如阶段 2：波特率输入）

### 验收（阶段 1）
- 16 通道 × 5 组帧计数统计（StdData 等）与 CANoe 对应组逐秒数值一致（容差内，需先确认 CANoe 的秒窗对齐规则）；
- 组结构（`1s` 命名、`t` 通道）与参考一致。

### 风险
- CANoe 秒窗对齐/计数语义（含 FD 帧、错误帧统计口径）需以参考文件实测校准；
- 阶段 2/3 依赖信息源，存在不可实现项，立项前需调研。

---

## 9. 测试与验收总则

1. **金标准回归**：`conda run -n blfmdf python -m pytest`（全量）+ `-m golden`（金标准）。
2. **逐项验收**：每项修复完成后用 `tools/full_compare.py` 复跑全量对比，验收标准见各修复项。
3. **新增对比维度**（修复项 3+8 引入）：
   - 逐信号 dtype 对比（整型/浮点/枚举三类全覆盖）；
   - 枚举文本逐值对比；
   - 组名集合对比（修复项 1）。
   建议扩展 `tools/full_compare.py` 输出这些维度，或新增 `tools/verify_storage.py`。
4. **体积验收**：全量样例输出 < 10 MB（修复项 7 后），参考 < 6 MB。
5. 所有验收在**同一 conda 环境**（blfmdf，asammdf 8.8.22）执行。

## 10. 待决策点汇总（评审时确认）

| # | 决策 | 默认建议 |
|---|---|---|
| D1 | 时间基准默认相对（修复项 2） | ✅ 已定：A. 默认相对 + 元数据记录绝对时间（修复项 2 已按此实施，见 §11） |
| D2 | 原始帧导出（修复项 5） | ✅ 已定：B. 选项化，默认关（用户决策：与 CANoe 导出一致；计划原推荐 A 未采纳，见 §11） |
| D3 | 总线统计范围（修复项 4） | A. 阶段 1 起步，后续按需扩展 |
| D4 | 枚举未知值的存储形式（修复项 3） | ✅ 已定：空字节 `b''`（实测 `FanPWMSt` raw=0 ∉ {255:...} 全部存 b''） |

---

## 11. 执行记录

### 修复项 5：原始帧组选项化（P1）✅ 已完成（2026-08-05）

- **决策（D2）**：定为 **B. 选项化，默认关**（用户决策：与 CANoe 导出一致；计划 §7 原推荐 A「默认开」未采纳）。
- **改动**：
  - [core/converter.py](core/converter.py)：`convert()` 新增 `raw_export: bool = False` 参数（默认关）；未绑定通道关闭时**跳过 `_collect_raw` 收集**（不迭代帧数据，摘要 `raw_frames/unknown_frames=0`），开启时行为与现状一致。
  - [gui/main_window.py](gui/main_window.py)：通道表下方新增复选框「导出未绑定通道的原始帧」（默认不勾选）；`ConvertWorker` 接收 `raw_export` 并传入 `convert()`；未绑定行摘要区分显示（关闭时显示「原始帧导出关闭」，避免误导为 0 帧）；下拉项文本「不绑定（导出原始帧）」→「不绑定」（是否导出由复选框统一控制）。
  - [README.md](README.md)：使用步骤与未绑定通道行为说明更新（默认不导出，与 CANoe 一致）。
  - [tests/test_converter.py](tests/test_converter.py)：5 个依赖原始帧导出的既有测试显式传 `raw_export=True`（回归）；新增 `test_convert_raw_export_off_by_default`（默认关：无 `Raw::` 组、摘要 raw_frames=0、解码数据不受影响）与 `test_convert_raw_export_off_skips_collection`（显式 False 同样跳过收集）。
- **测试**：全量 pytest **45/45 通过**（含金标准；TDD 先红后绿：修复前 `convert()` 无 `raw_export` 参数，断言失败 TypeError）。
- **验收**：
  - `raw_export=False` 输出组数 = 解码组数（无 `Raw::` 组）✓（`{"ABC"}`，测试断言）；
  - `raw_export=True` 行为与现状一致（回归）✓（5 个既有 raw 测试全过）；
  - GUI 复选框状态传递正确 ✓（冒烟检查：`raw_export` 默认 False、复选框存在且默认不勾选；代码路径 checkbox → `ConvertWorker.raw_export` → `convert(raw_export=…)`）。
- **偏差**：
  1. **计划方案中 `raw_export: bool = True`（默认开）按用户决策改为默认关**（D2 采纳选项 B），GUI 复选框默认勾选改为默认不勾选；其余按方案原样实施。
  2. **GUI 无自动化测试**（仓库无 GUI 测试文件），复选框状态传递以冒烟检查 + 代码审查验证。
- **遗留（待办）**：代码改动尚未提交（工作区，与修复项 1、2、3+8、6、7 叠加）；[2026-08-04-blf2mdf-design.md](superpowers/specs/2026-08-04-blf2mdf-design.md) 中「未绑定通道以原始帧数据组导出」的默认行为描述与 GUI 草图（L109-124）未同步（本项计划涉及文件未含设计文档，按需另行更新）。

### 修复项 2：时间基准（P1）✅ 已完成（2026-08-05）

- **改动**：
  - [core/blf_reader.py](core/blf_reader.py)：新增 `read_start_time(path)`——返回 BLF 文件头记录的测量开始时间（python-can `BLFReader.start_timestamp`，UTC 秒）。
  - [core/converter.py](core/converter.py)：时间轴归零基准由"最早已解码帧 `min_ts`"改为 **BLF 文件头测量开始**（与解码帧无关）；`min_ts/max_ts` 仍用于 duration 计算（差值不变）。各信号组与原始帧组时间戳统一减去文件头 start time。
  - [core/mdf_writer.py](core/mdf_writer.py)：`write_mdf` 新增 `abs_start_epoch` 参数，设置 `mdf.header.start_time`（naive UTC 整秒）。
  - [tests/test_converter.py](tests/test_converter.py)：新增 `test_convert_relative_timestamps_and_start_time`（解码组 t=[0,1]、原始组 t=[3]、header start_time=2026-07-22 10:40:00 + abs_time 断言）。
  - [tests/test_mdf_writer.py](tests/test_mdf_writer.py)：新增 `test_write_abs_start_time_metadata`（abs_start_epoch → header 逐字段）与 `test_write_without_abs_start_keeps_default_header`（默认不写）。
  - [tests/test_golden.py](tests/test_golden.py)：删除偏移对齐逻辑（`offset = t_our[0]-t_ref[0]`），改为时间轴一致性断言（首点差 < 1e-3）后直接逐点对比。
  - [README.md](README.md)：行为说明（相对时间 + 头部 start_time）。
- **测试**：全量 pytest **40/40 通过**（含金标准；TDD 先红后绿：修复前 converter 新测试断言绝对时间戳失败、mdf_writer 缺参数 TypeError、golden 时间轴断言失败）。
- **验收**：金标准 golden.mdf → `tools/full_compare.py` 对比参考 `_T058.mdf`：
  - **时间轴逐点对齐**：全部 8 个抽样信号 offset=0.000s（修复前 1784716800.000），逐点数值一致；15 组时间范围逐组全等（如 `ADC_4_CRC` 两侧均 `t=[0.005, 599.991]`、`VCU_2_CRC` 均 `t=[0.004, 599.903]`）；
  - **元数据**：自产 header `abs_time=1784716800000000000`、`time_flags=0x1`、`tz_offset=0` 与参考逐字段一致（roundtrip 实测）；
  - dtype 抽查与数值抽样全部一致（修复项 1/3+8/6/7 不回归）。
- **偏差**（4 处，均有实测依据）：
  1. **基准来源与计划不同（关键）**：计划 §6 写"全局起始时间 min_ts（首个通道首帧）"。实测 CANoe 基准 = **BLF 文件头测量开始**（1784716800.000，与解码帧无关）：样例中 ch10 的 .000 帧不在金标准绑定 DBC 内，最早**已解码**帧为 .002 → 以首帧归零整体偏移 2ms（golden 首次运行实测 ACChgL1Temp 差 -0.002、ZCUT_4 首帧 .075 vs 参考 .077）。改用文件头 `start_timestamp` 后逐帧对齐（ZCUT_4 两侧均 .077）。计划方案"首个通道首帧"在单文件、首帧必解码场景下与文件头一致，但不稳健，按实测修正。
  2. **验收数字修正**：计划写"起始 0.005、终止 599.991"。实测参考**全局最早解码帧 = 0.002**（CCU_13/ZCUT_1，ch11 0x288 @ 0.002）；0.005 是 ADC 组（ch1）范围。对齐以逐组时间范围与逐点数值一致为验收（full_compare 15 组全等），0.005/599.991 恰为 ADC_4_CRC 组值。
  3. **MDF 头部 start_time 可实现（计划风险项解除）**：asammdf 8.8.22 `mdf.header.start_time` setter 对 naive datetime 按 UTC 计算 abs_time 并置 FLAG_HD_LOCAL_TIME/tz_offset=0——与 CANoe 参考逐字段一致（实测 roundtrip abs_time/flags/tz 全同），无需降级为"仅注释记录"。
  4. **python-can BLFWriter 的 start_timestamp = 首帧时间（截断 3ms）**：测试 fixture 的 BLF 文件头时间即首帧时间，与"测量开始"语义等价，fixture 断言（t=[0,1]/[3]）不受影响。
- **遗留（待办）**：代码改动尚未提交（工作区，与修复项 1、6、7、3+8 叠加）。

### 修复项 3+8：信号存储精度与枚举表示 ✅ 已完成（2026-08-05）

- **改动**：
  - [core/dbc_loader.py](core/dbc_loader.py)：`SignalDef` 扩展 `is_signed` / `is_float` / `choices`（`{原始值: 文本}`，由 cantools 填充，choices 文本转 str）；`load()` 新增 DBC 文件编码探测（`_detect_encoding`：UTF-8 可整体解码→utf-8，否则→gbk）并传给 `load_file(encoding=…)`。
  - [core/decoder.py](core/decoder.py)：新增 `_signal_kind`（**观察值相关的存储类型规则**，见偏差 5）与 `_int_dtype`（按位宽最小整型：uint8/16/32/64、int8/16/32/64）；`decode_channel` 先原样收集解码值，收齐后按 kind 分派——float64 物理值（物理变换+choices 且有表外值时，**表内值存 nan**）/ |S 文本（表内 verbatim UTF-8 bytes，表外 `b''`）/ 最小整型原值（mux 非活跃 nan→0）；`SignalSeries.values` 改为混合 dtype 数组（dtype 即元数据，未另设 dtypes 字典，见偏差 4）。
  - [core/mdf_writer.py](core/mdf_writer.py)：信号样本按自身 dtype 原样写出（去掉 `.astype(np.float64)`）；`|S` 文本通道加 `encoding="utf-8"`（asammdf 字符串信号必需）。
  - [tests/test_decoder.py](tests/test_decoder.py)：`test_enum_signal_named_choice_unwraps_to_numeric` → `test_enum_signal_stored_as_text`（文本 + 表外 b''）；`test_mux_inactive_signal_is_nan` → `test_mux_inactive_signal_defaults_to_zero`（整型原值 + mux nan→0）；新增 `test_integer_signal_minimal_dtype`（uint8/16/32 按位宽）。
  - [tests/test_mdf_writer.py](tests/test_mdf_writer.py)：新增 `test_write_mixed_dtypes_roundtrip`（整型/|S 文本/float64 往返 dtype 不变）。
  - [tests/test_golden.py](tests/test_golden.py)：抽样对比由"枚举跳过"改为**枚举文本逐值比对**（数值信号前 3 个 + 文本信号前 2 个 × 各 100 点，字节相等）。
  - [tools/full_compare.py](tools/full_compare.py)：新增 §9.3 建议的两个维度——**逐信号 dtype 对比**（三类各抽查 ≤20）与**枚举文本逐值对比**（≤10 文本信号 × 前 200 点）。
- **测试**：全量 pytest **40/40 通过**（含金标准；TDD 先红后绿：修复前枚举测试断言 `[1.0, 2.0]` 数值、mux nan、writer 字符串转 float 失败；后新增观察值规则回归测试）。
- **验收**：cfg 绑定全量转换 `outputs/verify_fix38.mdf` → `tools/full_compare.py` 对比参考 `_T058.mdf`：
  - **dtype**：抽查 60 信号（整型 20 / 浮点 20 / 文本 20）**全部与参考一致**；**全量 3724 信号逐信号扫描 0 不一致**；
  - **枚举文本**：10 文本信号 × 200 点 = 2000 点**逐值全等**（含 `ADC_FlowReq` 尾空格、`b'Valid;'` 分号、GBK 中文 `b'Normal；'`）；另抽查 6 个难点信号（`HVAC_RearTempSelect`/`HVAC_DriverTempSelect`/`FanPWMSt`/`VCU_CruExitNotice` 等）× 500 点 dtype+值全等；
  - **数值不回归**：8 信号 × 200 点全一致、103/103 组匹配、组名/采样数/通道数全一致（修复项 1/6/7 验收不回归）；
  - **体积**：6.63 MB（验收 < 10 MB ✓；比修复项 7 的 2.97 MB 大，见偏差 2）。
- **偏差**（6 处，均有实测依据）：
  1. **DBC 编码（新增发现，计划未涉及）**：项目 DBC 为 GBK 编码（11/13 文件），cantools 默认 latin-1 解析出乱码（`'Normal£»'`），CANoe 按 GBK 解析存 `b'Normal；'`。不修编码则文本永远对不齐，故在 `dbc_loader` 增加编码探测（UTF-8 可整体解码→utf-8，否则→gbk），并确认全部 13 个 DBC 按此规则正常加载。
  2. **体积变化方向与计划预期相反**：计划验收写"文件体积进一步下降（整型 1-2 字节/样本）"。实测 2.97 MB → 6.63 MB：整型/文本的字节节省存在，但 1914 个文本信号的内容熵（UTF-8 中文等）使 zlib 压缩效果差于全 0.0 float64，净增约 3.6 MB。仍远低于 10 MB 总验收线，且与参考 5.4 MB 同量级；计划"进一步下降"表述按实测修正。
  3. **mux 非活跃整型信号 nan → 0**：cantools 对 mux 非活跃信号返回 nan，整型数组无法存 nan；按原始 0 存储（参考文件无 mux 场景可对照，测试中标注）。
  4. **未设独立的 `dtypes: dict[str, np.dtype]` 字段**：计划 §5.3 以"如"举例；`values` 各数组自带 dtype，即为信号级元数据，另设字典会引入冗余同步风险。
  5. **|S 定宽规则 = 最长观察值**（计划未明示）：实测 1894/1914 文本信号 |S 宽度 == 最长观察值字节长（如 `EMS_GasPedalActPstforMRRVD` choices 最长 8B 但仅出现 6B 值 → |S6）；全空信号 |S1。np 数组按观察值自动定宽恰好实现该规则。
  6. **物理变换+choices 信号的存储类型取决于观察值（关键发现，计划静态规则不完整）**：计划 §5.1 表假设"有 choices → 文本"。全量扫描发现 26 个反例（全部 `HVAC_*`）：同为 `CCU_2` 报文的 `HVAC_RearTempSelect` 与 `HVAC_DriverTempSelect`（scale=0.5/offset=18/choices={31:'Invalid'} 完全相同），参考却分别存 |S7 文本与 float64。根因（BLF 解码证实）：**观察到的原始值全部在表内 → 文本**（RearTempSelect 全 31 → 'Invalid'）；**混有表外值 → float64，且表内值存 nan**（DriverTempSelect 混 raw=0 表外值 → 18.0 + 表内 31 → nan，非物理 33.5）。38 个 float64+choices 信号全量核对无"表内值存物理值"反例，规则成立。实现：先收集解码值，收齐后按"是否全为 NamedSignalValue（表内）"决定 kind。新增回归测试 `test_transform_signal_with_choices_observation_based`。
- **D4 决策（表外值存储）已定**：CANoe 对未在 choices 中的原始值存**空字节 `b''`**（实测 `FanPWMSt`：唯一 choice 255，实际原始值 0 → 全部 b''；其余 20 个全空信号同型）。实现按此存储。
- **遗留（待办）**：代码改动尚未提交（工作区，与修复项 1、6、7 叠加）。

### 修复项 7：文件体积 ✅ 已完成（2026-08-05）

- **改动**：[core/mdf_writer.py](core/mdf_writer.py) `mdf.save(out_path, overwrite=True)` → 加 `compression=2`（转置 + deflate）。asammdf 8.8.22 `save` 默认 `compression=0`（不压缩）；注释同时修正计划的"zlib"措辞（2 为 transposition+deflate，1 才是纯 deflate）。新增 [tests/test_mdf_writer.py](tests/test_mdf_writer.py) `test_compression_shrinks_output`（50 万点写入：未压缩 8.00 MB → 压缩后 < 1 MB + 读回一致）。
- **测试**：全量 pytest **37/37 通过**（含金标准）；TDD 先红后绿（修复前断言失败：8.00 MB）。
- **验收**：全量样例转换（10 通道 + ch2 未绑定）输出 `verify_fix7.mdf` **2.97 MB**（原 326 MB，压缩比 109 倍，验收线 < 10 MB）；读回一致：104 组（103 解码 + `Raw::CAN2`）按信号集合匹配 103/103、通道数无差异、采样数 103 组全一致、抽样数值 8 信号全一致（与参考 `_T058.mdf` full_compare）。
- **偏差**：无。方案原文"compression=2（zlib，已验证）"的 zlib 措辞不准确（2 为转置+deflate），仅注释修正，值不变。
- **遗留（待办）**：代码改动尚未提交（工作区，与修复项 1、6 叠加）。

### 修复项 6：时间通道名 ✅ 已完成（2026-08-05）

- **改动**：[core/mdf_writer.py](core/mdf_writer.py) 解码组与原始帧组的主时间通道统一命名为 `t`：通过 `Signal(master_metadata=("t", SYNC_TYPE_TIME))`（首信号）在 append 时直接指定主通道名（asammdf 8.8.22 机制，见 [mdf_v4.py:2986-3001]）；原始帧组移除冗余的 `Time` 通道（其数据与主时间通道重复，由主通道 `t` 承载）；同步更新 [tests/test_mdf_writer.py](tests/test_mdf_writer.py)（解码组/原始组时间通道断言）与 [tests/test_golden.py](tests/test_golden.py)（`_signal_names` 注释）。
- **测试**：全量 pytest **36/36 通过**（含金标准 `test_golden_signal_coverage_and_values`、`test_golden_duration`）；TDD 先红后绿（修复前断言失败：`['time', 'Speed', 'Temp']`）。
- **验收**：转换样例 BLF（ch2 显式未绑定）→ 104 组（103 解码 + `Raw::CAN2`）全部组主时间通道为 `t`，无 `time`/`Time` 杂散通道；`t` 数据保存读回完好（ID/DLC 等信号 timestamps 经主通道获取一致）。
- **偏差**（2 处，均已实测验证）：
  1. 计划假设的 `mdf.rename_channel` 在 asammdf 8.8.22 **不存在**（`dir(MDF)` 无此方法，仅 `get_channel_name` 等 getter）。改用 `Signal.master_metadata` 在创建时指定主通道名（append 官方机制，mdf_v4.py `signals[0].master_metadata` 决定主通道名），无需写后改名，更干净。
  2. 原始帧组实为**两个时间通道并存**（asammdf 自动主通道 `time` + 显式 `Time` 通道，数据重复），无法"全部改名为 `t`"（同组重名）。故移除冗余 `Time` 通道、主通道命名 `t`，达成验收"全部时间通道名为 t"且无重复数据。
- **遗留（待办）**：代码改动尚未提交（工作区）；修复项 1 遗留的文档同步（design/implementation 计划中的组名描述）未在本项处理。

### 修复项 1：组命名 ✅ 已完成（2026-08-05）

- **改动**：[core/mdf_writer.py:30-34](core/mdf_writer.py#L30-L34) 组名由 `Signal::<节点>` 改为 `s.message_name`（报文名），跨通道同名报文加 `CAN<ch>::` 前缀兜底；同步更新 [tests/test_mdf_writer.py](tests/test_mdf_writer.py)（组名断言 + 去重测试更名为 `test_same_message_across_channels_dedupe`）与 [tests/test_converter.py](tests/test_converter.py) 断言；更新 [tests/test_golden.py](tests/test_golden.py) `_signal_names` 注释；[tools/full_compare.py](tools/full_compare.py) 新增组名对比维度（§9.3 建议项落实）。
- **测试**：全量 pytest **36/36 通过**（含金标准 `test_golden_signal_coverage_and_values`、`test_golden_duration`）；TDD 先红后绿（修复前 4 个测试按新预期失败）。
- **验收**：cfg 绑定（ch 1,3,6,8,9,10,11,12,13）复现 GUI 运行转换后 `tools/full_compare.py` 输出 **103/103 组完全匹配、匹配组组名全部一致**；组名集合与参考**严格相等**（无 `Signal::`、无 `CAN<ch>::` 前缀），未匹配组两侧各 0 个，采样数/时间范围 103 组全一致，抽样数值 8 信号全一致。
- **偏差**：无（按方案原样实施）。去重测试更名属测试语义随实现同步，非方案偏差。
- **遗留（待办）**：
  - 代码改动尚未提交（工作区）；
  - [2026-08-04-blf2mdf-design.md](superpowers/specs/2026-08-04-blf2mdf-design.md#L86-L88) 与 [2026-08-04-blf2mdf-implementation.md](superpowers/plans/2026-08-04-blf2mdf-implementation.md) 中 `Signal::<节点>` 的组名描述需同步为报文名（纯文档更新，不影响代码）。
