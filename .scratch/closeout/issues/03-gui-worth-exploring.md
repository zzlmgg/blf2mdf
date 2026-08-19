# 03 — GUI 面 Worth exploring 逐条裁决

Type: grilling
Status: open
Blocked by:

## Question

三条 GUI 面候选逐条裁决（做则同票落地）：

1. **视觉令牌收敛或双轨明示**（review §4 GUI P2）：同一色值 #207e4b 出现在 theme.py:42 / widgets.py:169 / main_window.py:670 三处——团队实际迭代方式是就地改色还是收敛到 theme.py 单点？双轨明示（承认就地改色为常态）比强制收敛更诚实？
2. **统一两个 worker adapter 骨架**（GUI P3）：ConvertWorker / BlfScanWorker 骨架同构是已兑现成本——拿一版对照后合并是否更可读？保留现状也算裁决
3. **test_gui_binding 表格状态查询收敛**（M7）：约 35 处 `cellWidget(...).currentText()` / `table.item(...).text()` / 私有方法直调收口到 MainWindow 上 2-3 个稳定方法面——「interface 是测试面」原则下测试穿透接口打实现的收口

约束：UI 显示与操作逻辑零变化（review Q9 硬约束先例）；核心评估标准为「重构后测试打接口而非实现」。
