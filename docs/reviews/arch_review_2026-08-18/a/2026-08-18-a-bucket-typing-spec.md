# A — 桶契约类型化 — 设计 Spec

> 来源：2026-08-18 架构审查（[2026-08-18-architecture-review.md](../2026-08-18-architecture-review.md) 候选 A / M1，唯一剩余 Strong）+ 本 spec 全部决策经 grilling 逐项确认（用户 Q1-Q4/Q6/Q7 按推荐、Q5 先选「收敛测试」后经 Round 2 事实面反转回「保留」、Q10-Q16 按推荐，2026-08-19）。词汇表见 [CONTEXT.md](../../CONTEXT.md)：对拍、逐位一致、回退 oracle、无残留、统计组布局、绑定、**解码桶**（本轮新增词条）。

## Problem Statement

M1 的三点问题：

1. **桶三态隐式多态 dict，形状分派跨模块散布**。解码桶有三种形态——feed 列表形（[core/decoder.py:412](../../core/decoder.py#L412) 注释只写这一种）、blocks 块形（[core/converter.py:169-177](../../core/converter.py#L169-L177) 写入）、数组形（[core/converter.py:79-103](../../core/converter.py#L79-L103) 装配）——同一批键承载三种结构，形状分派靠 `isinstance` 散布（[decoder.py:156](../../core/decoder.py#L156)、[mp_finish.py:91](../../core/mp_finish.py#L91)）；[converter.py:389](../../core/converter.py#L389) 的 `not isinstance(t[0], np.ndarray)` 是 stats_bufs 上的形状多态**死残留**。
2. **分类不变量双实现、等价性靠注释声明**。「未知 ID/短帧 → 未知不入桶」在 feed（[decoder.py:417-428](../../core/decoder.py#L417-L428)）与向量化路由（[converter.py:140-157](../../core/converter.py#L140-L157)）各实现一遍；等价性的最脆弱点——np.unique 首现序 vs setdefault 插入序——没有任何直接对拍，只由端到端对拍链间接兜底。
3. **不变量无集中声明 + finish 防御性死分支**。arb=归一化键、raw_id=首帧原始 id、插入序=系列序三个跨模块不变量只散在各 docstring 片段；[decoder.py:298](../../core/decoder.py#L298) 的 finish 防御性 length 掩码依赖「桶内无短帧」前提却自行再查一遍（注释自认「保留为防御」）。

## Solution

- 桶结构收口为 decoder.py 的**显式状态单类 `Bucket`**：三相位互斥字段组（feed 列表 / blocks 块 / 数组）+ 相位转换方法，isinstance 分派从 decoder/mp_finish/converter 三处收进类内（`to_array` / `n_frames` / `memory_estimate`）；三个跨模块不变量集中声明于类 docstring，`key == bucket.arb` 在 finish 入口断言。
- **分类规则收口到 dbc_loader**（M2 双入口模板同构）：`classify`（标量）+ `classify_batch`（批量）+ `message_table`（长度表构造）相邻定义；feed 与向量化路由各自调用；批量与标量逐元素等价由属性测试锁定，两条路由的桶级等价由新增**路由等价测试**直接对拍。
- finish 防御性 length 掩码删除——「桶内帧均已分类」成为类型不变量（构造即预检）；mux 无子组计数（靠数据内容分类，finish 真实职责）保留。
- 死残留（converter.py:389）顺手清。
- CONTEXT.md 新增「解码桶」词条（Q15 已定稿措辞）。

## User Stories

1. 作为维护者，我想桶结构是显式状态的单类（三相位字段组互斥、转换方法收敛），以便形状分派不再靠跨模块 isinstance 猜状态。
2. 作为维护者，我想分类规则（键查找 + 帧长校验）单一来源在 dbc_loader（标量 + 批量双入口相邻定义），以便改键规则（如 FD 专属 ID 空间）只改一处。
3. 作为维护者，我想「未知 ID/短帧 → 未知不入桶」的两条路由（feed / 向量化）有桶级逐元素直接对拍，以便等价性不再靠注释声明 + 端到端间接兜底。
4. 作为维护者，我想三个跨模块不变量（arb=归一化键、raw_id=首帧原始 id、插入序=系列序）集中声明于 Bucket docstring 与 CONTEXT.md 词条，以便新读者一次读到完整契约。
5. 作为维护者，我想 finish 不再防御短帧（length 掩码删除），以便「桶内已分类」由构造保证、防御性死分支消失。
6. 作为验收工程师，我想全量 pytest（269 基线 + 新增）与逐位对拍链全绿，以便零行为漂移有硬验收。
7. 作为验收工程师，我想黄金套件（test_parallel_decode 并行/串行等价、test_decoder_vectorized fuzz 对拍、test_decoder 分类语义）原样通过，以便 A 不重写任何既有测试——feed/decode_channel 保留为被测对象本体（Q5）。
8. 作为维护者，我想 reference_decode oracle 换调生产 `classify`，以便键空间契约（含帧长校验）由生产单一持有、oracle 不复制键规则（M2 先例；解码逻辑保持 cantools 独立）。

## Implementation Decisions

### A1. `Bucket` 类型（Q1=显式状态单类、Q2=owner decoder.py、Q11=按草案）

decoder.py 新建 `Bucket` dataclass（接口形状为 grilling Q11 草案，实施照此，字段名微调需在 plan 中说明理由）：

```python
@dataclass
class Bucket:
    """解码桶（跨模块契约，词条见 CONTEXT.md「解码桶」）：
    arb=归一化键（= buckets dict 键，finish 入口断言）；raw_id=首帧原始 id；
    桶内帧均已分类（构造即预检，finish 不再防御）；插入序=系列序（dict 插入序）。
    相位互斥（由工厂方法保证）：
      feed 相位（feed_ts: list[float], feed_data: list[bytes]）→ 数组相位（to_array）
      blocks 相位（blocks/ts_blocks/lens_blocks: list[ndarray]）→ 数组相位（to_array）
    数组相位 = ts (N,) float64 / lens (N,) int64 / data (N, L) uint8（finish 消费）。"""
    arb: int
    raw_id: int
    md: MessageDef
    feed_ts: list[float] | None = None
    feed_data: list[bytes] | None = None
    blocks: list[np.ndarray] | None = None
    ts_blocks: list[np.ndarray] | None = None
    lens_blocks: list[np.ndarray] | None = None
    ts: np.ndarray | None = None
    lens: np.ndarray | None = None
    data: np.ndarray | None = None

    @classmethod from_feed(arb, raw_id, md)          # feed setdefault 建桶（decoder.py:429-431 语义）
    @classmethod from_blocks(arb, raw_id, md, block, ts, lens)  # 向量化路由建桶（converter.py:167-172 语义）
    def add_frame(ts_seconds, data)                 # feed 追加
    def add_block(block, ts, lens)                  # 向量化追加（参数序修订：block 在前，与 from_blocks 一致；plan 澄清 ③，2026-08-19 code-review 同步）
    def to_array(self) -> None                      # 幂等：feed/blocks 相位 → 数组相位
    @property def n_frames(self) -> int             # mp_finish 任务/进度用（现 len(b["ts"])）
    def memory_estimate(self) -> int                # bucket_bytes 用（isinstance 分派收进类内）
```

要点：

- `to_array()` 幂等合一两个现转换：feed 相位 → 数组（现 `_normalize_bucket` + `_bucket_data_array` 语义，lens 从 data 字节长推导）；blocks 相位 → 数组（现 `_assemble_bucket` 语义）；数组相位 → no-op。**H2a 性能注释（一次末态连接、每字节只写一次、禁散点索引）随 `to_array` 迁入**——性能前提的文档载体不丢失。
- `memory_estimate()` 覆盖三相位（feed：`len(ts)*32 + Σlen(data)`；blocks/数组：nbytes 求和）——现 mp_finish.py:91-95 的 isinstance 分派整体消失；该函数现生产路径只在 `_read_vectorized` 装配后调用（恒数组相位），列表分支仅服务 feed 路径测试，类型化后不再需要区分。
- `key == bucket.arb` 在 `finish()` 入口断言（防字典键与字段漂移；不引入运行时防御层级，仅断言）。
- `_normalize_bucket` / `_bucket_data_array` 整体删除（语义内化进 `to_array`）。

### A2. 分类收口 dbc_loader（Q10=dbc_loader，M2 模板）

- `classify(dbc, arb, data_len) -> MessageDef | None`：归一化键空间查找 + `data_len >= md.frame_length` 校验——**分类规则唯一实现**。
- `message_table(dbc) -> (keys uint32 排序, lens int64, mds list)`：现 converter `_prep_decode_info`（converter.py:25-39）的逐通道表构造迁入，返回单通道表；`_read_vectorized` 保留 `{ch: message_table(dec.dbc)}` 包装。
- `classify_batch(table, norm, data_len) -> (found, valid)`：searchsorted + 判等 + 帧长比较的批量 twin，与 `classify` 相邻定义；**批量与标量逐元素等价由属性测试锁定**（随机帧 on 随机 DBC，同 A2 测试节）。
- 调用点：feed（decoder.py:417-428）改调 `classify`；`_read_vectorized`（converter.py:140-157）改调 `classify_batch` + `message_table`，unknown 记账逻辑原样（found/valid 掩码语义逐位不变）。
- 依赖方向零变化：decoder/converter 均已 import dbc_loader；converter→decoder→dbc_loader 单向保持。

### A3. 消费端改造

- **converter.py**：`_prep_decode_info` 删除（→ `message_table`）；`_read_vectorized` 建桶循环（:166-176）改 `Bucket.from_blocks` / `add_block`；`_assemble_bucket`（:79-103）删除 → 装配循环（:196-199）改 `b.to_array()`；:389 死残留删除。
- **decoder.py**：`feed` 改 `classify` + `from_feed` + `add_frame`；`finish()` 改 `to_array()` 循环 + `key==arb` 断言；`_finish_bucket_vectorized` / `_decode_bucket_reference` 改属性访问（15 处 `b["…"]` 字典访问点）；**Q12：删 `valid = lens >= md.frame_length` 防御掩码**（decoder.py:298-299），`decodable` 起点改为全真，mux 无子组计数（:306）与 unknown 记账保留。
- **mp_finish.py**：`_finish_bucket_worker` 签名收 typed `Bucket`、`_normalize_bucket` 调用（:41）删除；`bucket_bytes`（:82-96）改 `Σ bucket.memory_estimate()`；tasks（:170）与 `_report`（:191）的 `len(b["ts"])` 改 `b.n_frames`。

### A4. 测试（Q13=加路由等价测试、Q14=reference_decode 换调）

- **classify/classify_batch 参数化单测**（M2 风格）：已知/未知 ID/短帧/扩展帧边界 + 批量与标量逐元素一致（随机帧 on 随机信号 DBC，复用 test_decoder_vectorized 的 `_random_signal_dbc` 生成器）+ dtype 契约（uint32 键空间）。
- **Bucket 相位契约测试**：`to_array` 幂等（三相位各调两次结果不变）；同帧序列 feed 路径 vs 单块 blocks 路径 `to_array` 后逐元素等价；`n_frames`/`memory_estimate` 三相位；`key==arb` 断言触发；`from_feed`/`from_blocks` 的 raw_id 首帧捕获。
- **路由等价测试**（M1「等价性靠注释声明」的结构性收口）：`can.BLFWriter` 写合成 BLF（test_parallel_decode:90 先例）→ `_read_vectorized` 建桶（装配后）；同一批合成帧 `feed` 建桶（`to_array` 后）——桶级逐元素对拍：键集、插入序、ts/lens/data、raw_id。合成帧含未知 ID、短帧、扩展帧、mux 混合（复用 `_random_frames` 风格）。
- **reference_decode 换调 `classify`**（test_decoder_vectorized.py:98-141）：键查找 + 帧长校验改用生产函数；解码仍走 cantools `decode_message`（独立 oracle 的解码面不动）。行为逐帧等价（已知 ID 短帧 / 未知 ID / mux 无子组三路径已推演一致）。
- **既有测试零改动**：test_decoder 11 处 `decode_channel`、test_decoder_vectorized 5 处 fuzz 对拍、test_parallel_decode 2 处 feed 循环全部原样（Q5 保留的推论）。

## Testing Decisions

- **不新增 seam**：所有断言落在既有测试面——`classify`/`classify_batch`（dbc_loader 契约函数）、`Bucket`（decoder 模块内新类型）、`decode_channel`（公开接口，行为不变）、`_read_vectorized`（私有函数直调，test_converter / test_decoder_vectorized 直调私有函数是既有先例）、合成 BLF 双路由对拍（`can.BLFWriter` 是 test_parallel_decode 既有工具）。
- **零重写既有测试**：Q5 反转回「保留」的直接推论——黄金套件（并行/串行等价、fuzz 对拍、分类语义）一行不动，A 的回归面 = 既有 269 全绿 + 新增约 6 组。
- **验收方式**：`python -m pytest tests/ -q` 与 `pytest tests/ -q` 两种调用方式全绿（H1 后 pytest.ini `pythonpath = .` 保证）；逐位对拍链（`tools/mdf_compare.compare_files_identical` 对 AHT 并/串产物，样例在场时执行）；性能前提由「numpy 运算语义零变化 + H2a 注释随迁」保障——类型化只换容器，不重跑 AHT 基准（若本机方便可顺手记数，不作通过条件）。
- 双轴 code-review 0 findings（项目惯例，实施完成后走 receiving-code-review 流程）。

## Out of Scope

- feed 列表形删除 / 测试收敛（Q5 反转回**保留**：feed/decode_channel 是被测对象本体——test_decoder_vectorized fuzz 对拍、test_decoder 分类语义、test_parallel_decode 并行等价全走它；重写黄金测试收益低于风险）。
- stats_bufs 的 L4 缺省元组形状（`((), None, None, None)` vs `((), (), (), ())`）；raw_chunks / 原始帧路径（非解码桶，容器内 tuple 契约不动）。
- L6 `_read_vectorized` 10 位置参数面（当前仅 1 调用方，审查明示暂不必动）；L10 容器流衔接骨架三份复制。
- 分类在 feed 路径的**逐帧应用**形态（feed 保留即保留逐帧调用 `classify`——批查只服务向量化路由）。
- M8 绑定表、其余审查深化候选（A 之后剩 G 系列已全落地，无 Strong 候选）。
- 性能优化（类型化不改任何 numpy 运算语义；`to_array` 与现 `_assemble_bucket` 是同一份操作）。

## Further Notes

- **已核实事实（2026-08-19）**：① 生产路径 `feed` 已无调用者（convert 全走 `_read_vectorized`），feed 消费面 = `decode_channel`（模块地图公开接口）+ test_parallel_decode 2 处 feed 循环 + test_decoder 11 处 `decode_channel` + test_decoder_vectorized 5 处 `decode_channel` + 2 个可删 bench 脚本（bench_parallel_finish:62 / bench_bucket_dist:26，M8 残留，不在本轮范围）；② 桶字典访问点共 15 处（decoder 9 / converter 3 / mp_finish 3），isinstance 形状分派 2 处（decoder.py:156、mp_finish.py:91）+ 死残留 1 处（converter.py:389）；decoder.py 其余 5 处 isinstance 是信号值类型判断（NamedSignalValue/int），与桶无关；③ `bucket_bytes` 的列表分支生产不可达（convert 在装配后才调用），仅服务 feed 路径测试——`memory_estimate` 三相位统一覆盖后分支消失；④ finish 防御 length 掩码与 feed/批查预检语义同源（同一帧长规则），mux 无子组计数（decoder.py:306）靠数据内容分类、是 finish 真实职责，必须保留；⑤ `_read_vectorized` 的 np.unique 首现序 == feed 的 setdefault 插入序是两条路由最脆弱的等价点，无任何直接对拍（路由等价测试即为其收口）。
- 本 spec 按项目惯例置于 docs/reviews/a/（与 b/、d/、g/、h1/ 同布局）。
- 不提交 git（项目惯例，实施与提交由用户执行）。
- 后续：spec 批准后写 plan（任务粒度：dbc_loader 收口 → decoder `Bucket` 类型 → converter/mp_finish 消费端 → 测试新增 → 全量 pytest + 对拍链 → 双轴 code-review → 审查文档 §3 M1 标 ✅ / §4 A 落地 / §5 首要建议更新 / §2.1 行数刷新）。
