# 02 — bench/probe 脚本族清理

Type: task
Status: closed (2026-08-19)
Blocked by:

## Question

删除 9 个一次性决策时代的 bench/probe 脚本（review §5 首要建议 + M8/L9）并处理文档引用：

- 已核实：bench_bucket_dist / bench_parallel_finish / bench_spawn / bench_probe / bench_stages / probe_blf / convert_aht / run_gui_probe / verify_clean_env 九个脚本零生产/测试代码引用（仅 docs/README 历史提及）；其中 bench_stages 在 review §5 的 8 删清单之外但 M8 点名其 `sys.path.insert` 反向依赖 tests
- 方案 G/H 决策数据与发布期一次性验证均已完成使命；M8 的「tools 不 import tests」随删除即解

本票：逐脚本确认删除清单 → 删除 → README 提及处按「历史决策记录」处理（操作指引则更新、纯历史保留）→ blf2mdf.spec 打包配置核对（确认无引用被删脚本）→ pytest 全量回归。若发现某脚本仍有实用价值（如 convert_aht 复现 AHT 对拍、verify_clean_env 冻结验证），单独提出保留理由再裁决。

## Resolution (2026-08-19)

**裁决：删除全部 9 个**。逐脚本核实为一次性决策时代产物，零生产/测试引用（pytest 仅收集 `tests/`；blf2mdf.spec 只打包 main.py + assets + icon，test_package_config 锁定）：

- 三个 M8 反向依赖随删除即解（tools 不 import tests）：bench_bucket_dist / bench_parallel_finish / bench_stages（`sys.path.insert(0, tests)` + import conftest/test_golden）
- convert_aht：AHT 对拍已被测试套件覆盖（test_stats / test_mdf_writer / test_blf_reader 的 AHT 用例）；其硬编码 MAPPING 为 M8「绑定表三份」之一，删除后余两份（test_golden.BINDING + project_loader.DEFAULT_MAPPING，供 06 票参考）
- verify_clean_env：已被 `verify_pyside_package.py --launch-smoke` 取代（同款 clean PATH 启动冒烟，且 test_package_config.py:48-61 测试锁定）
- run_gui_probe：自身 docstring 标「（临时）」，硬编码 dist_probe2/build_probe2 旧路径；frozen_probe / frozen_gui_probe 本体零引用不受影响
- bench_spawn / bench_probe / probe_blf：方案 G 一次性测量/基准，无引用

**执行**：9 文件删除；stale pyc 清理；README 验证小节移除「自动化入口 `tools/run_gui_probe.py`」表述（操作指引则更新）；docs/reviews 与 docs/func_impro 提及属历史决策记录，原样保留。

**验收**：全量 pytest **289 passed / 0 失败**（63.01s，blfmdf env，与 2026-08-19 基线一致；6 条 warning 为 test_compare_cli 既有 gbk 解码警告，与本次删除无关）。
