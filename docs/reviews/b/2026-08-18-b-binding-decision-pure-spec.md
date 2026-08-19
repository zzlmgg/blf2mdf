# B — 绑定决策抽无 Qt 纯函数 — 设计 Spec

> 来源：2026-08-18 架构审查（[2026-08-18-architecture-review.md](2026-08-18-architecture-review.md) 候选 B / M4，Strong）+ 本 spec 全部决策经 grilling 逐项确认（用户 Q1-Q10 均按推荐，2026-08-18）。词汇表见 [CONTEXT.md](../../CONTEXT.md)：对拍、逐位一致、回退 oracle、无残留、统计组布局、**绑定**（本轮新增词条）。

## Problem Statement

M4 的三点问题（含一处审查时未记录的隐式契约）：

1. **绑定链路 key 是格式化字符串**。`DbcDef.display_name`（[core/dbc_loader.py:58-66](../../core/dbc_loader.py#L58-L66)）产出 `PFCAN1.dbc（A19G1）`，`auto_bindings`（[core/project_loader.py:112-125](../../core/project_loader.py#L112-L125)）以它为值返回 `{通道: 显示名}`，GUI 端 `_dbc_by_display`（[gui/main_window.py:811-820](../../gui/main_window.py#L811-L820)）再逐项文本比对反查——显示名格式一变，绑定静默断裂，仅 test_gui_binding.py:110-113 兜底。
2. **决策算法嵌在表格操作里**。`_rebuild_channel_table`（[gui/main_window.py:575-633](../../gui/main_window.py#L575-L633)）含双键型契约（:614-615 注释明言「auto 键 int、prev 键 "CANn" 字符串，不可混用」）——该键型 bug 已发生真实事故（[test_gui_binding.py:1-5](../../tests/test_gui_binding.py#L1-L5) docstring 记载：int 键 vs "CANn" 键恒不命中 → 选完项目全表「不绑定」）；35 处测试必须穿越 Qt 对象图才能打到这个算法。
3. **隐式契约：行集合来源双轨**。:589 `mapped = set(self.auto_bind or {})` 用 `self.auto_bind` 而非 `auto` 参数——传了 `auto` 而 `self.auto_bind` 为 None 时映射通道不产生行。生产路径两轨同值（`_select_project` 先设 self.auto_bind 再传同一份），测试因通道恰在 BLF 内未暴露；契约靠调用方纪律而非结构保证。

## Solution

- 新建 **gui/binding.py**（零 Qt import）：`BindingRow` dataclass + `decide_bindings()` 纯函数 + `derive_state()` 状态推导。决策算法（行集合、选择优先级、初始状态）从 MainWindow 整体抽出。
- **绑定键结构化**：`auto_bindings` 契约改为 `{通道号: DBC 文件路径}`；`display_name` 只作展示。combo 项 `userData = DbcDef`，`_start_convert` 直接 `currentData()`；`_dbc_by_display` 删除。
- `_rebuild_channel_table` 退化为渲染：收集 prev → 调 `decide_bindings` → 建行/填 combo/设状态。
- **UI 显示与操作逻辑零变化**（用户 Q9 确认）：行集合、选择优先级、状态三态文字与颜色、「无数据」行交互边界、窗口几何全部照现状。

## User Stories

1. 作为维护者，我想绑定键是结构化身份（DBC 文件路径），以便显示名格式变更不再导致绑定静默断裂（M4 核心诉求）。
2. 作为维护者，我想决策算法是零 Qt 纯函数，以便全部决策分支测试不经 QApplication/offscreen 即可锁定。
3. 作为维护者，我想双键型契约（auto int / prev "CANn"）与「行集合用 self.auto_bind 而非 auto 参数」的隐式契约消失，以便键型单一、行集合来源显式（Q3=A）。
4. 作为验收工程师，我想 UI 显示与操作逻辑逐项不变，以便行为等价由既有测试迁移锁定（Q9 确认：含「无数据」行用户交互后状态由信号接管、不回到「无数据」的现状语义）。
5. 作为验收工程师，我想全量 pytest 保持全绿（259 基线之上），以便回归由既有 + 新增测试承担。

## Implementation Decisions

### B1. gui/binding.py 模块（Q1=A）

新建模块，**零 Qt import**（可被无 qapp 的测试直接导入）。内容：

- `BindingRow(channel: int, binding: str | None, state: str)` dataclass——binding 为 DBC 路径或 None（未绑定）。
- 状态常量：`STATE_BOUND = "已绑定"` / `STATE_NOT_EXPORTED = "不导出"` / `STATE_NO_DATA = "无数据"`（导出供窗口颜色表与测试断言使用，防跨侧文字漂移）。
- `derive_state(in_blf: bool, binding: str | None) -> str`——状态是 (通道是否在 BLF, 选择) 的纯函数：`in_blf and binding` → 已绑定；`in_blf and not binding` → 不导出；`not in_blf` → 无数据。
- `decide_bindings(blf_channels: list[int], auto_bindings: dict[int, str] | None, prev: dict[int, str | None] | None, dbc_paths: list[str]) -> list[BindingRow]`：
  - 行集合 = `sorted(set(blf_channels) | set(auto_bindings or {}))`——行集合来源显式化（B3）；
  - 选择（三态分派，实施修订：原稿「auto 优先于 prev」与 B3「保持现状 keep_prev=True 语义」矛盾——现状 auto 参数仅在选项目时传入且彼时 prev 恒空，合一后 auto_bindings ≡ self.auto_bind，非选项目场景 prev 必须压过它，否则添加/移除 DBC 覆盖用户微调）：
    - `prev[ch]` 值有效（路径在 dbc_paths）→ 用户微调，优先；
    - `prev[ch] is None` → 显式「不绑定」，压制 auto；
    - `prev[ch]` 值无效（其 DBC 被移除）→ 回退 auto 重绑（现状 self.auto_bind 第 3 级兜底语义）；
    - 否则 `auto_bindings[ch]` 在有效集合内 → 命中；否则 None（不绑定）；
  - 状态：`ch not in blf_channels` → 无数据；否则 `derive_state(True, binding)`；
  - 行按通道号升序（与现状 `sorted(all_channels)` 一致）。

### B2. 绑定键结构化（Q2=A）

- [core/project_loader.py:112-125](../../core/project_loader.py#L112-L125) `auto_bindings` 返回值改 `{ch: d.path}`；docstring 改写：键为 DBC 文件路径（结构化身份），显示名只作展示。
- `DbcDef.display_name`（[core/dbc_loader.py:58-66](../../core/dbc_loader.py#L58-L66)）保留不动——combo 展示与「同名 DBC 靠文件夹后缀区分」语义不变。
- 同步改契约消费者：`tests/test_project_loader.py` 3 个契约测试（:171/:178/:186 断言值改路径）；`tools/render_pyside_ui.py:43-48` 与 `tools/frozen_gui_probe.py:68` 手工构造的 auto_bind 改 `d.path`。

### B3. 签名与调用点（Q3=A）

- `_rebuild_channel_table` 新签名 `(self, channels: list[int], prev: dict[int, str] | None = None)`；`keep_prev` 参数删除（keep_prev=False 语义 = 传 `prev=None`）；`auto` 参数删除（与 `self.auto_bind` 合一）。
- 新增私有 `_collect_prev() -> dict[int, str | None]`：从表格行 item(0) 解析 int 通道号 + `combo.currentData()` 取 path（UNBOUND 行 → None = 显式不绑定）——**prev 键型归一到 int 通道号**，双键型契约消失（实施修订：原稿 `dict[int, str]` 缺「显式不绑定」表达，None 值承载后与现状 keep_prev 收集 "不绑定" 语义一致）。
- 5 个调用点（:529 / :696 / :713 / :730 / :748）：
  - `_on_scan_done`（:529）、`_remove_dbc_at`（:696）、`_remove_selected_dbcs`（:713）、`_add_dbc`（:730）→ `prev=self._collect_prev()`（保持现状 keep_prev=True 语义）；
  - `_select_project`（:748）→ `prev=None`（选项目 = 重新套用自动匹配，覆盖用户微调；删除 :747-748 的 auto 双设，`self.auto_bind` 仍先赋值）。
- `self.auto_bind`（:167-169）类型注释改 `dict[int, str] | None`（值为路径）；docstring 同步。

### B4. 渲染层（Q4=A）

- `_rebuild_channel_table` 渲染步骤：建行 → combo（UNBOUND + 各 `dbc.display_name`，`addItem(text, userData=dbc)`）→ 按 `row.binding` 匹配 `userData.path` 设当前项 → `_set_status_item` 设状态（颜色表键改 B1 常量）。
- `_update_status`（:659-662）改调 `derive_state(True, currentData() 非 None)`——交互状态推导与纯函数同一实现（实施修订：原稿传 `ch in self.blf_channels` 会让「无数据」行在用户交互后回退「无数据」，与本节下一条及 Q9 矛盾；「无数据」只由初始渲染设定，信号接管后状态只取决于当前选择）。
- 「无数据」行交互边界照现状（Q9）：用户动过下拉后状态由信号接管、不再回到「无数据」。
- `UNBOUND = "不绑定"`（:34）留在 main_window——combo 空选项是渲染文字，不属于决策域。

### B5. 消费端与 `_dbc_by_display` 删除（Q5=A、Q8=A）

- `_start_convert`（:776-783）改 `bindings[ch] = combo.currentData()`——UNBOUND 项无 userData → None；有选择 → 该 DbcDef 对象（零查找、零反查表）。
- `_dbc_by_display`（:811-820）整体删除；其 3 处文本比对断言（test_gui_binding.py:109-113）删除——「理论上必有匹配」的防御由「选项全部来自 dbc_list + userData 结构保证」取代。

### B6. 测试（Q6=A）

- 新建 **tests/test_binding.py**（无 Qt、无 qapp fixture）：迁移 test_gui_binding 中绑定决策类 7 个测试（:34 自动绑定应用 / :45 兜底 / :53 prev 保留 / :63 缺 DBC 保持不绑定 / :71 映射无数据行 / :89 双项目同名区分 / :433 换项目残留行），并新增边界用例（实施修订：原稿「auto 优先于 prev」与三态分派相反，改为）：prev 优先于 auto（微调保留）、prev 显式不绑定压制 auto、prev 失效回退 auto 重绑、valid 过滤、行集合并集、状态推导（含 derive_state 三态直接断言）、空输入。共 14 用例。
- test_gui_binding.py 保留渲染/交互/窗口类测试（滚轮、滚动条、固定高度、几何、BLF 目录/输出跟随、拖拽、取消、日志——与绑定决策无关，原样保留）；新增 3 个薄接线测试（实施修订：原稿「1-2 个」按实际拆分为 3）：决策 → 表格 combo 文本/状态/userData 的渲染接线 + _start_convert 绑定组装两个场景（选中 → DbcDef / UNBOUND → None）。
- 文件头 docstring 的事故史（:1-5）保留为历史资产。

### B7. 范围外（Q7=A）

- L11 死代码（`_fit_table_width` 空方法、`_remove_dbc` 零调用、`_fit_window_height` 近 no-op）：独立清理项，不搭车。
- M8 绑定表三份知识（test_golden BINDING / convert_aht 硬编码 / DEFAULT_MAPPING）：绑定数据来源问题，独立条目独立处理。
- 其余审查深化候选（A 桶契约类型化等）。

## Testing Decisions

- **新增**：tests/test_binding.py 纯函数套件（14 用例，无 Qt、无 offscreen 依赖）。
- **改造**：test_gui_binding.py 32 用例 → 28（迁走 7 + 新增薄接线 3，其余原样）；test_project_loader.py 3 个契约测试断言改路径。
- **验收方式**：`python -m pytest tests/ -q` 与 `pytest tests/ -q` 两种调用方式全绿，全量用例数 ≥ 259 基线；不重跑 AHT 真实数据对拍——键契约变更不改变绑定结果（路径与显示名一一对应，`bindings` 的 DbcDef 解析结果不变），转换输出字节零变化，对拍链不受影响。
- 双轴 code-review 0 findings（项目惯例，实施完成后走 receiving-code-review 流程）。

## Out of Scope

- 任何 UI 显示/操作逻辑的变化（Q9 显式确认：行为等价是硬约束，全部由迁移测试锁定）。
- `display_name` 格式本身（展示格式是视觉契约，不动）。
- 决策域新抽象（不引入「绑定管理器」等层级——标准⑥）；prev 收集与 combo 渲染保持窗口职责（读 Qt 对象图无法纯化，纯化无收益）。
- L11 死代码、M8 绑定表收敛、其余审查候选。
- 性能路径（绑定决策在 UI 线程、行数 ≤ 数百，无性能前提）。

## Further Notes

- **已核实事实（2026-08-18）**：① `auto_bindings` 生产调用点仅 `_select_project`（gui/main_window.py:747），core 内零其他消费者；② `display_name` 消费点 = combo 展示（:612）+ `_dbc_by_display`（删）+ `auto_bindings` 返回值（改 path）；③ tools 侧 2 处手工构造 auto_bind（render_pyside_ui.py:43-48 / frozen_gui_probe.py:68）需同步改；④ 隐式契约：:589 行集合用 `self.auto_bind` 而非 `auto` 参数（生产两轨同值，测试未暴露——B3 显式化后行为不变）；⑤ prev 收集的键型事故史记载于 test_gui_binding.py:1-5。
- 顺带文档漂移：docs/func_impro/2026-08-13-mdf-canoe-comparison-v4.md:40 提及 `auto_bindings` 链路（未涉及返回类型，无需改）。
- 本 spec 按项目惯例置于 docs/reviews/（与 d/、g/、h1/ 同布局）。
- 不提交 git（用户指示）；提交由用户执行（项目惯例）。
- 后续：spec 批准后按 Implementation Decisions 实施（任务粒度：gui/binding.py 新建 → project_loader 契约改 → main_window 改造 → tools 同步 → 测试迁移/新增 → 全量 pytest → 双轴 code-review → 审查文档 §3/§4/§5 状态更新）。
