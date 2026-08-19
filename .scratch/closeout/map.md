# 综合收尾（Closeout 2026-08）

## Destination

把 V1.2 后已知残余收敛到「可宣称收尾」：架构线（E adapter 设计裁决、9 个 bench/probe 脚本清理、Worth exploring 分三组逐条裁决做则落地）+ 性能线（H3 输入探测实施，8.55s → ~1.5s）+ 最终验收（全量 pytest 全绿、对拍链、AHT 计时复测）。终点 = 无未决残余、验收全绿、文档状态与代码一致。

## Notes

- 领域词汇用 CONTEXT.md 术语：对拍 / 逐位一致 / 回退 oracle / 解码桶 / 无残留 / 绑定
- 硬前提：对拍链判定能力不可削弱（逐位一致是最高验收标准）；性能改动先探针归因、每步独立验收可回退
- 提交由用户执行（2026-08-15 纪律，本 effort 不代提交）；分支 arc
- 每张 ticket 裁决「做」则同票落地；裁决「不做」写入本 map 的 Out of scope
- 来源文档：架构审查 [docs/reviews/2026-08-18-architecture-review.md](../../docs/reviews/2026-08-18-architecture-review.md)（§4 候选表 / §5 首要建议）；性能 [docs/func_impro/2026-08-15-blf-mdf-conversion-master-plan.md](../../docs/func_impro/2026-08-15-blf-mdf-conversion-master-plan.md)（§5.2 H3）

## Decisions so far

<!-- 每张 ticket 解决后在此追加一行：gist + 链接 -->

- [02 — bench/probe 脚本族清理](issues/02-bench-probe-scripts-cleanup.md) — 9 个一次性脚本全部删除（含 3 个 M8「tools 不 import tests」反向依赖）；README 操作指引同步、docs 历史记录保留；全量 pytest 289 passed 无回归
- [01 — E: ContainerFrames → 帧序列转换 adapter](issues/01-e-containerframes-adapter.md) — 采纳并落地：载荷访问面 `payload_block`/`payload` + glen 恒存在归一（scattered 字段退役）；`_bucket_block` 双分支 → 1 行、测试 `_payload` 删除直调 adapter；未来表示变更触达面 7 处 → 1-2 处；289 passed + 230 组对拍 exit 0 + full_compare 与基线同分
- [03 — GUI 面 Worth exploring 逐条裁决](issues/03-gui-worth-exploring.md) — Q1 双轨明示+代码面收敛（theme 常量 POSITIVE_COLOR/NEGATIVE_COLOR，QSS 轨道保持就地）；Q2 worker 骨架保留现状（不做，入 Out of scope）；Q3 test_gui_binding 状态查询/驱动收口 `binding_row`/`set_binding_selection` 两方法面（`_collect_prev` 转生产消费者）；GUI 63 + 全量 289 passed

## Not yet specified

（无——charting 时所有可提问题均已成型为 ticket）

## Out of scope

- **1.28GB ccu3.0 平台 BLF 输入**（20260324 文件）：非本项目输入，转换失败属预期行为（用户 2026-08-19 裁定）；walk 对齐修复已于 2026-08-17 完成
- **解码路由回归 decoder ownership**：review §4 结论「现有测试锁足够，不必现在动手」
- **H5 压缩级别 9→6 裁决 / H6 池预热重叠 / H7d 桶分组 / H7c 解压重叠**：用户裁定「现阶段只做 H3」；destination 重画（重启性能线）时可作 fresh effort
- **H2b/H2c 共享内存回传**：2026-08-15 已否决（收益不抵复杂度）
- **统一两个 worker adapter 骨架**（GUI P3，[03 票裁决](issues/03-gui-worth-exploring.md)「不做」）：重复面仅 8 行 try/except 骨架；progress 信号载荷不同（(str,float) vs (float)）致基类无法统一信号，合并得不偿失，违背标准⑥
- **MainWindow 拆窗口/控制器/模型**：review §4 明确否决（deletion test 不成立）
- **新功能**：本 effort 只收残余，不做新能力
