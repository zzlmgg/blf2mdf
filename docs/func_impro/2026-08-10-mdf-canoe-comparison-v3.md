# 自产 MDF 与 CANoe 转换结果对比分析报告（v3，A19G1 + AHT 双文件全量对比）

日期：2026-08-10
状态：调查完成 + §5.1 唯一真差异已修复（core/converter.py + core/mdf_writer.py，含回归测试）

## 1. 问题（原始需求）

下面是两组 blf 文件分别用 canoe 和 我们的 main.py 转换的 mdf 数据，仔细对比两组结果，
分析我们的 main.py 转换的结果与 canoe 转换的结果是否完全一致？如果不一致，体现在哪里？

### 组别一（A19G1）

| 来源 | 文件 |
|---|---|
| blf | `inputs/blf/A19G1_ACFCAN_00112_20260614_141114.blf` |
| canoe 转的 mdf | `inputs/mdf/A19G1.mdf` |
| main.py 转的 mdf | `outputs/A19G1_20260810_091651.mdf` |

### 组别二（AHT）

| 来源 | 文件 |
|---|---|
| blf | `inputs/blf/AHT_ACFCANPUB_20260317_210430_59125089-ACFCAN_20260317_210930_59125099.blf` |
| canoe 转的 mdf | `inputs/mdf/AHT.mdf` |
| main.py 转的 mdf | `outputs/AHT_20260810_091751.mdf` |

## 2. 对比方法与环境

- 环境：conda env `blfmdf`（Python 3.12，asammdf 8.8.22，python-can，numpy）
- 方法：**按信号名集合配对组**（不按组序号，因组序两侧不同），全信号全长度逐点对比
  采样值 + 时间戳；统计组按「通道 × 统计项」定位对比；文件头逐字段对比。
- 临时分析脚本（未入仓库，均为只读对比，未改项目代码）：
  - 按名匹配全量对比 + 信号组配对 / 统计 t 轴 / 统计值
  - 时间戳量化与 BLF 原始值探测（can.BLFReader 直读 + 当前代码路径模拟）
  - 统计值逐通道×逐项差异量化 + 结构（组序/t 位置/组内信号序）对比
- 复现命令示例（原 compare_mdf_v2 已并入 `tools/mdf_compare.py` 的 reference 入口，
  2026-08-18 收口；CLI 经 `tools/full_compare.py`）：
  ```bash
  conda run -n blfmdf python tools/full_compare.py <ours.mdf> <ref.mdf> \
      --stats-ref-block 22 --stats-ref-idx "StdData:4,StdDataRate:5,ExtData:6,ExtDataRate:7,StdRemote:8,StdRemoteRate:9,ExtRemote:10,ExtRemoteRate:11,ErrorFrames:12,ErrorFrameRate:13"
  ```
  （注：reference 入口按信号名集合配对组、不按组序号，已修正原 compare_mdf_v2
  按组序号对齐导致的逐点汇总不可用问题，见本文 §5 数据。）

## 3. 结论速览

| 维度 | 组一 A19G1 | 组二 AHT |
|---|---|---|
| **解码信号值**（38/70 组全量逐点） | **零差异**（704 信号，578 万点） | **零差异**（925 信号） |
| **解码信号时间戳** | 差 **+72.98ns**（均值，std 0.29ns，范围 +72.5~+73.5ns） | **逐位一致** |
| **MDF 头部** | 差 624ms（新发现，见 §5.1，**已修复**） | **完全一致** |
| **1s 统计值** | 3841/40960 点不等（集中在 StdData/StdDataRate 忙通道） | 仅 10/40960 点差（均 ≤1e-12 浮点噪声） |
| **1s 统计 t 轴** | 139/256 点不等（末点 +73ns，内部点 ≤1ulp） | **1217/1217 逐位一致** |
| **统计组数量** | CANoe 352（22 项×16ch）vs 我们 160（10 项×16ch） | 同左 |
| **结构/元数据** | 组序、组内信号序、t 通道位置、单位、CN data_type 不同 | 同左 |

**总判定：不完全一致，但信号数据（核心内容）两组均与 CANoe 100% 一致；**
其余差异全部可解释，大部分为已归档的已知项（见 §6、§7），
本次对比新发现的真差异只有 1 处：**A19G1 MDF 头部 abs_time 丢失 624ms 小数秒**（§5.1）。

## 4. 信号数据对比（核心结论）

### 4.1 信号组配对

| 项 | A19G1 | AHT |
|---|---|---|
| CANoe 信号组数 | 38 | 70 |
| 自产信号组数 | 38 | 70 |
| 按信号名集合配对 | 38/38 | 70/70 |
| 信号总数 | 704 | 925 |
| **采样值全等** | **704/704（零差异）** | **925/925（零差异）** |
| **时间戳全等** | **0/704（全部差 ~+73ns）** | **925/925（逐位一致）** |

- 值对比含整型 / float64 / `|Sn` 枚举文本 bytes 全部类型。
- 绑定等价性：两组信号组集合与 CANoe 完全重合（38、70 组同名同信号），
  说明 GUI 按 ccu3.0 项目自动绑定（`inputs/dbc_ccu3.0/dbc_对应关系.txt`）与
  CANoe 的 DBC 绑定等效。

### 4.2 A19G1 时间戳 +73ns 的根因（设计内、不可修）

- BLF 内部时间戳为整数 ns（100ns 刻度），CANoe 读精确整数 ns，做差后 ×1e-9 构造
  t 通道（如 0.628791498 = 628791498 ns ×1e-9）。
- python-can 以 **float64 秒**读出（~1.78e9 量级，ULP ≈ 238ns），
  我们做 float64 大数减法（`fr.ts_seconds - abs_start_epoch`，[core/converter.py:214](core/converter.py#L214)）。
- 实测：起点 1781464020.624 的 float64 表示带 +72.48ns 误差 → 系统偏差 ≈+72.5ns，
  各帧 ±0.5ns 波动，总范围 +72.5~+73.5ns < 119ns 量化界。
- `core/stats.py align_timestamps`（[stats.py:47](core/stats.py#L47)）自适应策略：
  - **ms 网格文件**（AHT）：距 ms 网格最大偏差 < 200ns（`_MS_GRID_TOL=2e-4`）
    → round 到 ms，再按 `(ns 整数)×1e-9` 构造 → 与 CANoe 逐位一致；
  - **任意 ns 精度文件**（A19G1）：BLF 已丢失 ±119ns，不可恢复 → 保留 float64 原值。
- 结论：A19G1 时间戳 +73ns 属信息下限，改代码无法消除
  （除非从 BLF 原始二进制直接读整数 ns 刻度）。

### 4.3 时间基准零点确认

- 通过「CANoe BMS_1 首帧 t=0.628791498（= 628791498 ns）反推 CANoe 零点」验证：
  CANoe t 轴零点 = **整秒** 1781464020.0（= BLF 头 start 1781464020.624 的整数秒），
  与我们的 `int(start)` 零点一致 → t 轴本身无 624ms 偏移；
  624ms 差异只出现在 MDF 头部元数据（§5.1）。

## 5. 文件级差异明细

### 5.1 新发现：MDF 头部 abs_time 截断 624ms（仅 A19G1；唯一可修的真差异）

| 字段 | CANoe | 自产 |
|---|---|---|
| abs_time (ns) | 1781464020624000000（= 1781464020.624 完整保留） | 1781464020000000000（整秒截断） |
| start_time | 2026-06-14 19:07:00.624000 | 2026-06-14 19:07:00 |

- 根因（双重截断）：
  - [core/converter.py:117](core/converter.py#L117) `abs_start_epoch = int(blf_reader.read_start_time(blf_path))`
  - [core/mdf_writer.py:51](core/mdf_writer.py#L51) `datetime.fromtimestamp(int(abs_start_epoch), ...)`
- 此前 _T058 / AHT 的 BLF 头恰为整秒（AHT 头部逐字段一致即因此），从未暴露。
- 影响：下游按头部绝对时刻对齐的文件会差 624ms；数据 t 轴不受影响。
- **已修复（2026-08-10，含回归测试）**：
  - 精度前提：BLF 头 start 是 **SYSTEMTIME（8×uint16，毫秒精度）**，真值即
    1781464020.624 整，无亚毫秒信息；float64 表示误差 ±119ns（实测 +72.48ns）。
  - [core/converter.py](core/converter.py#L117)：读一次全精度 float，拆成两个值——
    t 轴零点 `abs_start_epoch = int(abs_start_time)`（**整秒不动**，CANoe 语义，
    实测首帧 t=0.628791498 反推；若把小数秒并入零点，t 轴会整体偏移 -624ms）；
    全精度 `abs_start_seconds` 传给 write_mdf。
  - [core/mdf_writer.py](core/mdf_writer.py#L44)：`datetime.fromtimestamp(abs_start_seconds)`
    不截断——µs 恢复对 ms 量化值可证明精确（float 误差 ≤±119ns < 0.5µs，
    1000 个毫秒值实测 1000/1000，含 496 个负误差样本）；abs_time 用
    `_abs_time_ns` **纯整数运算**精确写出（不经 float）。
  - 为什么不用报告初拟的 `int(abs_start_epoch * 1e9)` / asammdf 内建
    `int(dt.timestamp()*1e9)`：两者都是 float 路径，1.78e18 ns 量级舍入网格
    256ns，误差界 247~485ns > 半网格——A19G1 实测恰好精确属数值巧合，非通用
    保证；纯整数运算恒精确。
  - 验证：A19G1 真实转换后头部 abs_time/start_time/time_flags/tz_offset/dst
    与 CANoe 参考**逐字段一致**（1781464020624000000 / 19:07:00.624000）；
    t 轴 25443 点逐位不变；AHT 整秒起点无回归。回归测试：
    [tests/test_mdf_writer.py](tests/test_mdf_writer.py)（CANoe 参考值 + 整秒回归 +
    原截断行为测试改写）。

### 5.2 A19G1 统计值差异（预期行为，非 bug）

- 逐通道×逐项对比（CANoe 每通道 22 组、我们每通道 10 组，按项定位）：
  16 通道 × 10 项 × 256 点 = 40960 点，**3841 点不等**。
- 差异**全部集中在 StdData / StdDataRate 的有数据通道**：
  - StdData：ch0 108、ch1 20、ch2 233、ch3 252、ch4 19、ch6 224、ch8 249、
    ch9 209、ch10 240、ch11 252、ch12 145、ch14 237 点不等（合计 2188）；
  - StdDataRate：相应通道 100/3/145/175/1/113/224/229/195/240/119/107/1/1 点（合计 1653）；
  - ch5、ch7 等空通道全等；ExtData / StdRemote / ExtRemote / ErrorFrames 全等。
- 根因（已知，见记忆 canoe-1s-stats-clock-domain 与 2026-08-06 v2 报告 §B）：
  CANoe 1s 统计基于 CANoe 测量引擎**自身时钟域**，与 BLF 记录器时钟存在 ±30ms 级
  逐窗抖动，任何固定窗界（含 9ms 偏置）都无法从 BLF 完全复现；
  A19G1 帧距 2.9ms 恰好压窗界才暴露；AHT 边界不碰撞所以几乎全等。

### 5.3 A19G1 统计 t 轴差异（次要）

- 139/256 点不等，两个误差类：
  - 内部点 ≤1ulp（4.4e-16）：CANoe 用整数 ns×1e-9 构造（如 2.0090000000000003），
    我们非 ms 网格文件走了 float 加法直通（2.0+0.009），个别点差 1 ulp；
  - 末点 +73ns（254.426397489 vs 254.42639756202698）：与信号时间戳同一量化源
    （global_end 全精度保留）。
- AHT 统计 t 轴 1217/1217 逐位一致（ms 网格 → align_timestamps 完全恢复）。

### 5.4 AHT 统计值差异（可忽略）

- 仅 10/40960 点不等，全部为 StdDataRate 且 |d| ≤ 1e-12（读回浮点噪声），
  容差外 = 0；StdData 计数 2560 点全等。

## 6. 两组共有的结构/元数据差异（已归档已知项）

| # | 差异 | 实测数据 | 根因/性质（2026-08-06 v2 报告编号） |
|---|---|---|---|
| 1 | **缺 12 项统计**：统计组 352 vs 160 | Busload×4、ChipState×3、MinSendDist、BurstTime、FramesPerBurst、TransceiverErrors、Bursts | 阶段 1 只实现 10 项；4 项需 CAN 控制器硬件状态，BLF 中不存在，**原理上无法一致**（B1/C） |
| 2 | **统计组位置**：CANoe 在文件头部（G0-G351），我们在尾部 | A19G1 G38-G197；AHT G70-G229 | [mdf_writer.py:103](core/mdf_writer.py#L103) stats 组 append 在信号组之后（A3，可修） |
| 3 | **信号组组序不同** | A19G1 首异 @1（ref=VCU_7 vs ours=VCU_1）；AHT 亦不同 | 我们=BLF 首见序（bucket 插入序），CANoe=内部测量分配序，已排除全部可推导规则（C2，不可修） |
| 4 | **组内信号序部分不同**（按名配对） | A19G1 完全一致 8/38；AHT 29/70 | CANoe=DBC 文件书写序，我们=cantools 按 start_bit 重排序（A1，可修） |
| 5 | **t 通道位置**：我们每组首位（pos 0），CANoe 恒末位 | 两组 38/38、70/70 组全部末位 | [mdf_writer.py:75](core/mdf_writer.py#L75) 主时间通道挂在首个信号上（A2，可修） |
| 6 | **统计组单位/转换元数据**：CANoe unit=total/fr/s/% + CC 块；我们 unit 为空 | — | [mdf_writer.py:104](core/mdf_writer.py#L104) stats 信号未写 unit（可修） |
| 7 | **t 通道 CN data_type**：CANoe 0(UNSIGNED)，我们 4(REAL) | — | asammdf 无法写出声明为 unsigned 的 float 时间通道（C3，无实义） |
| 8 | **头部 UUID/comment**：CANoe 每次导出随机 UUID，我们为空 | — | 本质随机，不存在相同的值（C1，不可修） |

## 7. 最终判定

1. **信号数据（最核心内容）：两组均与 CANoe 100% 一致**——
   AHT 连时间戳都逐位一致（70 组 925 信号）；A19G1 值零差异、时间戳仅 +73ns 量化噪声。
2. **A19G1 独有、可修的真差异只有 1 处**：MDF 头部 abs_time 丢了 624ms 小数秒
   （converter.py + mdf_writer.py 双重 `int()` 截断）——本次对比新发现的唯一代码缺陷，
   _T058/AHT 因整秒起点从未暴露。**已于同日修复并验证**（见 §5.1）。
3. A19G1 时间戳 +73ns：BLF float64 量化的信息下限，**改代码无法消除**
   （除非从 BLF 原始二进制直接读整数 ns 刻度）。
4. 其余差异（12 项缺失统计、组序、组内信号序、t 位置、单位元数据、CN data_type、
   UUID）均为 2026-08-06 v2 报告已归档的已知项；
   其中 4 项统计（ChipState 3 项 + TransceiverErrors）+ 组序 + UUID 属**原理上不可一致**。

## 8. 建议的后续行动（未实施）

| 优先级 | 行动 | 收益 |
|---|---|---|
| 1 | ~~修 MDF 头部 624ms 截断（保留小数秒写入 abs_time；建议加回归测试）~~ **已实施**（2026-08-10，见 §5.1） | 消除本次唯一新发现的真差异 |
| 2（可选） | A1/A2/A3 结构对齐：DBC 信号原始序重排、t 通道移末位、统计组移文件头部 | 结构层更接近 CANoe |
| 3（不推荐） | ±119ns 时间戳与 12 项统计 | 信息下限 / 需硬件状态或逆向 CANoe 算法 |
