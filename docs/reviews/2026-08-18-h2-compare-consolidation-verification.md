# H2 对拍收口 Task 6 行为等价验证记录

> 计划：[2026-08-18-h2-compare-consolidation-plan.md](./2026-08-18-h2-compare-consolidation-plan.md) Task 6；spec 同目录。
> 验证日期：2026-08-18；环境：conda env `blfmdf`（Python 3.12.13 / numpy 2.5.1 / asammdf 8.8.22 / pytest 9.1.1）。

## 验证数据

| 角色 | 路径 | 说明 |
|---|---|---|
| 并行产物 | `inputs/blf/AHT_ACFCANPUB_20260317_210430_59125089-ACFCAN_20260317_210930_59125099_t.mdf` | 12,936,352 B，已有 |
| 串行产物 | `%TEMP%\h2_verify\AHT_serial.mdf` | 现场 `tools/convert_aht.py` 生成，12,936,352 B（与并行产物同大小），耗时 1155.2s（串行模式，10 通道） |
| CANoe 参考 | `inputs/mdf_canoe/AHT.mdf` | 已有 |

原版脚本从 `2165960`（重写历史后「提交H2的task1-5」的父提交）恢复，与重写前恢复内容逐字节一致。

## Step 1 — identical 语义等价（并 vs 串）

| 版本 | 输出 | 退出码 |
|---|---|---|
| 原版 `compare_two_mdf` | `=== 230 组对拍: 全部一致 ===` | 0 |
| 收口版（修复后） | `=== 230 组对拍: 全部一致 ===` | 0 |

- `diff` 逐字节一致；收口版两次独立运行输出一致（稳定）。
- **发现并修复**：原版汇总行带组数（`N 组对拍`），薄壳初版只打印 `全部一致`。按用户确认修复 `tools/compare_two_mdf.py`：元数据轻量打开（实测 0.03s）取组数，汇总行复刻原案（含差异分支 `N 组对拍: X 处差异`）。修复后与原版逐字节一致。

## Step 2 — reference 语义等价（串行产物 vs CANoe 参考）

原版 `full_compare --no-report` 结论：70/70 组匹配、dtype 类别抽查全部一致、枚举文本逐值全部一致、抽样数值全部一致、**160 组 × 601 点逐点全部一致**（自产 160 vs 参考 352 '1s' 组）。

收口版判定（默认 dims 全开 + `--stats-ref-block 22 --stats-ref-idx "StdData:4,…"`）共 **3434 处差异，逐类核验全部为已知真实差异，无意外**：

| 类 | 行数 | 核验 |
|---|---|---|
| header.comment | 1 | 已知：CANoe common_properties 4 字段 vs 我们空（memory `mdf-canoe-format-diffs`） |
| 组内信号序不同 | 41 | 原版 `deep_compare_latest` 记录「41/70」同组差异（VCU_9/ADC_4/…）；记录→判错升级，计划 Task 1 独有能力去向已注明 |
| data_type | 802 | 已知：CANoe 存原始 int（0/1/2/3/4）+ 转换，我们存物理值 float64/UTF-8（memory dtype 差异 ~44~59/60~67 通道，模块为逐通道全量） |
| bit_count | 812 | 同上存储表示差异 |
| flags | 908 | 同上（CANoe 16 vs 我们 0） |
| conversion | 870 | 已知：CANoe 线性/枚举转换元数据 vs 我们无（memory conversion 有无差异 ~53~59/60~67） |
| unit | 0 | 与 memory「unit 全一致」相符 |
| values 维度 | 0 | 对应原版「dtype 抽查一致 / 枚举文本逐值全部一致 / 抽样数值一致」 |
| stats 维度 | 0 | 对应原版「160 组 × 601 点逐点全部一致」 |

退出码 1（存在差异）——符合计划预期（判定现在真实存在，报告工具获得脚本化退出码，spec 精化 ②）。

## Step 3 — 统计 t 轴等价

原版 `deep_compare_latest`：`统计 t 轴: 自产 n=1217 [0]=0.0 [-1]=1215.356 | 参考 n=1217 [0]=0.0 [-1]=1215.356`（完全一致）。

收口版 stats 维度无「统计 t 轴」差异行——与打印值相符。

## Step 4 — pytest 全绿 + 稳定性

- `pytest tests/ -q`：**220 passed**（blfmdf 环境，修复前后各跑一次均全绿）。
- identical 对拍复跑两次，输出逐字节一致（稳定）。

## 结论

- identical 入口：判定、输出、退出码与原版**逐字节一致**（修复汇总行后）。
- reference 入口：values/stats 维度与原版「全部一致」结论吻合；header/structure 维度报出的 3434 行全部为文档已知真实差异（头部 comment、存储表示元数据、组内信号序 41 组），**无意外行**；退出码 1 符合预期。
- 行为等价成立（D6 实证完成）。

## 未提交事项

- `tools/compare_two_mdf.py` 汇总行修复（本验证发现，Task 2 跟进修复）——待用户提交。
- 验证中间产物（原版脚本副本、v1/v2 输出、串行产物）在 `%TEMP%\h2_verify\`，不入库。
- git 提交由用户执行（项目惯例）。
