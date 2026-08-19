# 06 — DEFAULT_MAPPING 单一来源裁决

Type: grilling
Status: open
Blocked by:

## Question

绑定表知识三份（M8）的收敛裁决：test_golden.py:16-27 BINDING / convert_aht.py:15-26 硬编码（后者随 ticket 02 删除）/ project_loader.DEFAULT_MAPPING——三处手工同步副本，改一处静默漂移。

候选方案成本权衡：
- **数据文件自动生成 DEFAULT_MAPPING**：消除手工同步，但引入构建/缓存机制，成本可能高于收益（review 标 Speculative）
- **轻量收敛**：DEFAULT_MAPPING 为唯一事实源，test_golden BINDING 改为引用/对拍断言（漂移在 pytest 阶段变响亮）

裁决：做 / 不做 / 选哪档。约束：绑定表是「绑定」术语的契约来源（CONTEXT.md：身份键 = DBC 路径），收敛不得改变键语义。
