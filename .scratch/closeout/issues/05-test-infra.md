# 05 — 测试基建面裁决

Type: grilling
Status: closed (2026-08-19)
Assignee: claude (claimed 2026-08-19)
Blocked by:

## Question

三项测试基建候选逐条裁决（做则同票落地）：

1. **conftest fixture 上收**（review §4 候选「conftest 上收 fixture」）：两份 GUI fixture 重复 + conftest import 时 mkdir 副作用——上收单一 fixture 并消除副作用
2. **sample_blf 显式化**：样例路径依赖「第一个 BLF」（排序漂移风险）——显式化样例选择语义
3. **冻结探针转 subprocess pytest**（frozen_gui_probe / frozen_probe）：进程级行为进测试体系（pytest 收集即 import 的探针在冻结上下文不可测）——评估将冻结探测转为 subprocess 级契约测试的收益 vs 成本

约束：不削弱现有 seam 质量（test_converter 契约测试标杆）；改动以「测试打接口而非实现」为评估标准。

## Resolution (2026-08-19)

用户裁定 Q1「做」+ Q2「做」+ Q3「不做」：

- **Q1 conftest fixture 上收（做）**：`qapp`（session）+ `window`（function）上收 conftest 单一来源；window 统一为 style 版规范形（yield + close，防跨测试窗口泄漏）；删除 import 时 `OUTPUTS_DIR.mkdir` 副作用及无引用常量 `OUTPUTS_DIR`/`MDF_DIR`（均为历史残留，GUI 默认输出早已改为 BLF 同目录 `_t` 命名）；两个 GUI 测试文件本地 fixture 定义及随附的 `import pytest`/`QApplication` 顶部 import/`QT_QPA_PLATFORM` 设置移除（env 一并上收 conftest）
- **Q2 sample_blf 显式化（做）**：固定具名 `SAMPLE_BLF_NAME = "A19G1_ACFCAN_00112_20260614_141114.blf"`（与冻结探针同文件），缺失即返回 None（既有 4 个测试点 skip-if-None 语义不变）；删除「排序取首 + 排除 1.2GB 文件」逻辑——新增/改名样例不再静默改变测试输入
- **Q3 冻结探针转 subprocess pytest（不做，入 Out of scope）**：探针仅在发布 exe 时运行（低频路径），subprocess 化需 exe 常备、否则常 skip 成死重量；构建配置与 spec 的 sed 同步脆弱、subprocess 时序断言脆；发布路径本就手动，README:73-88 已记录断言（PROBE_OK / GUI_PROBE_OK）
- **验收**：GUI style + binding + blf_reader + blf_vector **106 passed**；全量 pytest **291 passed = 基线零回归**；无生产代码改动，对拍链判定能力不受影响
- 改动面：tests/conftest.py（上收 + 常量清理 + 样例具名）、tests/test_gui_style.py、tests/test_gui_binding.py（删除本地 fixture 与随附 import/env）
