# H1 对拍 oracle 可导入性与测试保护 — 设计 Spec

> 来源：2026-08-18 架构审查（[2026-08-18-architecture-review.md](./2026-08-18-architecture-review.md) 第 3 节 H1）+ 剩余差距审计（本 spec 全部差距均经代码与实测证据核实）+ 逐项 grilling（用户确认：① spec 定位 = 收尾 H2 未覆盖的剩余差距；② STAT_NAMES 收敛方式 = 副本 + 相等性锁定测试）。词汇表见 [CONTEXT.md](../../CONTEXT.md)：对拍、逐位一致、回退 oracle。

## Problem Statement

对拍链是项目验收的根基（[master plan §8](../func_impro/2026-08-15-blf-mdf-conversion-master-plan.md) 点名 `compare_two_mdf` 逐位 + `full_compare` vs CANoe 参考 + pytest 为验收三件套）。H1 原问题：对比逻辑无法被 import，测试只能复制第三份实现（`_mdf_equal`），oracle 自身没有任何 pytest 保护——**oracle 出错则整条对拍链静默失真**。

H2（审查候选 F）已落地该方向的四个要素中的大部分：对拍判定收口为可导入深模块（[tools/mdf_compare.py](../../tools/mdf_compare.py)，`compare_files_identical` / `compare_files_reference` 双入口）、两个 CLI 退化为薄壳、[tests/test_parallel_decode.py](../../tests/test_parallel_decode.py) 的 `_mdf_equal` 第三份拷贝已删除（改为 import 同一实现）、黄金套件 [tests/test_mdf_compare.py](../../tests/test_mdf_compare.py) 已建立（15 用例，合成 MDF 无样例依赖）。

但 H1 的修复目标「oracle 出错不静默」尚未完全达成，剩余四项差距（均有证据）：

1. **导入 seam 只在特定调用方式下成立**（2026-08-18 实测）：`pytest tests/test_mdf_compare.py`（控制台脚本）收集即报 `ModuleNotFoundError: No module named 'tools'`；`python -m pytest` 才通过（cwd 入 sys.path）。oracle 的测试保护在另一种调用方式下**静默消失**——与 H1 描述的失败模式同构。
2. **黄金测试覆盖缺口**：15 用例相对模块真实契约仍有大面未覆盖（详见 D4 清单）——oracle 的部分判定路径出错不会被发现。
3. **STAT_NAMES 双副本无防漂移机制**：[core/stats.py:22-28](../../core/stats.py#L22-L28)（生产写出布局契约）与 [tools/mdf_compare.py:23-25](../../tools/mdf_compare.py#L23-L25)（oracle 消费端假设）各一份，值经实测完全一致但没有任何机制锁定相等——生产统计布局一变，oracle 用陈旧顺序校验，**对拍静默失真**（H1 失败模式的精确重现）。
4. **CLI 退出码契约零测试**：master plan §8 验证链以 `compare_two_mdf` exit 0 为验收语义，但退出码/参数接线回归没有任何测试保护；且两 CLI 壳依赖 `sys.path[0]=tools/` 的兄弟导入（实测 `import tools.compare_two_mdf` 报 `ModuleNotFoundError: No module named 'mdf_compare'`），模块导入路径不可用，退出码契约测试须走进程级。

## Solution

不改变已落地的判定设计与行为（H2 已定：单一深模块双入口、dims 四维默认全开、per-entry 容差、CLI 只做参数解析与报告）。补四项收尾，使「oracle 出错不静默」成为有测试保证的事实：

1. **导入 seam 加固**：`pytest.ini` 增加 `pythonpath = .` 一行配置，任意 pytest 调用方式（控制台脚本 / `python -m pytest` / IDE runner）下 `tools`、`core` 均可导入。
2. **黄金测试补全**：按模块真实契约逐维度补齐差异判定用例（文件头 10 字段、通道元数据、NaN/整型/dtype/文本各数值路径、stats 逐点、dims 关闭语义）。
3. **STAT_NAMES 副本 + 相等性锁定测试**：保留 tools/ 内副本（不引入 sys.path bootstrap、不破坏 §8 文档化的独立脚本调用约定），黄金套件新增断言 `mdf_compare.STAT_NAMES == core.stats.STAT_NAMES`——漂移在 pytest 阶段变响亮。
4. **CLI 退出码契约测试**：以 subprocess 断言两个 CLI 的进程返回码（0 = 一致 / 非 0 = 差异），端到端覆盖 argparse 参数接线。

seam 总数：判定逻辑 1 个（模块双入口，已有，不新增）；进程契约 2 个（两个 CLI 薄壳，以子进程穿）；收集配置 1 处（ini 一行）。**无新抽象、无生产代码（core/、gui/）改动**。

## User Stories

1. 作为测试作者，我想在任意 pytest 调用方式下（控制台脚本 / `python -m pytest` / IDE runner）都能收集 oracle 测试，以便测试保护不因运行方式不同而静默消失。
2. 作为验收工程师，我想 `compare_two_mdf` 的退出码契约（0 = 全部一致、非 0 = 存在差异）被测试锁定，以便脚本化验收不因 CLI 接线回归而静默失真。
3. 作为验收工程师，我想 `full_compare` 的退出码契约（0 = 判定一致、非 0 = 存在判定差异且报告仍写入）被测试锁定，以便 CANoe 参考对比的脚本化验收可靠。
4. 作为验收工程师，我想 `--skip-{header,structure,values,stats}` 逐维关闭参数的行为被测试覆盖，以便定向调试时行为与文档一致。
5. 作为测试作者，我想 oracle 黄金套件覆盖文件头全部 10 个字段（version / start_time / abs_time / tz_offset / flags / author / department / project / subject / comment）的差异判定，以便元数据漂移被捕获而非漏报。
6. 作为测试作者，我想 oracle 黄金套件覆盖通道元数据差异（unit / data_type / bit_count / bit_resolution / flags / conversion），以便存储表示漂移（与 CANoe 的已知差异族）在两侧文件间被显式报出。
7. 作为测试作者，我想 oracle 黄金套件覆盖 NaN 与 equal_nan 语义两侧，以便参考对比不因 NaN 误报（本应相等判为差异）或漏报（本应差异判为相等）。
8. 作为测试作者，我想 oracle 黄金套件覆盖整型信号比较路径，以便整型差异不落入浮点容差路径而被掩盖。
9. 作为测试作者，我想 oracle 黄金套件覆盖 dtype 不匹配的报错路径，以便两份产物 dtype 漂移被显式报出而不是静默通过。
10. 作为测试作者，我想 identical 入口覆盖文本通道归一化（尾随 \x00 剥离）与统计组逐点数值判定，以便自产对拍的全维度判定能力都有用例守护。
11. 作为维护者，我想 STAT_NAMES 双副本有相等性锁定测试，以便生产统计布局契约变更时 oracle 漂移在 pytest 阶段变响亮，而不是对拍静默失真。
12. 作为维护者，我想验收工具链的调用约定（master plan §8：`python tools/<脚本>` 独立运行）保持不破坏，以便文档化验收流程零迁移成本。
13. 作为测试作者，我想黄金套件保持合成 MDF、无样例数据依赖，以便无样例环境（本项目已知环境限制）同样可跑。
14. 作为验收工程师，我想本次收尾不改动任何判定行为（H2 行为等价实证已在案），以便收尾仅增加保护、不改变验收结论。

## Implementation Decisions

### D1. 逻辑 seam 维持现状（继承 H2 决定，本次不触碰）

判定逻辑以 [tools/mdf_compare.py](../../tools/mdf_compare.py) 双公开入口为唯一 seam：`compare_files_identical(ref_path, ours_path, *, dims=..., atol=1e-12, rtol=0.0)` 与 `compare_files_reference(ref_path, ours_path, *, stats_ref_layout, dims=..., atol=1e-6, rtol=1e-6)`；返回人类可读差异描述行列表，**空列表 = 一致**；`dims` 四维集合 `{header, structure, values, stats}` 默认全开；`stats_ref_layout` 必填并做 `ValueError` 校验。本 spec 不改判定行为、不改容差、不改对齐策略——只补保护。

### D2. 导入 seam 加固：`pytest.ini` 一行配置

`pytest.ini` 增加 `pythonpath = .`（pytest ≥7 ini 选项，相对 rootdir 解析 = 项目根）。现状证据（2026-08-18 实测，anaconda 3.13.9 / pytest 9.1.1）：

- `python -m pytest tests/test_mdf_compare.py` → 15 passed（cwd 入 sys.path，`from tools.mdf_compare import` 恰好可解析）
- `pytest tests/test_mdf_compare.py`（控制台脚本）→ 收集失败 `ModuleNotFoundError: No module named 'tools'`

该配置使 tests→tools、tests→core 的导入在任何调用方式下稳定（现有 core 系测试同样依赖此路径，是纯收益）。**不影响** CLI 薄壳的 `sys.path[0]=tools/` 脚本调用（§8 契约保持，见 D5）。

### D3. STAT_NAMES：副本保留 + 相等性锁定测试

两份 STAT_NAMES 值完全一致（同 10 项、同顺序，已实测），角色不同：

- [core/stats.py:22-28](../../core/stats.py#L22-L28)：**生产端写出布局契约**——converter 按此顺序写出 16 通道 × 10 项 '1s' 统计组。
- [tools/mdf_compare.py:23-25](../../tools/mdf_compare.py#L23-L25)：**oracle 消费端假设**——用于组内信号序校验（`_stats_diffs_reference`）、ch×10+i 块索引、`_validate_layout` 缺项检查。

决策：**保留 tools/ 副本，黄金套件新增一个相等性断言**（`tools.mdf_compare.STAT_NAMES == core.stats.STAT_NAMES`），漂移在 pytest 阶段变响亮。不选 `from core.stats import STAT_NAMES` 的原因：mdf_compare 引入 core 依赖后，`python tools/compare_two_mdf.py` 独立运行（sys.path[0]=tools/，无 core）即破，需改 §8 文档化调用方式（`python -m` 或路径 bootstrap）——成本高于收益，违背「验收工具链调用约定零迁移」的故事 12。副本 + 锁定测试是同等防护、零迁移的收敛方式（与「严格收口」不冲突：锁定测试即收口机制）。

### D4. 黄金测试补全清单（合成 MDF、无样例依赖）

沿用现有 `_write_mdf` 助手模式（asammdf 构造 + 固定 start_time + `.mf4` 后缀），全部用例穿两个公开入口，按模块真实契约补齐（现状 15 用例 → 补全后约 26~28 用例）：

| 维度 | 现状 | 补全 |
|---|---|---|
| header | 仅 comment | 其余 9 字段（version / start_time / abs_time / tz_offset / flags / author / department / project / subject）逐一构造差异 → 断言报字段名 |
| structure | 组序、通道序 | unit / data_type / bit_count / bit_resolution / flags / conversion 单字段差异 → 断言报通道元数据 |
| values | 浮点容差（reference）、长度（reference）、文本宽度（reference） | NaN/equal_nan 两侧；整型信号差异；dtype 不匹配；identical 文本 \x00 归一化；U→S 归一化（`_norm_bytes` 两分支） |
| stats | t 轴（两入口）、逐点（reference） | identical 逐点数值差异 |
| dims | 非法 dims 报错 | 关闭维度 → 该维度差异不报、其余维度仍报（两入口各一对） |

每个用例 = 已知差异构造 → 断言差异列表含（或确认不含）预期标记；与现有用例同一断言风格。

### D5. CLI 退出码契约测试（subprocess）

两个 CLI 薄壳依赖 `sys.path[0]=tools/` 的兄弟导入（实测 `import tools.compare_two_mdf` / `import tools.full_compare` 均报 `ModuleNotFoundError: No module named 'mdf_compare'`），模块导入路径不可用。退出码本质是**进程级契约**，测试以 `[sys.executable, "tools/<cli>.py", ...]` 子进程断言 `returncode`（真实端到端，同时覆盖 argparse 接线与 dims 映射）：

- `compare_two_mdf`：相等对 → 0；单点改值对 → 1；`--skip-values` 后仅数值差异对 → 0（数值维度关闭生效）。
- `full_compare`：判定一致对 → 0；单点改值 → 1；`--no-report` 不产生报告文件；缺省 `--stats-ref-block` / `--stats-ref-idx` → 解析失败（argparse 必填校验，非 0 退出）。

用例数据复用合成 MDF 构造（tmp_path 内），不依赖样例。测试文件新增于 tests/（如 tests/test_compare_cli.py），与 test_mdf_compare.py 并列。

## Testing Decisions

- **只测外部行为，不测实现细节**：逻辑层穿模块双入口（文件路径进、差异列表出）；进程层穿两个 CLI 子进程（参数进、返回码与输出出）；不触碰 mdf_compare 内部函数、不断言实现文本。
- **被测模块**：[tools/mdf_compare.py](../../tools/mdf_compare.py)（双入口，现有 15 用例 + D4 补全 + D3 锁定断言）、[tools/compare_two_mdf.py](../../tools/compare_two_mdf.py) 与 [tools/full_compare.py](../../tools/full_compare.py)（subprocess 返回码）、[pytest.ini](../../pytest.ini)（收集性——验收方式见下）。
- **测试数据**：合成 MDF（asammdf 构造；test_mdf_compare.py 现有 `_write_mdf` 模式复用），无样例数据依赖（本项目已知环境限制）。
- **先例**：[tests/test_mdf_compare.py](../../tests/test_mdf_compare.py) 现有黄金套件（合成 MDF 穿双入口）；test_converter 的「只经公共接口 + asammdf 读回断言」模式；H2 Task 6 行为等价实证（收口前后判定输出逐行一致，[验证记录](./h2/2026-08-18-h2-compare-consolidation-verification.md)）——本次只增测试、零判定逻辑改动，行为等价由既有用例 + 全量 pytest 守住。
- **验收方式**：`python -m pytest tests/ -q` 与 `pytest tests/ -q`（控制台脚本）**两种调用方式均全绿**（覆盖 D2 的收集性契约）；不重跑 AHT 真实数据对拍（无判定逻辑改动，H2 Task 6 实证在案）。

## Out of Scope

- core/、gui/ 生产代码的任何改动（含 [core/stats.py](../../core/stats.py) 不动）。
- mdf_compare 判定行为的变更（容差、维度、对齐策略——H2 已定，本次不改）。
- full_compare 报告逻辑（`_report` / `_Tee` / `_parse_layout`）移入模块（H2 D4 已定留在 CLI）。
- tools/ 其余脚本的清理（bench_*、probe_* 等，属审查 M8/L9 等条目）。
- 其余审查深化候选（D 失败自清 / C 归一化键 / G 死字段 / B 绑定决策 / A 桶契约）。
- pytest 收集方式的整体改造（如 conftest 上收 fixture、冻结探针转 subprocess——审查「Worth exploring」候选，不在本次范围）。

## Further Notes

- **已实测证据（2026-08-18，anaconda 3.13.9 / pytest 9.1.1）**：① `pytest tests/test_mdf_compare.py` 控制台脚本收集失败（`ModuleNotFoundError: No module named 'tools'`）；② `python -m pytest tests/test_mdf_compare.py` → 15 passed（4.23s）；③ `import tools.compare_two_mdf` / `tools.full_compare` 均失败（兄弟导入 `from mdf_compare import`）；④ `mdf_compare.STAT_NAMES == stats.STAT_NAMES` 实测为 True。
- 关联文档：架构审查报告（[2026-08-18-architecture-review.md](./2026-08-18-architecture-review.md)，H1 / H2 / 候选 F）；H2 spec / plan / verification（[h2/](./h2/)）；master plan §8 验证链（[2026-08-15-blf-mdf-conversion-master-plan.md](../func_impro/2026-08-15-blf-mdf-conversion-master-plan.md)）。
- 本 spec 按项目惯例置于 docs/reviews/ 根目录；无外部 issue tracker（项目以 docs/reviews/ 为 spec 住所，与 H2 同）。
- 不提交 git（用户指示）；提交由用户执行（项目惯例）。
- 后续：spec 批准后出实施计划（任务粒度参考 H2 plan：收集性修复 → 测试补全 → 锁定断言 → CLI 契约 → 全量验证）。
