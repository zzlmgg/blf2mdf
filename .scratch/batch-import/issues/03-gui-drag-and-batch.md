# 03 — 界面：拖入 → 勾选 → 批量转换（端到端闭环）

**What to build:** 「输入 BLF」行接受文件夹与多文件拖入，解析出候选后弹出勾选列表（逐个勾选 +
全选/全不选 + 「已选 N / 共 M」计数）；确认后扫描选中集合的通道并取并集，沿用既有的平台/项目/
CAN-DBC 绑定流程；点「开始转换」顺序转换全部选中文件，进度显示当前是第几个（i/N）与该文件
内部的阶段；产物按解析期算定的输出路径落位；结束后弹出「N 个 blf 文件全部转换完成」。

候选只有 1 个时不弹勾选列表、直接进入；最终只涉及 1 个文件时，输出框可手改且手改值优先、
完成提示与日志文案与今天逐字一致——这是单文件路径的回归硬门。

**Blocked by:** 01 — core 来源解析与输出路径映射；02 — core 批量编排与逐位一致对拍

**Status:** resolved (2026-09-23)

- [x] 拖入文件夹或（一次拖入）多个 .blf → 弹出勾选列表，含全选/全不选与已选计数
- [x] 候选只有 1 个 → 不弹列表，直接进入
- [x] 勾选确认后扫描选中文件，通道表 = 这些文件通道的并集；映射通道在所有选中文件里都不存在
      时显示「无数据」
- [x] 点「开始转换」→ 顺序转换 N 个文件，进度显示 i/N 与该文件内部阶段
- [x] 产物按输出路径落位（散 .blf 就地；文件夹进同级镜像树）
- [x] 结束弹窗显示「N 个 blf 文件全部转换完成」
- [x] 只涉及 1 个文件时：输出框可手改、手改值优先，完成提示与日志与今天逐字一致
- [x] 涉及多个文件时：输出框只读、「浏览…」禁用；「输入 BLF」行显示来源与文件数
- [x] 转换进行中锁定会改变批次的控件（与今天的忙碌态锁同一批控件）
- [x] 拖入的可导入项为 0 时整行拒绝（沿用今天的拒绝语义）
- [x] 既有测试零改动通过（单文件回归硬门）
- [x] README 的拖拽入口说明同步更新（文件夹已可用；压缩包仍标为下一阶段）

## Comments

## Answer（2026-09-23）

**落地**：`gui/main_window.py` 重排导入链路；新增 `gui/candidate_dialog.py`（候选勾选列表）与
`tests/test_gui_batch.py`（20 例）；`gui/theme.py` 补 `dialogHeading` / `dialogHint` /
`candidateList` 三块样式；README「使用」1/4 两条同步（文件夹可用、压缩包仍标下一阶段）。

链路（`_start_import`）：单个散 .blf 走今天的 `_load_blf`（逐字不变）；其余（文件夹 / 一次多个
条目）→ `_start_resolve`（ResolveWorker；解析期状态文案「正在解析来源…已发现 N 个 .blf」，
遍历前不知总数故报计数而非百分比，收口时归还「就绪」）→ `_on_resolve_done`：0 个候选明确
提示；1 个直接进入；多个弹 `CandidateDialog`（默认全选 + 全选/全不选 + 「已选 N / 共 M」，
一个都不勾时禁用「确定」）→ `_begin_scan`（BlfScanWorker 逐个探测、通道取**并集**）→
`_on_scan_done` 提交批次。转换 `BatchConvertWorker` 整批转交 `core.batch.run_batch`
（界面不自建转换循环；stage「第 i/N 个 · …」与 percent 都由 core 映射好）。

状态模型：`self.batch` = 已提交批次；`self.pending`/`pending_source` = 本次扫描的输入身份
（`_finish_scan` 收口时清空）；`self.blf_path` 只在「恰好一个文件」时有值；`_is_batch`
（`len(batch) > 1`，阈值只此一处）是批量/单文件的分岔判据，凡按它分岔的（输出框形态、
可转换判定、转换入口、进度文案）都读它。导入侧收口一律走 `_end_import()`（停解析/扫描线程 →
输入行回到已就位状态 → 调用方记日志或弹窗）。

契约（新增两处 core 出口，界面复用而不重写规则）：`source_resolver.blf_candidate(path)` =
散 .blf 的就地输出规则（拖入单个 .blf 不经解析，规则仍只有一处定义）；`batch.overall_percent(i, N, p)`
= 「i/N + 文件内进度」刻度（批量转换与多文件扫描共用）。

**验收**：冻结修订上全量 `pytest` **362 passed**（285.81s，无 skip / xfail）= 既有 342 例
零改动 + 本票新增 20 例；`tests/test_gui_binding.py`（28 例，含两条拖拽用例）与
`tests/test_gui_style.py`（35 例）**零改动**通过——单文件回归硬门；本票 `tests/test_gui_batch.py`
20 passed。12 项勾选框逐条对应用例：

1. 勾选列表（全选/全不选/计数）：`test_candidate_dialog_*` 5 例 +
   `test_folder_drop_lists_candidates_and_scans_selection` +
   `test_multiple_loose_files_drop_lists_candidates`
2. 候选 1 个不弹列表：`test_single_candidate_skips_the_checklist`（断言未调用勾选接缝）
3. 并集与「无数据」：`test_folder_drop_lists_candidates_and_scans_selection`（只勾 2 个 →
   并集 `[1, 2]`）、`test_batch_scan_union_keeps_bindings_and_marks_absent_channel_no_data`
4. i/N 与文件内阶段：`test_batch_progress_shows_file_index_and_completion_notice`
5. 产物落位：`test_multiple_loose_files_drop_lists_candidates`（散件就地）+
   `test_batch_end_to_end_lands_products_in_the_mirror_tree`（真身转换 → 镜像树，产物不进源树）
6. 「N 个 blf 文件全部转换完成」：`test_batch_progress_…`、`test_batch_end_to_end_…`
7. 单文件逐字一致：`test_single_loose_file_drop_keeps_today_flow`、
   `test_single_candidate_skips_the_checklist`，加上既有单文件用例零改动通过
8. 多文件只读/禁用/来源与文件数：`test_batch_makes_output_readonly_and_shows_source`
9. 转换中锁定控件：`test_batch_progress_…`（忙碌态锁的是今天那一批控件）
10. 导入项 0：`test_non_importable_drop_is_rejected_on_the_row`、
    `test_drop_without_importable_items_warns_and_keeps_state`
11. 既有测试零改动：全量 362 passed，其中既有 342 例逐条通过（本票新增的 20 例全在新文件里）
12. README：README「使用」步骤 1 与 4

**三处定调**：

1. **「可导入项为 0 → 整行拒绝」分两层**：结构性拒绝（非 .blf 且非文件夹）逐字沿用今天语义
   （悬停与放下都不接受、事件被消费）；而「文件夹里有没有 .blf」只有解析后才知道（悬停期递归
   遍历目录不可接受），故解析出 0 个候选时**明确提示**「未找到可导入的 .blf 文件」并保持原状——
   不是静默无反应，也不假装拒绝（事件早已放下）。
2. **批量开始前的一次性覆盖询问（是/否）**：与单文件「输出文件已存在 → 是否覆盖？」对齐，
   整批只问一次而不是逐文件弹窗，底线是「不静默覆盖」。票 04 将其升级为三选项
   （覆盖全部 / 跳过已存在 / 取消）并如实反映跳过数。
3. **批量取消的界面收口**：按 `BatchCancelled` 携带的已完成结局记
   「转换已取消（已完成的 N 个文件产物保留）」——取消不是失败，也不复述 core 的清理细节；
   「已完成 X / N」的措辞归票 05。
4. **转换进行中也拒绝拖入**（本票的行为变化，单文件同样受影响）：判据从「扫描线程在跑」扩到
   `_input_busy()`（解析 / 扫描 / 转换任一在跑）。勾选框 9 要求转换期锁定控件，而输入行在
   批量下**就是**批次的来源——转换中放行一次拖入等于中途换掉待转清单，与「锁定」自相矛盾。
   旧码此时 `scan_thread` 已归位、判据放行，落进 `_load_blf` 起一次新扫描：通道表与输出路径
   在转换中途被换，属未定义状态（`_load_blf` 现在同样读 `_input_busy()`，与拖入一致）。

**顺带落地（票 04 / 05 的条目；不做就会留下坏状态）**：

- 04 第三条（结束弹窗按结果分支）：全成功「N 个 blf 文件全部转换完成」；有失败「N 个 blf 文件
  转换成功 X 个，失败 Y 个」+ 失败文件名与原因——批次里有失败却只报「全部完成」是假话。
- 05 的两条（解析/扫描进行中拒绝拖入；解析/扫描可取消且不应用半途结果）：不做则扫描期间第二次
  拖入会打乱状态。

**留给下游**：03 resolved → 04（批量专属交互收口）与 05（批次生命周期收口）的 `Blocked by: 03`
均已满足，两票解锁。按「顺带落地」一段对账，04 剩余 = 共用配置提示、输出预检的三选项
（覆盖全部 / 跳过已存在 / 取消，含跳过数如实反映）；05 剩余 = 取消文案的「已完成 X / N」、
关闭窗口确认的批量措辞（今天的确认框文案未动，批量下仍沿用）。
