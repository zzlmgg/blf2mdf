# 自产 MDF 与 CANoe 转换结果对比分析报告（v4，整数 ns 时间戳改造后全量复验）

日期：2026-08-13
状态：调查完成（未改代码，仅分析与记录）

## 1. 问题（原始需求）

下面是两组 blf 文件分别用 canoe 和 我们的 main.py 转换的 mdf 数据，仔细对比两组结果，
分析我们的 main.py 转换的结果与 canoe 转换的结果是否完全一致？如果不一致，体现在哪里？

### 组别一（A19G1）

| 来源 | 文件 |
|---|---|
| blf | `inputs/blf/A19G1_ACFCAN_00112_20260614_141114.blf` |
| canoe 转的 mdf | `inputs/mdf_canoe/A19G1.mdf` |
| main.py 转的 mdf | `%TEMP%\blf2mdf_task\A19G1_ours.mdf`（本次生成，2.1MB，198 组） |

### 组别二（AHT）

| 来源 | 文件 |
|---|---|
| blf | `inputs/blf/AHT_ACFCANPUB_20260317_210430_59125089-ACFCAN_20260317_210930_59125099.blf` |
| canoe 转的 mdf | `inputs/mdf_canoe/AHT.mdf` |
| main.py 转的 mdf | `%TEMP%\blf2mdf_task\AHT_ours.mdf`（本次生成，12.9MB，230 组，73.5s） |

> 注：需求原文写 `inputs/mdf/`，实际 CANoe 参考文件位于 `inputs/mdf_canoe/`。

## 2. 对比背景（与 v3 的差异）

v3（2026-08-10）遗留的真差异为 A19G1 信号时间戳 +72.98ns 偏差（BLF float64 大数减法
精度损失）。此后工作区未提交的 `core/blf_reader.py` + `core/converter.py` 改造将该问题
解决：`Frame.ts_seconds` 改为**整数 ns 构造**（`ns × 1e-9`，与 CANoe 同构），统计输入
不再做 `ts - abs_start_epoch` 相对化。本次对比即对该改造的**全量复验**。

## 3. 对比方法与环境

- 环境：conda env `blfmdf`（Python 3.12，asammdf 8.8.22，numpy）
- 转换调用：**与 GUI（main.py）完全一致的调用链**——
  `project_loader.load_project + load_mapping → auto_bindings` → `convert(raw_export=False,
  parallel=True, stats_export=True)`（对应 `gui/main_window.py` ConvertWorker.run）
  - 两个项目文件夹 DBC 相同，绑定通道 {1,3,6,8,9,10,11,12,13,15}
- 对比方法：
  - 头部逐字段对比
  - **信号组按 acq_name 配对**（两侧 acq_name 集合完全相同），组内按信号名配对，
    全信号全长度逐点对比时间戳 + 采样值；t 通道为组内独立通道，其值即帧时间戳
  - 统计组（'1s'）按「通道 × 统计项」配对：canoe 通道取自 `Channel.source.name`
    （CAN1..CAN16，= BLF 通道 0..15），ours 通道按组顺序推断（每通道 10 项，通道优先）
  - 底层 dtype/conversion 元数据另作对比
- 临时分析脚本位于 `%TEMP%\blf2mdf_task\`（convert_batch.py / compare_v2.py /
  compare_stats.py），未入仓库，**未改项目代码**

## 4. 结论速览

| 维度 | 组一 A19G1 | 组二 AHT |
|---|---|---|
| **解码信号值**（38/70 组全量逐点） | **零差异**（704 信号） | **零差异**（925 信号） |
| **解码信号时间戳**（含 t 通道） | **逐位一致**（704/704，v3 的 +73ns 已消除） | **逐位一致**（925/925） |
| **MDF 头部** | start_time/abs_time 一致（含 624ms），仅 comment 缺 UUID | 同左 |
| **1s 统计值** | 137/40960 点不等（23/160 组差 1~2 帧，CANoe 时钟域，不可复现） | **全一致**（152 逐位 + 8 浮点末位 ≤1e-14） |
| **1s 统计 t 轴** | 138/256 点差 1 ulp（~1e-13s，构造方式差异，可修复） | **1217/1217 逐位一致** |
| **统计组数量** | CANoe 352（22 项×16ch）vs 我们 160（10 项×16ch） | 同左 |
| **结构/元数据** | 组序、底层 dtype、conversion 存在性不同（物理值一致） | 同左 |

**总判定：不完全一致，但信号数据（核心内容）两组均与 CANoe 100% 逐位一致**（v3 的
时间戳偏差已修复验证）；剩余差异共 5 类：统计组 12 项缺失（已知）、A19G1 统计计数
差 1~2 帧（CANoe 统计时钟域限制，BLF 无信息，**不可修复**）、A19G1 统计 t 轴 1 ulp
（构造方式，**可修复**）、底层存储格式（uint+conversion vs float64 直存，物理值一致）、
头部 comment 缺 UUID。

## 5. 信号数据对比（核心结论）

### 5.1 组配对与采样数

- A19G1：信号组 38/38 公共（canoe 38 = 390 总组 - 352 统计组；ours 38 = 198 - 160），
  acq_name 集合**完全相同**，每组 cycles（采样数）**全部相等**
- AHT：70/70 公共（canoe 422 - 352；ours 230 - 160），同上
- 组内信号名集合：**完全一致**（无 only_ours/only_canoe 信号）

### 5.2 时间戳与值（逐位）

| 组 | 信号数 | 时间戳逐位一致 | 值逐位一致 |
|---|---|---|---|
| A19G1 | 704 | 704/704 | 704/704 |
| AHT | 925 | 925/925 | 925/925 |

含 t 通道底层表示也一致（如 AHT `0.8360000000000001` 这类 float64 表示完全相同）。
asammdf 物理层对比，即 canoe 原始码经 conversion 换算后的值与 ours 直存 float64 值
逐位相等。

**结论：本次改造（整数 ns 时间戳构造）使两组信号时间戳均与 CANoe 逐位一致，
v3 报告的 A19G1 +72.98ns 偏差已消除。这是本次对比确认的核心改进。**

## 6. 统计组（'1s'）对比

### 6.1 组数量与缺失项

| | ours | CANoe |
|---|---|---|
| 组数 | 160 = 10 项 × 16 通道 | 352 = 22 项 × 16 通道 |
| 布局 | 通道优先，每通道 10 项 | 通道优先，每通道 22 项，source.name = CAN1..16 |

ours 覆盖的 10 项：StdData / StdDataRate / ExtData / ExtDataRate / StdRemote /
StdRemoteRate / ExtRemote / ExtRemoteRate / ErrorFrames / ErrorFrameRate。

**CANoe 独有 12 项（缺失，README 已声明范围）**：

| 项 | 值特征（CAN1 样本） |
|---|---|
| Busload / BusloadAvg / BusloadMin / BusloadMax | 负载率 %，非 0（A19G1 0~1.84%，AHT 0~11.6%） |
| Bursts / BurstTime / FramesPerBurst | 突发统计，非 0（AHT Bursts 最大 18217） |
| ChipState | 恒为文本 `'Active'`（|S6） |
| ChipStateTxErr / ChipStateRxErr / MinSendDist / TransceiverErrors | 恒 0 |

### 6.2 公共 160 对的对比结果

- **AHT：完全一致**——t 轴 160/160 逐位一致；值 152/160 逐位一致，8/160 为
  StdDataRate 浮点末位差异（≤1e-14，如 `128.88888888888889 vs 128.8888888888889`）。
- **A19G1：23/160 对值不等**（108 窗口累计多 1、6 窗口多 2，即差 1~2 帧，分布从
  窗口 1 到末尾）；t 轴 138/256 点差 1 ulp（~1e-13s 量级，打印显示值相同）。

### 6.3 A19G1 统计计数差异的根因调查（详细）

调查过程（每步均有数据验证）：

1. **排除量化误差**：窗口 1 边界（1.109s）附近 ±2ms 内**没有任何帧**，计数却差 1
   （ours 19 vs canoe 18），说明不是 BLF ±119ns 量化导致。
2. **排除常数时钟偏移**：用 canoe 累计计数反推各窗口实际边界
   `b_k ∈ (ts_sorted[cum-1], ts_sorted[cum]]`，254 个窗口无一致偏移区间
   （偏移 -56ms ~ +46ms 波动，无交集）。
3. **排除固定量化**：测试 raw / round/floor/ceil 到 1ms / 100us / 1us / 10ms 共 9 种
   时间戳量化假设，对 canoe 序列最多只拟合 166/255 窗口，全部不成立。
4. **帧总数守恒**：ours 与 canoe 的 StdData 帧总数相同（9330），差异仅为
   **个别帧在相邻窗口间的归属不同**（增量序列交替 36/37/38，无系统偏移）。

**根因结论**：CANoe 1s 统计基于自身时钟域（帧到达时刻），与 BLF 对象时间戳存在
不可预测的小偏差（±几 ms 内、非线性），个别接近窗口边界的帧被分入相邻窗口。
BLF 中**不存在**恢复该归属所需的信息，无法从 BLF 复现——与既有结论一致
（见 2026-08-05 对比与 `core/stats.py` docstring）。AHT 一致是因为 ms 网格帧
经对齐后精确落在整数 ms，远离窗口边界（k+0.009s），归属稳定。

### 6.4 A19G1 统计 t 轴 1 ulp 差异（可修复）

- ours：窗口边界用 **float64 加法**构造（`R + 0.009`，如 `2.0 + 0.009 = 2.009`）
- CANoe：**整数 ns × 1e-9** 构造（`2009000000 × 1e-9 = 2.0090000000000003`）
- 两者差 1 ulp（~1e-13s，物理意义为 0）。修复方向：统计 t 轴（`core/stats.py`
  `aggregate_channel` 中 `c_bound`/`r` 系列）改按整数 ns 构造，与信号 t 通道同构。
- AHT 无此问题：ms 网格文件经 `align_timestamps` round 到整数 ms 再 ns 构造，
  幂等收敛。

## 7. 其余元数据差异（物理值一致，仅存储表示不同）

| 差异 | CANoe | ours | 影响 |
|---|---|---|---|
| 信号通道底层 data_type | 0（uint）+ linear conversion | 4/7（float64/double）直存，无 conversion | asammdf 物理层值完全一致；仅文件字节级/查看器原始码显示不同 |
| t 通道底层 | uint64 原始 ns + conversion(a=1e-9) | float64 秒直存 | 同上 |
| 头部 comment | 含 Measurement.UUID / Recorder.UUID / Recorder.Name / Recorder.FileIndex | 仅 `<HDcomment><TX/><common_properties/></HDcomment>` | 不影响数据 |
| 组序 / 组内通道序 | 统计组在前、t 位置各不同 | 信号组在前 | 查看器默认布局不同 |

头部 start_time / abs_time / tz_offset / author / department / project / subject /
flags / version：**两组全部一致**（A19G1 含毫秒小数 624ms，v3 修复项无回归）。

## 8. 差异汇总与修复建议

| 差异项 | A19G1 | AHT | 可修复性 |
|---|---|---|---|
| 信号时间戳/值 | ✅ 全一致 | ✅ 全一致 | —（本次改造已验证） |
| 统计组 12 项缺失 | ❌ | ❌ | 需按 CANoe 语义实现（Busload、Bursts、ChipState 等） |
| 统计计数差 1~2 帧 | ❌ 23/160 组 | ✅ 全一致 | **不可**（CANoe 自身统计时钟域，BLF 无信息） |
| 统计 t 轴 1 ulp | ❌ 138 点 | ✅ 全一致 | **可**（统计 t 轴改整数 ns 构造） |
| 底层 dtype / conversion | 存储差异 | 存储差异 | 可选（与 CANoe 字节级同构需改写 mdf_writer） |
| header comment 缺 UUID | 存储差异 | 存储差异 | 可选 |

## 9. 最终结论

1. **信号数据（核心交付物）：两组均与 CANoe 100% 逐位一致**（A19G1 704 信号、
   AHT 925 信号的时间戳与值），v3 遗留的 +72.98ns 时间戳偏差已在本次改造中修复验证。
2. 唯一**不可修复**的实质差异：A19G1 统计组 23/160 对差 1~2 帧——CANoe 统计基于
   自身时钟域，BLF 中无恢复信息（AHT 因 ms 网格无此问题）。
3. 可修复项：统计 t 轴 1 ulp 构造差异（§6.4）；缺失 12 项统计为功能缺口（README
   已声明当前输出 10 项）。
4. 其余差异（底层 dtype/conversion、comment UUID）不影响测量数据物理值。

## 10. 复现

```powershell
# 转换（与 GUI 同参数）
C:\ProgramData\Anaconda3\envs\blfmdf\python.exe %TEMP%\blf2mdf_task\convert_batch.py
# 对比（信号组按 acq_name、统计组按 通道×统计项）
C:\ProgramData\Anaconda3\envs\blfmdf\python.exe %TEMP%\blf2mdf_task\compare_v2.py
C:\ProgramData\Anaconda3\envs\blfmdf\python.exe %TEMP%\blf2mdf_task\compare_stats.py
```

数据要点备忘（A19G1 CAN1 StdData 抽样）：
- ours 累计：`[0, 19, 52, 88, 125, 162, ...]`，canoe：`[0, 18, 51, 88, 125, 161, ...]`
- 窗口 1 归属差异帧位于 `[1.1, 1.109)`（t = 1.102125572 / 1.105021642 两帧之一），
  canoe 边界反推偏移无常数、无量化模式
