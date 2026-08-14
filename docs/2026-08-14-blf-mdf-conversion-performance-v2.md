# BLF→MDF 转换性能分析报告（第二轮）与提速方案

日期：2026-08-14
状态：**分析完成（全部实测取证）；所有提速方案待实施，未改动任何代码**
范围：AHT 82MB 样例转换速度根因调查 + 保证数据质量（输出逐位不变）前提下的提速方案
依据：`docs/2026-08-05-blf-mdf-conversion-performance.md`（第一轮，方案 A-G 已全部落地）

## 1. 目标与测试口径

用户实测流程（以 `inputs/blf/AHT_ACFCANPUB_20260317_210430_59125089-ACFCAN_20260317_210930_59125099.blf` 为例）：

1. 拖拽 BLF 到「输入BLF」→ 计时到输入完成（通道探测扫描）；
2. 项目选择 AHT（读入 10 个 DBC）；
3. 点「开始转换」→ 计时停留在「读入 BLF」阶段的时长，弹「转换完成」后统计总耗时。

本轮复现环境与口径：

| 项 | 值 |
|---|---|
| Python / 依赖 | 3.13.9（anaconda3）；asammdf 8.8.23 / numpy 2.3.5 / python-can 4.6.1 / cantools 42.0.3 |
| 机器 | Windows 10，8 核 |
| 样例 BLF | **82.2MB**（86,167,406 B），**5,650,848 帧**，58 个日志容器，13 个有数据通道（0,1,2,3,6,8,9,10,11,12,13,14,15） |
| 绑定 | AHT 项目 10 个 DBC（与 GUI/dbc_对应关系.txt 一致：1,3,6,8,9,10,11,12,13,15） |
| 转换参数 | 与 GUI 完全相同：`parallel=True`（方案 G 并行解码）、`raw_export=False`、`stats_export=True` |
| 输出 | 12.9MB（CANoe 参考 `inputs/mdf_canoe/AHT.mdf` 为 10.3MB） |

实测脚本位于系统临时目录（未进仓库，见 §7）：`%TEMP%\blf_bench.py`（全流程计时）、`%TEMP%\blf_walk_prof.py`（读入阶段内部拆解）。

## 2. 实测证据

### 2.1 用户感知路径计时（2026-08-14 实测）

| 步骤 | 实测 | 说明 |
|---|---|---|
| 拖入 BLF 输入完成 | **8.55s** | `blf_reader.probe_channels` 对象头级行走（后台线程） |
| 选择 AHT 项目 | 1.16s | 10 个 DBC 解析，会话一次性 |
| 转换总耗时 | **62.01s** | 点转换 → 弹完成（GUI worker 线程同款调用） |

### 2.2 转换各阶段耗时分布（62.01s，`result.timings` 实测）

| 阶段 | 耗时 | 占比 | 构成 |
|---|---|---|---|
| 读入 BLF | **38.28s** | **62%** | 单遍解析 565 万帧 + 逐帧路由（feed 入桶 + 统计收集） |
| 解码墙钟（并行 8 进程） | **10.53s** | **17%** | 桶 pickle 提交 + 8 worker 解码 + 结果回收 |
| 统计聚合 | 1.84s | 3% | 16 通道 searchsorted 聚合（含 list→数组转换） |
| 写 MDF | 8.18s | 13% | asammdf 组构造 + deflate-9 压缩（输出 12.9MB） |
| 其他（未归账缺口） | ~3.2s | 5% | 进程池 spawn 预热 ~1.7s（见 §3.4）+ 系列组装/时间轴对齐/收尾 |

每通道解码累计工作量（worker 内实测，互相重叠，和可大于墙钟）：

| 通道 | CAN1 | CAN3 | CAN6 | CAN8 | CAN9 | CAN10 | CAN11 | CAN12 | CAN13 | CAN15 | 合计 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 工作量 s | 5.27 | 7.89 | 11.16 | 1.29 | 0.55 | 0.34 | 1.51 | 0.05 | 0.43 | 4.50 | **33.0** |

8 worker 理想摊分 ≈ 33.0/8 ≈ 4.1s，实际墙钟 10.53s → **约 6s 花在桶 pickle 序列化/传输/回收**（35MB 样例已实测传输 90.4MB，本文件按帧数比例 ≈ 215MB）。

### 2.3 读入 BLF 38.3s 的内部构成（独立剖析脚本实测）

对同一文件单独计时：全量 `_iter_frames`（含帧构造）**22.98s**；纯容器解压与纯对象头行走分别计时；帧数 5,650,848、对象数 5,791,476（含非报文对象）、容器 58 个。

| 构成 | 耗时 | 占比 | 性质 |
|---|---|---|---|
| zlib 解压全部容器 | 1.46s | 4% | C 扩展，已最优 |
| 对象头行走（逐对象找 LOBJ、解头） | 5.89s | 15% | 纯 Python 循环 |
| 帧构造 + 生成器产出 | ~15.6s | 41% | 纯 Python（= 22.98 − 1.46 − 5.89，含 yield 开销） |
| convert 循环逐帧路由（feed 入桶 + 统计 4 列表 append ×2 处） | **~15.3s** | **40%** | 纯 Python（= 读入 38.28 − 22.98，两次独立运行差值推导，±10% 量级估计） |

单帧总成本 ≈ 38.28s / 565 万帧 ≈ **6.8µs**。

### 2.4 与第一轮历史的衔接（瓶颈已转移）

| 里程碑 | 总耗时 | 手段 |
|---|---|---|
| 原始实现 | 245-252s | 11 遍全文件扫描 + 逐帧 cantools 解码 + O(W×N) 统计 |
| 方案 A+B | 130.2s | 单遍扫描、统计 searchsorted |
| 方案 C（解码向量化） | 37.8s | numpy 批量位提取（逐位对拍） |
| 方案 G（并行解码） | 35.7-46.4s（35MB 样例） | per-bucket 多进程 finish |
| **本轮基线（82MB 样例）** | **62.0s** | 以上全部已生效 |

**结论：解码已不是瓶颈。当前 62% 的时间花在「读入 BLF」的纯 Python 逐帧解析与逐帧路由上——这正是第一轮报告没有覆盖的阶段。**

## 3. 根因分析

### 根因 1（62%）：BLF 解析与帧路由为纯 Python 逐帧循环

- 解析侧 `blf_reader._iter_frames._walk`（[core/blf_reader.py:257](core/blf_reader.py#L257)）：逐对象 `data.index` 找 LOBJ、两次 `struct.unpack_from`、bytes 切片、`Frame` dataclass 构造，565 万次；
- 路由侧 convert 循环（[core/converter.py:180](core/converter.py#L180)）：每帧 stats_bufs 元组解包 + 4 次 list append + `decoders.get` + feed 内 dict 查 + 2 次 append，565 万次；
- BLF 对象头为**定长结构**、时间戳为**整数 ns 算术**（`float(ms_part + rel_ns) * 1e-9`，与 CANoe 时间轴同构）——全部可在 numpy 数组上批量完成且**逐位保持**，纯 Python 循环不是必须的。

### 根因 2（17%）：并行解码的 IPC 传输开销

桶内存储为 Python `list[bytes]`（每帧一个独立 bytes 对象），pickle 提交 565 万个对象（估算 ~215MB 传输）+ 结果回收 ≈ 6s。改 uint8 数组可坍缩序列化项数（见方案 H2）。

### 根因 3（13%）：写 MDF 为 asammdf 固有成本

组构造（Python）+ deflate-9 压缩（C，对齐 CANoe 体积，修复项 9）。除压缩级别权衡（方案 H5）外无质量零影响的显著空间。

### 根因 4（输入 8.55s）：probe 对象头行走同为纯 Python 逐对象循环

与根因 1 同构（[core/blf_reader.py:110](core/blf_reader.py#L110) `probe_channels._walk`），只解对象头、不构造帧，但仍是逐对象 Python 循环。

### 非瓶颈项（排除）

- zlib 解压 1.46s（C 扩展，不可再省）；
- 统计聚合 1.84s（searchsorted 已落地；H1 后输入直接为数组，可再降）；
- DBC 加载 1.16s（会话一次性，用户不计入转换）。

### 根因 1 之外的补充说明（进程池 spawn，~1.7s）

`make_pool` + 预热在 `t_read` 之前**同步**执行（[core/converter.py:165-179](core/converter.py#L165-L179)）：不在「读入 BLF」阶段计时内，但在 `t_total` 之内——即 62.01 − 58.83 = 3.2s 缺口的一部分。代码注释声称「spawn 成本藏在扫描期」与实际不符：它发生在扫描开始前，延后了读取的启动。

## 4. 提速方案（按收益排序，全部以「输出逐位不变」为前提）

| # | 方案 | 状态 | 预期节省 | 质量影响 | 验证手段（见 §6） |
|---|---|---|---|---|---|
| H1 | BLF 解析 + 帧路由向量化（numpy 批量对象行走） | ⬜ 待实施 | 读入 38.3s → 预估 4-6s | 零（逐位一致） | full_compare + compare_two_mdf + golden + 既有 82 项测试 |
| H2 | 桶存储 bytes → uint8 数组，并行解码 IPC 减负 | ⬜ 待实施（与 H1 配套） | 解码 10.5s → 预估 ~5s | 零 | 同上 + test_parallel_decode |
| H3 | 输入探测向量化 或 (路径,大小,mtime) 缓存 | ⬜ 待实施 | 输入 8.55s → 预估 ~1.5s | 零 | probe vs list 对拍测试 |
| H4 | 统计聚合输入数组化（省 list→np.asarray 转换） | ⬜ 待实施（H1 顺带） | 1.84s → 预估 ~0.5s | 零 | test_stats + full_compare 统计组 |
| H5 | 写 MDF 压缩级别 9→6（可选权衡） | ⬜ 待实施 | 8.2s → 预估 ~6s | **值不变、文件 +1-2%**（12.9MB → ~13.2MB，离 CANoe 10.3MB 更远） | full_compare（值逐点一致） |
| H6 | 进程池 spawn 预热重叠化（真正藏进扫描期或 lazy 化） | ⬜ 待实施 | ~1.7s | 零 | 计时复核 |

### 方案 H1（主攻方向）：BLF 解析 + 帧路由向量化

**现状**：读入 38.3s 中 zlib 仅 1.5s（4%），其余 96% 为纯 Python 逐帧循环（对象头行走 5.9s + 帧构造 15.6s + convert 路由 15.3s）。

**方案**：
- 容器解压后 `np.frombuffer` 一次处理整容器：uint32 视图搜索 `"LOBJ"`（小端 0x4A424F4C）得全部候选对象起点，用「候选数组 + searchsorted」严格复刻现行 `data.index(b"LOBJ", pos, pos+8)` 的 8 字节窗口扫描语义（含假阳性与跨容器尾部规则）；
- 对象头字段用结构化 dtype 批量解出（channel/id/dlc/flags 等，定长类型一次提取；FD64 变长 payload 用索引数组取段）；
- 时间戳保持**整数 ns 算术在 int64 数组上计算**（`ms_part + rel_ns` → `×1e-9`），与 [blf_reader.py](core/blf_reader.py) 模块 docstring 的 CANoe 同构语义逐位一致，不引入任何新的舍入；
- 帧数据直接进 (N,L) uint8 桶数组（替代 list[bytes]，见 H2），统计四列（ts/ext/remote/err）直接由解析列切片，替代逐帧 4 append。

**必须逐条复刻的语义清单（正确性契约）**：
1. 跨容器对象尾部衔接（`_tail` 语义）与容器级 struct.error 兜底；
2. 对象头版本 1/2 的时间单位（flags==1 → rel×10000）与未知版本整体跳过；
3. 四种报文类型（CAN_MESSAGE/CAN_MESSAGE2/CAN_ERROR_EXT/CAN_FD_MESSAGE/CAN_FD_MESSAGE_64）字段偏移、`dlc2len`、FD64 的 `min(valid_bytes, 可用长度)` 截断与 `ljust` 补零（python-can issue:1905 语义）；
4. LOBJ 假阳性扫描窗口语义（见上）；
5. **确定性**：桶插入序 = 报文系列顺序（feed 首次出现序，[decoder.py:402](core/decoder.py#L402) 契约）——向量化分组须按首现序排序；桶内帧保持时间序。

**质量影响**：零——输出与现实现逐位一致。验证链现成（§6），项目内已有两次同套路先例：`probe_channels`（commit aa7a44d，对象头级行走对拍通过）与方案 C 解码向量化（逐位对拍通过）。

**风险**（中等）：语义清单中 4、5 与 FD64 变长项为易错点；工作量约等于当初方案 C。对拍工具链可兜底（任何偏差都会被逐点对比捕获）。

### 方案 H2：桶存储数组化（并行解码 IPC 减负）

**现状**：桶 `{"ts": list[float], "data": list[bytes]}`（[decoder.py:402-406](core/decoder.py#L402-L406)）pickle 565 万个独立对象（估算 ~215MB）。

**方案**：桶改存 `(N,) float64` 时间戳与 `(N,L) uint8` 数据数组（H1 的天然产物）；`_finish_bucket_vectorized` 内的 `np.fromiter`/`_bucket_data_array` 相应简化。pickle 序列化项数从百万级坍缩为每桶一个数组，传输按字节连续复制。

**质量影响**：零——桶内容是同一批字节的另一种容器，finish 的计算路径与输出逐位不变（对拍验证）。

### 方案 H3：输入探测向量化或缓存

**现状**：`probe_channels._walk` 逐对象 Python 循环（82MB 实测 8.55s，其中行走 ~5.9s + zlib ~1.2s + 文件读入）。

**方案**（二选一或叠加）：
- 与 H1 同款向量化行走（只取 channel 字段，不构造帧）；
- 按 (路径, 大小, mtime) 缓存探测结果（GUI 重拖同一文件 / 会话间复用）。

**质量影响**：零——通道集合语义不变（已有 `test_scan_cache_*` 类回归先例与 probe vs list 对拍）。

### 方案 H4：统计聚合输入数组化

H1 落地后 stats_bufs 直接为数组列，`aggregate_channel` 入口省掉 16 通道 × 4 列的 `np.asarray(list)`（[stats.py:92-96](core/stats.py#L92-L96)），统计聚合 1.84s → 预估 ~0.5s。

### 方案 H5（可选权衡，默认不建议）：写 MDF 压缩级别 9→6

deflate-9 是为对齐 CANoe 体积定的（修复项 9）。降级到 6 可省 ~2s，但文件 +1-2%。属质量-速度权衡，是否接受由需求裁决。

### 方案 H6：进程池 spawn 重叠化

现状 spawn 预热 ~1.7s 同步发生在扫描开始前（§3.4）。改为 lazy 提交（首个桶任务触发 spawn）或在读入期间与扫描重叠，总耗时省 ~1.7s，阶段计时语义不变。

## 5. 预期收益

| 落地组合 | 转换总耗时（82MB 样例） | 输入阶段 |
|---|---|---|
| 现状 | **62.0s** | 8.55s |
| H1 + H2 + H4 + H6 | **预估 ~22-28s**（读入 ~5 + 解码 ~5 + 统计 ~0.5 + 写 8.2 + 杂项 ~2） | 8.55s |
| 上组 + H3 | 同上 | **~1.5s** |
| 上组 + H5（可选） | 预估 ~20-25s | ~1.5s |
| 只做 H3-H6 不做 H1 | 预估 ~52s | ~1.5s |

**核心结论：不做 H1，其余方案合计仅省 ~10s；H1 是唯一值得做的大工程（约 2.2-2.8× 总提速），且数据质量有现成对拍工具链逐位兜底。**

实施顺序建议：**H1（含 H2/H4 配套）→ H3 → 可选 H5/H6**，每步独立验收、可回退。

## 6. 质量保证与验证手段（对拍工具链）

| 工具/测试 | 作用 |
|---|---|
| `tools/full_compare.py` | 自产 MDF vs CANoe 参考逐组逐点对比（160 组 × 601 点） |
| `tools/compare_two_mdf.py` | 新旧自产输出互比（优化前后逐位一致验证） |
| `tests/test_golden.py`（golden 3 项）+ 常规 82 项 | 金标准转换 + 数值对 CANoe 验证回归 |
| `tests/test_blf_reader.py` | iter_all_messages 流语义、probe/list 对拍、取消语义 |
| `tests/test_decoder_vectorized.py` | 向量化 vs 逐帧参考逐位对拍（先例） |
| `tests/test_parallel_decode.py` | 并行 vs 串行逐位等价 |

历史先例证明该路线可行：方案 C（解码向量化）与 probe_channels 均为「向量化/轻量化 + 逐位对拍」落地，零质量偏差。

## 7. 实测脚本与复现方式

- `%TEMP%\blf_bench.py`：全流程计时（probe → DBC 加载 → convert(parallel=True) → result.timings），输出写临时目录；
- `%TEMP%\blf_walk_prof.py`：读入阶段拆解（容器枚举 → zlib 全量计时 → 纯对象头行走计时 → 全量 `_iter_frames` 计时）；
- 运行：`& C:\ProgramData\anaconda3\python.exe <脚本>`（PATH 上的 python 是商店空壳，须用 anaconda3）；
- 本轮基准输出 `%TEMP%\AHT_bench.mdf`（12.9MB）供复核，未写入仓库。

**本轮未改动任何代码**：仓库仅新增本文档（`docs/prompts.txt` 的修改为用户原有未提交内容）。

## 8. 后续

待用户指示是否进入 H1 实施（届时按项目惯例：先出 H1 实施计划文档——语义复刻清单、确定性保证、对拍验收步骤——确认后再动代码）。
