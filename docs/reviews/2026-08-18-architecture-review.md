# BLF→MDF 代码架构合理性审查

日期：2026-08-18　|　审查范围：core/（9 模块）、gui/（5 模块）、main.py、tests/（19 文件）、tools/（18 脚本）、blf2mdf.spec　|　分支：arc（22cb7ff，H1 落地后现状）

> **2026-08-18 更新（H1 落地后，基准 commit 22cb7ff）**：§3 H1/H2 均标 ✅ 完成（H2 原为计划状态、H1 无状态）；§2.1 tools/tests 数量刷新（tools 21→18、tests 17→19 文件）；§4 候选 F 已落地、tools 收口范围如实修正；§5 首要建议由「先做 F」改为「D → C → G → B → A」。全量 pytest 现状：**249 passed / 0 失败**（anaconda3 实测，2026-08-18）。
>
> **2026-08-18 更新（D 实施完成后）**：§3 M3 标 ✅ 完成；§4 候选 D 已落地；§5 首要建议改为「C → G → B → A」；§2.1 行数刷新（converter 536→531、mdf_writer 130→145）；全量 pytest 现状：**253 passed / 0 失败**（anaconda3 实测，2026-08-18，249 基线 + 4 新增）。D 实施按项目惯例未 commit（由用户执行）。
>
> **2026-08-18 更新（C 实施完成后）**：§3 M2 标 ✅ 完成；§4 候选 C 已落地；§5 首要建议改为「G → B → A」；§2.1 行数刷新（converter 531→530、dbc_loader 108→126、tests/test_dbc_loader 113→134）；全量 pytest 现状：**259 passed / 0 失败**（anaconda3 实测，2026-08-18，253 基线 + 6 新增边界用例）。C 实施按项目惯例未 commit（由用户执行）。
>
> **2026-08-18 更新（G 实施完成后）**：§3 L2 标 ✅ 完成；§4 候选 G 已落地；§5 首要建议改为「B → A」；§2.1 行数刷新（stats 154→152）；CONTEXT.md 新增「统计组布局」词条；全量 pytest 现状：**259 passed / 0 失败**（anaconda3 实测，2026-08-18，用例数不变、零测试改动）。G 实施按项目惯例未 commit（由用户执行）。
>
> **2026-08-18 更新（B 实施完成后）**：§3 M4 标 ✅ 完成；§4 候选 B 已落地；§5 首要建议改为「A」；§2.1 行数刷新（main_window 981→985 净增 4：决策算法 65 行移出至新文件 gui/binding.py，新增 _collect_prev 等接线；tests 19→20 文件）；全量 pytest 现状：**269 passed / 0 失败**（anaconda3 实测，2026-08-18，259 基线 − 7 迁移 + 14 纯函数 + 3 接线）。B 实施按项目惯例未 commit（由用户执行）。
>
> **2026-08-19 更新（A 实施完成后）**：§3 M1 标 ✅ 完成；§4 候选 A 已落地；§5 首要建议更新（无剩余 Strong 候选）；§2.1 行数刷新（decoder 453→551 / converter 530→472 / mp_finish 279→271 / dbc_loader 126→175、tests 20→21 文件新增 test_bucket.py；§2.2 主链路图同步刷新已删除函数名）；CONTEXT.md「解码桶」词条（spec 阶段已加，核对通过）；全量 pytest 现状：**289 passed / 0 失败**（anaconda3 实测，2026-08-19，269 基线 + 新增 20：test_bucket 9 + test_dbc_loader 10 + test_parallel_decode 1）。A 实施按项目惯例未 commit（由用户执行）。

## 0. 摘要

生产代码（core/ + gui/）架构**健康，无高严重度问题**；两处「高」级问题集中在验收工具链（tools/ 对拍脚本），它恰恰是保护本项目最独特资产——与 CANoe 逐位一致对拍链——的环节，而该环节自身没有测试保护。

审查标准为用户明示的六条：① 架构干净 ② 调用链扁平 ③ 契约明确 ④ 严格收口 ⑤ 易于人类理解维护 ⑥ 无真实必要性不新增抽象和层级。整体上 ①② 达标良好（主链路 6 跳且每层有真实行为；依赖单向无环；GUI↔core seam 干净）；主要偏差集中在 **④ 严格收口**——归一化键、半成品清理、进度带计划、绑定匹配契约等跨模块规则各有多份独立实现；以及 ③ 的部分——桶 dict 三态隐式多态契约。测试体系的 seam 质量两极分化：test_converter 是契约测试标杆，test_gui_binding/test_gui_style 则打在 widget 对象图与实现文本上。

按词汇表表述：core 的深模块（位提取、单遍路由、窗界聚合、ContainerFrames 双契约）分布健康，但三个**真实接缝**（解码桶、绑定匹配、对拍逻辑）的接口仍是隐式多态 dict / 格式化字符串 / 不可 import 的 CLI 函数——接缝真实存在而契约没有显式成型。

**2026-08-18 补充**：本文档三个真实接缝中，对拍逻辑接缝（H1/H2）已显式成型——收口为 tools/mdf_compare.py 双入口深模块并获黄金测试保护，两个「高」级问题全部修复，且修复本身零生产代码改动（core/、gui/ 未触碰）；绑定匹配接缝（M4/B）同日成型——决策抽为 gui/binding.py 无 Qt 纯函数、绑定键结构化为 DBC 路径（显示名只作展示），三个接缝仅剩解码桶（M1/A）未显式成型。详见 §3 状态标注。

---

## 1. 审查范围与标准

| 维度 | 内容 |
|---|---|
| 标准 | 用户明示六条：架构干净、调用链扁平、契约明确、严格收口、易理解维护、不新增无谓抽象 |
| 词汇 | module / interface / implementation / depth（深/浅模块）/ seam / adapter / leverage / locality；deletion test；「interface 是测试面」；「一个 adapter = 假设接缝，两个 = 真实接缝」 |
| 方法 | 4 个并行分区审查代理全文通读（core 管线 / core 读写侧 / GUI 与入口 / 测试与工具链），每项论断附 文件:行号 证据；关键论断经第二遍交叉核验 |
| 硬前提 | 输出与 CANoe 逐位一致 + 性能基线（[master plan](../func_impro/2026-08-15-blf-mdf-conversion-master-plan.md)）不可牺牲；架构判断不因性能注释放水，重构建议不破坏已验证优化 |
| 热度依据 | 近 60 次提交改动频率：tests 65 / core 61 / docs 48 / gui 25 / tools 23 |

分区报告已交叉比对，重叠发现已合并（.mf4 清理、`_find_ccu3_root` 错位等由两个分区独立发现）。

## 2. 现状架构总览

### 2.1 模块地图

| 文件 | 行数 | 职责 | 公开接口（一句话契约） |
|---|---|---|---|
| core/blf_reader.py | 454 | BLF 标量解析（oracle）+ 探测 | `Frame`、`probe_channels`、`read_start_time`、`list_channels`、`iter_messages`、`ScanCancelled`（全项目取消异常唯一定义点） |
| core/blf_vector.py | 456 | H1 向量化快路径 + 回退编排 | `ContainerFrames`（packed/scattered 双契约）、`iter_container_frames`（快路径失败回退标量，决策唯一收口点） |
| core/converter.py | 472 | 转换编排 | `convert()`、`ConversionResult`、`ChannelSummary`；私有 `_read_vectorized`（单遍扫描路由，A 落地后分类/建桶收敛至 `classify_batch`/`Bucket`） |
| core/decoder.py | 551 | 帧→信号物理值 | `SignalSeries`、`DecodeStats`、`ChannelDecoder`、`decode_channel`（测试面包装）、`Bucket`（三相位显式状态单类，A 落地后） |
| core/mp_finish.py | 271 | 并行 per-bucket finish | `make_pool`、`bucket_bytes`、`finish_all`（结果与串行 finish 同形；worker 消费 typed `Bucket`） |
| core/stats.py | 152 | CANoe 1s 统计语义 | `STAT_NAMES`/`STAT_CHANNELS`、`aggregate_channel`（deep）、`align_timestamps`（时间网格规则唯一实现）、`ChannelStats`（不含通道身份，见词汇表「统计组布局」） |
| core/mdf_writer.py | 145 | MDF 4.10 写出 adapter | `RawGroup`、`write_mdf`（无残留契约：抛出时本次调用不留下任何输出文件）；import 时改 asammdf 全局压缩级别 |
| core/dbc_loader.py | 175 | DBC 域模型 + 分类规则 | `SignalDef`/`MessageDef`/`DbcDef`、`load`；`normalize_id`/`normalize_ids`（归一化键唯一实现，M2 收口后）；`classify`/`classify_batch`/`message_table`（分类规则唯一实现，A 收口后） |
| core/project_loader.py | 125 | ccu3.0 项目载入/匹配 | `DEFAULT_MAPPING`、`list_projects`、`load_mapping`、`load_project`、`auto_bindings` |
| gui/main_window.py | 985 | 主窗口 + 转换编排 + 状态管理 | `MainWindow`、`ConvertWorker`/`BlfScanWorker`（QThread adapter）、`_find_ccu3_root` |
| gui/binding.py | 65 | 绑定决策纯函数（B 落地后新增，零 Qt import） | `BindingRow`、`decide_bindings`、`derive_state`、STATE_* 三态常量 |
| gui/widgets.py | 421 | 无业务 PySide 展示组件（零 core 依赖） | `AppShell`、`CompactCombo`、`DbcListWidget`、`TitleBar`、`SummaryDialog` 等 |
| gui/theme.py | 266 | 视觉令牌与 QSS | `WINDOW_WIDTH/HEIGHT`、`APP_QSS`、`apply_theme` |
| gui/windows_effects.py | 70 | Win11 玻璃/圆角 + 软件回退 | `apply_light_glass`、`sync_rounded_window` |
| gui/resources.py | 26 | 运行时路径与图标 | `resource_path`、`install_application_icon` |
| main.py | 27 | 程序入口 | `main()`；`freeze_support()` 铁律 |
| tests/ | ~5300 | 21 文件 | 见 §4（18 原始 + mdf_factory.py 共享工厂 + test_compare_cli.py CLI 契约；B 落地新增 test_binding.py 纯函数套件；A 落地新增 test_bucket.py 相位契约套件） |
| tools/ | ~1700 | 18 脚本 | 见 §4（compare 族已收口为 mdf_compare + 2 CLI 薄壳；bench/probe 族 8 个仍可删） |

### 2.2 依赖方向与主链路

```mermaid
flowchart LR
    subgraph gui["gui/（无 core 依赖的反向泄漏）"]
        MW[main_window] --> W[widgets/theme/windows_effects]
    end
    subgraph core
        CV[converter] --> BV[blf_vector] --> BR[blf_reader]
        CV --> DC[decoder]
        CV --> MP[mp_finish] --> DC
        CV --> ST[stats]
        CV --> MWF[mdf_writer] --> ST
        CV --> PL[project_loader] --> DL[dbc_loader]
        DC --> DL
    end
    MW -->|convert / probe_channels / list_projects| CV
```

依赖单向无环；blf_vector 单向依赖 blf_reader 的 4 个私有名（`_iter_containers`/`_walk_container`/`_ms_part_ns`/`ScanCancelled`）——快路径依赖 oracle，方向正确。

主转换链路（convert → 位提取共 6 跳，**每层有真实行为，无纯转发层**）：

```
ConvertWorker.run
 └─ convert ─ read_start_time
     ├─ _read_vectorized ─ iter_container_frames ─ _parse_fast（快）| _walk_container（回退=oracle）
     │     └─ _bucket_block → Bucket.from_blocks/add_block → 写入 dec.buckets → to_array
     ├─ 串行: dec.finish ─ Bucket.to_array ─ _finish_bucket_vectorized ─ _extract_signal ─ _extract_bits
     ├─ 并行: mp_finish.finish_all ─ _finish_bucket_worker（worker 内 bucket.to_array）─（同一对桶函数，非复制语义）
     ├─ 统计: aggregate_channel × 16 ─ align_timestamps
     └─ 写出: write_mdf ─ asammdf append/save + os.replace
```

---

## 3. 问题清单（跨模块合并，按严重度）

### 高（2 项，均在验收工具链）

**H1. 对拍 oracle 不可 import、自身无测试保护**
- 位置：[tools/compare_two_mdf.py:19-20](../../tools/compare_two_mdf.py#L19-L20)（`main()` 内直接 `MDF(sys.argv[1])`）；[tests/test_parallel_decode.py:216-245](../../tests/test_parallel_decode.py#L216-L245)（第 3 份 `_mdf_equal` 拷贝）
- 违反：③ 契约明确、⑤ 易维护
- 证据：对拍链是项目验收的根基（master plan §8 点名 compare_two_mdf + full_compare），但对比逻辑无法被 import，测试只能复制；oracle 自身没有任何 pytest 保护——oracle 出错则整条对拍链静默失真。
- 方向：对比逻辑提为可导入函数，工具与测试共用同一实现，CLI 只做参数解析，并补 oracle 自身的回归测试。
- **状态：✅ 已完成**（2026-08-18）——四项收尾全部落地且**零生产代码改动**（core/、gui/、tools/ 三个被测脚本均未修改）：① pytest.ini 增 `pythonpath = .`，控制台脚本 / `python -m pytest` / IDE runner 任意调用方式下 tools、core 均可导入（H1 前控制台脚本收集即报 `ModuleNotFoundError`——测试保护随调用方式静默消失，与 H1 描述的失败模式同构）；② 黄金套件 15 → 41 用例（header 其余 8 字段 + version no-op、通道元数据 5 字段 + bit_resolution no-op、NaN 两侧语义、整型/dtype/文本差异、stats 逐点、dims 关闭语义；spec 清单两处按实测调整：asammdf 不支持 U dtype append、S 尾随 \x00 被剥除）；③ STAT_NAMES 双副本相等性锁定断言（core/stats 写出布局 vs mdf_compare 消费端，任一侧漂移在 pytest 阶段变响亮而非对拍静默失真）；④ 两 CLI 退出码 subprocess 进程级契约测试（[tests/test_compare_cli.py](../../tests/test_compare_cli.py) 3 项，端到端覆盖 argparse 接线与 dims 映射）。详见 [spec](./h1/2026-08-18-h1-oracle-protection-spec.md) / [plan](./h1/2026-08-18-h1-oracle-protection-plan.md)。

**H2. tools/ 对拍脚本族 7 个各自重实现同一逻辑，能力散落三处**
- 位置：compare_mdf / compare_mdf_v2 / compare_mdf_deep / deep_compare_mdf / deep_compare_latest / compare_two_mdf / full_compare
- 违反：④ 严格收口、② 扁平
- 证据：每个脚本独立重写「加载两组、按名对齐、逐点比较」；头部对比（compare_mdf_v2.py:35-39、compare_mdf_deep.py:48-56）与统计 t 轴细查（deep_compare_latest.py:69-83）是独有能力，必须合并而非纯删；deep_compare_mdf.py docstring 自称「对比两个 MDF」而 main 只读 sys.argv[1]（变质残留）。
- 方向：收口为两个保留工具（自产对拍 + CANoe 报告），其余删除。
- **状态：✅ 已完成**（2026-08-18）——收口为 [tools/mdf_compare.py](../../tools/mdf_compare.py) 单一深模块（`compare_files_identical` / `compare_files_reference` 双入口，四维判定默认全开），两个保留 CLI（compare_two_mdf / full_compare）退化为 thin adapter（full_compare 新增必填参考统计布局参数），删除 5 个冗余/变质脚本；oracle 获黄金测试保护（tests/test_mdf_compare.py 15 用例，合成 MDF 无样例依赖；H1 再补至 41 用例）。行为等价实证（Task 6）：identical 入口对 AHT 并/串产物与原版输出逐字节一致；reference 入口 3434 处判定差异全部核验为文档已知真实差异（头部 comment、存储表示元数据、41 组组内信号序），values/stats 维度零差异与原版「160 组 × 601 点逐点全部一致」结论吻合。详见 [spec](./2026-08-18-h2-compare-consolidation-spec.md) / [plan](./2026-08-18-h2-compare-consolidation-plan.md) / [验证记录](./2026-08-18-h2-compare-consolidation-verification.md)。

### 中（8 项）

**M1. 解码桶契约三态隐式多态 + 分类逻辑双实现**
- 位置：桶三形态——feed 列表形（[decoder.py:412](../../core/decoder.py#L412) 注释只写这一种）、blocks 块形（[converter.py:169-177](../../core/converter.py#L169-L177) 写入）、数组形（[converter.py:79-103](../../core/converter.py#L79-L103) 装配）；形状分派靠 isinstance 散布 [decoder.py:156](../../core/decoder.py#L156)、[mp_finish.py:91](../../core/mp_finish.py#L91)；「未知 ID/短帧 → 未知不入桶」分类不变量在 feed（decoder.py:417-428）与向量化路由（[converter.py:140-157](../../core/converter.py#L140-L157)）各实现一遍，等价性靠注释声明 + 测试锁定而非结构保证
- 违反：③ 契约明确、④ 收口、⑤
- 证据：不变量（arb=归一化键、raw_id=首帧原始 id、插入序=系列序）只在各自 docstring 片段陈述，无集中声明；[converter.py:389](../../core/converter.py#L389) 的 `not isinstance(t[0], np.ndarray)` 是形状多态的死残留。
- 方向：桶结构收口到单一 owner，三态收敛为显式状态或单一产物；分类规则单实现（此改动触及性能前提——单遍扫描三方收集，需评估后动）。
- **状态：✅ 已完成**（2026-08-19，实施 A，[spec](./a/2026-08-18-a-bucket-typing-spec.md)）——桶结构收口为 decoder.py 显式状态单类 `Bucket`（三相位互斥字段组 + `from_feed`/`from_blocks`/`add_frame`/`add_block`/`to_array`/`n_frames`/`memory_estimate`，isinstance 分派从 decoder/mp_finish/converter 三处收进类内，H2a 性能注释随迁）；分类规则收口 dbc_loader（`classify`/`classify_batch`/`message_table` 相邻定义，批量与标量等价由属性测试锁定，M2 双入口模板同构）；两条建桶路由（feed/向量化）桶级等价由新增路由等价测试直接对拍（np.unique 首现序 vs setdefault 插入序收口）；finish 防御 length 掩码删除（构造即预检，mux 无子组计数保留）；converter.py:389 死残留清除；oracle（reference_decode）换调生产 classify（解码面保持 cantools 独立）；CONTEXT.md 新增「解码桶」词条。双轴 code-review 0 findings。全量 pytest 289 passed。

**M2. 归一化键规则三处独立实现，无单一归属点**
- 位置：[dbc_loader.py:77](../../core/dbc_loader.py#L77)、[decoder.py:417](../../core/decoder.py#L417)、[converter.py:120-121](../../core/converter.py#L120-L121) 各自写出 `arb | (0x80000000 if is_ext else 0)`
- 违反：③ 契约明确、④ 收口
- 证据：[converter.py:28](../../core/converter.py#L28) docstring 承认「与 dbc.messages 键契约一致」——契约靠注释转发而非引用；改键规则（如 FD 专属 ID 空间）需同改 3 模块。
- 方向：归一化收敛为一个归属点，三处改为调用。
- **状态：✅ 已完成**（2026-08-18，实施 C）——归一化收口到 dbc_loader 模块级：`normalize_id`（标量）+ `normalize_ids`（numpy 批量）双入口相邻定义，共享私有 `_EFF_BIT = 0x80000000` 常量；三处调用点（dbc_loader 构造键 / decoder feed 查键 / converter 向量化热路径）全部改调，键值逐位不变；oracle（test_decoder_vectorized reference_decode）换调生产 `normalize_id`（H1 先例：测试基准用生产契约函数）；解构方向（blf_vector 掩码解析 is_ext、test_parallel_decode 反解）明确不收口——生产零消费者，YAGNI。测试 6 项：参数化边界 5 例（标准帧高位置 0、扩展帧置位、29 位上限 0x1FFFFFFF 扩展、0x80000000 标准帧 OR 0 幂等、0x80000001 扩展帧幂等）+ 批量版与标量逐位一致且 dtype 保 uint32。双轴 code-review 0 findings。全量 259 passed。

**M3. 半成品 .mf4 清理收口分裂：命名知识泄漏 + 清理双份**
- 位置：[mdf_writer.py:133-145](../../core/mdf_writer.py#L133-L145)（save/replace 包入 try/except BaseException 自清理，修复前 save/replace 无保护）；converter 修复前 :511-523 两份硬编码 `<out>.mf4` 清理（现已删除，仅剩取消分支 :513-518）
- 违反：④ 收口
- 证据：converter 复述 writer 的中间文件命名（deletion test：writer 改临时文件策略则 converter 清理静默失效）；writer 自身失败时不清理——任何其他调用方直接调 write_mdf 都会留残留。
- 方向：「失败无残留」成为 write_mdf 自身契约（内部 try/清理），converter 仅保留取消语义分支。
- **状态：✅ 已完成**（2026-08-18，[spec D](./2026-08-18-d-write-no-residue-spec.md)）——无残留契约收进 write_mdf：save/replace 包入 try/except BaseException，失败清理本次写出的半成品 `<stem>.mf4`、out_path 保持调用前状态（旧产物保留，失败 ≠ 全清），清理自身失败静默吞掉、主异常优先上抛；converter 异常清理分支整体删除，取消分支简化为 `Path(out_path).unlink(missing_ok=True)`（取消 = 放弃本次转换的 converter 业务语义，不属于 writer 失败契约）。deletion test 通过：writer 改临时文件策略不再让 converter 清理静默失效。测试 5 项：writer 3 个注入点（save 失败 / replace 失败 / KeyboardInterrupt，均断言半成品被清理 + 旧产物保留）+ converter 纯传播测试 + 写后取消状态机测试（取消条件 = write_mdf 完成标志，不依赖检查点计数，对 fixture 通道数变化免疫）。全量 253 passed。

**M4. 绑定匹配契约以「显示名字符串」跨三模块，且决策算法嵌在表格操作里**
- 位置：display_name 定义 [dbc_loader.py:41-48](../../core/dbc_loader.py#L41-L48)；auto_bindings 返回 {通道: 显示名}（[project_loader.py:112-125](../../core/project_loader.py#L112-L125)，docstring 明说「匹配直接以显示名比对」）；GUI 端 [main_window.py:616-622](../../gui/main_window.py#L616-L622)（combo 文本 in valid）、:811-820（`_dbc_by_display` 逐项文本比对）；决策算法 `_rebuild_channel_table`（:575-633）含双键型契约（auto 键 int、prev 键 "CANn" 字符串，注释明言不可混用）
- 违反：③ 契约明确、⑤
- 证据：绑定链路 key 是格式化字符串（`PFCAN1.dbc（A19G1）`），display_name 格式一变绑定静默断裂，仅 [test_gui_binding.py:110-113](../../tests/test_gui_binding.py#L110-L113) 兜底；该文件 docstring（:1-5）记载键型 bug 真实事故史。
- 方向：匹配键改结构化身份（显示名只作展示）；「(channels, prev, auto_bind, 有效 DBC 名) → 每行选择+状态」决策抽为无 Qt 纯函数，窗口方法退化为渲染。
- **状态：✅ 已完成**（2026-08-18，实施 B，[spec](./2026-08-18-b-binding-decision-pure-spec.md)）——① 新建 gui/binding.py（65 行，**零 Qt import**）：`BindingRow` dataclass + `decide_bindings`（行集合并集、三态分派：prev 值有效=用户微调优先 / prev=None=显式不绑定压制 auto / prev 值无效=回退 auto 重绑、状态推导）+ `derive_state` + STATE_* 三态常量，测试免 offscreen/qapp；② 绑定键结构化：project_loader `auto_bindings` 返回 `{ch: path}`（显示名只作展示）、combo `userData=DbcDef`、`_start_convert` 直接 `currentData()`、`_dbc_by_display` 删除（零查找、零反查表）；③ `_rebuild_channel_table` 退化为渲染（收集 prev → decide_bindings → 建行/填 combo/设状态），双键型契约归一为 int 通道号 + None 显式不绑定（事故史保留 test_binding.py docstring）；④ 测试：14 纯函数用例（7 迁移 + 7 边界）+ 3 薄接线（test_gui_binding 32→28），UI 显示与操作逻辑零变化（Q9 硬约束，场景矩阵逐项核验）。双轴 code-review：Standards 轴代码 0 findings（4 项 spec 文档漂移已随修订）；Spec 轴捕获 1 个实质问题（prev 失效未回退 auto 违反 Q9 现状语义）已修复并补测试。全量 269 passed。

**M5. 进度百分比带计划散布三模块**
- 位置：解码 10→90 在 [converter.py:424-425](../../core/converter.py#L424-L425)（串行）与 [mp_finish.py:191-194](../../core/mp_finish.py#L191-L194)（并行）各自写死；读取 5→10、统计 92→95 另在 converter 两处
- 违反：④ 收口
- 证据：任一方改带即破坏「单调不降」不变量（test_converter.py:140-141、test_parallel_decode.py:124-125 锁行为但未锁「计划属于谁」）。
- 方向：带计划收口为单一决策点（常量/函数共享），勿引入「进度管理器」抽象（标准 ⑥）。

**M6. mdf_writer import 即改 asammdf 全局状态**
- 位置：[mdf_writer.py:17-18](../../core/mdf_writer.py#L17-L18)
- 违反：① 架构干净、③
- 证据：`_v4_blocks.COMPRESSION_LEVEL = 9` 在 import 时执行——副作用不体现在 write_mdf 接口上，import 顺序敏感。注释已交代 asammdf save 不暴露该参数的硬约束。
- 方向：副作用至少进模块 docstring 显式声明（受限作用域的既定事实），或集中到显式初始化点。

**M7. GUI 测试打 widget 对象图 / 私有方法 / 实现文本**
- 位置：[test_gui_binding.py](../../tests/test_gui_binding.py) 35 处 `cellWidget(...).currentText()`/`table.item(...).text()`/私有方法直调（如 :36-42）；[test_gui_style.py:42](../../tests/test_gui_style.py#L42) 断言 QSS 源码字符串（换行缩进敏感）、:580 像素色值、:176-178 精确尺寸
- 违反：③、⑤（「interface 是测试面」原则——测试穿透接口打实现）
- 证据：任何表控件重构（QTableWidget→QTableView+model）一次性打碎 test_gui_binding 全文件；QSS 重构需逐字符改测试。视觉定稿锁有真实价值（唯一自动化的视觉契约守护），但断言对象应是渲染结果/结构而非文本格式。
- 方向：表格状态查询/驱动收敛为 MainWindow 上少量稳定方法面；QSS 断言放宽为结构断言（选择器/属性存在性）。

**M8. tests ↔ tools 双向依赖 + 绑定表知识三份**
- 位置：bench 脚本 `sys.path.insert(0, .../tests)` 反向依赖（bench_bucket_dist.py:14-16、bench_parallel_finish.py:26-28、bench_stages.py:12-14）；test_package_config.py:43,49 反向 import tools；通道↔DBC 绑定表三处（test_golden.py:16-27 BINDING / convert_aht.py:15-26 硬编码 / project_loader.DEFAULT_MAPPING）
- 违反：②、④
- 方向：绑定表与样例路径收敛到单一中性来源；tools 不 import tests。

### 低（12 项，择要）

| # | 位置 | 问题 | 违反 |
|---|---|---|---|
| L1 | [mp_finish.py:74-76](../../core/mp_finish.py#L74-L76) vs :157-158 | worker 数决策双实现（env 覆盖仅 converter 路径生效）；`decoders`/`channels` 双真值源无防御 | ④ |
| L2 | [stats.py:154](../../core/stats.py#L154) | `ChannelStats(channel=0)` 硬编码死字段，mdf_writer 从不读它——通道身份靠「converter 按 STAT_CHANNELS 顺序 append」的未成文不变量。**状态：✅ 已完成**（2026-08-18，候选 G）：`channel`/`signal_names` 双死字段删除（grilling Q1/Q5）、docstring 明示顺序即通道（Q2）、CONTEXT.md 收「统计组布局」词条（Q3）、不补测试（Q4）。详见 [G spec](./g/2026-08-18-g-channelstats-dead-field-spec.md) | ③ |
| L3 | [mp_finish.py:63](../../core/mp_finish.py#L63) | warm_up 依赖 `pool._max_workers` stdlib 私有属性，make_pool 已知 n 却不传 | ⑤ |
| L4 | [converter.py:484/493](../../core/converter.py#L484) | 同一「无统计数据」缺省两种元组形状 `((), None, None, None)` vs `((), (), (), ())` | ⑤ |
| L5 | [mp_finish.py:48-49](../../core/mp_finish.py#L48-L49) | `_finish_bucket_worker` 6 元组位置契约，两处解包靠位置记忆，前两元素被丢弃 | ③ |
| L6 | [converter.py:106-107](../../core/converter.py#L106-L107) | `_read_vectorized` 10 个位置参数 + 写 4 个可变结构——全管线最 deep 的函数，接口是裸结构集合（当前仅 1 调用方，暂不必动） | ③⑤ |
| L7 | [converter.py:368-372](../../core/converter.py#L368-L372) | make_pool 失败静默吞掉，用户无从得知本次串行还是并行 | ⑤ |
| L8 | [blf_reader.py:159-170](../../core/blf_reader.py#L159-L170) | read_start_time 走 float 中转两次，而模块内已有 `_systemtime_ns` 纯整数路径（float 路径有 ±119ns 安全论证，属已知冗余） | ①⑤ |
| L9 | [blf_reader.py:432-454](../../core/blf_reader.py#L432-L454) | `scan_channels` 零调用者、零测试引用；且 H1 计划文档（docs/func_impro/2026-08-14-H1-vectorized-parse-plan.md:21）声称它是「对拍参考与测试覆盖对象」——**文档漂移**，事实无测试引用。deletion test：直接删，复杂度不转移 | ⑥ |
| L10 | blf_reader.py:151-155 / 381-390 / blf_vector.py:437-446 | 尾部衔接 + ms_part 推导骨架三份复制 | ⑥ |
| L11 | [main_window.py:656-657](../../gui/main_window.py#L656-L657)、:751-753、:651-654 | `_fit_table_width` 空方法零调用；`_remove_dbc` 零调用（仅 :685 用 `_remove_dbc_at`）；`_fit_window_height` 近 no-op | ⑤ |
| L12 | 其余命名/住所类：ScanCancelled 定义在 blf_reader 却同时是 convert 的取消信号（[blf_reader.py:16](../../core/blf_reader.py#L16)）；关窗确认文案与取消语义矛盾（[main_window.py:970-977](../../gui/main_window.py#L970-L977)）；DbcCombo 定义在 main_window 与 widgets.py 自述冲突；conftest 无 fixture 且 import 时 mkdir；test_parallel_decode 鸭子类型伪造 Frame；test_main_entry 按函数名断言 AST | ③⑤ |

---

## 4. 深化候选

> 词汇：**深模块** = 小接口 + 大实现（leverage/locality）；**浅模块** = 接口近于实现。按「一个 adapter = 假设接缝，两个 = 真实接缝」判定哪些接缝值得显式成型。

| 候选 | 强度 | 说明 |
|---|---|---|
| A. 桶记录类型化 + owner 集中（M1） | ✅ 已落地（2026-08-19） | 桶结构收口 decoder.py `Bucket` 显式状态单类（三相位互斥 + 转换方法收敛）；分类规则唯一实现 dbc_loader（classify/classify_batch/message_table）；路由等价测试直接对拍两条建桶路由；finish 防御掩码删除；死残留清除；reference_decode 换调 classify；289 passed。详见 [A spec](./a/2026-08-18-a-bucket-typing-spec.md) |
| B. 绑定决策抽无 Qt 纯函数（M4） | ✅ 已落地（2026-08-18） | 决策抽为 gui/binding.py 纯函数（65 行零 Qt）+ 绑定键结构化（路径）+ userData=DbcDef 直取、`_dbc_by_display` 删除；14 纯函数用例免 Qt + 3 薄接线；双键型契约归一；269 passed。详见 §3 M4 状态 |
| C. 归一化键单一来源（M2） | ✅ 已落地（2026-08-18） | 归一化键唯一实现落 dbc_loader（`normalize_id`/`normalize_ids`/`_EFF_BIT`），三处调用点 + oracle 全部改调；6 项边界测试；259 passed。详见 §3 M2 状态 |
| D. write_mdf 失败无残留归 writer（M3） | ✅ 已落地（2026-08-18） | 契约已实施：失败清理本次半成品、out_path 保留旧产物、BaseException 覆盖、取消留 converter 侧；converter 异常清理分支删除、取消分支简化为 unlink(missing_ok=True)。5 个新测试；全量 253 passed。详见 [D spec](./2026-08-18-d-write-no-residue-spec.md) |
| E. ContainerFrames → 帧序列转换 adapter | **Strong** | test_blf_vector 手工重建帧语义（`_payload`+`_assert_eq`），H7b 一次表示变更迫使 7 处测试更新（master plan 已实证该成本）。adapter 同时简化 converter 与测试两侧 |
| F. 对拍逻辑收口为可导入模块并纳入 pytest（H1/H2） | ✅ 已落地（2026-08-18） | 修复两个「高」级问题：compare 族 7 脚本 → mdf_compare 深模块 + 2 CLI 薄壳（tools 21→18；**8 个 bench/probe 可删脚本未动**——M8/L9 残留，H1 spec 明示 Out of Scope）；oracle 获 41 用例黄金测试 + CLI 退出码进程契约 + STAT_NAMES 双副本锁定。行为等价验证通过（H2 验证记录：identical 与原版逐字节一致、reference 3434 处判定差异全部核验为已知真实差异）；全量 pytest 现状 249 passed / 0 失败。详见 [H2 验证记录](./2026-08-18-h2-compare-consolidation-verification.md) / [H1 spec](./h1/2026-08-18-h1-oracle-protection-spec.md) |
| G. ChannelStats 通道身份显式化或删死字段（L2） | ✅ 已落地（2026-08-18） | `channel` + 零读者的 `signal_names` 双死字段删除（grilling Q1/Q5）；类 docstring 明示「对象不含通道身份，通道由调用方按 STAT_CHANNELS 顺序持有」（Q2）；CONTEXT.md 新增「统计组布局」词条（Q3）；不补测试、零测试改动（Q4）。259 passed 用例数不变。详见 [G spec](./g/2026-08-18-g-channelstats-dead-field-spec.md) |
| 视觉令牌收敛或双轨明示（GUI P2） | Worth exploring | 同一色值 #207e4b 出现在 theme.py:42 / widgets.py:169 / main_window.py:670 三处；若团队实际迭代方式是就地改色，双轨明示比强制收敛诚实 |
| 统一两个 worker adapter 骨架（GUI P3） | Worth exploring | 两个 adapter 证明 seam 真实；骨架同构是已兑现成本；合并是否更可读需拿一版对照再定 |
| worker 数 / 进度带单一来源（L1/M5） | Worth exploring | 收口方式为最小常量/函数共享，不引入「进度管理器」抽象 |
| test_gui_binding 表格状态查询收敛（M7） | Worth exploring | 约 35 处 widget 图访问收到 2-3 个方法面 |
| `_find_ccu3_root` 下沉 project_loader（读写侧 P5） | Worth exploring | 模块自述职责与实现分离；测试跨包归属暴露错位，locality 直接改善 |
| 容器流衔接骨架三份收敛（L10） | Worth exploring | 收敛后 blf_vector 对 blf_reader 的 4 个私有名依赖可降为正式接口 |
| read_start_time 整数化（L8） | Worth exploring | 方向明确，当前 float 路径有安全论证，非急需 |
| conftest 上收 fixture + sample_blf 显式化；冻结探针转 subprocess pytest | Worth exploring | 消除两份 GUI fixture 重复与「第一个 BLF」漂移；进程级行为进测试体系 |
| 并行退化显式化（L7）/ 取消协议中性化（L12） | Speculative | 可观测性与命名洁癖，成本极低可搭车 |
| DEFAULT_MAPPING 从数据文件自动生成 | Speculative | 消除手工同步副本，但引入构建/缓存机制，成本可能高于收益 |
| 解码路由回归 decoder 模块 ownership | Speculative | 分类单实现收益真实，但单遍三方收集是性能前提，拆开需回调接口，可能违背标准 ⑥；现有测试锁足以兜底，不必现在动手 |
| MainWindow 拆窗口/控制器/模型 | **明确否决** | deletion test：981 行中约 300 行是声明式 QWidget 构建，拆开是把同一复杂度摊到更多文件 + 新增跨文件事件布线，违反标准 ⑥。实际深度在 `_rebuild_channel_table`，已由候选 B 覆盖 |

---

## 5. 首要建议

**F（H1/H2 对拍收口）已落地（2026-08-18）；D（write_mdf 失败无残留归 writer）已实施完毕（2026-08-18，253 passed）；C（归一化键单一来源）已实施完毕（2026-08-18，259 passed）；G（ChannelStats 死字段）已实施完毕（2026-08-18，259 passed）；B（绑定决策抽无 Qt 纯函数）已实施完毕（2026-08-18，269 passed）；A（桶契约类型化）已实施完毕（2026-08-19，289 passed）。五个 Strong 候选（A/B/F/H1/H2）全部落地，无剩余 Strong 候选。下一步：M8/L9 bench/probe 脚本清理（8 个可删脚本）等 Worth exploring 条目。**

理由：
1. **B（绑定决策抽无 Qt 纯函数）已落地**：全 GUI 分区唯一有 bug 史的算法抽为 gui/binding.py 纯函数，14 个决策用例不再穿越 Qt 对象图；绑定键结构化（路径）后显示名格式变更不再静默断裂。收口类（C/D/M3/G/L2/B/M4）至此全部落地，架构剩余主要工作重心转入 core 侧。
2. **A（桶契约类型化）已落地**：解码桶收口为 `Bucket` 显式状态单类，分类规则唯一实现落 dbc_loader，两条建桶路由由路由等价测试直接对拍——M1 的「等价性靠注释声明」结构性收口完成，单遍扫描性能前提（numpy 运算语义零变化 + H2a 注释随迁）未触碰。
3. F 的「减法」只完成了 compare 族（21→18）；**8 个 bench/probe 可删脚本**（bench_bucket_dist / bench_parallel_finish / bench_spawn / bench_probe / probe_blf / convert_aht / run_gui_probe / verify_clean_env）仍待清，属 M8/L9 条目，可搭车任意收口任务。

---

## 6. 已符合标准的资产（审查建议不得破坏）

1. **并行/串行语义等价锚定在单一契约**：worker 复用同一实现函数而非 fork 解码逻辑（[mp_finish.py:43-47](../../core/mp_finish.py#L43-L47)）；`verify=True` 把对拍做进生产接口——「interface 是测试面」正面教材。
2. **「回退即 oracle」三层机制**：快路径字段权威声明（blf_vector.py:12-15）+ 对拍测试「标量正常而快路径抛异常 = 违约」（test_blf_vector.py:141-164）+ fuzz 对拍（:464-517）；守卫/回退决策单点收口（blf_vector.py:447-456）。双实现是受控设计决策，不是失控重复。
3. **GUI↔core seam 形状干净**：core 全同步、零 Qt；跨 seam 仅两个单向回调（progress_cb 出、cancel_cb 入）；产品决策（raw_export=False、parallel=True）单点集中在 ConvertWorker；三条取消入口（扫描按钮/转换按钮/closeEvent）全部汇到 `threading.Event.set()` 一个咽喉。
4. **时间戳整数构造主题贯穿**：blf_reader docstring → blf_vector 守卫分判 → mdf_writer `_abs_time_ns` 纯整数写出，浮点舍入论证全程可追溯，测试锁定逐位一致。
5. **stats.py 模块 docstring 即 CANoe 语义规格**；`align_timestamps` 作为时间网格规则唯一实现被两处复用——正确的 leverage 与 locality。
6. **test_converter.py 是 seam 质量标杆**：只经 `convert()` 公共接口 + asammdf 读回断言，唯一 monkeypatch 打在 adapter 接缝；test_blf_reader 的 `_raw_rel_ns` 用 python-can struct 独立重解析（零共享代码的双实现对拍）；双 oracle 模式（test_stats / test_decoder_vectorized 保留优化前慢参考）leverage 极高。
7. **性能注释不遮架构判断**：每处向量化均标注「与参考逐位一致 + 测试名/实测数据」，性能理由以数据陈述而非借口。

---

## 7. 附录：方法局限与后续

- 本次为静态架构审查，未做运行时剖析；审查结论不与任何性能数据冲突（性能硬前提已声明）。
- 未逐行比对 docs/superpowers 下的 12 份设计/计划文档与代码现状（仅抽查 master plan 一处发现 L9 文档漂移）。
- 审查不修改任何代码；所有问题条目均含 文件:行号 证据，可按条目逐一复核。

**下一步**（按 improve-codebase-architecture 流程）：两个「高」级候选（H1/H2）已走完 grilling 并落地；**D（write_mdf 失败无残留归 writer）已实施完毕（2026-08-18，253 passed）**，实施记录见 §3 M3 状态；**C（归一化键单一来源）已实施完毕（2026-08-18，259 passed）**，实施记录见 §3 M2 状态；**G（ChannelStats 死字段）已实施完毕（2026-08-18，259 passed）**，实施记录见 §3 L2 状态；**B（绑定决策抽无 Qt 纯函数）已实施完毕（2026-08-18，269 passed）**，实施记录见 §3 M4 状态；**A（桶契约类型化）已实施完毕（2026-08-19，289 passed）**，实施记录见 §3 M1 状态。候选表中其余条目只描述问题与方向、未设计接口；五个 Strong 候选（A/B/F/H1/H2）全部落地，后续按 §5 处理 M8/L9 bench/probe 脚本清理等 Worth exploring 条目。
