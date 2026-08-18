# BLF→MDF 转换性能总计划（方案盘点 + 剩余计划）

日期：2026-08-15
状态：**H7 主线（H7e/H7a/H7b）已实施并验收（见 superpowers/plans/2026-08-16-H7-readin-residual.md）；剩余 H3/H5/H6 待确认后实施**
基准：`docs/2026-08-14-blf-mdf-conversion-performance-v2.md`（方案 A-G 与 H1-H6 的原始定义，状态以本文件为准）
前提：**输出与现实现逐位一致**（compare_two_mdf 逐位、full_compare vs CANoe 与基线同分、pytest 203 项）

**本文件取代以下 4 个零散文档**（内容全部并入本文件，无丢失）：
`2026-08-14-H1-vectorized-parse-plan.md`（H1 计划，已实施；字段规格与实施核对修正见附录 A，该文件因有用户未提交修改暂保留）、
`2026-08-15-blf-mdf-conversion-performance-v3.md`（H1 验收报告，已删除）、
`2026-08-15-H2-bucket-ipc-plan.md`（H2 计划与决策，已删除）、
`2026-08-15-H7-readin-residual-plan.md`（H7 设计，已删除）。

---

## 1. 方案盘点（v2 全部方案 × 当前代码状态）

| # | 方案（v2 定义） | 状态 | 证据 / 说明 |
|---|---|---|---|
| A-G | 第一轮：单遍扫描、统计 searchsorted、解码向量化、并行解码 | ✅ 已实现 | v2 之前全部落地（v2 §2.4 历史表） |
| H1 | BLF 解析 + 帧路由向量化（numpy 批量对象行走） | ✅ **已实现并验收**（commit cfd6fc2） | 62.0s → 27.3s；输出逐位不变（§3.1） |
| H2 | 桶存储 bytes → (N,L) uint8 数组 | ✅ **主体已随 H1 落地** | 解码墙钟 10.53 → 1.49s；提交侧 pickle 565 万对象 → 每桶一个数组 |
| H2a | 桶组装去重分配（追加式 concatenate → 块收集 + 末次装配） | ✅ **已实施、代码未提交**（由用户自行提交） | 装配 1.65s → 0.12s；compare 230 组一致（§3.2） |
| H2b | 结果侧共享内存回传（407MB → 零拷贝） | ❌ **已否决**（2026-08-15 用户决策） | 收益 ~0.5-0.9s 不抵复杂度（§4.1） |
| H2c | 提交侧共享内存（207MB → 零拷贝） | ❌ **已否决**（2026-08-15 用户决策） | 收益仅 ~0.1-0.2s（§4.1） |
| H3 | 输入探测向量化 或 (路径,大小,mtime) 缓存 | ⬜ **待实施** | `probe_channels` 仍是逐对象标量行走（[blf_reader.py:105](core/blf_reader.py#L105)），输入阶段 8.55s（§5.2） |
| H4 | 统计聚合输入数组化 | ✅ **已实现**（随 H1） | 1.84s → 0.31s |
| H5 | 写 MDF 压缩级别 9→6 | ⬜ **待需求裁决** | ~2s 换文件 +1-2%（质量-速度权衡，§5.3） |
| H6 | 进程池 spawn 预热重叠化 | ◐ **部分落地** | warm_up 全量预热修复已随 H1 提交（解码墙钟虚增 ~1.7s 消除）；但池创建仍同步在读入启动前（[converter.py:364](core/converter.py#L364)，`_read_vectorized` 在 :378），spawn ~1.7s 仍占总耗时（§5.4） |
| H7 | 读入残余（载荷双重拷贝 + 字段提取 + 候选扫描） | ✅ **已实施并验收（H7e/H7a/H7b）** | 读入 14.1 → ~9.1s（H7e ~1.6 + H7a ~0.45 + H7b ~3.0）；输出逐位不变（§5.1 验收口径逐项通过） |

**一句话现状**：解码、统计已不是瓶颈；大头依次是读入残余（H7）、输入探测（H3）、写 MDF（H5）、池预热残余（H6）。

## 2. 性能时间线

| 里程碑 | 总耗时（AHT 82MB 样例，parallel=True） | 手段 |
|---|---|---|
| 原始实现 | 245-252s | 11 遍扫描 + 逐帧解码 |
| 方案 A+B | 130.2s | 单遍扫描、searchsorted 统计 |
| 方案 C | 37.8s | 解码向量化 |
| 方案 G | 35.7s | per-bucket 多进程 |
| **v2 基线** | **62.0s** | 读入 38.3 + 解码 10.5 + 写 8.2 + 统计 1.8 + 杂项 ~3.2 |
| H1 后（v3 语境） | **27.3s** | 读入 13.44 + 解码 1.49 + 写 7.73 + 统计 0.31 |
| H2a 后（本机同机对照） | **29.8s** | 读入 16.2 → 13.9s；与 v3 语境的差值为机器差异，同机对照才可比 |
| H7e+a+b 后（预估） | 本机 ~24.7s（v3 语境 ~22s） | 读入 ~9.1s |
| + H7d/H7c（复测后） | 本机 ~23-24s | 读入 ~7.5-8s |

另有用户感知的**输入阶段**（拖入 BLF 到输入完成）：v2 实测 8.55s，H3 目标 ~1.5s（§5.2）。

## 3. 已实现项验收证据（合并自 v3 报告与 H2 计划）

### 3.1 H1（commit cfd6fc2，2026-08-15 验收）

- 转换总耗时 62.01 → **27.34s**（2.27×）；读入 38.28 → 13.44s（未达计划 ≤6s，遗留见 §5.1）；解码墙钟 10.53 → 1.49s；统计 1.84 → 0.31s
- 输出与变更前基线**逐位一致**：`compare_two_mdf` 230 组全部一致 exit 0；`full_compare` vs CANoe 参考 70/70 组一致、160 组 × 601 点统计逐点全部一致、dtype 抽查 60 信号、枚举文本 2000 点
- pytest：203 通过 / 4 跳过 / 4 失败 / 4 错误（失败与错误均为环境性——样例数据缺失，与基线同集）
- 收尾：开发期 A/B 钩子（`BLF_LEGACY_READ`、旧 `_collect_raw`）与剖析探针已删除
- 58 容器 100% 快路径命中（0 回退标量 walk）
- 对象头字段规格见附录 A（含实施核对的三处修正）

### 3.2 H2a（2026-08-15 实施，代码未提交、由用户自行提交）

- 改动：[converter.py](core/converter.py) 桶改块收集 + 新 `_assemble_bucket`（块切片批量拷贝），删除 `_append_bucket_rows`
- 实施期设计纠正（实测）：初版 row/col 散点索引在 1.64 亿位置桶规模上实测 7.6s（比旧追加还慢）→ 改块切片批量拷贝 0.12s。**教训：大数据路径禁用散点索引**
- 验收：装配函数内 1.65 → 0.12s；本机读入墙钟 16.2 → 13.8s；pytest 203 通过（环境性失败与基线同集）；compare_two_mdf 230 组一致 exit 0

### 3.3 H2 残余 IPC 实测（结论：止步于 H2a）

- 双向 IPC 合计 614MB：提交侧 70 桶 pickle 207MB（序列化仅 0.12s）、结果侧回传 407MB（父反序列化 0.59s）
- 解码墙钟 2.05s = worker CPU 理想摊分 0.81s + IPC/调度 ~1.2s
- 三个实测排除项：packed 变长传输（padding 零膨胀，零收益）、md 瘦身（仅 0.14MB）、lens int32（收益 ~0.05s 不值）

## 4. 决策记录（合并自 H2 计划 §7 与 H7 计划 §3.6）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | H2b 结果侧共享内存回传 | **不做**——用户否决：收益 ~0.5-0.9s（约总耗时 2-3%）不抵复杂度 |
| 2 | H2c 提交侧共享内存 | **不做**——用户否决：收益 ~0.1-0.2s |
| 3 | H2a 桶组装去重分配 | **已实施并验收**（§3.2；代码未提交，由用户自行提交） |
| 4 | H5 压缩级别 9→6 | **待需求裁决**（文件 +1-2%，离 CANoe 10.3MB 更远） |
| 5 | 无扫描解析（固定点/二进制倍增） | 不做——s↔obj_size 循环依赖需倍增跳表，H7a 后残余仅 ~0.2s，不值 |
| 6 | C 扩展解析 | 不做——GUI 冻结打包复杂度陡增，收益上限 ~2s，列为后续选项 |
| 7 | 多容器并行解析 | 不做——内存峰值与复杂度高，收益与 H7c 重叠 |

**提交纪律**：所有代码提交由用户自行执行（用户 2026-08-15 声明，本计划不代提交）。

## 5. 剩余计划

### 5.1 读入残余（H7 主线：H7e / H7a / H7b，按 §7 顺序实施）

**实测现状**（2026-08-15 探针 `%TEMP%\h7_read_probe.py`，本机读入 14.1s 拆解）：

| 阶段 | 实测 | 细拆 |
|---|---|---|
| 文件读取 + zlib 解压 | 1.7s | decompress 1.5s（58 容器） |
| 候选扫描 `_candidates` | **0.70s** | 4 遍 u32 比较 0.60 + 连接排序 0.07；候选 5.79M **全部 ≡0 mod 4，0/58 容器错位** |
| 字段提取 `_u32/_u64/_u16/_u8_at` | **2.42s** | 逐字节散点 gather ~3.4 亿次 |
| 容器级载荷打包 | **~3.1s** | np.take 2.8s（362MB 结果 + 1.45GB int32 索引） |
| 状态机掩码 + 发射 gather + 时间戳 | ~2.3s | 掩码链 ~20 个 (N,) 临时、gather ~540MB |
| 桶级打包 `_bucket_block`（二次拷贝） | 1.65s | take 1.2s + 索引 656MB——**同一批载荷拷第二遍（164MB）** |
| 路由 + 统计收集 + 杂项 | ~2.3s | 含每桶 `norm == k` 全扫、16 通道统计掩码 |
| 桶装配 `_assemble_bucket` | 0.12s | H2a 已修 |

**归因修正**：v3 §4 的「候选窗口状态机 2.5s」实为候选扫描 0.7s + 字段提取 2.4s 的一部分；真正的大头是**载荷被拷贝两次**（容器级 362MB + 桶级 164MB，合计 take 4.0s）与**逐字节散点字段提取 2.4s**。

**方案总览**（按收益 × 风险排序）：

| # | 方案 | 预计节省 | 复杂度 | 风险 | 建议 |
|---|---|---|---|---|---|
| H7e | 字段提取对齐视图化（4 次字节 gather → 1 次 u32/u64 视图 gather；错位容器走现路径） | ~1.6s | 低 | 极低（两种读法逐位相同） | **做（先）** |
| H7b | 载荷打包下放桶级（快路径不打包，桶直接自容器字节 gather，每字节只拷一次） | ~3.0s | 中 | 中（契约变更，测试更新 7 处：5 直接断言 + 2 helper） | **做** |
| H7a | 候选扫描单类化（实测 0/58 错位）+ 免排序 + 双探针守卫 | ~0.45s | 中低 | 中低（回退语义变化，测试更新 3 处，含 1 处「回退→接受」翻转） | 做 |
| H7d | 桶分组向量化（每桶全扫 → 每通道一次稳定排序切分） | ~0.3-0.8s | 中 | 中（首现序契约） | **先测量再定** |
| H7c | zlib 解压线程重叠（GIL 释放 → 藏入解析期） | ~1.0-1.3s | 中 | 中低（线程 + 取消语义） | 后测 |

落地后：读入 14.1 → H7e+a+b ≈ 9.1s → +H7d/c ≈ 7.5-8s；总耗时本机 29.8 → ~24.7 → ~23-24s。此后写 MDF 7.7s 重新成为最大头（H5 回归议题）。

#### H7e：字段提取对齐视图化（blf_vector 内，~1.6s）

- **根因**：`_u32_at` 对每个位置做 4 次逐字节散点 gather；候选 4 对齐（实测 100%）时 c+8/c+12/c+4/c+16/p+4… 全部 4 对齐——u32 视图一次 gather 即得同值；`_u64_at`（7 次）同理
- **设计**：`_parse_fast` 内建零拷贝视图 `v32 = np.frombuffer(data, dtype="<u4", count=len(data)//4, offset=0)`（u16 取 v32 低 16 位，u64 用两个 u32 视图读对拼——见实施明细）；`aligned = bool(np.all((c & 3) == 0))`（一次 (N,) 比较）——真 → 视图读路径；假 → 现字节 gather 路径（同值慢读，零行为变化，fuzz/手工容器自动落此）。`_u8_at` 不动
- **质量影响**：零（同一字节串两种读法逐位相同）。**测试零改动**
- **验收**：pytest 全绿 + compare_two_mdf exit 0 + `_u*_at` 计时 2.42 → ~0.8s

##### H7e 实施明细（代码级核对）

- **改动位置**：[blf_vector.py:65-100](core/blf_vector.py#L65-L100) 四个 `_u*_at`（模块级，现吃 b8 逐字节 gather）；调用点全部在 `_parse_fast` 内（:148-150 头字段、:195-196 flags/rel、:218-270 四类消息字段）。`_u8_at` 已是单 gather，不动
- **视图构造**：`v32 = np.frombuffer(data, dtype="<u4", count=len(data)//4, offset=0)`——与 `_candidates`（:114）同款零拷贝写法（不带 offset 须截齐长度，**勿用切片** `data[:n*4]`，那会全量拷贝）。视图版 helper 新增模块级 `_u32_at_v(v32, p, max_pos)`/`_u16_at_v`：safe 掩码语义与现版一致（`q = np.where(safe, p, 0)`），读出 `v32[q >> 2]`；可证 `p+4 ≤ max_pos ⇒ p>>2 < len(v32)`，无越界。u16 = `v32[q >> 2] & 0xFFFF`
- **u64 简化决策**：候选 ≡0 mod 4 但 mod 8 可能混合（同容器内偏移 0/4 并存）→ 不做 v64 双视图分支，**用两个 u32 视图读对拼**：`lo = v32[q >> 2]`、`hi = v32[(q + 4) >> 2]`（c+24、c+28 均 4 对齐），7 次字节 gather → 2 次视图 gather，实现最简、无 8 对齐分支
- **分派**：`aligned = bool(np.all((c & 3) == 0))`（一次 (N,) 归约，~0.01s 级）；真 → 视图路径，假 → 现字节路径（fuzz/手工构造的错位容器自动落此，零行为变化）。分派建议在 `_parse_fast` 内选 helper（视图版为模块级新函数，便于探针按函数计时对账）
- **测试零改动成立**：两读法读同一字节串，逐位相同；fuzz 生成的前缀垃圾容器（1-3 字节）天然覆盖错位回退路径

#### H7b：载荷打包下放桶级（blf_vector + converter，~3.0s）

- **根因**：载荷拷两遍——容器级打包 362MB 只为统一契约，桶级再 gather 一遍 164MB。桶级拷贝是固有成本（载荷必须离开容器缓冲进桶），容器级拷贝纯属中转，可整体删除
- **设计**：
  1. `ContainerFrames` 增 `scattered: bool = False`、`glen: np.ndarray | None = None`
  2. 快路径不再打包：`data8` = 容器字节引用（frombuffer 视图，零拷贝），`data_off` = 帧载荷在容器内的绝对偏移，`data_len` = `lens`，`glen` = 补零前实际长度；**补零语义（FD64 截断 ljust）下沉到消费者**
  3. 回退路径 `_frames_to_container` 保持 packed 契约（scattered=False，glen=None）
  4. `_bucket_block` 增散点分支：`src2 = data_off[sel][:,None] + arange(L)`，take from 容器字节；零掩码 `col >= glen[sel]`（覆盖行尾补零 + FD64 截断补零；全满桶沿用现「跳掩码」优化）
  5. 内存：cf 持有容器字节至本容器路由结束（与现 `data` 生命周期一致），无新峰值

##### H7b 实施明细（2026-08-15 代码级核对，供实施直接执行）

**消费面核对结论**（全仓 grep）：`ContainerFrames.data8/data_off` 的消费方只有两处——`converter._bucket_block`（解码桶与 raw 路径共用同一函数，[converter.py:52-70](core/converter.py#L52-L70)，raw 收集 :187 也走它）与 `tests/test_blf_vector.py`。契约变更面收敛，无其他消费者。

**旧设计两处修正**（读代码后核实）：

1. **「`_assert_eq`/`_assert_streams_equal` 零改动」是错的**：[test_blf_vector.py:126](tests/test_blf_vector.py#L126) 与 :526 直接按 `data8[data_off:data_off+data_len]` 切片。scattered 下 FD64 补零行（glen < data_len）会读出容器内垃圾字节，且切片可能越过 data8 尾（字节切片静默截短）→ 必须改为 scattered 感知的重建（2 处）。
2. **「4 处断言更新」不足**：实际直接断言 5 处（:327 `bytes(cf.data8)`、:348、:358、:364、:376），加 helper 2 处 = **共 7 处测试点**；:239（窗口假阳性用例）切片长度 1 == glen，无需改。

**代码修改清单**（2 文件，一次落地）：

1. [blf_vector.py:34-47](core/blf_vector.py#L34-L47) `ContainerFrames`：增 `scattered: bool = False`、`glen: np.ndarray | None = None` 两字段。
2. [blf_vector.py:282-302](core/blf_vector.py#L282-L302) `_parse_fast` 打包段替换为 scattered 输出：`data8 = b8`（零拷贝视图，不再 ravel 打包块）、`data_off = doff_full[idx]`（容器内绝对偏移，替代 `arange(n_emit)*Lmax`）、`data_len = lens` 不变、`glen = glen_full[idx]`、`scattered = True`。**`glen_full`（:211）现成**——快路径已算「实际 gather 字节数」（FD64 截断 clamp），零新增计算。n_emit==0 分支：data8 空数组 + glen 空数组。
3. [blf_vector.py:340-356](core/blf_vector.py#L340-L356) `_frames_to_container` 不动——默认值即 packed 契约（scattered=False，glen=None）；`_empty_container_frames` 不动（空帧不进 `_bucket_block`）。
4. [converter.py:52-70](core/converter.py#L52-L70) `_bucket_block` 增散点分支：

   ```python
   lens = cf.data_len[sel]; n = len(lens)
   if n == 0: return cf.ts[sel], lens, np.zeros((0, 0), dtype=np.uint8)
   L = int(lens.max()); idt = np.int32 if cf.data8.size < 2**31 else np.int64
   src2 = cf.data_off[sel].astype(idt)[:, None] + np.arange(L, dtype=idt)
   block = np.take(cf.data8, src2, mode="clip")
   if cf.scattered:
       gsel = cf.glen[sel]
       if not bool(np.all(gsel == L)):        # 全满桶跳过掩码（同现优化）
           block[np.arange(L)[None, :] >= gsel[:, None]] = 0
   else:
       if not bool(np.all(lens == L)):        # 现 packed 逻辑原样保留
           block[np.arange(L)[None, :] >= lens[:, None]] = 0
   return cf.ts[sel], lens, block
   ```

   **关键点**：
   - 掩码条件从 `lens` 改为 `glen`（FD64 补零列 col ≥ glen）；跳过掩码的条件相应改为 `np.all(gsel == L)`
   - `mode="clip"` 越界读：scattered 下 `data_off + L` 可越过容器尾（FD64 的 vb > available 时）——clip 读出的是容器末字节垃圾，由 glen 掩码清零兜底（glen 已含容器尾截断，恒有 `data_off + glen ≤ max_pos`）
   - 块宽 L = `lens.max()`（最终长度）不变 → `_assemble_bucket` 零改动（scattered 块恒 (m, L) 全宽，:90 的 pad 分支不触发；跨块变宽时 pad 补零语义仍正确——pad 列均 ≥ 各帧 glen）
   - raw 路径零改动（走同一 `_bucket_block`）
   - 内存：`cf.data8` 引用整容器解压字节（frombuffer 视图），至本容器路由结束释放——与现 `data` 生命周期一致，无新峰值；`idt` 判断用 `data8.size`（现为容器 ~10MB，int32 恒成立，逻辑照旧安全）

**测试更新清单**（[tests/test_blf_vector.py](tests/test_blf_vector.py)，7 处 + 1 新 helper）：

- 新增模块级 helper（packed/scattered 双契约统一重建）：

  ```python
  def _payload(cf, i):
      """帧载荷重建：scattered = 容器字节切片 + 补零（ljust 语义）；packed = 直接切片。"""
      o = int(cf.data_off[i]); n = int(cf.data_len[i])
      if cf.scattered:
          g = int(cf.glen[i])
          return bytes(cf.data8[o:o + g]) + b"\x00" * (n - g)
      return bytes(cf.data8[o:o + n])
  ```

- :126 `_assert_eq`、:526 `_assert_streams_equal`：`d = _payload(cf, i)`
- :327 `test_dlc_out_of_range_classic`：`bytes(cf.data8) == b"\xAA" * 8` → `_payload(cf, 0) == b"\xAA" * 8`（经典帧 glen == 8 == data_len）
- :348 `test_fd64_valid_bytes_padding`：断言改为 `cf.glen[0] == 30` 且 `_payload(cf, 0) == b"\xCC" * 30 + b"\x00" * 30`（补零改由消费者补）
- :358/:364 `test_fd64_ext_data_offset`：两分支同法（glen 均为 40——第二分支 ext_off=200 时 available 被容器尾截断 clamp 到 40）；`_payload` 重建 == `b"\xDD"*40 + b"\x00"*20` / `b"\xEE"*40 + b"\x00"*20`
- :376 `test_fd64_lying_header_size_field`：断言改为 `cf.glen[0] == 0`（dfl 负 → available 0）且 `_payload(cf, 0) == b"\x00" * 60`
- 回退路径测试全绿不依赖修改（packed 契约保持，helper 双分支覆盖）
- 边界自检：vb=0 的 FD64（dlen=0、glen=0）→ L>0 时全零行由掩码 `col >= 0` 全清零；scattered 不变量 `data_off + glen ≤ len(data8)` 且 `data_len ≥ glen` 恒成立；**禁止就地写 cf.data8**（b8 视图共享容器字节，掩码只写 take 拷贝出的 block）

**TDD 步骤（红-绿，每步可回退）**：

| 步 | 内容 | 预期 |
|---|---|---|
| 1 | 测试先行：新增 `_payload` + 改 2 helper 调用 + 5 处直接断言 | pytest test_blf_vector.py **红**（现代码无 scattered/glen 字段 → AttributeError/断言失败，确认红在预期位置） |
| 2 | 代码一次落地：blf_vector.py（dataclass + 打包段）+ converter.py（`_bucket_block` 分支）——**两处必须同一步完成**：`_parse_fast` 一旦输出 scattered，`_read_vectorized` 立即把 cf 送进 `_bucket_block`，旧 packed 寻址逻辑对绝对偏移寻址错误 | pytest test_blf_vector.py **绿**（快/回退两路） |
| 3 | 全量 pytest | 203 通过 + 环境性失败与基线同集（重点 `test_convert_fd_bucketing_and_raw_assembly` 端到端、test_converter 全量） |
| 4 | AHT 样例转换 + `compare_two_mdf <新输出> <AHT_bench.mdf>` | 230 组全部一致 exit 0（逐位不变） |
| 5 | `full_compare <新输出> inputs/mdf_canoe/AHT.mdf` | 与基线同分（70/70 + 160 组 × 601 点） |
| 6 | 计时验收：h7 探针同口径复测 | 读入 ~14.1 → ~11.1s（本机）；`_parse_fast` 内 take 消失、桶级 `_bucket_block` 体量仍 ~164MB；`_parse_fast` 内打包计时归零 |

**风险**：中（契约变更，回归网齐全——test_blf_vector 快/回退两路逐位 + FD 端到端 + 230 组对拍）。任一验收失败即停。

#### H7a：候选扫描单类化 + 免排序 + 双探针守卫（blf_vector，~0.45s）

- **根因**：4 遍全容器扫描 + 排序只为覆盖 4 个对齐类，实测候选 100% 单类——3 遍扫描与排序白做
- **设计**：
  1. 起始窗口探测：5 个标量位置 p ∈ [0,4]（p+4 ≤ max_pos）检查 `data[p:p+4] == b"LOBJ"` 得 `j_first`（walk 首窗口 [0,8) 首个命中）；无命中 → 走现行 N==0 分支（语义不变）
  2. 只扫 `o = j_first % 4` 类：一次 frombuffer+nonzero → **免 concatenate/排序**；一致错位容器（前缀垃圾 1-3 字节，全对象 ≡ o mod 4）由 o 自动覆盖，与 walk 及现行 4 遍行为同解析
  3. **守卫 = 双探针**（每容器 ≤ 6 次 4 字节比较）：旧设计「每窗口 3 探针」经代码级核对为死代码+漏点，由重叠定理替代（见实施明细）——仅坏轮（gap > 4）与末窗口（first == N）两处需要探针
  4. N==0 而起始窗口有 LOBJ（全错位病态容器）→ 回退（勿 raise，避免「标量正常而快路径抛异常」违约）
- **等价性论证**：有效轮（gap ∈ {0,4}）由**重叠定理**（两个 LOBJ 起点距离 1..3 时 magic 字节必冲突）保证 walk 首个命中 = 扫描候选；gap > 4 坏轮与 first==N 末窗口由双探针覆盖；探针全空 ⇒ 快路径 = walk。AHT 实测 0/58 错位 → 探针零触发零开销
- **回退语义变化**（输出仍正确，个别容器「接受 ↔ 回退」互换）：实施明细逐用例清单（3 处测试更新，含 1 处「回退 → 接受」的语义改善）
- **验收**：pytest 全绿（更新后）+ compare/full_compare + 候选计时 0.70 → ~0.25s

##### H7a 实施明细（2026-08-15 代码级核对，供实施直接执行）

**walk 语义依据**（对拍 oracle 的行为本源，[can/io/blf.py:242-259](C:/ProgramData/anaconda3/Lib/site-packages/can/io/blf.py#L242-L259)）：
- 每轮 `pos = data.index(b"LOBJ", pos, pos + 8)`——窗口 [pos, pos+8)（end 越界按 len 截断），完整匹配 ⇒ 命中 p ≤ pos+4 且 p+4 ≤ len；无命中且 pos+8 > max_pos → 容器干净终结；否则 raise（:243-247）
- `next_pos = pos + obj_size`（obj_size 含 base 头，= 到下个对象起点的距离，:255-259）→ 窗口下界 s 按 obj_size 前缀和推进，与 `_parse_fast` :177 的 s 定义一致

**重叠定理（守卫简化的依据）**：两个完整 LOBJ 起点距离 d ∈ [1,3] 时，后者的第 (4−d) 号字节必须是前者 magic 的第 d 字节——"LOBJ" 自身错位 1..3 比较必冲突（d=1: 'O' vs 'L'；d=2: 'B' vs 'L'；d=3: 'J' vs 'L'），故任意两个 LOBJ 起点距离 ≥ 4。推论：
- 扫描候选 c_i 存在且 gap = c_i − s_i ≤ 4（有效轮）时，s_i+1..c_i−1 内无 LOBJ——**walk 首窗命中必为 c_i**，旧设计「每窗口 3 探针守卫」是死代码（gap=4 时探 s_i+1..3 也恒空：与 c_i=s_i+4 距离 ≤3；gap=0 时 s_i+4 处若有 LOBJ 必被扫描为候选，由现行防御 :181-182 捕获）；
- gap > 4 的坏轮：候选不可见，walk 首窗 [s_first, s_first+8) 内可能有错位 LOBJ（p = s_first+1..3；p = s_first+4 若存在必被扫描为候选 ⇒ gap ≤ 4 矛盾）——需**首坏轮探针**；
- first == N 时 walk 会从 s_end 继续搜窗——需**末窗口探针**（s_end+1..3；s_end+4 处若有 LOBJ 必被扫描 ⇒ 归入坏轮情形）。

**代码修改**（[blf_vector.py](core/blf_vector.py)）：

1. `_candidates` (:103-121) 重写为起始探测 + 单类扫描，返回 `(c, j_first)`：

```python
def _candidates(data: bytes):
    """单对齐类扫描：起始窗口探测定类 o，只扫 ≡ o mod 4 的 LOBJ。

    起始探测复刻 walk 首窗口 [0,8)（can/io/blf.py:242，命中 p ≤ 4）。
    返回 (候选位置 int64 升序, j_first；起始窗口无 LOBJ 则 -1)。
    """
    L = len(data)
    j_first = -1
    for j in range(5):
        if j + 4 <= L and data[j:j + 4] == b"LOBJ":
            j_first = j
            break
    if j_first < 0:
        return np.empty(0, dtype=np.int64), -1
    o = j_first % 4
    n = (L - o) // 4
    if n <= 0:
        return np.empty(0, dtype=np.int64), j_first
    v = np.frombuffer(data, dtype="<u4", count=n, offset=o)   # 零拷贝（勿切片）
    return np.nonzero(v == _LOBJ)[0].astype(np.int64) * 4 + o, j_first
```

2. `_parse_fast` N==0 分支 (:137-141)：`c, j_first = _candidates(data)`；N==0 时——max_pos < 8 → 尾部分支不变；否则 j_first ≥ 0 → `return None`（全错位容器，勿 raise）；j_first < 0 → raise 不变。

3. 双探针（插入 :179 `first` 计算之后任意位置，纯回退判定）：

```python
    # H7a 双探针：重叠定理下仅两处窗口可能有扫描不可见的 walk 命中
    if first < N and int(c[first]) - int(s[first]) > 4:
        w = int(s[first])
        for j in (1, 2, 3):
            if w + j + 4 <= max_pos and data[w + j:w + j + 4] == b"LOBJ":
                return None
    if first == N and N >= 1:
        s_end = int(s[N - 1]) + int(obj_size[N - 1])   # walk 末轮后的搜索起点
        for j in (1, 2, 3):
            if s_end + j + 4 <= max_pos and data[s_end + j:s_end + j + 4] == b"LOBJ":
                return None
```

（注意两点：s_end 是 `s[N-1] + obj_size[N-1]`，不是截断数组 s 的末元素——s 被 `[:N]` 截断，末元素只是最后一轮窗口下界，walk 的最终搜索起点还要加上末对象 obj_size；探针条件按 `p+4 ≤ max_pos` 逐位置判定，**不是** `s+8 ≤ max_pos`——walk 的 index end 越界按 len 截断，`pos+8 > max_pos` 的「干净终结」只在无命中时才生效，错位 LOBJ 只要完整在容器内就会被 walk 命中。）

4. 旧设计的「每窗口 3 探针守卫」从未写入代码，仅计划文档内纠正（本明细即纠正后版本）。

**测试更新（3 处，逐用例核实；`_check` 口径对「快路径拒绝」合法放行，fuzz 两例无需改动）**：

| 用例 | 现行行为 | H7a 后 | 断言改动 |
|---|---|---|---|
| `test_gap_in_window_0_to_4` (:242-246) | k=0..4 全接受 | k=0/4 接受；k=1..3 末探针命中 → 回退 | k=1..3 改 `_check(data) is None`；k=0/4 不动 |
| `test_false_positive_in_window_parsed_identically` (:229-239) | 接受且与 walk 一致（fp@50 双方同解析） | fp@50 ≡ 2 mod 4 不可见 → 末探针命中 → 回退 | 改 `_check(data) is None` |
| `test_false_positive_in_payload_rejected` (:223-226) | 回退（fp@42 触发 c[first] < s[first] 防御） | fp@42 不可见 → 候选 {0,48} 全有效 → **接受**且与 walk 一致（2 帧，帧 0 载荷 b"ABLOBJCD"） | 改断言接受 + `_assert_eq` 对拍；建议改名或加注释说明语义翻转（「拒绝」→「与 oracle 同解析」） |

- fuzz 两例（:444-497）`_check` 对「拒绝/回退」合法放行 → 探针只增回退、不增不一致，无需改动
- 一致错位容器（前缀垃圾 1-3 字节，fuzz kind 6 构造）：o = j_first % 4 自动覆盖，与现行 4 遍行为一致，无测试改动

**实施前探针**（读入探针加 3 行）：gap = c−s 分布直方（预期全覆盖 {0,4}）、`first < N` 容器数（预期 0）、双探针命中计数（预期 0）。**探针全零是守卫零触发与收益实收的前提**——AHT 现有证据「类分布 {0: 5791489} + 0/58 多类容器」已间接支持，直方图补直接证据。
**实施后修订（用户批准）**：双探针 j 范围由 `(1, 2, 3)` 改为 `range(5)`——原论证隐含 s ≡ o mod 4 前提，错位容器 obj_size ≢ 0 mod 4 时漏检 s+0/s+4；AHT 零触发，fuzz 覆盖。

#### H7d：桶分组向量化（converter，先测量后实施）

现状：每桶 `grp = valid & (norm == k)` 对容器帧全扫（58 容器 × 70 桶 × ~9.7 万帧 ≈ 4 亿次比较）。设计：每通道每容器一次 `np.argsort(norm, kind="stable")` → 边界切分 → 桶块按首现序输出（stable 排序保持首现序契约）。风险：插入序契约、小桶退化。**先对读入残余 ~2.3s 做复测归因，确认收益再实施**。

#### H7c：zlib 解压线程重叠（blf_reader，后测）

`zlib.decompress` 释放 GIL → 单 worker 线程预取解压下一容器（queue 深度 1-2），与解析真并行。收益 = 解析远长于解压时把 1.5s 藏进解析期。风险：进度/取消语义、内存 +1-2 容器。在 H7e/a/b 落地后（解析缩短）按实测决定。

### 5.2 输入探测 H3（v2 遗留，H7 未覆盖）

`probe_channels._walk`（[blf_reader.py:105](core/blf_reader.py#L105)）仍是逐对象 Python 循环——与 H1 前的读入同构，v2 实测输入阶段 8.55s（其中行走 ~5.9s + zlib ~1.2s + 文件读入）。

**方案**（二选一或叠加）：
1. 与 H1 同款向量化行走（只取 channel 字段，不构造帧；复用 `_candidates` 扫描 + 掩码提取，改动集中在 probe_channels 一个函数）；
2. 按 (路径, 大小, mtime) 缓存探测结果（GUI 重拖同一文件 / 会话间复用）。

**质量影响**：零——通道集合语义不变（已有 probe vs list 对拍测试与 `test_scan_cache_*` 回归先例）。
**验收**：`tests/test_blf_reader.py` probe/list 对拍全绿 + 输入阶段实测 8.55 → ~1.5s。

### 5.3 写 MDF H5（待需求裁决）

deflate-9 是为对齐 CANoe 体积定的。降级到 6 可省 ~2s，但文件 +1-2%（12.9MB → ~13.2MB，离 CANoe 10.3MB 更远）。属质量-速度权衡，是否接受由需求裁决，**默认不实施**。

### 5.4 池预热残余 H6（先测量）

v2 实测 spawn ~1.7s 同步发生在读入启动前（[converter.py:364](core/converter.py#L364) make_pool 在 `_read_vectorized` :378 之前）。warm_up 全量预热修复已随 H1 提交（解码墙钟不再虚增），但池创建本身仍占总耗时。

**候选设计**：后台线程创建池，读入期间 spawn 与解析并行，解码前 join。**注意**：spawn 8 个 worker 是 CPU 密集操作，与同样 CPU 密集的向量化解析并行可能只是平移而非节省——先测本机池创建真实耗时与并行干扰再定。

## 6. 预期收益汇总（诚实评估）

| 落地组合 | 读入（本机） | 转换总耗时（本机语境） | v3 语境 |
|---|---|---|---|
| 现状（H1+H2a 后） | 14.1s | 29.8s | 27.3s |
| + H7e + H7a | ~12.0s | ~27.7s | ~25.2s |
| + H7b | ~9.1s | ~24.7s | ~22.2s |
| + H7d/H7c（复测后） | ~7.5-8s | ~23-24s | ~21-22s |
| + H3（输入阶段） | —（不计入转换） | — | 输入 8.55 → ~1.5s |
| + H5（若裁决同意） | — | −~2s | 文件 +1-2% |

**置信度**：H7b/e 收益由 take 4.0s 与字段提取 2.4s 的实测拆解支撑，置信高；H7a 的 0.45s 由扫描 0.60→0.15s 推算，置信中高；H7d/H7c/H6 需复测后才承诺。全部保留「回退即 oracle」防线，逐位对拍链全程护航。

## 7. 实施顺序与纪律

| 步 | 内容 | 验收 |
|---|---|---|
| 1 | H7e：对齐视图字段提取（错位容器走现路径）——见 §5.1「H7e 实施明细」 | pytest 全绿（零测试改动）；compare exit 0；`_u*_at` 2.42 → ~0.8s |
| 2 | H7a：单类扫描 + 免排序 + 双探针守卫——见 §5.1「H7a 实施明细」（+3 处测试语义更新，含 1 处「回退→接受」翻转；先跑 gap 直方探针） | pytest 全绿；compare exit 0 + full_compare 同分；候选 0.70 → ~0.25s |
| 3 | H7b：打包下放桶级，散点契约——见 §5.1「H7b 实施明细」（+7 处测试点：5 直接断言 + 2 helper；TDD 先红后绿） | pytest 全绿；compare 230 组 exit 0 + full_compare 同分；读入 ~14.1 → ~11.1s |
| 4 | 复测残余（路由/统计/分组 ~2.3s）→ 定 H7d 取舍 | 探针归因 |
| 5 | （若做）H7d 分组 / H7c 解压重叠 | 各自验收 |
| 6 | H3：probe 向量化（或缓存） | probe/list 对拍 + 输入阶段计时 |
| 7 | （裁决后）H5；H6 先测量再定 | 各自验收 |

任一验收失败即停（定位→最小修正→重验，不叠加猜测）。每步独立可回退。
**提交操作由用户自行执行**（沿用 2026-08-15 声明，不代提交）。

## 8. 验证链与复现

- 对拍链：`tools/compare_two_mdf.py`（新旧自产逐位，230 组）、`tools/full_compare.py`（vs CANoe 参考 70/70 + 160 组 × 601 点）、`pytest`（203 通过 + 环境性跳过）
- 探针（未进仓库，临时目录）：`%TEMP%\h7_read_probe.py`（读入拆解）、`%TEMP%\h2_ipc_probe.py`（IPC 体积）、`%TEMP%\blf_bench.py`（全流程计时）
- 运行：`& C:\ProgramData\anaconda3\python.exe <脚本>`（PATH 上的 python 是商店空壳，须用 anaconda3）
- 样例：`inputs/blf/AHT_ACFCANPUB_*.blf`（82MB，5,650,848 帧）；参数与 GUI 一致（parallel=True、raw_export=False、stats_export=True）；基线输出 `%TEMP%\AHT_bench.mdf`（12.9MB）

## 附录 A：对象头字段规格（H1 实施核对修正版）

源自 H1 计划文档 §2.2/§2.3，含 **2026-08-14 实施核对的三处修正**（该文档未提交的更正，随本文档保留）：

- 基础头（16B，`<4sHHLL`）：header_size(u16@+4)、header_version(u16@+6)、obj_size(u32@+8)、obj_type(u32@+12)。
- 版本 1：V1 头 16B，flags=u32@+16，rel=u64@+24；版本 2：V2 头 24B（`<LBxHQ8x`），flags=**u32**@+16，rel=u64@+24（修正：V2 flags 为 L 字段 u32，非 u8）。
- 时间单位：`flags == 1`（精确相等，非位测试）→ rel×10000（10µs 单位）；否则 rel 按 1ns 计。

消息对象字段（偏移均相对「对象头末尾」c+hsz，hsz = 32(v1)/40(v2)）：

| 类型 | 结构 | channel | flags/远程 | dlc | can_id | is_fd | data 区 |
|---|---|---|---|---|---|---|---|
| CAN_MESSAGE(1)/CAN_MESSAGE2(86) | `<HBBL8s` | u16@0 −1 | u8@2 & 0x80 | u8@3（原值，**不** dlc2len） | u32@4 | False | @8，长 min(dlc,8) |
| CAN_ERROR_EXT(73) | `<HHLBBBxLLH2x8s` | u16@0 −1 | —（远程 False） | u8@10（原值） | u32@16 | False | @24，长 min(dlc,8)，is_error=True |
| CAN_FD_MESSAGE(100) | `<HBBLLBBB5x64s` | u16@0 −1 | u8@2 & 0x80 | dlc2len(u8@3) | u32@4 | u8@12 & 0x1 | @20，长 min(valid_bytes(u8@13),64)（修正：is_fd 标志 u8@12、valid_bytes u8@13） |
| CAN_FD_MESSAGE_64(101) | `<BBBBLLLLLLLHBBL` | u8@0 −1 | fd_flags(u32@12) & 0x0010 | dlc2len(u8@1) | u32@4 | fd_flags & 0x1000 | @40 |

- `arbitration_id = can_id & 0x1FFFFFFF`；`is_extended = bool(can_id & 0x80000000)`。
- FD64 数据长度（python-can issue:1905）：`dfl = min(valid_bytes(u8@2), (ext_data_offset(u8@35，即 m[13]) or obj_size) − header_size − 40)`（修正：ext_data_offset 在 u8@35）；`msg_data = data[msg_off : msg_off + dfl]`（切片按容器尾截断）再 `.ljust(valid_bytes, b"\x00")` → 最终长度 = valid_bytes（可能 >64，≤255）。
- dlc2len 表：`[0,1,2,3,4,5,6,7,8,12,16,20,24,32,48,64]`（dlc>15 → 64，python-can `can/util.py` 实测）。
