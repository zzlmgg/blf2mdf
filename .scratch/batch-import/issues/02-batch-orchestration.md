# 02 — core 批量编排与逐位一致对拍

**What to build:** 给一份待转清单（每项 = BLF 路径 + 输出路径，与 01 的候选条目同形）和一套
通道↔DBC 绑定，按顺序逐个调用既有的单文件转换。每个文件独立汇报成功或失败原因，单个文件
失败**不中断**批次，剩余文件继续；整体进度按 (i + 文件内进度)/N 上报且单调不降；取消时停止
队列——当前在转文件按其自身契约清理（无残留），已完成文件的产物全部保留。

这个模块是「批次」这一概念的**唯一定义处**，界面层不得自己写转换循环。

**Blocked by:** 01 — core 来源解析与输出路径映射（清单条目同形，避免两个会话对条目形状理解不一致）

**Status:** resolved (2026-09-23)

- [x] 按顺序转换 N 个文件，逐文件返回成功结果或失败原因
- [x] 单个文件失败不中断批次，剩余文件继续转换
- [x] 失败文件的输出遵守无残留：不留半成品，该路径上一次成功的产物保留
- [x] 取消 = 停止队列 + 当前在转文件清理 + 已完成文件的产物全部保留
- [x] 整体进度单调不降，映射为 (i + 文件内进度)/N
- [x] 全部成功与部分失败的返回结构可区分（含失败清单）
- [x] 硬门：N 份批量输出与逐个单文件转同一批的输出**逐位一致**
- [x] 模块无 Qt 依赖，可脱离界面直接单测
- [x] CONTEXT.md 登记「批次」词条，并把「取消信号」「无残留」两条契约扩展到批次语义
      （批次取消 ≠ 单文件取消：已完成产物必须保留）

## Answer（2026-09-23）

**落地**：新增 `core/batch.py`（`run_batch` + `FileOutcome` / `BatchResult` / `BatchCancelled`）与
`tests/test_batch.py`（10 例，含「无 Qt」子进程断言）；`mdf_writer` 接手镜像树中间目录的创建与失败
回收（票 01 交下来的），`tests/test_mdf_writer.py` 补 3 例；CONTEXT.md 登记「批次」（含失败语义）并
扩展「取消信号」「无残留」（含目录链、批量下逐文件成立）「绑定」（按 spec「无数据」批量定义）。

契约：`run_batch(candidates: list[Candidate], bindings, *, progress_cb, cancel_cb, parallel=True,
raw_export, stats_export) -> BatchResult`；逐文件结局 `FileOutcome{candidate, result | error}`
（`.ok` 区分成败），失败清单 `BatchResult.failures`（空 = 全部成功）。进度
`stage = 「第 i/N 个 · <文件内阶段>」`、`percent = (index + 文件内进度/100) × 100 / N`（i 从 1、index 从 0）；
`parallel` 是**文件内**解码开关（批内恒串行，界面不得自建转换循环）。

**验收**：全量 pytest **342 passed**（147.48s，零回归）；本票文件 10 passed、`test_mdf_writer.py` 16 passed。
9 项勾选框逐条有对应用例：顺序与逐文件结局、失败不中断且失败清单可区分、失败保留旧产物不留半成品、
失败不在镜像树留空枝、取消三件套（停队列 / 当前文件清理 / 已完成产物保留）、进度单调且按文件区间映射、
硬门逐位一致（串行与并行各一遍）、无 Qt 依赖、CONTEXT.md 三处契约。提交 `3fc66e7`，分支 `mu`。

**两处定调**：

- **硬门用对拍链判「逐位一致」，不用字节比较**：同一 BLF 两次转换的 `##FH` 块嵌保存时刻
  （实测差 3 字节），字节级比较必假红。判定走 `tools.mdf_compare.compare_files_identical`
  （按组序号对齐 + 文件头/结构/数值/统计四维度）。
- **取消（写成功后放弃）可能留一个空的镜像树空枝**：目录回收只属**失败**契约，精确回收需随调用
  消亡的建账，不做；边界写在 `write_mdf` docstring。敏感度（deletion test）：移除
  `_drop_created_dirs` → 2 测试红；批内强改 `stats_export=False` → 两个硬门用例红。

**留给下游**（本票不做）：

- 票 05 记「已完成 X / N」不必解析进度文案：`except BatchCancelled as exc:` → `exc.outcomes` 即已完成
  结局（含各自 `result.timings`）。继承 `ConversionCancelled` 保证界面既有 `except ConversionCancelled`
  一网打尽。
- `parallel` 默认 True 同现界面 `ConvertWorker`（`gui/main_window.py:105`）；界面改为只调 `run_batch`。
- 02 `resolved` → **03（拖入 → 勾选 → 批量转换）已解锁**；04 / 05 依赖 03。
