# 08 — 最终验收与文档收尾

Type: task
Status: open
Blocked by: 01, 02, 03, 04, 05, 06, 07

## Question

综合收尾的终点门（所有前置 ticket 闭票后执行）：

1. **全量 pytest**：当前 289 passed / 0 失败基线，最终全绿
2. **对拍链**：compare_two_mdf 逐位一致（AHT 230 组）+ full_compare vs CANoe 参考（70/70 + 160 组 × 601 点）+ golden 套件
3. **AHT 82MB 计时复测**：H3 落地后输入阶段 ~1.5s、转换总耗时与本机基线（~25s）对比记录
4. **文档收尾**：review 文档状态刷新（§5 首要建议、行数、pytest 数字）+ 本 map 全部 ticket 决策归档
5. **范围裁决**：打包验证（冻结 exe 干净环境）是否重复——V1.2 已验过一轮，本票裁决是否需要再验

验收通过 = 本 map 的 destination 达成。
