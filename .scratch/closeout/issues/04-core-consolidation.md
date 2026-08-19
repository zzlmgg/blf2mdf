# 04 — core 收口面裁决（含搭车项）

Type: grilling
Status: closed (2026-08-19)
Assignee: claude (claimed 2026-08-19)
Blocked by:

## Question

四条 core 面候选逐条裁决（做则同票落地）+ 两项搭车：

1. **worker 数 / 进度带单一来源**（L1/M5）：worker 数决策双实现（mp_finish.py:74-76 vs :157-158，env 覆盖仅 converter 路径生效）+ 解码 10→90 进度带在 converter.py:424-425 与 mp_finish.py:191-194 各自写死——收口为最小常量/函数共享，**不引入「进度管理器」抽象**（标准⑥）
2. **`_find_ccu3_root` 下沉 project_loader**（读写侧 P5）：模块自述职责与实现分离，测试跨包归属暴露错位，locality 直接改善
3. **容器流衔接骨架三份收敛**（L10）：blf_reader 尾部衔接 + ms_part 推导骨架三份复制——收敛后 blf_vector 对 blf_reader 的 4 个私有名依赖可降为正式接口
4. **read_start_time 整数化时机**（L8）：float 中转两次 vs 模块内已有 `_systemtime_ns` 纯整数路径（float 路径有 ±119ns 安全论证，属已知冗余）——现在做 or 明确搁置？

**搭车项**（review §4 Speculative「成本极低可搭车」）：并行退化显式化（make_pool 失败静默吞掉，converter.py:368-372，用户无从得知本次串行还是并行）+ 取消协议中性化（ScanCancelled 定义在 blf_reader 却是 convert 的取消信号）。

约束：改动不触碰已验证性能路径的 numpy 运算语义；每步 pytest 全量 + 对拍链可回退。

## Resolution (2026-08-19)

用户裁定「Q1-Q6 按推荐，Q4 先搁置不做」，逐条落地如下：

- **Q1 做**：worker 数 / 解码进度带单一来源——`mp_finish.resolve_workers(workers, n_channels, env_override)`（env 覆盖仅 make_pool 路径保留原语义）+ `decode_progress(fraction)`（10 + 80×比例，串行/并行共用）；顺带收 `warm_up` 对 `pool._max_workers` 私有属性的依赖
- **Q2 做**：`_find_ccu3_root` 下沉为 `project_loader.find_ccu3_root(root)`（inputs 布局优先，None 时 GUI 降级手动 DBC）；main_window 改模块级 `CCU3_ROOT`，测试与 README 同源改调
- **Q3 做**：容器流衔接骨架「转正 + 就地」——`iter_containers` / `ms_part_ns` / `walk_container` 转公开接口，blf_vector 与测试改调；不引入 send() 风格更深抽象（标准⑥）
- **Q4 搁置不做**（入 map Out of scope）：read_start_time 整数化——float 中转 ±119ns 安全论证已成立，属已知冗余，不在此 effort 动已验证路径
- **Q5 做**：并行退化显式化——`ConversionResult.warnings: list[str]`（末位字段，默认空）；make_pool 失败 / 桶内存超阈值两条路径上报「并行不可用…已回退串行」，GUI 在 timings 上方渲染；+2 测试（monkeypatch 两条路径）
- **Q6 做**：取消协议中性化——ScanCancelled → `ConversionCancelled`（core/gui/测试同步），CONTEXT.md 增「取消信号」词条（取消 ≠ 失败，产物清理属「无残留」调用方语义）

验收（2026-08-19）：

- 全量 pytest **291 passed**（289 + 2 新增退化告警测试）
- 对拍链：compare_two_mdf 新 AHT 输出 vs 20260817 基线 **230 组逐位一致，exit 0**；full_compare vs CANoe 参考 **70/70 组信号集合完全匹配、0 未匹配组、3434 处已知真实差异 = 基线同分**（review 基线：header.comment 1 + 组内信号序 41 + 存储表示元数据 3392）
- 计时复测 **21.47s**、warnings=[]（并行正常）——Q1-Q6 全部行为中性，无可观测性能变化
- 一次性验收脚本 `.scratch/closeout/accept-04/accept_aht.py` 与 `full_compare.log` 留档
