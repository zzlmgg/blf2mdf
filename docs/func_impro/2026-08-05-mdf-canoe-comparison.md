# 自产 MDF 与 CANoe 转换结果对比分析报告

日期：2026-08-05
状态：调查完成（Phase 1-3 证据齐全，未实施修复）

## 1. 问题

以下两个转换结果是否完全一致？如果不一致，哪里不一致？是什么原因导致的不一致？

| 文件 | 来源 | 大小 |
|---|---|---|
| `outputs/20260805_115505.mdf` | 当前代码（GUI 转换运行） | 326,173,552 B (~326 MB) |
| `inputs/mdf/_T058.mdf` | CANoe 13.0（BLF_MDF_13.0 导出，参考/金标准） | 5,652,092 B (~5.4 MB) |

前提：两侧转换使用相同的通道配置（DBC 绑定）。源数据为
`inputs/blf/ACFCANPUB_20260722_104000_59489600-*.blf`（10 分钟 CAN/CAN-FD 日志，12 个通道：0,1,2,3,6,8,9,10,11,12,13,14）。

调查环境：conda 环境 `blfmdf`（Python 3.12，asammdf 8.8.22）。

## 2. 结论速览

**不完全一致，但解码信号的数据内容完全一致。**

- 103/103 个解码报文组：信号名集合、组内通道数、每信号采样数**全部一致**；
- 抽样数值对比（8 信号 × 200 点，容差 1e-6）：**全部一致**；金标准测试（3 信号 × 100 点）同样通过；
- 单位一致（同源 DBC）；
- 差异集中在 **7 类结构性/表示性差异**（详见 §5）：组命名、时间基准、枚举存储、总线统计缺失、原始帧组多余、时间通道名、文件体积（58 倍）。

## 3. 调查方法

按系统化调试流程执行：

1. **结构对比**：读取两个 MDF 的全部组（ChannelGroup）、通道（Channel）——组名、通道数、信号名、单位、数据类型（现为 `tools/mdf_compare.py` 的 structure 维度，经 `tools/full_compare.py` 调用；原 `tools/compare_mdf.py` 已并入，2026-08-18 收口）。
2. **组匹配**：以"组内信号名集合"为键，将两侧组逐一配对（`tools/full_compare.py`）。
3. **数据对比**：对匹配组逐组核对通道数、每信号采样数、时间范围；再对共同信号做数值抽样对比（时间偏移对齐 + 窗口内最近邻，容差 1e-6）。
4. **通道归属还原**：读取 CANoe 导出配置 `inputs/mdf/BLF_MDF_13.0.cfg` 的 DBC↔通道绑定；逐通道重放 BLF 帧，确认各通道帧 ID 与 DBC 报文 ID 的归属。
5. **体积构成分析**：估算两侧未压缩数据量，对比实际文件大小，定位 58 倍差距来源。

## 4. 结构对比结果

### 4.1 总览

| 维度 | 自产 `20260805_115505.mdf` | CANoe `_T058.mdf` |
|---|---|---|
| 组总数 | 104 | 455 |
| 解码信号组 | 103（组名 `Signal::<节点>` / `CAN<ch>::Signal::<节点>`） | 103（组名 = 报文名，如 `ADC_4`、`VCU_2`） |
| 总线统计组 | 0 | 352（22 种统计量 × 16 通道，组名 `1s`） |
| 原始帧组 | 1（`Raw::CAN2`，12,000 帧） | 0 |

### 4.2 组匹配

以信号名集合为键匹配：**103/103 全部配对成功**（自产侧唯一未匹配组是 `Raw::CAN2`，参考侧 0 个未匹配）。

配对组的逐组核对：

- **通道数（含时间通道）**：全部一致；
- **每信号采样数**：全部一致（例：`ADC_4_CRC` 两侧均 59,999 点；`VCU_2_CRC` 均 6,000 点；`BMC_12Volt` 均 2,998 点）；
- **时间范围**：一致（见 §5.2 的时间基准差异）。

### 4.3 数值抽样对比

| 信号 | 对齐偏移 (s) | 对比点数 | 结果 |
|---|---|---|---|
| ADC_4_CRC | 1784716800.000 | 200 | 一致 |
| ADC_TotalRefTorque_A | 1784716800.000 | 200 | 一致 |
| ADC_10_CRC | 1784716800.000 | 200 | 一致 |
| VCU_2_CRC | 1784716800.000 | 200 | 一致 |
| VCU_8_CRC | 1784716800.000 | 200 | 一致 |
| IPS_1_CRC | 1784716800.000 | 200 | 一致 |
| BMC_1_CRC | 1784716800.000 | 200 | 一致 |
| DCU_1_CRC | 1784716800.000 | 200 | 一致 |

对齐方法：自产时间戳减去固定偏移 `1784716800`（UNIX 纪元）后，参考时间点窗口 1e-3 s 内取最近邻，数值容差 `max(1e-6, 1e-6×|参考值|)`。金标准测试（`tests/test_golden.py`）此前亦验证了 3 个共同信号 × 100 点一致。

## 5. 差异详述与根因

### 5.1 组命名：节点名 vs 报文名

| 侧 | 组名示例 | 含义 |
|---|---|---|
| CANoe | `ADC_4`、`ADC_8`、`VCU_2` | 报文名（每组 = 一个报文） |
| 自产 | `Signal::ADC_VIU`、`CAN1::Signal::ADC_VIU` | DBC 发送节点名（`Signal::<节点>`，同节点名再次出现时加通道前缀 `CAN<ch>::`） |

**根因**：`core/mdf_writer.py:30-34` 用 `s.node`（来自 `core/decoder.py:73` 的 `md.sender_node`）命名组，而非 `s.message_name`。

**后果**：同一节点的多个报文被赋予相同的组名（如报文 `ADC_4` 与 `ADC_8` 都叫 `Signal::ADC_VIU`），组名丢失报文信息，且无法从组名判断通道（首个出现不带通道前缀）。数据本身未合并（仍是一组一个报文），仅是命名问题。

### 5.2 时间基准：绝对 vs 相对

| 侧 | 时间通道 | 起始值 | 终止值 |
|---|---|---|---|
| 自产 | `time`（float64, 单位 s） | 1784716800.005（2026-07-22 10:40:00 UTC） | 1784717399.991 |
| CANoe | `t`（float64, 单位 s） | 0.005 | 599.991 |

**根因**：`core/blf_reader.py` 的 `ts_seconds` 保留 BLF 记录的绝对时间戳；CANoe 导出时以测量开始时刻归零。两者为恒定偏移 1784716800 s（正好是测量起始的 UNIX 时间，与文件名 `20260722_104000` 吻合），对齐后数值逐点一致。

### 5.3 枚举信号存储：文本 vs 数值

| 侧 | `ADC_VLCSt` 样本 | dtype |
|---|---|---|
| CANoe | `b'VLC not working'` | \|S15（文本） |
| 自产 | `0.0`（枚举索引的物理值） | float64 |

**根因**：`core/decoder.py:59-60` 将 `NamedSignalValue` 显式转为 `.value`（数值）后以 float64 存储；CANoe 将枚举信号按 DBC value table 的文本存储。语义等价（0.0 ↔ 'VLC not working' 均来自同一 DBC），但表示形式不同，下游工具（如文本显示、离线分析）行为会不同。金标准测试因此跳过枚举信号的数值断言。

### 5.4 总线统计组缺失

CANoe 额外包含 **352 个 `1s` 统计组 = 22 种统计量 × 16 通道**（对全部 16 个 CAN 通道，与是否绑定 DBC 无关）：

```
Busload, BusloadAvg, BusloadMin, BusloadMax, StdData, StdDataRate,
ExtData, ExtDataRate, StdRemote, StdRemoteRate, ExtRemote, ExtRemoteRate,
ErrorFrames, ErrorFrameRate, ChipState, ChipStateTxErr, ChipStateRxErr,
MinSendDist, BurstTime, FramesPerBurst, TransceiverErrors, Bursts
```

自产文件完全没有总线统计。

**根因**：工具未实现总线统计导出功能（属设计范围差异，非缺陷；见设计文档「输出内容：…不含总线统计通道」）。

### 5.5 原始帧组：自产独有

自产文件含 1 个 `Raw::CAN2` 原始帧组（12,000 帧，ID 0x35C/0x398 = `CCU_VCU_7`/`CCU_VCU_4`，均为 CAN-FD，扩展位全 0）。CANoe 文件没有任何原始帧组。

**通道归属还原**（重放 BLF 各通道帧数/ID 分布）：

| 通道 | 帧数 | 主要 ID | 归属 |
|---|---|---|---|
| CAN0 | 25,000 | 918/1031/992/1359 | 不在任何已绑定 DBC → 被过滤丢弃 |
| CAN2 | 138,000 | 422/258/256/860/817… | 仅 860/920 ∈ 已绑定 VDCCIDC 报文 → 12,000 帧保留为原始帧 |
| CAN14 | 31,200 | 257/335 | 不在任何已绑定 DBC → 被过滤丢弃 |

**根因**（设计 + 过滤机制共同作用）：

1. 设计：`README.md`「未绑定 DBC 的通道导出原始帧」——ch2 在本次运行中未绑定 DBC（CANoe cfg 中 ch2 也未绑定：DBC 绑定为 0,1,3,6,8,9,10,11,12,13,15）；
2. 过滤：`core/converter.py:48,96` 的 `known_ids` 过滤——未绑定通道只保留"本次转换中任一已绑定 DBC 有报文定义"的帧。ch2 上恰好有 12,000 帧的 ID 属于已绑定的 `VDCCIDC_CANFD.dbc`（绑定在 ch12），故得以保留；CAN0/CAN14 的帧全部无匹配 ID，被完全丢弃，因此只有 `Raw::CAN2` 一个原始帧组。

**cfg 绑定还原**（`inputs/mdf/BLF_MDF_13.0.cfg`）：0→VDCTBOX_CANFD、1→VDCPublic_CANFD1、3→VDCCCU_CANFD2、6→VDCCCU_CANFD3、8→VDCCZF_CANFD、9→VDCCZL_CANFD、10→VDCCZR_CANFD、11→VDCCZT_CANFD、12→VDCCIDC_CANFD、13→VDCCCU_CANFD1、15→VDCPublic_CANFD2（ch2/ch14 未绑定；ch15 在 BLF 中不存在，无产出）。实际有数据的解码通道与自产一致（1,3,6,8,9,10,11,12,13）。

### 5.6 时间通道名与位置

| 侧 | 通道名 | 组内位置 |
|---|---|---|
| 自产 | `time` | 首位 |
| CANoe | `t` | 末位 |

**根因**：`core/mdf_writer.py:53` 命名 `time`；asammdf append 时信号按列表顺序写出。

### 5.7 文件体积：326 MB vs 5.4 MB（58 倍）

两侧的**信号数据量相同**（未压缩体积估算均 ≈ 640 MB：值 ≈ 325 MB + 时间戳 ≈ 315 MB），差距来自存储方式：

| 项 | 自产 | CANoe |
|---|---|---|
| 每信号时间戳 | **独立存储**：每个信号各带一份 float64 时间戳数组（≈ 315 MB 冗余） | **组内共享**：每组只有一个 `t` 通道 |
| 压缩率 | ≈ 2 倍（asammdf save 默认） | ≈ 60~114 倍（CANoe 高倍压缩；数值数据低熵，压缩率高） |
| 实际文件 | 326 MB | 5.4 MB |

**根因**：`core/mdf_writer.py:36-46,51-63` 为每个 `Signal` 显式传入独立 `timestamps`（asammdf 按信号存储时间戳），而非复用组级时间通道；输出未采用高压缩级别。

## 6. 附带发现

- 自产组名 `Signal::<节点>` 有跨通道歧义：`mdf_writer.py` 仅在同名重复时追加通道前缀，首个出现不带通道前缀，无法从组名反推通道。
- 金标准测试的 `BINDING`（`tests/test_golden.py:16-27`）将 ch2→VDCCIDC，与本次 GUI 运行（ch2 未绑定）不同——金标准因此会多出 `CCU_VCU_4/7` 两个解码组（参考侧没有），属测试自身的通道选择，与本次对比结论无关。
- 参考文件 `1s` 统计组覆盖全部 16 个通道（含无数据的 15），说明统计导出与 DBC 绑定无关。

## 7. 结论

| 判定 | 依据 |
|---|---|
| 解码信号数据一致 | 103/103 组信号集合/通道数/采样数全等；抽样数值全等（1e-6） |
| 组命名不一致 | 节点名 vs 报文名（§5.1） |
| 时间基准不一致 | 绝对 vs 相对，恒定偏移 1784716800 s（§5.2） |
| 枚举存储不一致 | 数值 vs 文本（§5.3） |
| 功能范围不一致 | CANoe 多总线统计 352 组（§5.4）；自产多原始帧组（§5.5） |
| 元数据不一致 | 时间通道名/位置（§5.6） |
| 体积不一致 | 58 倍：时间戳冗余 + 压缩率（§5.7） |

**修复优先级建议**（如后续对齐 CANoe 输出）：§5.1 组名改报文名（改动小、收益最大）；§5.2/§5.3 属表示层差异，需按下游工具需求决定；§5.4/§5.5 属功能范围决策；§5.7 共享时间通道 + 提高压缩可显著缩小体积。

## 8. 复现

对比脚本（调查产物，位于 `tools/`）：

```bash
# 结构对比（组/通道/信号名/单位/类型）——原 compare_mdf.py 已并入 mdf_compare 的 structure 维度：
conda run -n blfmdf python tools/full_compare.py outputs/20260805_115505.mdf inputs/mdf/_T058.mdf \
    --stats-ref-block 22 --stats-ref-idx "StdData:4,StdDataRate:5,ExtData:6,ExtDataRate:7,StdRemote:8,StdRemoteRate:9,ExtRemote:10,ExtRemoteRate:11,ErrorFrames:12,ErrorFrameRate:13" \
    --skip-header --skip-values --skip-stats
# 信号级全量对比（组匹配 + 采样数 + 时间范围 + 抽样数值）
conda run -n blfmdf python tools/full_compare.py outputs/20260805_115505.mdf inputs/mdf/_T058.mdf
# 金标准回归（信号覆盖 ≥90% + 抽样数值 + 时长）
conda run -n blfmdf python -m pytest -m golden
```
