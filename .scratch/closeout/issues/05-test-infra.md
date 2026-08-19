# 05 — 测试基建面裁决

Type: grilling
Status: open
Blocked by:

## Question

三项测试基建候选逐条裁决（做则同票落地）：

1. **conftest fixture 上收**（review §4 候选「conftest 上收 fixture」）：两份 GUI fixture 重复 + conftest import 时 mkdir 副作用——上收单一 fixture 并消除副作用
2. **sample_blf 显式化**：样例路径依赖「第一个 BLF」（排序漂移风险）——显式化样例选择语义
3. **冻结探针转 subprocess pytest**（frozen_gui_probe / frozen_probe）：进程级行为进测试体系（pytest 收集即 import 的探针在冻结上下文不可测）——评估将冻结探测转为 subprocess 级契约测试的收益 vs 成本

约束：不削弱现有 seam 质量（test_converter 契约测试标杆）；改动以「测试打接口而非实现」为评估标准。
