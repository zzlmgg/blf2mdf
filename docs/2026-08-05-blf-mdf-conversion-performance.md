# BLF→MDF 转换性能分析报告与提速方案

日期：2026-08-05
状态：分析完成；**方案 A ✅ 已完成（实测 245.1s → 130.2s，1.88×）、方案 B ✅ 已完成（聚合 17.7s → 0.56s）、方案 C ✅ 已完成（实测 130.2s → 37.8s，解码阶段 74s → ≤12.7s）**；方案 D 已并入方案 C（feed 键查预检），E-G 待实施（见 §4 状态与 §6 执行记录）
范围：转换慢的根因调查 + 保证数据质量前提下的提速方案

## 1. 目标与范围

目标：定位 BLF→MDF 转换（`core/converter.py` 管线）耗时根因，给出**不改变输出数据质量**的提速方案。

根因结论基于真实输入的三次完整转换计时 + 独立微基准（测量脚本位于系统临时目录，已清理）；方案 A 已于 2026-08-05 实施并验证（见 §6）。

| 项 | 结论 |
|---|---|
| 完整转换耗时 | **245~252s**（约 4 分钟，三次运行一致） |
| 瓶颈归属 | 全文件扫描 ×11（58%）→ 逐帧解码（30%）→ 统计聚合（8%）→ 写出（3%） |
| 数据质量影响 | 各方案均为零或可对拍验证（见 §4） |

---

## 2. 实测证据

### 2.1 输入规模

| 指标 | 值 |
|---|---|
| BLF 文件 | `inputs/blf/ACFCANPUB_20260722_104000_...blf`，35MB |
| 帧数 | **2,377,398**（12 通道有数据：0,1,2,3,6,8,9,10,11,12,13,14） |
| DBC 绑定 | 10 个（与 GUI 默认一致：ch1,3,6,8,9,10,11,12,13,15） |
| python-can 全文件单遍解析 | **~13-15s**（首次冷扫描 15.3s，后续 ~13s；约 15.5 万帧/秒） |

### 2.2 阶段耗时分布（第三次完整转换，245.1s，wall≈cpu 全程无 I/O 等待）

| 阶段 | 耗时 | 占比 | 构成 |
|---|---|---|---|
| 10 个绑定通道解码阶段（合计） | 203.7s | 83% | 10 次全文件扫描 ~130s + 逐帧解码 ~74s |
| 总线统计收集（合计） | 33.6s | 14% | 1 次全文件扫描 12.6s + 聚合 ~20s |
| MDF 写出（append + save + 压缩） | 7.8s | **3%** | 含 160 个统计小组合并压缩 |
| 其他（DBC 加载 10 个共 7.3s 等） | <1s | <1% | 单次会话一次性开销 |

单通道明细（解码阶段耗时 = 通道总耗时 − 单遍扫描基线 ~13s）：

| 通道 | 总耗时 | 解码估计 | 帧数（解码/未知） | 单帧解码成本 |
|---|---|---|---|---|
| CAN1 | 17.2s | ~4.6s | 143,997 / 111,059 | ~18µs（含未知帧解码尝试+异常） |
| CAN3 | 54.1s | **~41.5s** | 433,754 / 64,150 | **~83µs** |
| CAN6 | 29.0s | ~16.4s | 251,205 / 67,362 | ~52µs |
| CAN8 | 17.3s | ~4.7s | 88,468 / 175,601 | ~18µs |
| CAN9-13、15 | 13.3-16.5s | ~0.6-3.9s | 合计 ~30 万帧 | — |

独立验证：stats 聚合用真实通道规模合成数据实测 **42.4s**（真实数据掩码较稀疏，实际 ~20s）。

### 2.3 关键事实

- 三次运行分布一致（CAN3 为最大解码热点，54.1/61.0/43.9s）；
- 「完成」回调后归因余量 = 0s（POST-RETURN 实测 0.0s），**无隐藏开销**；
- 压缩后端为 C 扩展 `deflate`（非 stdlib zlib），`COMPRESSION_LEVEL=9` 不是瓶颈；
- `read_start_time`（[core/blf_reader.py:44](core/blf_reader.py#L44)）只读文件头，开销可忽略。

---

## 3. 根因分析

### 根因 1（58%）：结构性多次全文件扫描 ✅ 已由方案 A 修复（2026-08-05）

旧实现（方案 A 前）：[converter.py:112](core/converter.py#L112) 对每个绑定通道调用 `blf_reader.iter_messages(blf_path, ch)`。python-can 的 `BLFReader` 无索引、纯 Python 逐对象解析（实测 ~15.5 万帧/秒），**每次调用都从头解析全部 238 万帧**再按通道过滤。10 个绑定通道 = 10 遍；`scan_channels` 再扫第 11 遍；GUI 启动时 `list_channels`（[gui/main_window.py](gui/main_window.py)）还要第 12 遍（~13s，属会话一次性成本）。

→ 全文件被纯 Python 解析器从头解析了 11 遍，约 **143s（58%）**。现实现为 [converter.py:129](core/converter.py#L129) 的单遍扫描（11 遍 → 1 遍），扫描成本已并入转换总量 ~13s。

### 根因 2（30%）：逐帧 cantools 解码 ✅ 已由方案 C 修复（向量化，2026-08-06）；方案 D 并入

[decoder.py:79](core/decoder.py#L79) 每帧调用 `cantools.decode_message`（纯 Python 位运算），按信号逐个取位。成本与报文信号数成正比：CAN3（VDCCCU_CANFD2.dbc，报文信号极多）单帧 ~83µs，49.8 万帧 → 41.5s；CAN6 ~52µs → 16.4s。

附加损耗：对未知 ID 帧**先尝试解码再捕获异常**（CAN1 有 11.1 万未知帧，白付解码+异常开销）；键查 `dbc.messages.get(arb)` 在解码之后。

### 根因 3（8%）：统计聚合 O(W×N) ⬜ 待方案 B（searchsorted）

[core/stats.py:98-100](core/stats.py#L98-L100) 对每个窗界 b 做 `np.sum(ts[mask] < b)` **全量线性扫描**：601 个窗界 × 10 项 × 16 通道。本可用 `np.searchsorted`（O(log N)）却写成 O(N) 逐界扫描。合成数据同规模实测 42.4s。

### 非瓶颈项（排除）

- MDF 写出仅 7.8s（3%）：压缩等级 9 用的是 C 扩展 `deflate`，影响极小；
- asammdf `append` 未传 `common_timebase` 导致组内 O(N) 时间基比较：1-3s 量级，小；
- DBC 加载 10 个共 7.3s、BLF 文件头读取可忽略。

---

## 4. 提速方案（按收益排序，均保证数据质量）

| # | 方案 | 状态 | 预期节省 | 质量影响 | 验证手段 |
|---|---|---|---|---|---|
| A | 单次全文件扫描（合并 11 遍为 1 遍） | ✅ 已完成 | 实测 115s | 零（已对拍） | 见 §6 执行记录 |
| B | stats 聚合改 `np.searchsorted` | ✅ 已完成 | 实测 17.1s | 零（已对拍） | 见 §6 执行记录 |
| C | 解码向量化（numpy 批量位提取） | ✅ 已完成 | 实测 9.8s（解码 finish） | 零（已对拍） | 见 §6 执行记录 |
| D | 未知 ID 预检（跳过无效解码+异常） | ✅ 已并入方案C（feed 键查预检） | ~0（并入） | 零 | 见 §6 执行记录 |
| E | `append` 传 `common_timebase=True` | ✅ 已完成（实测收益 ≈0，见 §6） | 原估 1-3s | 零（已对拍） | 见 §6 执行记录 |
| F | GUI 启动：`list_channels` 缓存/后台化 | ⬜ 待实施 | 启动 ~13s → ~0 | 零 | 手动 |
| G | 可选：多进程并行解码 | ✅ 已完成（per-bucket 变体，实测净省 ~10.8s，见 §6） | 原估 30-40s（基于方案C前基线，已过时） | 零（已对拍） | 见 §6 执行记录 |

### 方案 A：单次全文件扫描 ✅ 已完成（2026-08-05，执行记录见 §6）

**现状（实施前）**：[core/converter.py:112](core/converter.py#L112) 逐通道 `iter_messages`（每遍全文件）+ [core/converter.py:162](core/converter.py#L162) 统计再扫一遍 + GUI 启动 `list_channels` 再扫一遍。

**方案（已实施）**：`convert()` 内一次 `BLFReader` 遍历：按通道路由帧到解码器与统计收集器（ts/ext/remote/err），解码逻辑不变（逐帧 `decode_message` 调用与实施前完全一致，仅顺序从「按通道分组」变为「单遍流式」）；`scan_channels` 并入同一遍。

**质量影响**：零（已对拍验证，见 §6）。

**风险/注意**：内存从「逐通道流式」变为「全部通道桶同时驻留」（实测峰值 1.2GB，35MB BLF 无压力；超大文件可退化回按通道两遍或分块）；进度语义从「按通道」变为「单遍+解码」两阶段，GUI 显示需微调。

**验收（已完成）**：全量 pytest 55/55 通过（含金标准对 CANoe 参考验证）；新旧输出逐组全量对比 265 组结构/顺序/值全部一致；实测耗时 245.1s → 130.2s（1.88×）。

### 方案 B：stats 聚合改 searchsorted ✅ 已完成（2026-08-05，执行记录见 §6）

**现状**：[core/stats.py:98-100](core/stats.py#L98-L100) 每窗界一次 `np.sum(ts[mask] < b)`，O(W×N)。

**方案**：`cum = np.searchsorted(ts_sorted, c_bound, side="left")`（`sum(x < b)` ≡ `searchsorted(x, b, 'left')`）。BLF 帧按时间序写入（单调）；先断言单调，乱序文件排序后同样成立（结果等价）。

**质量影响**：零——同样的严格 `<` 语义。

**验收（已完成）**：现成 `tools/full_compare.py` 的 160 组 × 601 点逐点对比直接验证；耗时 33.6s → ~13s（仅剩扫描）。

### 方案 C：解码向量化 ✅ 已完成（2026-08-06，执行记录见 §6）

**现状（实施前）**：[core/decoder.py:67](core/decoder.py#L67) 每帧一次纯 Python 位提取。

**方案（已实施）**：按 (通道, 报文 ID) 分桶后，将帧数据收集为 `(N, L)` uint8 数组（参考 [core/converter.py:61](core/converter.py#L61) `_collect_raw` 既有模式），用 numpy 位运算批量提取每个信号（start_bit/length/字节序与 cantools 一致的小端位约定），scale/offset、choices 映射全部向量化；mux 信号按切换值分桶后向量化；无法向量化的报文（选择信号带变换/choices 的 mux）回退逐帧 cantools 解码。feed 仅按桶收集原始字节，未知 ID 键查即预检（方案 D 顺带完成）。

**风险**：DBC 位序约定、符号扩展、浮点、mux 边界为易错点。**必须**先与优化前输出做全量 diff（`tools/full_compare.py` 已具备逐点对比能力）再上线；36 个测试作回归基线。

**验收（已完成）**：属性对拍零差异（随机 30 轮 + 13 真实 DBC × 5 轮 + mux 40 轮）；全量 pytest 82/82 通过（含 golden 2 项）；`tools/full_compare.py` 160 组 × 601 点逐点全部一致；实测解码阶段 74s → **≤12.7s**（≥5.8×，验收口径 ≤15s 达标；其中向量化 finish 9.8s = 7.5×），总耗时 130.2s → **37.8s**（3.4×）；CAN3 解码 finish 实测 4.62s。

### 方案 D：未知 ID 预检 ✅ 已并入方案C（feed 键查预检）

**现状**：[core/decoder.py:68](core/decoder.py#L68) 未知帧先 `decode_message` 抛异常再计数。

**方案（已并入方案C）**：在解码前先查 `arb in dbc.messages`，未知直接计 unknown 跳过（现代码 `dbc.messages.get(arb)` 键查本就在解码之后，可前移）。方案C 的 feed 重写已将其前移为桶键查预检（见 §6 方案C 执行记录）。

**质量影响**：零——分类结果与现在完全一致。

### 方案 E：append 传 common_timebase ✅ 已完成（2026-08-06，执行记录见 §6）

**现状**：[core/mdf_writer.py:76](core/mdf_writer.py#L76) 组内信号共享同一时间戳数组对象（构造保证），但 asammdf 默认逐信号 O(N) `array_equal` 比较。

**方案（已实施）**：`mdf.append(signals, acq_name=..., common_timebase=True)`（信号组与 Raw 组两处；统计组单信号无比较不加）。

**质量影响**：零——组内时间基按构造即为同一数组（已对拍验证）。

**注意**：实测收益与初估 1-3s 不符（写 MDF 阶段 8.20s → 8.36s，噪声范围内，收益 ≈ 0）——160 个统计组各单信号无比较、解码组比较合计仅数毫秒；保留为防御性清理（杜绝误入 unique+interp 插值路径）。

### 方案 F：GUI 启动优化

`list_channels` 全文件扫描（~13s）可缓存复用/后台线程；10 个 DBC 解析（7.3s）可推迟到需要时。属会话一次性成本，不影响转换本体。

### 方案 G（可选）：多进程并行解码 ✅ 已完成（2026-08-06，per-bucket 变体，执行记录见 §6）

各通道解码相互独立：单遍扫描后按（通道, 桶）分发到多进程（per-bucket 变体——per-channel 原型实测净省仅 1.71s，关键路径被 CAN3 单通道任务链路锁死；改按桶后 47 桶摊到 8 worker，最大单桶 6 万帧 ≈0.8s 解码）。**原估 30-40s 基于方案 C 前基线（解码 74s），已过时**；实施后实测总耗时 46.43s → 35.67s（净省 ~10.8s），详实方案见 `docs/2026-08-06-blf-mdf-parallel-decode-plan.md`。

---

## 5. 预期收益

| 落地组合 | 预期耗时 | 提速 |
|---|---|---|
| 现状 | 245-252s | 1× |
| A（已实施，实测） | **130.2s** | **1.88×** |
| A + B + D + E（低风险项） | ~110s | ~2.2× |
| A + B + C + D + E（含向量化） | ~50-70s | ~3.5-5× |
| A + B + C（已实施，实测） | **37.8s** | **6.5×（vs 现状）/ 3.4×（vs 方案A）** |

实施顺序建议：A → B → D/E（独立小项）→ C（需对拍）。每项独立可验收、可回退。

## 6. 执行记录

### 方案 A：单次全文件扫描 ✅ 已完成（2026-08-05）

- **涉及文件**：
  - `core/decoder.py`：新增 `ChannelDecoder`（feed/finish 增量式解码器，逐帧语义与 `decode_channel` 完全一致）；`decode_channel` 保留为流式包装，接口不变；
  - `core/blf_reader.py`：新增 `iter_all_messages`（单遍全量帧流）；`scan_channels`/`iter_messages`/`list_channels` 保留（API 兼容）；
  - `core/converter.py`：`convert` 改为单遍扫描——一次遍历路由帧到各通道解码器与统计收集器，删除逐通道 `iter_messages` 与 `scan_channels` 的重复全文件扫描（11 遍 → 1 遍）；
  - `tests/test_blf_reader.py`：新增 `test_iter_all_messages_matches_iter_messages`（单遍流 = 各通道过滤流之和）。
- **实测耗时**：245.1s → **130.2s**（1.88×，节省 115s）；阶段分布：单遍读取+全部解码 89.6s（含 1 遍扫描 ~13s）、收尾 numpy 转换 ~15s、统计聚合 17.4s、MDF 写出 7.8s。
- **质量验证**：
  - 新旧输出逐组全量对比：265 组结构/顺序一致，全部通道值一致（含 nan 语义），header start_time 一致；
  - 全量 pytest **55/55 通过**（基线 54 + 新增单遍流测试；含金标准对 CANoe 参考 _T058.mdf 的覆盖率与数值验证）；全套耗时 371s → 248s（金标准转换随之提速）。
- **内存**：峰值 1.2GB（全部通道解码桶同时驻留，旧流程逐通道峰值更低）——35MB 量级无压力；超大文件需分块或退化为两遍。
- **后续**：方案 B（stats searchsorted，~17s）、方案 C（解码向量化，~74s 中大部分）为剩余大头；D/E 小项随时可做。

### 方案 B：stats 聚合改 searchsorted ✅ 已完成（2026-08-05）

- **涉及文件**：
  - `core/stats.py`：逐窗界全量扫描（`np.sum(ts[mask] < b)`，O(N)/界）→ `np.searchsorted(tsm, bound, side="left")`（O(log N)/界）；入口断言 ts 单调（正常 BLF 时间序，跳过排序），乱序输入对**掩码取值后**的 tsm 排序兜底——注意排序只能作用于掩码应用后的元素集，绝不能重排整 ts（会破坏掩码-帧对应，回归测试已锁定）；
  - `tests/test_stats.py`：新增 4 项回归——`test_searchsorted_matches_reference_random`（随机数据 vs 旧 O(W×N) 参考实现逐点逐位对比，覆盖空/单帧/重复时间戳/混合分类）、`test_unsorted_timestamps_equivalent`、`test_unsorted_mixed_classes_mask_alignment`（锁定掩码对齐 bug）、`test_duplicate_timestamps_strict_less`（严格 < 语义）。
- **实测耗时**：16 通道聚合 **17.67s → 0.56s**（31.6×，节省 ~17.1s，与预期 ~20s 吻合）；统计阶段仅剩单遍扫描（本机冷缓存 ~23.7s）。
- **质量验证**：
  - 全量 pytest **59/59 通过**（基线 55 + 新增 4；含 golden 全量转换与 CANoe 参考验证）；
  - `tools/full_compare.py` 对拍（golden.mdf vs _T058.mdf）：**160 组 × 601 点逐点全部一致**，103 组解码组结构/顺序/值、dtype、文本枚举抽查全部一致。
- **实施中发现并修复的隐患**：初版实现将排序错误地作用于整 ts（未同步重排分类掩码），乱序输入下会错配掩码-帧（错误帧被计入 ExtRemote）；随机等价性测试当场捕获。修复为掩码应用后对 tsm 排序。真实 BLF 单调故不受影响，但乱序文件曾会产生错误输出——现已回归锁定。

### 方案 E：append 传 common_timebase ✅ 已完成（2026-08-06）

- **涉及文件**：`core/mdf_writer.py` 信号组/Raw 组两处 append 加 `common_timebase=True`（统计组单信号不加）；`tools/compare_two_mdf.py` 新增自产互比逐组对拍工具（full_compare 面向自产 vs CANoe 结构，不适用自产互比）。
- **前置确认**：asammdf 8.8.22 `MDF4.append` 源码——默认对 `signals[1:]` 逐信号 `array_equal`、不同则 `unique+interp`；`True` 直接取 `t = t_`。本项目各组时间戳为同一数组对象，默认路径不触发插值 → 输出逐位一致。
- **实测收益**：写 MDF 8.20s → 8.36s（噪声范围内，**收益 ≈ 0**，与初估 1-3s 不符；大头是组构造 + deflate 压缩）；保留为防御性清理。
- **质量验证**：常规 pytest 80/80、全量含 golden 82/82 通过（golden 为 E 后输出 vs CANoe 参考逐点验证）；`tools/compare_two_mdf.py` 对拍 E 前后输出 **265 组全部一致**；E 后基线 46.43s（分布与 E 前一致，对方案 G 评估无影响）。

### 方案C：解码向量化 + 0x7DF 溢出截断 ✅ 已完成（2026-08-06）

- **涉及文件**：
  - `core/decoder.py`：`feed` 改为按 (通道, 报文 ID) 分桶收集原始字节（未知 ID 键查即预检，方案 D 顺带完成）；`finish` 新增向量化路径 `_extract_bits`/`_sign_extend`/`_extract_signal`（大端/小端/跨界/符号/浮点/>64 位截断）、`_bucket_data_array`/`_choices_lookup`/`_signal_kind_vec`/`_physical`/`_text_array`/`_store_signal`/`_mux_plan`/`_finish_bucket_vectorized`；无法向量化的报文（选择信号带变换/choices 的 mux）回退逐帧参考 `_decode_bucket_reference`；新增 `_clamped_int_array`（0x7DF 溢出截断）；`ChannelDecoder.feed()/finish()` 接口不变（converter 零改动）；
  - `core/dbc_loader.py`：`SignalDef` 扩展 `byte_order`/`is_multiplexer`/`multiplexer_ids` 三个字段（cantools Signal 元数据透传，带默认值不破坏既有构造点）；
  - `tests/test_decoder_vectorized.py`：新建（位提取单元 + 逐帧参考实现常驻 + 属性对拍 + 真实 DBC 对拍 + mux 对拍 + 溢出回归）；
  - `tests/test_dbc_loader.py`：新增 `test_signal_metadata_fields`。
- **实测耗时**（10 绑定 = golden BINDING ch 1,2,3,6,8,9,10,11,12,13；样例 BLF 35MB / 2,377,398 帧）：
  - **总耗时 130.2s → 37.8s**（相对方案A 3.4×，相对现状 245.1s 6.5×）；
  - 阶段分布：单遍读取+feed+统计收集 19.98s（纯 BLF 解析 17.10s，feed 帧路由+统计收集 ≈2.9s）、解码 finish 合计 **9.84s**（CAN3 4.62s / CAN6 2.67s / CAN1 0.71s / CAN8 0.63s / 其余 <0.4s）、统计聚合 0.59s、MDF 写出 7.13s；
  - **解码阶段（feed+finish）≤12.7s（上界），相对方案A基线 ~74s ≥5.8× 加速（验收口径 ≤15s 达标）**；向量化 finish 本身 9.84s = 7.5×；
  - 时长 600.0s（参考文件 10 分钟）；各通道 decoded/unknown 帧数与方案B 基线完全一致（如 CAN3 433,754/64,150、CAN6 251,205/67,362）。
- **质量验证**：
  - 属性对拍：随机信号 DBC × 随机帧 30 轮、13 个真实 DBC × 5 轮、mux 随机帧 40 轮——dtype/值/nan/未知帧计数/unknown_ids 全部零差异；
  - 全量 pytest：常规套件（不含 golden）**80/80 通过**（37.35s）；全量含 golden **82/82 通过**（122.47s）；golden 2 项 **2/2 通过**（86.47s）；
  - `tools/full_compare.py` 对拍（golden.mdf vs CANoe `_T058.mdf`）：**160 组 × 601 点逐点全部一致**；103 组匹配组组名/结构/顺序一致，dtype 抽查 60 信号、枚举文本 10 信号 × 200 点共 2000 点、数值 8 信号 × 200 点全部一致；自产侧 2 组未匹配（CCU_VCU_7/CCU_VCU_4，与 CANoe 信号命名差异的既有已知项，参考侧 0 组未匹配）。
- **新增测试清单**：`tests/test_decoder_vectorized.py` 22 项——`test_extract_bits_random_matches_bit_reference[big_endian/little_endian]`（随机逐位对拍 ×200×6 数据规模）、`test_extract_signal_signed_and_float`、`test_extract_signal_truncates_over_64_bits`、`test_vectorized_matches_reference_random`（30 轮）、`test_vectorized_matches_reference_real_dbc`（13 个真实 DBC × 5 轮）、`test_vectorized_mux_matches_reference_random`（40 轮）、`test_overflow_512bit_signal_truncates`/`test_overflow_signed_64bit_wraps`/`test_overflow_512bit_little_endian_truncates`；`tests/test_dbc_loader.py` 1 项（`test_signal_metadata_fields`）。
- **0x7DF 修复说明**：512 位信号（`Fun_Diag_Request` 等 12 个）值 >2^64−1 时旧实现 `np.asarray([...], dtype=uint64)` 抛 `OverflowError` 崩溃（样例 BLF 无 0x7DF 帧，属潜伏 bug）。按用户决策顺带修复：**按模截断存储低 64 位**（MDF 整型通道最大 64 位），有符号按补码回绕；向量化路径（提取低 64 位）与参考路径（`_clamped_int_array` 按模截断）输出一致，3 项溢出回归锁定。
- **关键偏离（对计划草稿的修正，均以参考对拍为准）**：
  1. 大端位提取用 `data64.view(">u8")` 反转字节组内位序（位 p = 字 p//64 的位 63−p%64），大端/小端统一位流空间后再提取；
  2. mux 未知帧计数：feed 对已知 ID **短帧直接计未知、不入桶**（短帧入桶会使该报文系列位置前移、与逐帧参考的系列顺序不符，真实 DBC 对拍失败后修正；finish 的 valid 掩码保留为防御）；finish 对**无子组的 mux 值帧**计未知（`decodable &= ~bad`），unknown_ids 按桶只加一次（set 去重后与参考等价）；
  3. `_msg_vectorizable` 支持面判定保留：无 mux、或选择信号为 (1,0) 无 choices 的单级 mux 走向量化，其余（选择信号带变换/choices 的 mux）回退逐帧参考；
  4. `BIG_DBC` 测试夹具修正为 `7|512@0`/`7|64@0`（大端 `@0` 的 start bit 0 实际在位流位置 7（sawtooth），只有 start_bit=7 才是位流起点 0）；
  5. 随机信号 DBC 生成器三处修正：`if not free` 的 ndarray 多元素 bool 歧义（ValueError）、len_max 恒为 0（`rng.integers(1,1)` ValueError）、占用位集必须按 cantools 校验空间标记且**字节序标记与 cantools 内部语义相反**（@1 = little_endian、@0 = big_endian，cantools 42.0.3 `dbc.py:1599` 实测）；
  6. `_physical` 守卫取 max(|min|, |max|)（全负大值 raw 时 |min| 才是上界，旧守卫低估会恒走快路径差 4 ulp）；`_choices_lookup` 键按符号取 int64/uint64 与 raw 同 dtype 空间（防跨空间经 float64 提升 ≥2^53 判等错配）；`_text_array` 定宽按 **UTF-8 字节数**（按字符数定宽会对非 ASCII 文本（GBK 中文）截断）；
  7. >64 位信号截断位序：大端取位流**末 64 位**（`pos += length−64`）、小端从 pos 起 64 位（pos 不动），且仅 length>64 时调整（length≤64 时偏移为负会错取字边界）；
  8. `_decode_bucket_reference` 用归一化键（含 EFF 位）调用 `decode_message`，与 feed/参考实现键契约一致；
  9. 性能脚本：brief 的 `from tests.test_golden import BINDING` 因 test_golden 内部 `from conftest import ...`（conftest 非顶层模块）ImportError，改为 `sys.path.insert(0, "tests")` 后从 `test_golden` 导入**同一 BINDING 常量**；另叠加 `progress_cb` 阶段计时（回调 14 次，开销可忽略）以得到分阶段实测；golden BINDING 与 GUI 默认（ch15=VDCPublic_CANFD2）相比用 CAN2=VDCCIDC_CANFD（样例 BLF 有数据通道 0,1,2,3,6,8,9,10,11,12,13,14，CAN2 有数据而 CAN15 无），10 个绑定均为有数据通道。
