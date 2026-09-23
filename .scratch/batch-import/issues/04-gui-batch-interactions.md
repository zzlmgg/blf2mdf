# 04 — 界面：批量专属的交互收口

**What to build:** 让批量特有的三个决策在界面上显式化，而不是悄悄发生。

其一，实际参与批量的文件超过 1 个时弹「共用一套配置」提示：说明这批文件共用同一套
平台 + 项目 + CAN-DBC 匹配、切换会同时作用于全部文件，并告知「某个文件中不存在的通道
不会被导出」；只涉及 1 个文件时不弹；在提示上选择返回勾选列表时不改变任何现有配置。

其二，批量开始前一次性检查所有输出路径是否已存在，已存在的合成**一次**询问
（覆盖全部 / 跳过已存在 / 取消），而不是逐文件弹窗。

其三，结束时按结果分支弹窗：全部成功显示「N 个 blf 文件全部转换完成」；有失败显示
「N 个 blf 文件转换成功 X 个，失败 Y 个」并列出失败文件名。

**Blocked by:** 03 — 界面：拖入 → 勾选 → 批量转换（端到端闭环）

**Status:** resolved (2026-09-23)

- [x] 涉及 > 1 个文件时弹出共用配置提示，确认后才进入配置与转换
- [x] 只涉及 1 个文件时不弹该提示
- [x] 提示上返回勾选列表 → 回到列表，现有平台/项目/绑定未被改动
- [x] 批量开始前一次性检查输出存在性，并一次询问覆盖全部 / 跳过已存在 / 取消
- [x] 选择「跳过已存在」时已存在的文件不转、其余照转，结果弹窗如实反映跳过数
- [x] 部分失败 → 结果弹窗给出成功/失败数量与失败文件名清单
- [x] 全部成功 → 结果弹窗显示「N 个 blf 文件全部转换完成」
- [x] 日志逐文件记录结果与耗时

## Comments

（无）

## Answer（2026-09-23）

**落地**：`gui/main_window.py` 新增两个接缝（`_confirm_shared_config` /
`_choose_overwrite`）并重排导入与批量入口；`gui/candidate_dialog.py` 加 `checked`
形参；`tests/test_gui_batch.py` 新增 7 例（共 27 例）；README「使用」1/4 两条同步。

**链路**。导入侧拆出 `_choose_import_selection`：候选 1 个不弹列表；勾选结果 > 1 个
才走 `_confirm_shared_config`（继续 → 前进；返回勾选列表 → 带上次勾选重开列表，可
反复）。提示落在**扫描之前**，故「返回」时平台/项目/绑定一个都还没配（它们在扫描
之后才配），反悔代价为零——这也是勾选能原样带回去的原因。转换侧 `_start_batch_convert`
先做输出预检：已存在的合成一次三选一，跳过者记进 `self.skipped` 并从待转清单里摘除
（界面拥有跳过策略，`core.batch.run_batch` 只收不冲突的子集，其「outcomes = 已转
文件」的契约不动）；收尾按结果分支报数，跳过的留在批次原位上。

**三处定调**（写下来是因为它们各自都有一个「另一种做法也行」的岔路）：

1. **有跳过时的成功文案与票面字面不同**：勾选框 7 要求「全部成功 → 『N 个 blf 文件
   全部转换完成』」，勾选框 5 要求「结果弹窗如实反映跳过数」——一旦发生跳过，这两条
   互相拉扯（跳过的文件确实没被转换，说「全部转换完成」是假话）。取勾选框 5：无跳过
   时逐字仍是「N 个 blf 文件全部转换完成」（票 03 的用例原样通过），有跳过时改报
   「N 个 blf 文件转换成功 C 个，跳过 S 个已存在」，N = 已转 + 跳过（本批文件数），
   状态栏与日志同源。
2. **一个都不剩时照走 core**：全部已存在且选择跳过 → 待转清单为空，仍然调用
   `run_batch([])` 而不是在界面短路「没什么可转」。理由是只留一条收尾路径：弹窗、
   日志、状态栏都由同一段代码产出，报「转换成功 0 个，跳过 N 个已存在」是真话；
   短路会多出一条不走 `_finish()` 的旁路。用例 `test_batch_skip_existing_with_
   nothing_left_reports_zero_converted` 用真身 core 验证。
3. **进度分母 ≠ 结果分母**：跳过之后，进度条报「第 i/N 个」的 N 是**实转**文件数，
   结果弹窗的 N 是**本批**文件数（含跳过）。两者都如实，但同屏数字不一致——跳过的
   文件没有被转换，就不该占进度条的名额。

**验收**：全量 `pytest` **369 passed**（134.82s，审查修改后的冻结修订）= 票 03 的 362 例
零改动（含
`tests/test_gui_binding.py` / `tests/test_gui_style.py` 两个单文件回归硬门）+ 本票
新增 7 例；本票 `tests/test_gui_batch.py` 27 passed。8 项勾选框对应：

1. 提示（> 1 个）：`test_shared_config_notice_precedes_the_batch_configuration`
2. 1 个不弹：`test_shared_config_notice_absent_when_only_one_file_is_involved`
   （勾选剩 1 个 + 候选只有 1 个两条路）
3. 返回不动配置：`test_shared_config_notice_returns_to_checklist_without_touching_config`
   （提示期两次快照逐项相等，第二轮带上上次勾选）
   文案要点：`test_shared_config_notice_states_scope_and_absent_channels`
4. 一次性存在性检查 + 三选一：`test_batch_precheck_dialog_offers_overwrite_skip_and_cancel`、
   `test_batch_convert_asks_once_before_overwriting_outputs`（一次询问、取消不碰产物）
5. 跳过照转、报数如实：`test_batch_skip_existing_converts_the_rest_and_reports_skips`、
   `test_batch_skip_existing_with_nothing_left_reports_zero_converted`
6. 部分失败报数点名：`test_batch_failure_notice_counts_and_lists_failed_files`（票 03
   落地，本票保持）
7. 全成功文案：`test_batch_progress_shows_file_index_and_completion_notice`（票 03）
   与本票无跳过的分支
8. 日志逐文件结果 + 耗时：同上两条用例（`完成 (总耗时 X.X s)` / `失败 — 原因` /
   `跳过（输出已存在）`），新增断言锁**日志顺序 = 批次顺序**

**未做（有意留边界）**：

- **失败文件没有耗时**：`完成` 行带耗时，`失败` 行只有原因——`core.batch.FileOutcome`
  的失败分支不携带耗时（`convert` 直接抛异常，只有成功才有 `result.timings`），界面
  层拿不到。补它要给 `FileOutcome` 加字段 + 在 `run_batch` 里计时，属 core 契约变更，
  不在「界面收口」这一票的范围内；若将来需要（例：某个文件转 8 分钟后失败），单开一票。
- **取消文案与关闭窗口确认**归票 05（`_on_batch_cancelled` 的「已完成 X / N」、批量下的
  关窗措辞）。本票未动。
- **`checked` 形参算不算越界**：票面只要求「返回时不改变现有配置」，没要求保留勾选。
  保留勾选是 story 22「反悔后代价为零」的直译（返回一次就要重勾 30 个文件，等于惩罚
  反悔）。默认不传 = 全选，`CandidateDialog` 的既有契约不变；代价是票 03 的 8 个
  `_choose_candidates` 桩签名要跟着加 `checked=None`（仅本特性自己的测试文件，两个
  单文件回归硬门零改动）。

**审查记录**（`/code-review` 两轴，均在本票内处置）：标准轴 6 条已改——`self.skipped`
的归零点提前到预检之前（原来取消分支会留下上一批的清单，与注释声称的「每次开头重新
算定」不符）、日志改为按 `self.batch` 顺序生成（原来跳过的行被挪到末尾）、
`_choose_candidates` 补类型标注、`_is_batch` 的「阈值只此一处」改为如实说明导入阶段
另有两处「1 个」判断、跳过措辞收敛到一处（`skip_clause` + 两种分隔符）、常量改名
`PRECHECK_*`（`OVERWRITE_CANCEL` 原名混了 CONTEXT.md 的「取消」= 中止进行中的转换，
这里是「不开始」）；`untouched` 输出路径集合改为直接按候选判定。未采纳 2 条：
`checked` 形参改名 `prev`（对话框自身是 `checked=` 进、`checked_candidates()` 出，
同源更顺）、测试里 `candidate` 遮蔽 `Candidate`（Python 大小写敏感，不构成遮蔽）。
