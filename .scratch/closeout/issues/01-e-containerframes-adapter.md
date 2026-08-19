# 01 — E: ContainerFrames → 帧序列转换 adapter

Type: prototype
Status: open
Blocked by:

## Question

候选 E（review §4 唯一未落地的 Strong）：为 `ContainerFrames`（packed/scattered 双契约，[blf_vector.py:34-54](../../../core/blf_vector.py#L34-L54)）成型一个「帧序列转换 adapter」是否值得？H7b 已实证表示变更成本——一次 scattered 契约引入迫使 test_blf_vector 7 处测试更新（`_payload` helper + 5 处直接断言，[master plan §5.1 H7b](../../../docs/func_impro/2026-08-15-blf-mdf-conversion-master-plan.md) 逐处记载）；adapter 同时简化 converter（`_bucket_block` 消费面）与测试两侧的帧语义重建。

本票裁决：先产出粗 adapter 设计（形状、接口、测试面如何收敛），再定采纳与否；采纳则落地并更新测试（对拍链与 pytest 全量验收，289+ 全绿）。

约束：scattered 补零语义（glen < data_len 行尾补零）是消费者契约，adapter 不得改变逐位一致结果；不新增无谓抽象（标准⑥）。
