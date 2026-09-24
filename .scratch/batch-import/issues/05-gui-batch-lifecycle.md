# 05 — 界面：批次生命周期收口

**What to build:** 批量下取消、关闭与并发拖入的语义收口——都是「进行中的批次被打断」这一类
边界，与 03/04 的正常路径分开做，便于各自验收。

转换中取消 = 停止队列 + 清理当前在转文件 + 已完成文件的产物**全部保留**，并在日志里说明
「已完成 X / N」（与单文件取消「放弃本次转换」的语义区分开）。转换进行中关闭窗口要先询问确认。
解析/扫描进行中拒绝新的拖入（与「浏览…」按钮禁用保持一致，避免「接受了却无动作」）。

**Blocked by:** 03 — 界面：拖入 → 勾选 → 批量转换（端到端闭环）

**Status:** resolved (2026-09-24)

- [x] 批量转换中点取消 → 队列停止、当前文件清理、已完成产物保留，日志记「已完成 X / N」
- [x] 取消不与无残留契约冲突：已完成文件完全不受影响
- [x] 批量转换进行中关闭窗口 → 先询问；选择不关则转换继续
- [x] 解析/扫描进行中拖入被拒绝，且不启动新的加载
- [x] 解析/扫描可取消，取消后界面回到原状（不应用半途结果）

## Comments

（无）

## Answer（2026-09-24）

**落地**：`gui/main_window.py` 新增模块级 `_outcome_line` / `_skip_line`（批量结束与
批量取消共用同一套逐文件写法）、`BatchConvertWorker.cancelled` 由 `Signal(object)` 改
`Signal(object, int)`（分母随信号交出）、重写 `_on_batch_cancelled`、`closeEvent` 的
关窗措辞按批量 / 单文件分岔、`_finish()` 加 None 守卫；`tests/test_gui_batch.py` 新增
5 例（共 32 例）；README「使用」4 同步。

**链路**。5 个勾选框里 3 个的语义本来就落在 core（`core/batch.py:98-100`：取消不记入
失败清单、抛携带 `outcomes` 的 `BatchCancelled`，在转文件由单文件契约自己清理、已完成
文件的产物不受影响），另 2 个（解析/扫描中拒绝拖入、解析/扫描可取消回原状）由票 03
顺带落地。故本票的实际增量是**把 5 条都钉上测试**，加上取消日志的报数、关窗措辞与迟到
信号的修复——票面本身没有要求新的转换行为。

**三处定调**（各自都有一个「另一种做法也行」的岔路）：

1. **取消报的 N 取队列，不取本批**：N = 本次队列文件数（预检跳过的没进队列），与进度
   条同分母——票 04 定调 3 已写下「进度分母 ≠ 结果分母」，取消这一路跟着进度条走。代价
   是本批 3 个文件（跳过 1 个）取消时日志报「/ 2」，补偿是在取消日志里点名跳过的文件
   （`_skip_line`），让这个分母自解释。反过来的读法更糟：取本批为 N 则 X 永远到不了 N
   （跳过的不会再转），一个永远完不成的比例。
2. **X = 已定论的文件数（成功与失败都算）**：票 02 的 Answer 已经把这条定死——
   「`exc.outcomes` 即已完成结局」。逐文件照记耗时/原因，取消前已经发生的失败不因取消
   而无声；字面上「已完成」含失败读着略拗，但同一条日志下方就有 `失败 — 原因` 行，信息
   不缺。
3. **N 随信号交出，界面不重算**：`cancelled` 从 `Signal(object)` 变成
   `Signal(object, int)`，`total = len(self.candidates)` 由 worker 自己数。队列是 core
   的输入，界面重算一遍就是第二处定义（取消是异步到达的，界面手上的 `self.batch` 也可
   能已经不是这一次的了）。

**验收**：全量 `pytest` **374 passed**（129.22s，审查修改后的冻结修订）= 票 04 的 369 例
零改动（含 `tests/test_gui_binding.py` / `tests/test_gui_style.py` 两个单文件回归硬门）
+ 本票新增 5 例；本票 `tests/test_gui_batch.py` 32 passed。5 项勾选框对应：

1. 取消停队列 / 清当前 / 留已完成 / 记「已完成 X / N」：
   `test_batch_cancel_stops_queue_and_keeps_finished_products`——已完成文件的产物与单独
   转换**逐位一致**（`tools.mdf_compare.compare_files_identical`），在转文件不落半成品
2. 取消不与无残留契约冲突：同上
   + `test_batch_cancel_reports_the_queue_as_denominator`（跳过的文件不转、旧产物原样）
3. 关窗先询问：`test_close_during_batch_conversion_asks_first`（选「不关」→ 窗口还在、
   线程还在跑、取消事件未置位；选「关」→ 线程收线且迟到的结局仍进日志）
4. 解析/扫描中拒绝拖入：`test_resolve_in_progress_rejects_drops_and_cancel_restores`
   （Drop 与 DragEnter 都被拒 + `resolve` 只被调用一次）
5. 解析/扫描可取消回原状：同上 + `test_scan_cancel_discards_half_scanned_channels`
   （取消前后界面快照逐字段相等，半途扫到的通道不落 `blf_channels`）

**修掉的真实缺陷**：关窗取消由 `closeEvent` 自己收线并把 `worker_thread` 置 None，而
worker 的结局信号还在队列里、随后才被投递——此时 `_finish()` 对 None 调 `.quit()` 抛
`AttributeError`，被 Qt 经 `sys.excepthook` 静默吞掉，界面永久停在「批量转换中…」，这次
取消的结局与日志一并丢失。探针（`.tmp/probe_late_signal.py`）复现，`_finish()` 加守卫后
修好；删掉守卫 `test_close_during_batch_conversion_asks_first` 即红（deletion test 在提交
后的冻结修订上重跑验过：删守卫 → 1 failed / 31 passed，红点 `tests/test_gui_batch.py:1040`
的 `assert sys_errors == []`）。这是本票唯一改到非新增代码的地方。

**未做（有意留边界）**：

- **关窗时三条线程的收尾仍各写一遍**（resolve / scan / worker 的 `quit(); wait(); = None`）：
  `_finish()` 只管 worker 且还顺带复位界面，抽成一个共用 helper 会动到票 01/03 的既有路径，
  不在本票范围。
- **两个等待桩的公共循环不抽 helper**：`_looping_resolve_until_cancel` /
  `_looping_run_batch_until_cancel` 共享的只是一个两行的 `while not cancel_cb(): sleep`，
  抽出来的收益低于「不新增无谓抽象」的门槛，保持各写一遍。

**审查记录**（`/code-review` 两轴，均在本票内处置）：两轴结论一致——5 个勾选框全部满足，
无功能缺口，无 hard violation。标准轴 3 条已改：取消日志补点名跳过的文件（原来
`跳过（输出已存在）` 只写在批量结束路径，取消路径既不点名跳过者又报一个不含它的分母，
用户对不上账）、抽 `_skip_line` 与 `_outcome_line` 同源共用、`_settle_batch` 两个从不传
的形参删掉（`_looping_resolve_until_cancel` 的死默认值同理）。未采纳：`_state_snapshot`
与 `_config_snapshot` 合并（判的不是一回事：全界面原状 vs 配置项）、closeEvent 复用
`_finish()`（理由见上）、等待桩抽公共 helper（理由见上）。两轴都点到 README 的准确性问题
一并改掉：「只有正在转的那个文件不落半成品」低估了在转文件的后果——取消落在写 MDF 之后
时 `core/converter.py:450-452` 会删掉**完整**产物（「取消 = 放弃本次转换」），README 改为
如实写「放弃本次转换——不留半成品，连已写出的完整产物也会删掉」。
