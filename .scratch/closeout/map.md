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
- [04 — core 收口面裁决（含搭车项）](issues/04-core-consolidation.md) — Q1 worker 数/进度带单一来源（resolve_workers + decode_progress）、Q2 find_ccu3_root 下沉 project_loader、Q3 容器流衔接三私有名转正、Q5 退化显式化（ConversionResult.warnings）、Q6 取消协议 ConversionCancelled 全部做；Q4 read_start_time 整数化搁置（入 Out of scope）；291 passed + 230 组逐位一致 + full_compare 同分（3434 已知真实差异）
- [07 — H3 输入探测提速](issues/07-h3-input-probe.md) — 方案 1 向量化行走单独做（缓存不做）：probe_channels 新增 _probe_fast 复用 _candidates + 掩码提取，终止分型按 probe 裁剪标量、回退 oracle 不 raise；行走 ~6.3s → ~0.24s（96%↓）；输入阶段冷 12.32 → 6.22s、GUI 热口径 ~2.2s（zlib 1.78s 硬下限）；291 passed + 230 组逐位一致 + full_compare 同分

## Not yet specified

- **输入阶段 zlib 解压残余**（[07 票](issues/07-h3-input-probe.md) 裁决暴露）：行走归零后 zlib 1.78s 为输入阶段硬下限（本机；v2 测 1.2s 系机器漂移）；并行解压（zlib 释放 GIL，ThreadPool）可压到 ~0.9s 但触达面出 probe 单函数（iter_containers 共用层 + 进度语义）——重启性能线时作 fresh effort 评估
- **can→asammdf import 链 ~4s**（[07 票](issues/07-h3-input-probe.md) 裁决暴露）：`from can.io.blf import ...` 触发 can.io.logger→mf4→asammdf；GUI 启动经 mdf_writer 已缓存 asammdf，拖入仅残余首拖 ~0.5s；绕过包 __init__ 不可行（blf.py 相对导入）——重启性能线时评估

## Out of scope

- **1.28GB ccu3.0 平台 BLF 输入**（20260324 文件）：非本项目输入，转换失败属预期行为（用户 2026-08-19 裁定）；walk 对齐修复已于 2026-08-17 完成
- **解码路由回归 decoder ownership**：review §4 结论「现有测试锁足够，不必现在动手」
- **H5 压缩级别 9→6 裁决 / H6 池预热重叠 / H7d 桶分组 / H7c 解压重叠**：用户裁定「现阶段只做 H3」；destination 重画（重启性能线）时可作 fresh effort
- **H2b/H2c 共享内存回传**：2026-08-15 已否决（收益不抵复杂度）
- **统一两个 worker adapter 骨架**（GUI P3，[03 票裁决](issues/03-gui-worth-exploring.md)「不做」）：重复面仅 8 行 try/except 骨架；progress 信号载荷不同（(str,float) vs (float)）致基类无法统一信号，合并得不偿失，违背标准⑥
- **MainWindow 拆窗口/控制器/模型**：review §4 明确否决（deletion test 不成立）
- **read_start_time 整数化时机**（L8，[04 票裁决](issues/04-core-consolidation.md)「Q4 搁置不做」）：float 中转 ±119ns 安全论证已成立，属已知冗余；整数化无行为收益，不值得动已验证路径
- **新功能**：本 effort 只收残余，不做新能力
