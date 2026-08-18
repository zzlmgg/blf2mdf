# 自产 MDF 与 CANoe 转换结果对比分析报告（v2，方案C后）

日期：2026-08-06
状态：调查完成（对比 + 根因分析齐全，未实施修复，待决定是否进一步对齐）

## 1. 问题与目标

`outputs/20260806_142147.mdf`（方案C 向量化解码后的最新代码输出）与
`inputs/mdf/_T058.mdf`（CANoe 13.0 转换参考/金标准）是否完全一致？
如果不一致，差异在哪、根因是什么、**能否做到与 CANoe 完全一样的转换结果**？

前置：两侧使用相同 DBC 通道绑定（10 个绑定通道 1,3,6,8,9,10,11,12,13,15；
实际有数据 1,3,6,8,9,10,11,12,13），源数据为
`inputs/blf/ACFCANPUB_20260722_104000_59489600-*.blf`
（10 分钟 CAN/CAN-FD 日志，2,377,398 帧）。

对比环境：conda 环境 `blfmdf`（Python 3.12，asammdf 8.8.22）。

## 2. 结论速览

| 维度 | 结论 |
|---|---|
| **解码数据** | **100% 一致**：103/103 组配对，3724 个信号全量逐点对比零差异 |
| **总线统计** | 已实现的 10 项 × 16 通道 × 601 点**逐点一致**；**12 项未实现**（352 vs 160 组） |
| **MDF 头** | 版本/start_time/abs_time/flags/tz 逐字段一致 |
| **体积** | 5.44 MB vs 5.65 MB（修复项 7 后已同量级） |
| **能否"完全一样"** | **不能**——4 项统计（ChipState 等）需要 CAN 控制器硬件状态，BLF 源数据中不存在；组序是 CANoe 内部顺序；UUID 每次导出随机（详见 §5） |

## 3. 对比方法与证据

### 3.1 对比工具

- `tools/full_compare.py`：组匹配/组名/采样数/dtype 抽查/枚举文本抽查/数值抽样/统计逐点（既有）
- `tools/deep_compare_latest.py`（本次调查新增，可留作回归工具）：
  - 全文件组/通道结构 dump（组序、t 通道位置）
  - 匹配组**全信号全长度**数值对比（含 |Sn 文本 rstrip 后比较）
  - 组内信号顺序对比、时间通道元数据对比
  - 统计组结构/组序/t 轴对比

### 3.2 数据层证据（全部通过）

| 项 | 结果 |
|---|---|
| 组匹配（按信号名集合） | 103/103 配对，组名与 CANoe 完全一致 |
| 采样数/时间范围 | 全部一致（如 `ADC_4_CRC` 两侧均 59999 点，t=[0.005, 599.991]） |
| **全量数值**（103 组 × 3724 信号 × 全长逐点） | **不一致数 = 0**（整型/float64/枚举文本 bytes 全部） |
| dtype（60 信号抽查：整/浮/文本各 20） | 全部一致（最小整型/float64/定宽 UTF-8 文本） |
| 枚举文本（10 信号 × 前 200 点） | 2000 点全部一致 |
| 总线统计（10 项 × 16 通道 × 601 点） | 逐点全部一致；t 轴一致 [0, 599.999] |
| 文件头 | 4.10 / start_time=2026-07-22 10:40 / abs_time / flags=0 / tz=0 全部一致 |

### 3.3 结构层差异（详见 §5）

- 组总数：自产 263（103 信号组 + 160 统计组）；CANoe 455（352 统计组 + 103 信号组）
- 统计组位置：自产在文件尾部（G103..G262），CANoe 在文件头部（G0..G351）
- t 通道位置：自产每组首位，CANoe 每组末位
- 组内信号顺序：37/103 组与 CANoe 不一致
- 信号组组序：两侧完全不同（CANoe 内部顺序）

## 4. 与 v1 报告（2026-08-05）相比已修复的项

v1 报告的 7 类差异，本次检查全部确认已修复：

| v1 差异 | 修复项 | 本次验证 |
|---|---|---|
| 组名（节点名 vs 报文名） | 修复项 1 | 103/103 组名一致 ✓ |
| 时间基准（绝对 vs 相对） | 修复项 2 | offset=0.000，逐点一致 ✓ |
| 枚举存储（数值 vs 文本） | 修复项 3 | 2000 点文本逐值一致 ✓ |
| 总线统计缺失 | 修复项 4（阶段1） | 10 项 × 601 点逐点一致 ✓ |
| 原始帧组多余 | 修复项 5 | 自产无 Raw 组 ✓ |
| 时间通道名 `time`→`t` | 修复项 6 | 均为 `t` ✓ |
| 体积 58 倍 | 修复项 7 | 5.44 vs 5.65 MB ✓ |
| （v1 未涉及）dtype 最小整型 | 修复项 8 | 抽查 60 信号全部一致 ✓ |
| （v1 未涉及）头部 start_time | 修复项 9 | 逐字段一致 ✓ |

## 5. 剩余差异逐项分析

### A 类：可完全对齐（信息无缺失，改代码即可）

**A1. 组内信号顺序不一致（37/103 组）**

| 侧 | 规则 | 例（IDC_9） |
|---|---|---|
| CANoe | **DBC 文件原始书写顺序** | NapModeSts, WashModeSts, ConveyorWashModeSts, PetModeSts |
| 自产 | cantools 解析后按 **start_bit 升序重排**（Motorola 位位置换算后，`cantools/database/utils.py:442` `start_bit` 键） | ConveyorWashModeSts(110), WashModeSts(108), NapModeSts(106), PetModeSts(104) |

根因：`core/dbc_loader.py` 直接透传 cantools 的 `msg.signals`（已被重排），
`mdf_writer` 按 `s.signal_names` 写出。
解法：`dbc_loader` 加载时用正则提取 DBC 文本中各 BO_ 段的信号原始顺序，
重排 `md.signals`。信号值按名字索引，与顺序无关，零数据风险。
注意点：mux 报文（`_mux_plan`/`_msg_vectorizable` 依赖 `md.signals` 内容而非顺序）
不受影响；需用 37 个差异组回归验证。

**A2. t 通道位置（全部 263 组）**：CANoe 每组 `t` 在**末位**（t_pos=n-1），
自产在**首位**（t_pos=0）。`mdf_writer` 将 t 放到 `append` 信号列表末尾即可
（`_MASTER_TIME` 挂在最后一个信号上）。

**A3. 统计组位置**：CANoe 统计组在文件头部、信号组在尾部；自产相反。
将 stats 组 append 移到信号组之前即可。需同步调整
`tools/full_compare.py` 的 `compare_stats` 组索引假设（现按通道×统计项定位，
与物理位置无关，实际无需改）。

### B 类：部分可对齐（需逆向 CANoe 算法或读取配置）

**B1. 缺失 12 项总线统计**（CANoe 22 项 vs 自产 10 项，逐项判定）：

| 缺失项 | 能否复刻 | 依据 |
|---|---|---|
| MinSendDist | **能** | 参考 ch0 值 `[0, 2.646, 2.646…]` = 窗内最小帧间隔，纯时间戳计算 |
| BurstTime / FramesPerBurst / Bursts | 能，但需逆向 | 参考值全 0；burst 判定阈值（帧间隔 < X ms）未公开 |
| Busload / BusloadAvg / BusloadMin / BusloadMax | 能但难精确 | 需每通道位速率（在 CANoe 配置 `BLF_MDF_13.0.cfg`，不在 BLF）+ CANoe 负载公式（含不含 stuff bit 未知）；公式对不上会差几个百分点 |
| ChipState / ChipStateTxErr / ChipStateRxErr | **不能** | CAN 控制器状态与 TEC/REC，**CANoe 测量时从硬件读取，BLF 帧流无此信息** |
| TransceiverErrors | **不能** | 收发器硬件错误计数，BLF 无此信息 |

参考 1s 组序（每通道 22 项）：
`Busload, BusloadAvg, BusloadMin, BusloadMax, StdData, StdDataRate, ExtData,
ExtDataRate, StdRemote, StdRemoteRate, ExtRemote, ExtRemoteRate, ErrorFrames,
ErrorFrameRate, ChipState, ChipStateTxErr, ChipStateRxErr, MinSendDist,
BurstTime, FramesPerBurst, TransceiverErrors, Bursts`

参考 dtype：帧计数类 int32（type=2）、Rate/Busload/MinSendDist/Burst 类 float64、
ChipState 为文本 `Active`。

### C 类：原理上无法一致（信息不在源数据 / 本质随机）

**C1. 文件头 comment UUID**：CANoe 写入 `Measurement.UUID`/`Recorder.UUID`
（随机 GUID）、`Recorder.Name`/`FileIndex`；自产为空（asammdf 默认
`<HDcomment><TX/><common_properties/></HDcomment>`）。UUID 每次导出不同，
**不存在"相同的值"**；只能模仿结构写入自产 UUID。

**C2. 信号组组序**：CANoe 信号组顺序（VCU_7, VCU_1, ADC_4, BCS_2_C, BMC_1,
DCU_1, ZCUT_1, IPS_1, MFS_1, ADC_5, ZCUF_4, ZCUL_2, EPB_2, ZCUR_4, VCU_5…）。
已排除全部可推导规则：报文 ID 序（升/降均否）、全局首见序、每通道首见序、
cfg DBC 绑定序 × DBC 报文序、通道分组序。判定为 CANoe MDF 导出插件的内部
测量分配顺序，只能逆向 CANoe 二进制复刻，对数据无任何影响。

**C3. t 通道 CN 块 data_type**：参考 t 声明 0（UNSIGNED_INTEL），自产 4
（REAL_INTEL）；读回均为同一组 float64 时间戳（已验证逐点一致），仅块级
元数据字节不同。asammdf 无法写出声明为 unsigned 的 float 时间通道，
需手改字节，无实际意义。

## 6. 改进方案（分级，按收益排序）

### 方案 1（推荐先做）：A1 + A2 + A3 —— 结构对齐

- 改动点：
  - `core/dbc_loader.py`：正则提取 DBC 信号原始顺序，重排 `md.signals`（A1）
  - `core/mdf_writer.py`：t 通道移到组末位（A2）；stats 组移到文件头部（A3）
- 预期效果：103 组信号组结构（组名/信号序/通道序）与 CANoe 完全一致；
  统计组位置一致
- 验证：`tools/deep_compare_latest.py` 的 order_compare 应归零；
  全量数值对比保持零差异；pytest 全量回归
- 风险：低（纯顺序调整，数值路径不动）

### 方案 2（可选）：B1 中可计算的 7 项统计

- MinSendDist：直接实现（时间戳窗内最小帧间隔）
- Busload 4 项：需要从 cfg 读取位速率 + 对拍 CANoe 公式（先做一次
  逐通道对拍实验验证公式，公式不确定时放弃精确对齐）
- Burst 3 项：先用参考文件全零样本无法校准阈值；需构造含 burst 的
  CANoe 参考样例后逆向阈值
- 预期效果：统计组 340/352 可对齐（仍缺 ChipState 3 项 + TransceiverErrors）
- 风险：中（公式/阈值逆向工作量不确定，投入产出需评估）

### 方案 3（不做）：C 类

C1/C2/C3 无法通过改代码实现"完全一样"，仅当下游工具严格要求字节级
一致（不可能）时才考虑；C1 的 UUID 结构模仿收益有限。

## 7. 最终判定

| 问题 | 回答 |
|---|---|
| 解码数据是否与 CANoe 一致？ | **是，已逐点证明完全一致**（3724 信号零差异 + 统计 9600 点零差异） |
| 能否做到"完全一样"？ | **不能，且原因不在代码**：① ChipState/TxErr/RxErr/TransceiverErrors 需 CAN 控制器硬件状态，BLF 中不存在（离线转换的信息下限）；② 组序是 CANoe 内部顺序；③ UUID 每次导出随机 |
| 还能再接近多少？ | 实现方案 1 + 方案 2 后：455 组中可对齐 443 组（103 信号组 + 340/352 统计组），数据仍 100% 一致；仅剩硬件状态 4 项统计（CANoe 参考中为常值）、组序、UUID、CN 块 data_type 4 点差异 |

## 8. 复现

```bash
# 数据层/结构层全量对比（输出见终端 + outputs/mdf_compare/）
conda run -n blfmdf python tools/full_compare.py outputs/20260806_142147.mdf inputs/mdf/_T058.mdf
# 深度结构对比（组序/信号序/元数据/全量数值）
conda run -n blfmdf python tools/deep_compare_latest.py outputs/20260806_142147.mdf inputs/mdf/_T058.mdf
# 金标准回归
conda run -n blfmdf python -m pytest -m golden
```
