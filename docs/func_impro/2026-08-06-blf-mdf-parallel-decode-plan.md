# 方案 G：多进程并行解码 — 详细实施方案

日期：2026-08-06
状态：方案设计完成（待实施）；基线已复测
依据：`docs/2026-08-05-blf-mdf-conversion-performance.md` §4 方案 G；本次实测基线（`tools/bench_stages.py`，本机 8 核 / Python 3.13.9 / conda `blfmdf`）

---

## 1. 结论摘要（先读这里）

| 项 | 结论 |
|---|---|
| 方案 G 的原始估算（30-40s）**已过时** | 该估算基于方案 C 实施前（解码阶段 74s）。方案 C 向量化后解码阶段已降至 ~12s，G 可并行的部分只剩这一块 |
| 本次实测基线 | 总耗时 **47.0s**：单遍扫描+feed 25.7s（55%）→ 解码 finish 12.0s（26%）→ 写 MDF 8.2s（17%）→ 统计 0.76s |
| G 可实现的收益 | 解码 finish 12.0s → 期望 **~6-7s**（关键路径 = CAN3 单通道 5.5s），净省 **~4-5s（约 10%）** |
| 代价 | 内存 ~1.5-2×、进程生命周期管理、Windows spawn 语义、测试与对拍工作量（约 2-4 人日） |
| 结论 | **收益真实但有限**。方案 G 按文档范围（「单遍扫描后按通道分发到多进程」）可以做，但不要期待文档中「再省 30-40s」的效果；若目标是继续大幅提速，剩余大头是单遍扫描（25.7s）与 MDF 写出（8.2s），见 §10 附录与 §9 建议 |
| 实施前提 | 先做方案 E（common_timebase，1-3s、一行改动）再启动本方案；E 完成后重测基线 |

**决策门（实施第 1 步后触发）**：原型实测「并行 finish 净省 < 2s」→ 中止方案，回退文档并把 G 标记为「已评估、不实施」。

---

## 2. 现状基线（2026-08-06 复测，与文档 §6 方案C 记录对比）

`tools/bench_stages.py`（progress_cb 分阶段计时，样例 BLF 35MB / 2,377,398 帧 / 10 绑定通道）：

| 阶段 | 本次实测 | 文档方案C记录 | 说明 |
|---|---|---|---|
| 读取 BLF（单遍扫描 + feed 路由 + 统计收集） | **25.74s** | 19.98s | 纯 python-can 解析 ~17s + feed/路由 ~3s；本次偏高（机器负载/冷缓存） |
| 解码 finish 合计 | **11.99s** | 9.84s | CAN3 5.51 / CAN6 3.29 / CAN1 0.89 / CAN8 0.78 / 其余 <0.5s |
| 统计聚合 | 0.76s | 0.59s | searchsorted 后已可忽略 |
| 写 MDF | 8.20s | 7.13s | append + save(compression=2, deflate) |
| 收尾 | 0.34s | 0.0s | — |
| **总计** | **47.02s** | 37.8s | 文档为 8-06 早些时候记录，本次负载不同，两者都是有效基线 |

**方案 G 的并行候选区间 = 解码 finish 11.99s（26%）**。其余阶段：单遍扫描是单个串行文件流（python-can BLFReader 无索引、纯 Python 解析），G 方案（按文档定义）不触碰；写 MDF 是 asammdf 内部，组序有依赖，均不在本方案范围。

---

## 3. 根因复核：为什么 G 的收益从「30-40s」缩水到「4-5s」

文档 §2.2 写方案 G 时的阶段分布（方案 A 后、方案 C 前）：解码阶段合计 203.7s（扫描 130s + 逐帧解码 74s）→ 并行解码预估省 30-40s。

方案 C（2026-08-06 已完成）把逐帧 cantools 解码向量化：解码 finish 74s → ≤12.7s（实测 9.8-12.0s）。**「逐帧解码」这一 G 的并行对象已被向量化吃掉，剩余 finish 只有 ~12s，且其中 CAN3 单通道独占 5.5s（46%）——并行后墙钟下限就是最大单通道 5.5s，而非 12s/N。** 这是收益缩水的根本原因（两层：总量变小 + 单通道成为关键路径）。

辅助根因：当前代码的单遍扫描（`convert()` 内 `iter_all_messages` 循环）本身是设计上的串行单点（一个文件、一个解析器、逐帧路由），G 方案文档定义「单遍扫描后按通道分发」，即只并行解码、不并行扫描——所以扫描的 25.7s 依旧串行。

---

## 4. 架构选型

### 4.1 候选对比

| 候选 | 方案 | 预期省时 | 判定 |
|---|---|---|---|
| **G2：单遍扫描保持串行，finish 并行（推荐）** | 扫描后把每通道解码桶分派到进程池，子进程跑现有 `ChannelDecoder.finish()`（零改动复用），结果按通道序收回 | ~4-5s | ✅ 符合文档定义、复用现有代码、确定性可控 |
| G1：每通道一个进程各自重读 BLF | 子进程各自 `BLFReader` 全文件解析再过滤本通道 | **负收益** | ❌ 10 进程 × 17s 解析 = 170s CPU 总量，8 核下墙钟 ~21s > 现 17s；只有 finish 并行了，扫描反而变差 |
| G3：按字节偏移分块并行解析 | 全文件按对象边界（BODG 签名）分块，子进程独立解析各块 | ~8-10s（最大） | ⚠️ 需重写 python-can 的 BLF 对象解码（~150 行、格式细节多），质量风险高，超出 G 文档范围 → §10 附录另议 |
| G4：帧级流水线（生产者/消费者队列） | 扫描进程逐帧入队，解码进程消费 | ❌ | 238 万帧 × 队列 IPC ≥ 10µs/帧 ≈ 24s+，比现状还慢 |

### 4.2 G2 概要架构

```
父进程（convert，串行）                       子进程 × min(cpu, 通道数)
┌──────────────────────────────┐   ┌──────────────────────────────┐
│ iter_all_messages 单遍扫描     │   │ ChannelDecoder.finish()       │
│   ├→ stats_bufs（统计，不变）    │   │   = _finish_bucket_vectorized  │
│   ├→ ChannelDecoder.feed ×10  │   │    + _decode_bucket_reference  │
│   │    （桶在父进程内存累积）    │   │  （现有代码，零改动）           │
│   └→ 扫描期预热进程池           │   │                              │
│ 扫描完 → 按通道提交 (dbc, 桶) →  │──→│ 返回 (series, 新增 unknown)    │
│ as_completed 收结果            │←──│                              │
│ 按通道序合并 → 组序不变 → 写MDF  │   └──────────────────────────────┘
└──────────────────────────────┘
```

---

## 5. 详细设计

### 5.1 模块划分（新增 1 个模块，改 2 个文件）

| 文件 | 改动 |
|---|---|
| `core/mp_finish.py`（**新增**，~80 行） | 顶层 worker 函数 + 池创建/预热/回收封装 + LPT 通道分配 + 串行回退 |
| `core/converter.py`（改） | `convert()` 增加 `parallel: bool = False` 参数；finish 循环分「串行/并行」两分支，**其余代码不动**；并行分支的总结/组序/统计语义与串行分支完全一致 |
| `gui/main_window.py`（改） | `ConvertWorker` 传 `parallel=True`（自动回退）；`closeEvent` 等待逻辑不变（pool 在 convert 内 asyncio/上下文管理收尾） |
| `core/decoder.py`（**零改动**） | 复用 `ChannelDecoder`/`DecodeStats` 原样 |

### 5.2 worker 函数（核心，5 行复用现有 API）

```python
# core/mp_finish.py
from core.decoder import ChannelDecoder, DecodeStats

def _finish_channel_worker(channel: int, dbc, buckets: dict):
    """子进程入口：对单通道桶跑现有 finish()。返回 (series, 新增未知帧数, 新增未知 ID 列表)。

    复用 ChannelDecoder.finish() 本身 → 与串行路径同一份代码，零漂移。
    stats 从空开始（finish 只统计解码期未知帧）；feed 期计数在父进程（见 5.5）。
    """
    dec = ChannelDecoder(dbc, channel)
    dec.buckets = buckets          # 直接注入父进程累积的桶
    series, stats = dec.finish()   # 现有逻辑，含向量化/回退/未知分类
    return channel, series, stats.unknown_frames, sorted(stats.unknown_ids)
```

- 必须是**模块顶层函数**（multiprocessing 按「模块+函数名」pickle 引用，不能是闭包/lambda）；
- 进程内不触碰 Qt、不触碰 blf_reader/文件 I/O，只做 numpy 解码；
- 桶内 payload（`{"ts": list[float], "data": list[bytes], "arb": int, "raw_id": int, "md": MessageDef}`）全部可 pickle（已验证：`MessageDef` 字典 33KB；`DbcDef` 含 cantools db 也整体可 pickle，13 个共 7.55MB——**但通用路径不需要**，见 5.4）。

### 5.3 进程模型（Windows spawn 语义）

- **上下文**：`multiprocessing.get_context("spawn")` + `concurrent.futures.ProcessPoolExecutor`（Windows 无 fork，spawn 是唯一选择）。
- **`__main__` 守卫**：入口脚本必须有 `if __name__ == "__main__":` 守卫（`main.py` 已有 ✓）。子进程会以 `__mp_main__` 重新执行入口模块：GUI 场景子进程会 import PySide6（~1s，可接受）；**实测** 4 worker 冷启动（spawn + numpy + cantools import + 首个任务往返）= **1.46s**。
- **预热**：扫描期（25.7s）子进程完全空闲——在 feed 循环开始前创建池并提交一个 no-op 任务，让 worker 在扫描期间完成 spawn+import，扫描结束即用（把 1.5s 启动成本藏进扫描期，净收益 +1.5s）。
- **池生命周期**：`convert()` 内 try/finally —— finally 里 `pool.shutdown(wait=True, cancel_futures=True)`；子进程异常（BrokenProcessPool）→ catch → 剩余通道转串行 finish（见 5.7）。GUI 的 QThread 内创建/销毁池无冲突（池与 Qt 事件循环无交互，子进程不碰 Qt）。
- **worker 数**：`min(os.cpu_count(), len(bound_channels))`，环境变量 `BLF_MP_WORKERS` 可覆盖；单通道时直接走串行路径（不建池）。

### 5.4 数据传递

| 数据 | 体积（实测/估算） | 传递方式 |
|---|---|---|
| DBC 定义 | `MessageDef` 字典 33KB / 通道；`DbcDef` 整体 0.2-1MB / 通道 | **任务参数**（每任务随桶一起 pickle）。样例 10 绑定 DBC **全部可向量化**（已实测，无 `_decode_bucket_reference` 回退报文），vectorized 路径只用到 `b["md"]`；为通用性仍随任务传 `DbcDef`（含 cantools db，回退路径需要），0.2-1MB/通道可忽略 |
| 解码桶 | CAN3 约 15-30MB（433K 帧 × ts/data/对象头），全通道合计 ~100MB pickle | 任务参数。Windows 管道吞吐 ~0.5-1.5GB/s → 单程 ~0.1-0.2s，可忽略 |
| 结果 | 单通道 series 数组（CAN3 2964 信号约几十 MB） | 任务返回值（子进程 pickle 回传），同量级 |

**内存预算**：父进程现状峰值 ~1.2GB（桶 + 全部 series 常驻，实测文档 §6 方案A）；并行期间叠加子进程各持单通道桶副本 + 解码数组（最大子进程 ~CAN3，数百 MB）→ **峰值 ~1.5-2× 现状**。35MB 量级 BLF 无压力；超大文件防护见 5.7 内存阈值回退。

### 5.5 统计合并语义（与串行严格等价）

串行路径的 `DecodeStats` 由 feed（父进程）与 finish（同进程）共同累积：

- feed 期：`total_frames`、未知帧（未知 ID + 短帧）、`unknown_ids`（[decoder.py:387-401](core/decoder.py#L387-L401)）；
- finish 期：mux 无子组值帧 / 回退路径解码失败帧的未知（[decoder.py:281-284](core/decoder.py#L281-L284)、[decoder.py:326-330](core/decoder.py#L326-L330)）。

并行拆分（结果与串行逐项相同）：

```python
# 父进程：feed 期计数保留在 dec.stats（现状不变）
# 子进程：返回 stats.unknown_frames / sorted(stats.unknown_ids)（从空 DecodeStats 起算）
# 合并：
total       = dec.stats.total_frames
unknown     = dec.stats.unknown_frames + child_unknown
unknown_ids = dec.stats.unknown_ids | set(child_ids)
decoded     = total - unknown          # ChannelSummary 语义不变
```

`unknown_ids` 排序回传是为 pickle 集合的确定性（set 无序，排序后再并，union 结果与串行 set 相等——summary 只取 `len()`，无顺序需求，排序仅保证跨进程可复现）。

### 5.6 确定性保证（输出逐位一致）

- **组序**：MDF 组序 = `all_series` 顺序 = 串行路径按 `channels`（排序）逐通道 extend。并行路径把结果按通道收集进 dict，**收齐后仍按 `channels` 序 extend** → 组序逐位一致。
- **系列内顺序**：桶插入顺序 = 系列顺序（finish 按 `buckets` 插入序迭代），子进程与串行同一代码 → 一致。
- **数值**：numpy 运算在子进程逐位相同（相同输入、相同操作序）；无随机性、无共享可变状态（桶按值传递）。
- **进度回调**（仅展示层）：`解码 CANn` 按完成序回调（as_completed），percent = `10 + 80 × 完成数/total`（单调）；串行路径行为不变。GUI 显示为文本 + 进度条，语义无要求。
- 验收口径：并行输出 MDF 与串行输出 **逐组全量一致**（§7 测试 2 锁定）。

### 5.7 失败回退与开关

| 触发 | 行为 |
|---|---|
| 池创建/提交失败、BrokenProcessPool、worker 崩溃 | catch → 未完成通道**原地转串行 finish**，结果与全串行一致（正确性零损失，仅慢） |
| 通道数 < 2 或 `BLF_MP_WORKERS=0` | 直接串行路径（不建池） |
| 桶总字节数 > 阈值（默认 1.5GB，可 env 覆盖） | 串行路径（内存保护） |
| `BLF_MP_WORKERS` | 手动覆盖 worker 数 |
| 反病毒/环境导致 spawn 慢 | 无感知：预热期已覆盖启动成本；真失败走回退 |

`convert()` 默认 `parallel=False`（既有 API 调用方行为不变、pytest 常规套件不引入 spawn）；仅 GUI 传 `parallel=True`。新增测试显式覆盖两路径。

### 5.8 converter 改动点（并行的 finish 分支示意）

```python
# core/converter.py —— 替换现有「finish 循环」为两分支之一（143-177 行区域）
if parallel and len(decoders) >= 2 and total_bytes_ok(decoders):
    results = mp_finish.finish_all(decoders, channels, progress_cb)  # dict[ch] -> (series, stats)
else:
    results = {ch: decoders[ch].finish() for ch in ...}              # 现状逻辑
# 之后的 summaries / all_series / note_range 构造代码两分支共用（按 channels 序取 results）
```

`finish_all()` 内部：LPT（最长处理时间优先）把通道 bin-pack 进 `min(cpu, 通道数)` 个 worker（按桶帧数加权；CAN3 独占一 worker，其余小通道分摊）→ 提交 → as_completed 收集 → 5.5 合并 → 返回。

---

## 6. 实施步骤（每步独立可验收、可回退）

| 步骤 | 内容 | 验收 |
|---|---|---|
| **0. 前置** | 先实施方案 E（`mdf.append(..., common_timebase=True)`，见文档 §4E）；E 完成后用 `tools/bench_stages.py` 重测基线 | E 验收（对拍 + pytest） |
| **1. 原型（决策门）** | 新建 `core/mp_finish.py`：worker 函数 + 预热 + 提交/收集；用 `tools/bench_parallel_finish.py`（新脚本）在真实样例上只跑「扫描+并行 finish」，记录：finish 墙钟、IPC 传输量、CAN3 关键路径、spawn 预热效果；对比串行 finish 12.0s | **决策门：净省 ≥ 2s 才继续**，否则中止回退（§1） |
| **2. 集成 converter** | `parallel` 参数 + 双分支 + 合并语义 + 回退（5.5/5.7/5.8） | 常规 pytest 全绿（默认串行不受影响）；并行/串行双跑输出逐组一致（§7 测试 2） |
| **3. GUI 开启** | `ConvertWorker` 传 `parallel=True`；确认 closeEvent 等待期间池随线程退出；子进程不弹 Qt（守卫已保证） | 手动：GUI 转换完成、关闭窗口无卡死/残留进程（任务管理器确认） |
| **4. 质量验证** | §7 全部测试 + 对拍 + 金标准 | 全量 pytest 含 golden 通过；full_compare 对拍零差异 |
| **5. 收尾** | 本方案执行记录追加到性能文档 §6；G 行状态更新；删除原型脚本或归入 tools/ | 文档一致 |

---

## 7. 质量验证（沿用项目既有的「对拍」纪律）

新增 `tests/test_parallel_decode.py`（风格对齐 `tests/test_decoder_vectorized.py`）：

| # | 测试 | 锁定点 |
|---|---|---|
| 1 | `test_worker_finish_matches_serial_finish`：随机/真实 DBC 合成桶 → 串行 finish vs `_finish_channel_worker` → series 逐信号逐点相等 + stats 相等 | worker 与串行同一语义 |
| 2 | `test_parallel_matches_serial_real_blf`（golden 标记）：样例 BLF 双跑（parallel=False/True）→ asammdf 读回**逐组逐通道**全量对比（复用 full_compare 思路） | 输出逐位一致（组序/值/nan/dtype） |
| 3 | `test_parallel_deterministic_group_order`：并行两跑组名顺序一致 | 确定性 |
| 4 | `test_fallback_serial_on_pool_failure`：monkeypatch 池抛错 → convert 正常完成且结果 = 串行 | 回退路径 |
| 5 | `test_single_channel_no_pool`：parallel=True + 1 通道 → 断言未建池（monkeypatch 记录） | 短路逻辑 |
| 6 | `test_unknown_merge_equivalence`：合成 feed（含未知 ID/短帧/mux 坏值）→ 串行 vs 并行 unknown/unknown_ids 相等 | 5.5 合并语义 |

金标准：`test_golden.py` 增加一项 `test_golden_parallel`（parallel=True 转换 vs `_T058.mdf`，断言逻辑复用现有函数），或把现有 golden 函数参数化两跑——**并行输出必须同样 160 组 × 601 点逐点一致 + 信号覆盖 ≥90%**。

对拍：`tools/full_compare.py` 对并行输出（golden_par.mdf）vs CANoe `_T058.mdf` 全量跑一遍。

---

## 8. 验收标准

1. 全量 pytest（含 golden、含新增 test_parallel_decode）通过；
2. 并行 vs 串行输出逐组全量一致（测试 2）；并行 vs CANoe 参考 160 组 × 601 点逐点一致；
3. 本机实测：finish 12.0s → ≤7.5s（净省 ≥4s），总耗时 47s → ≤42.5s；报告分阶段实测；
4. 回退路径实测一次（人为使池失败 → 输出 = 串行）；
5. 内存峰值实测（并行期间）记录在案，不超 2.5GB（35MB 样例）；
6. GUI 手工验收：进度显示正常、关闭窗口无残留进程。

---

## 9. 风险清单

| 风险 | 等级 | 缓解 |
|---|---|---|
| 收益不足（决策门） | 中 | 第 1 步原型即裁量，净省 <2s 中止；已如实重估为 ~4-5s |
| spawn 环境差异（杀软扫描 conda env、慢启动） | 低 | 预热期覆盖；失败自动回退串行 |
| 子进程 import `__main__` 副作用（GUI → PySide6） | 低 | `main.py` 守卫 ✓；子进程不触 Qt；实测 1.46s 已含 |
| 桶 pickle 内存翻倍 | 中 | 阈值回退（5.7）；文档如实标注 |
| 进度回调顺序非确定性 | 低 | 仅展示层；percent 单调；组序/数值不受影响（5.6） |
| 并行与 E/其他待办交互 | 低 | 实施顺序固定：E → G；每步独立验收 |
| 测试套件时长增加（双跑 golden） | 低 | 并行跑更快；双跑合计 ~1.5× 单次 |

## 10. 附录 A：G3 并行解析（超出本方案范围，供后续决策）

若目标是「继续大幅提速」，剩余唯一大块是单遍扫描 25.7s（其中纯解析 ~17-20s，feed 路由 ~3s，统计收集 ~2s）。G3 思路：BLF 每个对象头部有 4 字节签名 `BODG`（0x47444F42）——先用 `bytes.find`（C 速度）扫出全部对象偏移（35MB，~2s），按偏移均分 N 块，子进程各自读字节区间并用**移植自 python-can 的对象解码**（~150 行）独立解析 → 解析墙钟 ~17s/4 + 索引 2s ≈ 6-7s，再叠加 G2 的 finish 并行 → 总耗时可望 47 → ~28-30s（-35%）。

代价与风险：需重写 BLF 对象解码（对象类型繁多：CAN2/CANFD/错误帧/远程帧/时间戳分辨率），正确性必须按方案 C 纪律全量对拍（属性测试 + 逐帧等价 + CANoe 金标准）；python-can 版本升级会带来漂移维护。**建议**：若用户认为「还要再快一倍」是硬目标，单独立方案 H 评估 G3；G 与 G3 正交（G 先落地，H 可叠加）。

## 11. 附录 B：与其他待办项优先级

| 项 | 预期 | 成本 | 建议 |
|---|---|---|---|
| E（common_timebase） | 文档估 1-3s，**实测 ~0**（见 §12 执行记录） | 一行 | ✅ 已完成（2026-08-06） |
| G（本方案） | 4-5s | 2-4 人日 + 内存×2 | 可选；决策门把关 |
| F（GUI list_channels 缓存） | 启动 13s → ~0 | 小 | 与 G 无关，随时可做 |
| G3（并行解析） | 8-10s | 3-5 人日 + 高验证成本 | 若需继续提速再立方案 H |

## 12. 执行记录

### 第 0 步：方案 E（common_timebase）✅ 已完成（2026-08-06）

- **涉及文件**：
  - `core/mdf_writer.py`：信号组与 Raw 组两处 `mdf.append(..., common_timebase=True)`（统计组每次仅 append 单信号，`signals[1:]` 循环为空、无比较，不加）；
  - `tools/compare_two_mdf.py`（新增）：自产 vs 自产逐组全量对拍工具（组序/组名/通道序/dtype/采样/t 轴逐位一致；full_compare 面向「自产 vs CANoe」结构，不适用自产互比）。
- **前置确认（Phase 1/2）**：asammdf 8.8.22 `MDF4.append` 源码——`common_timebase=False` 对 `signals[1:]` 逐信号 `array_equal` O(N) 比较、不同则 `unique+interp`；`True` 直接取 `t = t_`。本项目各组时间戳构造即为同一数组对象，默认路径比较全同后同样走 `t = t_`，**不触发插值** → 输出逐位一致。
- **实测收益（与文档估计 1-3s 不符）**：写 MDF 阶段 8.20s → 8.36s（运行噪声范围内，**净收益 ≈ 0**）。原因：160 个统计组各单信号无比较；103 个解码组 + Raw 组的 `array_equal` 合计仅数毫秒；写阶段大头是 asammdf 组构造 + save 压缩（deflate），与 common_timebase 无关。保留改动（消除无效比较、防御组内时间基不一致时误入插值路径），**如实记录收益 ≈ 0**。
- **质量验证**：
  - 常规 pytest（不含 golden）**80/80 通过**（45.9s）；全量含 golden **82/82 通过**（167.6s，golden 为 E 后输出 vs CANoe 参考逐点验证）；
  - `tools/compare_two_mdf.py` 对拍 E 前（bench_g_e0.mdf）vs E 后（bench_g.mdf）：**265 组组序/组名/通道序/dtype/采样/t 轴全部一致**；
  - E 后基线（bench_stages.py）：总 46.43s——扫描+feed 24.77s / finish 12.19s / 统计 0.76s / 写 MDF 8.36s（与 E 前 47.02s 分布一致，E 对 G 的评估基线无实质影响）。
- **对 G 的影响**：E 未改变任何阶段计时，方案 G 的基线（finish 12.0s、决策门净省 ≥2s）维持不变，可直接进入第 1 步原型。

### 第 1 步：并行 finish 原型 ✅ 已完成（2026-08-06，决策门 PASS）

- **涉及文件**：`core/mp_finish.py`（新增，per-bucket 并行 finish：worker/LPT 分派/提交收集/串行兜底/统计合并）；`tools/bench_parallel_finish.py`、`tools/bench_bucket_dist.py`（新增原型脚本）。
- **per-channel 原型（首版，未达决策门）**：按通道分派（10 任务 → 8 worker，LPT）。实测：spawn 预热 1.72s（隐藏于扫描期 22.95s）、桶 pickle 90.4MB（≈73B/帧，与 §5.4 估算吻合）、并行 finish **10.23s vs 串行 11.94s，净省 1.71s < 2s**，等价性校验全量一致。根因：CAN3 完成于 10.23s（= 总墙钟）——关键路径不是解码（5.5s）而是 CAN3 单任务全链路（30MB 桶 pickle + 5.5s 解码 + 2964 信号大结果回传）。
- **per-bucket 变体（用户决策转此方向）**：任务粒度改为 (通道, 桶)（总 ~200 桶 → 8 worker LPT）。关键洞察：`_finish_bucket_vectorized` 不使用 `dbc` 参数（报文定义在 `bucket["md"]`）→ 任务参数仅 (ch, arb, bucket)，**仅含非向量化报文的通道**随任务传 DbcDef（样例 10 通道全向量化，零 DBC 重复 pickle）；系列顺序 = 桶插入序（父进程按 `dec.buckets` 插入序组装）；统计合并 = feed 父进程 + Σ桶子进程返回。
- **桶分布实测**（`tools/bench_bucket_dist.py`）：CAN3 47 桶 / 43.4 万帧，最大桶 6 万帧（13.8%）；CAN6 8 桶 / 25.1 万帧；8 worker LPT 后各通道最大负载 **~6 万帧 ≈ 0.8s 解码**（vs 串行 CAN3 43.4 万帧 5.5s）。
- **per-bucket 实测**：并行 finish **6.61s vs 串行 12.00s，净省 5.39s**（决策门 ≥2s **PASS**）；spawn 预热 1.70s（隐藏于扫描期 23.41s）；桶 pickle 总量不变 90.4MB；等价性校验（并行 vs 串行全量逐位对拍）**全部一致**；冒烟合成数据（含未知 ID/mux）verify 通过。
- **对实施的影响**：第 2 步（converter 集成）按 per-bucket 设计执行；worker 数可调（`BLF_MP_WORKERS`），8 worker 为当前最优设定。

### 第 2 步：converter 集成 + GUI 开启 ✅ 已完成（2026-08-06）

- **涉及文件**：
  - `core/converter.py`：`convert()` 新增 `parallel: bool = False` 参数；池在 feed 前创建 + 预热（`mp_finish.make_pool`，单通道短路、创建失败自动回退串行）；feed 后桶内存估算超阈值（`BLF_MP_MEM_THRESHOLD`，默认 1.5GB）→ shutdown 池转串行；解码阶段双分支（并行 `mp_finish.finish_all` / 串行现状语义）；整个函数体 try/finally 保证池在任何路径（feed/写 MDF 异常）均回收；summaries/组序构造两分支共用（按 `channels` 排序，输出逐位一致）；
  - `core/mp_finish.py`：`make_pool`（worker 数 = min(cpu, 通道数)，`BLF_MP_WORKERS` 覆盖）+ `bucket_bytes`（内存估算）；
  - `gui/main_window.py`：`ConvertWorker` 传 `parallel=True`（GUI 默认开启，内部自动回退）；
  - `tests/test_parallel_decode.py`（新增 6 项，见 §7）：合成全量等价、真实样例双跑逐组对拍（golden）、LPT 确定性、池失败回退、单通道短路、未知合并等价；合成 BLF 用 python-can `BLFWriter`（4.6.1 接口为 `on_message_received`；64 字节 CANFD 报文需 `is_fd=True`）；
  - `tools/bench_stages.py`：加 `--parallel` 开关；**补 `if __name__ == "__main__"` 守卫**（§5.3 教训实证：spawn 子进程以 `__mp_main__` 重执行无守卫脚本 → 递归启动子进程、输出文件被占用 WinError 32）。
- **实测**：并行 convert 总耗时 **35.67s vs 串行 46.43s**（净省 ~10.8s；并行 finish ~3s，与原型 6.61s 差异来自本次机器负载/预热复用）；各通道解码/未知帧数逐项与串行基线一致。
- **质量验证**：
  - 常规 pytest **80/80**（converter 重构零回归）+ 新增并行测试 **5/5**（非 golden）+ golden 双跑 **1/1**（真实样例并行 vs 串行输出逐组全量一致）；
  - 金标准 `tools/full_compare.py`：并行输出（bench_g.mdf）vs CANoe `_T058.mdf` **160 组 × 601 点全部一致** + 8 信号 × 200 点数值一致；
  - 旧 golden 2 项复跑确认串行路径无回归。
- **收尾（步骤 4/5，2026-08-06）**：最终全量 pytest **88/88 通过**（常规 80 + 新增并行 5 + golden 3，9m20s）；金标准 full_compare 160 组 × 601 点全一致；并行 convert 实测 35.67s（vs 串行 46.43s，净省 ~10.8s）。**方案 G 完成**。
- **遗留（GUI 手工验收）**：启动 GUI 转换一次，确认进度显示正常、转换中关闭窗口无残留 python 子进程（代码路径与金标准一致，此处为交互层验收）。
