# G — ChannelStats 死字段删除 — 设计 Spec

> 来源：2026-08-18 架构审查（[2026-08-18-architecture-review.md](../2026-08-18-architecture-review.md) 候选 G / L2，Strong）+ 本 spec 全部决策经 grilling 逐项确认（用户 Q1-Q5 均按推荐，2026-08-18）。词汇表见 [CONTEXT.md](../../CONTEXT.md)：对拍、逐位一致、回退 oracle、无残留、统计组布局。

## Problem Statement

`ChannelStats.channel` 是硬编码死字段（[core/stats.py:154](../../core/stats.py#L154) 恒写 `channel=0`），全仓零读者：mdf_writer 写统计组只读 `cs.values` / `cs.t`，test_stats 从不触碰，GUI 用的是另一类 `ChannelSummary.channel`（真实身份、有读者）。写值无人消费，且与 `ChannelSummary.channel` 同名——读者可能误以为该字段携带通道身份，正是 L2 危险性的来源。

顺带核查（grilling 轮次 2）发现 `ChannelStats.signal_names` property（[core/stats.py:78-79](../../core/stats.py#L78-L79)）同为**零读者**：mdf_writer 写统计组走模块常量 `STAT_NAMES` 直接迭代（[core/mdf_writer.py:124](../../core/mdf_writer.py#L124)），grep 命中的 `signal_names` 全部是 `SignalSeries`（另一个类）——同一判据（deletion test：零读者、删之复杂度不转移）、同一位置的死代码。

「顺序即通道」的真实不变量**不在删除范围内**：输出布局（16 通道 × 10 项 = 160 个 "1s" 组按序）已有三层锁定——converter 显式迭代 `STAT_CHANNELS` 组装（[core/converter.py:488](../../core/converter.py#L488)）、test_converter 160 组位置断言（[tests/test_converter.py:207-223](../../tests/test_converter.py#L207-L223)）、mdf_compare reference 模式按通道块强制（[tools/mdf_compare.py:217-228](../../tools/mdf_compare.py#L217-L228)）。G 只删对象上这层**虚假身份**，不动布局。

## Solution

删除 `ChannelStats.channel` 字段、构造参数与 `aggregate_channel` 的 `channel=0` 实参；一并删除零读者的 `signal_names` property。类 docstring 改写，明示「对象不含通道身份；通道由调用方按通道序持有」。CONTEXT.md 新增「统计组布局」词条（已写入）。成功路径输出字节零变化。

## User Stories

1. 作为维护者，我想 ChannelStats 不携带虚假的通道身份，以便读者不会误把死字段当契约（与 `ChannelSummary.channel` 同名假象消除）。
2. 作为维护者，我想删除零读者的 `signal_names`，以便同类死代码不残留（Q5=A）。
3. 作为维护者，我想「顺序即通道」契约有显式声明（类 docstring），以便 L2「未成文」的表述缺口关闭（Q2=A）。
4. 作为词汇表读者，我想「统计组布局」有唯一词条，以便未来审查引用该契约时含义唯一（Q3=A）。
5. 作为验收工程师，我想删除后全量 pytest 保持全绿（259 基线、用例数不变），以便回归由既有测试承担（Q4=B：不补新测试）。
6. 作为验收工程师，我想成功路径输出字节零变化，以便对拍逐位一致与性能基线不受影响。

## Implementation Decisions

### G1. 删除范围：channel 字段 + 构造参数 + signal_names

[core/stats.py](../../core/stats.py) 三处删除：`ChannelStats.__init__` 的 `channel` 参数与 `self.channel` 赋值；`aggregate_channel` 返回语句的 `channel=0` 实参（构造签名收敛为 `ChannelStats(t, values)`）；`signal_names` property（Q5=A）。`mdf_writer` 的 `ChannelStats` import 保留——`write_mdf` 签名注解 `stats_groups: list[ChannelStats]` 仍需该名字。

### G2. 契约声明：类 docstring 明示顺序即通道

`ChannelStats` 类 docstring 改写为：「一个 1s 统计结果（聚合自单通道帧流）。对象不含通道身份——通道由调用方按 STAT_CHANNELS 顺序持有；输出组序 = 通道 × 统计项（见词汇表『统计组布局』）。」（Q2=A 只取 docstring 一处；converter 组装处已是显式迭代，不加冗余注释；不新增运行时断言——writer 不拥有「16 通道」决策，违反标准⑥。）

### G3. 文档：CONTEXT.md 新增「统计组布局」词条

词条内容（**已写入** [CONTEXT.md](../../CONTEXT.md)）：「总线统计（1s 组）的通道身份由组序携带、不写入组内：组序 = 通道序 × 统计项序。布局是跨模块输出契约——组装、写出、对拍消费三侧必须一致；统计结果对象本身不含通道身份，通道由调用方按通道序持有。」正文零实现细节（无文件:行号）。（Q3=A）

### G4. 不补测试

删除无新行为、无新契约可锁；「断言属性不存在」是测试实现而非契约（Q4=B）。回归由全量 pytest（259 基线）+ test_converter 既有的 160 组位置布局断言（`test_convert_stats_export_default_ones_groups`）承担。

## Testing Decisions

- **无新增测试**（Q4=B）。
- **零测试改动**：grep 证实无任何测试读 `ChannelStats.channel` / `.signal_names`（test_stats 只经 `aggregate_channel` 构造，从不触碰这两者）。
- **验收方式**：`python -m pytest tests/ -q` 与 `pytest tests/ -q` 两种调用方式全绿（259 基线、用例数不变）；不重跑 AHT 真实数据对拍（成功路径零字节变化，对拍链不受影响）。

## Out of Scope

- 「顺序即通道」输出布局本身的改动（三层锁定维持现状；G 只删对象上的虚假身份，不重新设计布局）。
- 显式化方向（真实通道号入对象、mdf_writer 消费或断言）：已否决（Q1=A，全仓零消费者，YAGNI）。
- `ChannelSummary` / `RawGroup` / `SignalSeries` 等其他类的 channel 字段（均有真实读者，非死字段）。
- 其余审查深化候选（B 绑定决策 / A 桶契约）与 tools/ 脚本清理（bench_*、probe_*）。
- 性能与对拍路径的任何变化（成功输出字节不变）。

## Further Notes

- **已核实事实（2026-08-18）**：① `ChannelStats.channel` 全仓零读者（core/gui/tests/tools 全量 grep，`.channel` 命中均为 `Frame`/`ContainerFrames`/`ChannelSummary`/`SignalSeries`/`RawGroup` 等其它类）；② `ChannelStats.signal_names` 同为零读者（grep 命中的 `signal_names` 全部是 `SignalSeries`）；③ 「顺序即通道」三层锁定：converter.py:488 显式迭代 `STAT_CHANNELS`、test_converter.py:207-223 断言 160 组位置布局（组 1+10 = ch1 StdData、组 1+150+1 = ch15 StdDataRate）、mdf_compare.py reference 模式按「每通道块 10 组、块内 STAT_NAMES 序」强制且 STAT_NAMES 双副本相等性锁定断言；④ mdf_writer 写统计组走模块常量 `STAT_NAMES`（mdf_writer.py:124），不依赖对象属性；⑤ `aggregate_channel` 全仓唯一构造点 = converter（stats.py 自身 + converter.py:494）。
- 本 spec 按项目惯例置于 docs/reviews/g/（与 d/、h1/ 同布局）。
- 不提交 git（用户指示）；提交由用户执行（项目惯例）。
- 后续：spec 批准后按 Implementation Decisions 实施（任务粒度：stats.py 删除 + docstring → 全量 pytest 验证 → 审查文档 §3/§4/§5 状态更新）。
