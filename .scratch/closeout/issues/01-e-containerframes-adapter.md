# 01 — E: ContainerFrames → 帧序列转换 adapter

Type: prototype
Status: resolved
Blocked by:

## Question

候选 E（review §4 唯一未落地的 Strong）：为 `ContainerFrames`（packed/scattered 双契约，[blf_vector.py:34-54](../../../core/blf_vector.py#L34-L54)）成型一个「帧序列转换 adapter」是否值得？H7b 已实证表示变更成本——一次 scattered 契约引入迫使 test_blf_vector 7 处测试更新（`_payload` helper + 5 处直接断言，[master plan §5.1 H7b](../../../docs/func_impro/2026-08-15-blf-mdf-conversion-master-plan.md) 逐处记载）；adapter 同时简化 converter（`_bucket_block` 消费面）与测试两侧的帧语义重建。

本票裁决：先产出粗 adapter 设计（形状、接口、测试面如何收敛），再定采纳与否；采纳则落地并更新测试（对拍链与 pytest 全量验收，289+ 全绿）。

约束：scattered 补零语义（glen < data_len 行尾补零）是消费者契约，adapter 不得改变逐位一致结果；不新增无谓抽象（标准⑥）。

## Answer

**裁决（2026-08-19，用户）：采纳并落地。** 原型 [proto-e-adapter/design.md](../proto-e-adapter/design.md) 成型，等价性预验证 [check.py](../proto-e-adapter/check.py) 全绿后按设计落地。

**形状**：adapter ≠ 新模块/包装类（`FrameSeq` 只服务测试 = 假设接缝，浅模块）——adapter = `ContainerFrames` 的载荷访问面（`payload_block(sel)` / `payload(i)` 两方法）+ 表示归一（`glen` 恒存在、packed 路径 `glen = data_len`；`scattered` 字段退役删除）。双物理布局保留（性能差异），消费语义唯一化：第 i 帧载荷 = `data8[data_off:data_off+glen]` 真实字节 + 行尾补零至 `data_len`。

**测试面收敛**：H7b 的 7 处测试更新成本 → 表示变更触达面 1-2 处；`_payload` 双分支 helper 删除（7 调用点直调 `cf.payload(i)`）；`_bucket_block` 双分支 6 行 → 1 行调用；两处 glen 表示断言保留。

**验收全链**（对拍链判定能力未削弱，判定目标仍为 `Frame.data` oracle）：
- check.py 预验证：9 个语义角落 + 两个真实样例 × 快/回退两路径 2,764 块全部逐位一致
- pytest：test_blf_vector 30 passed；全量 **289 passed**（基线持平）
- AHT 样例转换（GUI 绑定链复刻，[accept_aht.py](../proto-e-adapter/accept_aht.py)）：总耗时 23.45s（读入 7.27s，无性能回退）
- `compare_two_mdf` vs outputs/cmp_20260817_AHT.mdf：**230 组全部一致，exit 0**
- `full_compare` vs CANoe 参考：70/70 组信号集合完全匹配，3434 处判定差异 = 与基线同分（review F 记录的已知真实差异基线）

提交由用户执行（本 effort 纪律，不代提交）。
