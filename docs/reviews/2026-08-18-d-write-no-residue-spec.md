# D — write_mdf 失败无残留归 writer — 设计 Spec

> 来源：2026-08-18 架构审查（[2026-08-18-architecture-review.md](./2026-08-18-architecture-review.md) 候选 D / M3，Strong）+ 本 spec 全部决策经 grilling 逐项确认（用户 Q1-Q10 均按推荐，2026-08-18）。词汇表见 [CONTEXT.md](../../CONTEXT.md)：对拍、逐位一致、回退 oracle、无残留。

## Problem Statement

转换失败时「输出位置不留半成品」的清理逻辑散落在 converter 层两处硬编码分支（[core/converter.py:511-523](../../core/converter.py#L511-L523)），而写出模块 write_mdf 自身失败时不清理——任何直接调用 write_mdf 的方（含未来新调用方）都会留下 `<stem>.mf4` 半成品残留。deletion test 判定：converter 复述了 writer 的中间文件命名（writer 改临时文件策略则 converter 清理静默失效）；且 converter 的「删 out_path」在失败场景下实际删除的是**上一次成功产物**（replace 是原子单步，write_mdf 失败时 out_path 从未被本次调用触碰）——一个没写清楚、删除范围超出本次调用边界的清理。

## Solution

把「失败无残留」收进 write_mdf 自身契约：write_mdf 抛出（含 KeyboardInterrupt 等 BaseException）时，本次调用不留下任何输出文件——清理本次写入的半成品，out_path 保持调用前状态（旧产物保留）。converter 的异常清理分支随之删除；取消分支保留但简化为只删完整产物（取消删完整产物是 converter 层业务语义，不属于 writer 契约）。写路径本身（save 到 `<stem>.mf4` → os.replace 原子替换）不变，成功输出字节零变化。

## User Stories

1. 作为维护者，我想 write_mdf 自身保证「失败无残留」，以便任何调用方（含未来新调用方）无需复述清理逻辑。
2. 作为维护者，我想 converter 不再复述 writer 的中间文件命名，以便 writer 将来调整临时文件策略时清理不会静默失效（deletion test 通过）。
3. 作为重跑用户，我想转换失败时上一次成功产物保留，以便不丢失有效输出（Q1=B：失败 ≠ 全清）。
4. 作为 GUI 用户，我想转换失败时仍看到错误弹窗（现状保持），以便知道转换未成功、不会把旧产物误当新产物（GUI 零改动，错误提示承担防误用）。
5. 作为验收工程师，我想失败清理的语义被测试锁定（save 失败与 replace 失败两个注入点），以便契约不回归。
6. 作为维护者，我想 Ctrl+C（KeyboardInterrupt）时半成品同样被清理，以便任何异常路径都不留残留（Q3=B：BaseException）。
7. 作为维护者，我想清理自身失败（文件被锁等）时不吞掉主异常，以便真实错误总是可见。
8. 作为验收工程师，我想 converter 在 write_mdf 失败时上抛且不重复清理，以便「清理只在一处」的契约边界有测试守护。
9. 作为 GUI 用户，我想取消转换后输出位置无产物（现状保持），以便「取消 = 放弃本次转换」语义不变。
10. 作为维护者，我想取消分支不包含死代码（写成功后 .mf4 必已消费，删两份中 .mf4 那份无效），以便代码不暗示错误契约。
11. 作为测试作者，我想「write_mdf 完成后取消」这一分支有测试覆盖，以便 converter 侧唯一剩余清理代码有守护（现状 5 个取消测试均未覆盖该分支）。
12. 作为维护者，我想「无残留」术语在 CONTEXT.md 有定义，以便未来审查引用该契约时有唯一含义（Q9=A）。
13. 作为验收工程师，我想成功路径行为零变化（字节级），以便对拍逐位一致与性能基线不受影响。

## Implementation Decisions

### D1. 契约语义：失败清理本次半成品，out_path 保持调用前状态

write_mdf 抛出异常时：清理本次写入的半成品文件；**out_path 不被触碰**——保留调用前状态（旧产物保留，Q1=B）。契约可精确表述为「本次调用不留下任何文件」。删除旧产物是破坏性操作且超出 writer 职责（旧文件不是本次调用产生的）；防误用由调用层错误提示承担。

### D2. 契约范围：仅异常路径；取消不在契约内

「无残留」只覆盖 write_mdf 自身失败。取消（ScanCancelled）是 converter 层业务语义：取消检查点全部位于 converter 层（输入扫描、并行 finish、写前、写后），取消时 write_mdf 已成功返回，删除完整产物是「取消放弃本次转换」的决定，不属于 writer 契约（Q2=A）。write_mdf 不新增取消相关参数，不引入跨层异常依赖。

### D3. 捕获范围：BaseException + 清理失败吞掉

清理逻辑捕获 BaseException（含 KeyboardInterrupt/SystemExit，Q3=B）；清理动作自身失败（`OSError`，如文件被锁）静默吞掉——主异常优先上抛。顺带修复现状缺陷：converter 现状在 except 块内直接 os.remove，清理抛错会覆盖原异常。

### D4. 实现形状：try 覆盖 save 与 os.replace，except 清理单路径

write_mdf 内部用单个 try 包住 save 与 os.replace 两个调用，except 清理后重新抛出。清理对象为**单一路径** `Path(out_path).with_suffix(".mf4")`——asammdf 8.8 的 MDF4.save 对目标路径无条件执行 `with_suffix(".mf4")`（已核实源码 [mdf_v4.py:10926](../../../C:/ProgramData/anaconda3/Lib/site-packages/asammdf/blocks/mdf_v4.py#L10926)），save 实际写入的文件恒等于该路径，与 out_path 是否带 `.mf4` 后缀无关。形状为数行 try/except，不新增函数、不新增抽象。

### D5. converter 异常分支删除，取消分支简化

converter 的异常清理分支（写后 except 内两份文件删除）整体删除——清理归 writer。取消分支保留但简化：write_mdf 成功返回后 `.mf4` 必已消费（replace 已改名到 out_path），取消分支只删 out_path，注释更新为「写成功后无 .mf4 残留，仅删完整产物」并说明该删除是业务语义（Q6=A）。

### D6. 文档：CONTEXT.md 新增「无残留」词条

词条内容：「无残留：write_mdf 的失败契约——调用抛出（含 BaseException）时，本次调用不留下任何输出文件，out_path 保持调用前状态；取消（ScanCancelled）不在该契约内，取消删除完整产物是 converter 层业务语义。」（Q9=A）

## Testing Decisions

- **只测外部行为，不测实现细节**：writer 层穿 `write_mdf` 接口 + 注入真实失败点（`MDF.save` 抛错、`core.mdf_writer.os.replace` 抛错、`MDF.save` 抛 KeyboardInterrupt）；converter 层穿 `convert` 接口。不触碰内部辅助、不断言实现文本。
- **测试 seam 共 2 个，均为现有，无新增**：① `write_mdf` 接口（tests/test_mdf_writer.py，契约主体所在层）；② `convert` 接口 + `cancel_cb`（tests/test_converter.py，既有取消测试模式延续）。
- **被测模块**：core/mdf_writer（新增失败注入 3 用例）、core/converter（传播测试重写 + 写后取消新增 1 用例）。
  - writer 层用例 1：save 抛 OSError → write_mdf 抛 OSError、`<stem>.mf4` 不存在、out_path 保持调用前状态（预置旧文件 → 断言仍在）。
  - writer 层用例 2：os.replace 抛 OSError → 同断言（覆盖「save 成功、replace 失败」残留场景）。
  - writer 层用例 3：save 抛 KeyboardInterrupt → 清理 + KeyboardInterrupt 上抛（D3 验证）。
  - converter 层重写现有失败清理测试：write_mdf 抛错 → convert 上抛，不断言文件清理（清理归 writer 测试，converter 不再复述）。
  - converter 层新增写后取消用例：cancel_cb 首次调用放行（写前检查点）、第二次调用置位（写后检查点）→ out_path 被删 + ScanCancelled（D5 唯一剩余清理分支的守护）。
- **测试数据**：合成 BLF/DBC（既有 fixture 模式，无样例数据依赖）。
- **先例**：tests/test_mdf_writer.py 现有成功路径直测；tests/test_converter.py 现有 cancel_cb 计数翻转模式（test_convert_cancel_mid_read）与 monkeypatch 注入先例（test_convert_write_failure_cleans_partial_files）。
- **验收方式**：`python -m pytest tests/ -q` 与 `pytest tests/ -q` 两种调用方式全绿（249 passed 基线 + 新增 ~4 用例）；不重跑 AHT 真实数据对拍（成功路径零字节变化，对拍链不受影响）。

## Out of Scope

- 取消收进 writer（Q2=B 已否决；writer 不新增取消参数）。
- 原子写升级（如 .savetemp 二级临时文件）：现状 save→replace 已是原子替换结构，无残留契约下无需额外临时文件。
- converter 其他检查点/清理逻辑（输入扫描、并行 finish 阶段无文件输出，无残留问题）。
- GUI 改动（错误弹窗现状已足够）。
- 其余审查深化候选（C 归一化键 / G 死字段 / B 绑定决策 / A 桶契约）与 tools/ 脚本清理（bench_*、probe_*）。
- 性能与对拍路径的任何变化（写路径与成功输出字节不变）。

## Further Notes

- **已核实事实（2026-08-18）**：① asammdf 8.8 MDF4.save 无条件 `with_suffix(".mf4")`（源码 [mdf_v4.py:10926](../../../C:/ProgramData/anaconda3/Lib/site-packages/asammdf/blocks/mdf_v4.py#L10926)）；② write_mdf 生产调用者唯一 = converter（gui/ 不直接调用）；③ 现状测试 [tests/test_converter.py:192](../../tests/test_converter.py#L192) `test_convert_write_failure_cleans_partial_files` monkeypatch 掉整个 write_mdf 断言 converter 清理——D 落地后该测试失效，按 Testing Decisions 重写；④ 现状 5 个取消测试覆盖写前/读中/并行 finish 检查点，均未覆盖「write_mdf 完成后取消」分支；⑤ GUI 异常处理仅为错误弹窗，无删除输出文件逻辑。
- 本 spec 按项目惯例置于 docs/reviews/ 根目录；无外部 issue tracker（项目以 docs/reviews/ 为 spec 住所，与 H1/H2 同）。
- 不提交 git（用户指示）；提交由用户执行（项目惯例）。
- 后续：spec 批准后按 Implementation Decisions 实施（任务粒度：writer 契约 → converter 简化 → CONTEXT.md 词条 → 测试 4 用例 → 全量验证）。
