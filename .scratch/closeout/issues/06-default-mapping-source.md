# 06 — DEFAULT_MAPPING 单一来源裁决

Type: grilling
Status: closed (2026-08-19)
Assignee: claude (claimed 2026-08-19)
Blocked by:

## Question

绑定表知识三份（M8）的收敛裁决：test_golden.py:16-27 BINDING / convert_aht.py:15-26 硬编码（后者随 ticket 02 删除）/ project_loader.DEFAULT_MAPPING——三处手工同步副本，改一处静默漂移。

候选方案成本权衡：
- **数据文件自动生成 DEFAULT_MAPPING**：消除手工同步，但引入构建/缓存机制，成本可能高于收益（review 标 Speculative）
- **轻量收敛**：DEFAULT_MAPPING 为唯一事实源，test_golden BINDING 改为引用/对拍断言（漂移在 pytest 阶段变响亮）

裁决：做 / 不做 / 选哪档。约束：绑定表是「绑定」术语的契约来源（CONTEXT.md：身份键 = DBC 路径），收敛不得改变键语义。

## Resolution (2026-08-19)

用户裁定：Q1「档 1（意图显性化）」做 + Q2「方案 A 不做（入 Out of scope）」。

**现状核实（票面前提已过期）**：「三份」中两份已删——convert_aht.py 硬编码（02 票）、test_golden.py BINDING（H2 commit 5b05e3a）。现存副本：生产侧 `DEFAULT_MAPPING`（总线名→通道号，发布 exe 无 txt 时的事实源；spec 不打包 txt）+ `inputs/dbc_ccu3.0/dbc_对应关系.txt`（源码运行形态事实源，GUI load_mapping 优先读，实测与 DEFAULT_MAPPING 逐条一致）+ 测试侧 MAPPING_TEXT（txt 等价快照）/ EXPECTED_MAPPING（期望锚）。

**轻量收敛（方案 B）已完整落地，无需实质动作**：四边互锁断言全部存在——`_parse_mapping_text(MAPPING_TEXT) == EXPECTED_MAPPING`（:35）、`DEFAULT_MAPPING == EXPECTED_MAPPING`（:47）、真实 txt 解析 == 锚（:86，存在时跑缺 skip）。任一处改键不改锚，pytest 必红。M8 的「漂移在 pytest 阶段变响亮」已达成。

**Q1 档 1 落地**（意图显性化）：
- :47 对拍断言从 `test_load_mapping_missing_file_falls_back` 提出为独立契约测试 `test_default_mapping_matches_contract_anchor`（命名即意图，docstring 写明改表必须同步 EXPECTED_MAPPING 与 dbc_对应关系.txt）
- `DEFAULT_MAPPING` 注释补「改表必须同步 dbc_对应关系.txt 与 EXPECTED_MAPPING（契约测试锚）」
- 明确否决「EXPECTED_MAPPING 派生自 DEFAULT_MAPPING」：派生后对拍断言变恒真，锚失去意义

**Q2 方案 A 不做**：机制已反向存在（txt 优先、DEFAULT 仅回退）；发布 exe 无 txt 全靠内置回退，回退值必须活在代码里，「由文件生成」语义不成立；构建/缓存机制属 Speculative 成本。入 map Out of scope。

**验收**：test_project_loader 19 passed（18+1）；全量 pytest 291 passed = 基线零回归。键语义（总线名→通道号 → auto_bindings 派生 channel→DBC path，落 CONTEXT.md「绑定」词条身份键 = DBC 路径）零变更。
