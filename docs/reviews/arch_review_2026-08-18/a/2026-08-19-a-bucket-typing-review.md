# A 桶契约类型化 双轴 code-review

日期：2026-08-19　|　审查范围：`git diff 653eb4b...HEAD`（提交 ea08291「提价A的task1-4」，11 文件 +1765/−156）　|　分支：arc　|　方法：Standards 轴 + Spec 轴并行子代理，B/D/G 先例同流程

## Standards 轴

标准来源：CONTEXT.md 契约词条 + 用户六条质量标准（架构干净 / 调用链扁平 / 契约明确 / 严格收口 / 易维护 / 不新增无谓抽象）+ plan Global Constraints + Fowler smell baseline。

**硬性契约核对全部通过**：

- 字段名逐字按 spec Q11（arb/raw_id/md/feed_ts/feed_data/blocks/ts_blocks/lens_blocks/ts/lens/data，11 字段与 spec A1 一致）；
- 依赖方向零变化（dbc_loader 仅新增 `from __future__ import annotations`，无 decoder/converter 反向引用）；
- 黄金套件断言一行未动（test_decoder 不在 diff；test_decoder_vectorized 16 行 = import + reference_decode 换调一处；test_parallel_decode = 2 行 import + 追加路由等价测试）；
- classify/classify_batch/message_table 相邻定义于 dbc_loader 为唯一实现，decoder/converter 无键规则残留；
- Bucket 三相位互斥（仅工厂方法构造、to_array 幂等置 None、无直接构造）；
- finish 入口 `assert arb == b.arb` 在位（decoder.py:527）；
- isinstance 形状分派确从 decoder/mp_finish/converter 三处消失（残留 5 处均为 NamedSignalValue/int 值类型判断）；
- 全量 pytest 289 passed / 0 failed，零行为漂移成立。

**Findings（5 项，全 nitpick，0 blocking / 0 substantive）**：

| # | 位置 | 内容 | 处置 |
|---|---|---|---|
| S1 | docs/reviews/a/2026-08-19-a-bucket-typing-plan.md:517 | 澄清 ④ 记「288 passed（287 基线）」与 spec 269 基线（spec 撰写时点）跨时点比对不符 | **误报关闭**——plan 数字链自洽：Task 2 后 287（269+Task1 4+Task2 8+既有新增）→ Task 3 后 288 → Task 4/5 后 289，逐任务时点实测记录 |
| S2 | docs/reviews/a/2026-08-18-a-bucket-typing-spec.md:63 | spec A1 接口签名 `add_block(ts, lens, block)` 与实现 `add_block(block, ts, lens)` 不同步（plan 澄清 ③ 已记录理由） | **已修订**——spec 签名同步为 block 在前并标注理由（文档漂移类随修订关闭，B 先例） |
| S3 | tests/test_dbc_loader.py:137 | A 测试块模块中部自引 import，load/normalize_id 与顶部重复 | **已修订**——新名（classify/classify_batch/message_table）并入顶部 import，中部块删除 |
| S4 | tests/test_decoder_vectorized.py:114 | oracle 换调 classify 后 decode_message 的 KeyError 分支不可达（键已预检在表内），保留为防御 | **保留**——oracle 防御无害（mux 无子组仍走 DecodeError），改动属无谓（标准 ⑥） |
| S5 | core/converter.py:59 vs core/decoder.py:60 | (block, ts, lens) 入参同序但 `_bucket_block` 返回 (ts, lens, block)，调用点解包重排——同类数据双序并存 | **保留**——`_bucket_block` 为既有函数（A 范围外），调用点重排显式且局部，judgement |

## Spec 轴

Spec 来源：docs/reviews/a/2026-08-18-a-bucket-typing-spec.md（Q1-Q16 全部 settle）；落地以 plan 正文 + 三处实施澄清为据。

**Findings：0 项（blocking 0 / substantive 0 / nitpick 0）**

核验覆盖（全对位）：

- **A1**：三相位单类逐字落地（字段名与 Q11 草案逐字、docstring 与 A1 定稿逐字）；`finish()` 入口断言在 to_array 之前触发，test_finish_asserts_key_matches_arb 实证；H2a 性能注释随迁进 to_array；
- **A2**：classify/message_table/classify_batch 相邻定义、feed 与 `_read_vectorized` 均改调、批量/标量等价由属性测试锁定；classify 语义「未知 ID 或 data_len < frame_length → None」一致；
- **A3**：`_prep_decode_info`/`_assemble_bucket`/`_normalize_bucket`/`_bucket_data_array` 删除（grep 无残留，仅注释提及）；15 处 `b["…"]` 全部收敛属性访问；Q12 防御 length 掩码删除（mux 无子组计数与 unknown 记账保留）；mp_finish typed worker + bucket_bytes→memory_estimate + tasks/_report→n_frames；converter.py:389 死残留删除；
- **A4**：Bucket 相位 9 用例 + 分类边界 4 用例 + 路由等价测试（键集/插入序/ts/lens/data/raw_id/unknown 记账直接对拍）+ reference_decode 换调（解码面保持 cantools 独立）；
- **Out of Scope 零触碰**：feed 列表形保留、stats_bufs L4 形状未动、raw_chunks/原始帧路径未动、`_read_vectorized` 参数面未动、L10/M8/bench 脚本均不在 diff；
- **实施澄清三处**（①worker `bucket.to_array()`、②前向引用注解、③add_block 参数序 + 单块零拷贝）均落地且均在 plan 有修订记录；
- **实证**：新测试 20 passed（3.5s）；黄金套件 42 passed（16.8s）；AHT 并/串逐位对拍由 plan Task 5 记录（`对拍差异: []`，样例在场已复跑）。

## 结论

双轴 code-review **0 实质性 findings**（blocking 0 / substantive 0）；Standards 轴 5 项 nitpick 中 2 项随修订关闭（spec 签名同步、重复 import 清理）、2 项核验后保留（judgement）、1 项误报。代码零改动进 review 后状态：仅 tests/test_dbc_loader.py import 合并（纯卫生，行为零变化）。

全量 pytest：**289 passed / 0 failed**（anaconda3 Python 3.13.9，复跑见 plan Task 6 Step 3 记录）。
