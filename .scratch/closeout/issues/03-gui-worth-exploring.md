# 03 — GUI 面 Worth exploring 逐条裁决

Type: grilling
Status: closed (2026-08-19)
Assignee: claude (claimed 2026-08-19)
Blocked by:

## Question

三条 GUI 面候选逐条裁决（做则同票落地）：

1. **视觉令牌收敛或双轨明示**（review §4 GUI P2）：同一色值 #207e4b 出现在 theme.py:42 / widgets.py:169 / main_window.py:670 三处——团队实际迭代方式是就地改色还是收敛到 theme.py 单点？双轨明示（承认就地改色为常态）比强制收敛更诚实？
2. **统一两个 worker adapter 骨架**（GUI P3）：ConvertWorker / BlfScanWorker 骨架同构是已兑现成本——拿一版对照后合并是否更可读？保留现状也算裁决
3. **test_gui_binding 表格状态查询收敛**（M7）：约 35 处 `cellWidget(...).currentText()` / `table.item(...).text()` / 私有方法直调收口到 MainWindow 上 2-3 个稳定方法面——「interface 是测试面」原则下测试穿透接口打实现的收口

约束：UI 显示与操作逻辑零变化（review Q9 硬约束先例）；核心评估标准为「重构后测试打接口而非实现」。

## Resolution (2026-08-19)

**裁决**：Q1 做（双轨明示 + 代码面轻量收敛）；Q2 不做（保留现状，入 map Out of scope）；Q3 做（两方法面收口）。

**Q1 视觉令牌**：实际 4 处 #207e4b（review 记 3 处，漏 theme.py:227 QSS 进度条 chunk），分两轨。theme.py 新增 `POSITIVE_COLOR`/`NEGATIVE_COLOR` 常量 + 模块 docstring 双轨政策（QSS 字符串面就地维护——f-string 化不值得；代码面一律引用常量）；[widgets.py:169](../..//gui/widgets.py#L169) paintEvent 与 [main_window.py](..//gui/main_window.py#L686) `_set_status_item` 状态颜色表改引常量。QSS 两处（theme.py:43/:227）保持就地。test_gui_style 色值断言零改动（QSS 与像素值不变）。

**Q2 worker 骨架**：裁决「不做」。已拿两版对照（main_window.py:95-156）：重复面仅 try/except 骨架 ~8 行；progress 信号载荷不同（(str,float) vs (float)）→ 基类无法统一持有 progress 信号，子类仍各自声明信号；合并省下的 8 行以模板方法间接性换取，违背标准⑥「不新增无谓抽象」。已写入 map Out of scope。

**Q3 test_gui_binding 收口**：MainWindow 新增两个稳定方法面——`binding_row(row) -> (通道名, 显示名, 路径|None, 状态)`（None 即「不绑定」）与 `set_binding_selection(row, 显示名)`（setCurrentText + _update_status 状态接管，等价用户改下拉）；`_collect_prev` 改用 binding_row（方法面获得生产侧消费者，验证为真实接口而非测试专用 shim）。测试收口：test_rebuild 7 处查询直调 → 4 处 `binding_row` 断言；两处驱动（:400/:434）→ `set_binding_selection`（后者用 `mw.UNBOUND`）。保留穿透的仅三类本就测 widget 行为/布局的断言：滚轮事件（test_dbc_combo_ignores_mouse_wheel 的 cellWidget 是事件派发对象，非状态查询）、布局（verticalScrollBar/height/size）、类型（isinstance DbcCombo）——表格控件重构应打破它们，不该被收口掩盖。test_gui_style 的表格穿透属样式/布局断言，不在本票范围。UI 显示与操作逻辑零变化。

**验收**：GUI 测试 63 passed；全量 pytest **289 passed / 0 失败**（57.54s，与 2026-08-19 基线一致；6 条 warning 为 test_compare_cli 既有 gbk 解码警告）。
