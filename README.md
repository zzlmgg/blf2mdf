# blf2mdf

把车上采集的 Vector .blf 日志结合 .dbc 信号矩阵转换为 .mdf（MDF 4.10）测量文件。

## 使用

运行 `C:\ProgramData\Anaconda3\envs\blfmdf\python.exe main.py`，启动
PySide6/QSS 的紧凑 Windows 桌面面板：

1. 浏览或拖入 `.blf` 文件，后台轻量扫描其中记录的总线通道（对象头级
   通道探测，带进度条、可取消）。也可拖入**文件夹**或**一次拖入多个
   `.blf`**：先弹出勾选列表（逐个勾选 + 全选/全不选 + 已选计数）；勾中
   超过 1 个文件时提示这一批共用一套平台/项目/CAN-DBC 配置（切换同时
   作用于全部文件，某个文件里不存在的通道不导出），可返回列表重勾。
   确认后扫描选中文件的通道并取并集；候选只有 1 个时不弹列表、直接进入。
   （压缩包拖入为下一阶段。）
2. 选择 ccu3.0 项目自动载入/匹配 DBC，或点击 DBC 标题旁绿色 `+` 手动添加。
3. 在 `Channel ↔ DBC` 中检查或调整绑定；不绑定的通道状态为“不导出”，
   映射通道在所有选中文件里都不存在时为“无数据”。
4. 确认输出路径后点击“开始转换”；批量下若已有输出文件存在，一次性问
   覆盖全部 / 跳过已存在 / 取消（不逐文件弹窗，跳过数如实计入结束提示）。
   转换带进度条（批量显示“第 i/N 个”与文件内阶段）、并行解码（内存超阈值
   自动回退串行）、可中途取消（批量下取消 = 停下队列，已完成文件的产物保留，
   日志记“已完成 X / N”并点名跳过的文件；正在转的那个文件按单文件取消处理，
   放弃本次转换——不留半成品，连已写出的完整产物也会删掉），完成后可打开
   完整转换摘要；批量结束按结果分支提示（全成功报“全部转换完成”，有失败则
   报成功/失败个数并点名失败文件），逐文件结局与耗时进日志。涉及 1 个文件时
   输出路径可手动修改（手改值优先）；涉及多个文件时输出路径只读，产物按
   来源落位——散 `.blf` 就地生成 `<主名>_t.mdf`，文件夹来源落进与源同级的
   `<文件夹名>_t/` 镜像树。

未绑定 DBC 的通道**不导出原始帧**（与当前 CANoe 对比基线一致）；界面固定使用
`raw_export=False`，不提供会改变这一语义的开关。

时间基准与 CANoe 逐位一致：时间通道 `t` 为**相对时间**，帧时间戳按**整数 ns
构造**（BLF 头 SYSTEMTIME 毫秒整数 + 对象头相对整数，`× 1e-9` 一次舍入，与
CANoe t 轴同构）；t 轴零点 = 测量开始的**整数秒**，头部 `start_time`/
`abs_time` 保留 SYSTEMTIME 毫秒小数，两者相加可得绝对时间戳。自动化验收见
下文 **Golden 测试守卫**（`canoe_golden/` + `pytest -m golden`）；历史对拍记录见
`docs/2026-08-13-mdf-canoe-comparison-v4.md`。

输出包含与 CANoe 一致的 `1s` 总线统计组（默认开启，10 项 × 全部 16 个通道：
StdData/ExtData/StdRemote/ExtRemote/ErrorFrames 及各自 Rate，逐秒聚合，
周期 1s；未绑定/无数据通道输出全 0）。统计语义按 CANoe 参考文件实测校准
（首窗 1.1s、9ms 输出时刻偏置、帧时间戳毫秒网格，见 `core/stats.py`
docstring）。已知差异：CANoe 共 22 项统计，其余 12 项（Busload、Bursts、
ChipState 等）未实现；非毫秒网格记录（如 A19G1）的统计计数与 CANoe 差
1~2 帧——CANoe 统计基于自身测量时钟域，BLF 无对应信息、不可复现（毫秒
网格记录如 AHT 则一致），详见 `docs/2026-08-13-mdf-canoe-comparison-v4.md`。

记录中途出现数据空洞时（如台架断电：A66T 有 391.5s 全文件零对象空洞），统计组
的**采样时刻**与 CANoe 不同相位——CANoe 在空洞处重启 1s 网格（洞内不出点、洞后
按洞后首帧重新对齐，A66T 洞后相位差 188ms），本工具按整段连续 1s 网格出点、洞内
如实输出 0（静默期速率为 0）。同一时刻的计数一致，**客户关心的信号与信号时间轴
不受影响**（A66T 5,332,820 个对象全量逐位一致）。golden 硬门对该类样例按
自产轴契约验收（前段一致 + 末点一致 + 网格自洽），已知差异以 `已知差异: `
前缀落报告层，见 `tools/mdf_compare.py`。

## 打包发布

发布物为 `exe_publish/BLF2MDF.exe`（PyInstaller onefile 窗口程序，目标体积
不超过 65 MiB）。仅含实际导入的依赖：PySide6 Core/Gui/Widgets、numpy、
asammdf、python-can、cantools 等；VC++ 运行库已内置，唯一系统级依赖是
System32 自带 ICU，Win10 1709+/Win11 可用，**免安装、可在无 Python 环境
机器直接运行**。

数据文件不打包。发布目录即构建输出目录 `exe_publish/`（`BLF2MDF.exe` +
`dbc_ccu3.0/` 直接旁挂），exe 自动在自身目录下找 `inputs/dbc_ccu3.0` 或
`dbc_ccu3.0`（两种布局由 `project_loader.find_ccu3_root()` 自动兼容，优先
inputs 布局），输出写到 exe 旁的 `outputs/`：

```
发布目录/
├── BLF2MDF.exe
└── dbc_ccu3.0/
```

（blf、额外 dbc 可放任意路径，转换时手动选择。）发布压缩包由用户自行组装
（exe + dbc_ccu3.0 打一个包即可）；发布前用
`tools/verify_pyside_package.py --launch-smoke exe_publish\BLF2MDF.exe` 做
体积 + 干净 PATH 启动冒烟验证（缺 DBC 目录也能启动，UI 降级为手动添加
DBC——list_projects 对缺失目录返回空）。

新版构建命令（必须使用 blfmdf conda 环境）：

```powershell
C:\ProgramData\Anaconda3\envs\blfmdf\python.exe -m PyInstaller `
  --noconfirm --clean `
  --workpath build_pyside_qss `
  --distpath exe_publish `
  blf2mdf.spec

C:\ProgramData\Anaconda3\envs\blfmdf\python.exe `
  tools\verify_pyside_package.py --launch-smoke `
  exe_publish\BLF2MDF.exe
```

验证：启动 exe 应出现 `BLF → MDF` 窗口；`tools/frozen_probe.py` 与
`tools/frozen_gui_probe.py` 是与 blf2mdf.spec 同裁剪配置的冻结探针
（`sed 's/\[.main.py.\]/[tools\/frozen_probe.py]/; s/name=.blf2mdf./name=frozen_probe/'`，
GUI 探针另加 `-e 's/console=False/console=True/'`），exe 同目录放置
`inputs/blf/A19G1_ACFCAN_00112_20260614_141114.blf` 与
`inputs/dbc/VDCCCU_CANFD1.dbc` 后运行。注意探针按以上固定路径找样例：
仓库中 BLF 位于 `inputs/blf/`，而 `inputs/dbc/` 目录已不存在——
`VDCCCU_CANFD1.dbc` 现位于 `inputs/dbc_zhilian/`，需先复制到
`inputs/dbc/` 才能跑通探针：
- `frozen_probe` 应打印 `PROBE_OK`（验证 multiprocessing spawn 路径）；
- `frozen_gui_probe` 应打印 `GUI_PROBE_OK`，验证真实 GUI 转换期间
  「BLF → MDF 转换」窗口数恒为 1——即点击转换不会弹出第二个面板
  （回归防护，见下）。

**冻结打包铁律**：入口脚本 `main.py` 的 `__main__` 块必须调用
`multiprocessing.freeze_support()`。spawn worker 以
`exe --multiprocessing-fork` 启动并重新执行入口脚本，没有该拦截会再开一个
主面板并阻塞在事件循环（用户实测：点转换弹新面板，关掉才开始转）。PyInstaller
6.21 不自动注入该调用，`tests/test_main_entry.py` 用 AST 断言防止回归。

spec 内已裁剪未用的 Qt 模块 DLL/插件/翻译（约 60MB）与 conda
ICU（33MB，System32 自带）；不装 UPX 是因为 onefile 内置 zlib 压缩后收益仅
5~15% 且杀软误报风险高。

## 开发

依赖：`pip install -r requirements.txt`（Python 3.12，conda 环境 blfmdf）

### 测试

```powershell
# 全量（含 golden，约 2 分钟）
C:\ProgramData\Anaconda3\envs\blfmdf\python.exe -m pytest

# 日常开发：跳过 golden
C:\ProgramData\Anaconda3\envs\blfmdf\python.exe -m pytest -m "not golden"

# 仅 golden
C:\ProgramData\Anaconda3\envs\blfmdf\python.exe -m pytest -m golden

# golden 并显示转换过程打印
C:\ProgramData\Anaconda3\envs\blfmdf\python.exe -m pytest -m golden -s
```

`outputs/` 在 `.gitignore` 中，不入库；GUI 与手工转换的落盘目录，可整目录删除。

### Golden 测试守卫（`pytest -m golden`）

实现：`tests/test_canoe_parity.py`（对拍逻辑复用 `tools/verify_vs_canoe.compare_one`，
不复制判定代码）。每条用例**现场**把 `source.blf` 转成 mdf，再与同目录
`canoe.mdf` 对比；断言通过后删除本次写出的 mdf，不在 `outputs/verify_canoe/`
堆积。手工验收脚本 `tools/verify_vs_canoe.py` 仍可用于生成保留产物与
Markdown 报告（`--skip-convert` 复用 `--outdir` 下最新 `cmp_*.mdf`）。

**金样本目录**（`canoe_golden/`，不入库，需本地自备）：

```
canoe_golden/
└── <平台>/                    # 如 ccu3.0 → inputs/dbc_ccu3.0
    └── <项目>/                # load_project 的项目文件夹名，如 A19G1
        └── <BLF 主文件名>/    # 样本目录名
            ├── source.blf     # 原始采集
            └── canoe.mdf      # CANoe 转换参考（权威对照）
```

空平台（如 `ccu4.0`）不产生用例；任一样本目录缺 `source.blf` 或 `canoe.mdf`
则收集/运行失败。

**当前样本（4 条，均在 ccu3.0）**

| 项目 | 样本目录（BLF 主文件名） | 说明 |
|------|---------------------------|------|
| A19G1 | `A19G1_ACFCAN_00112_20260614_141114` | 基准样例 |
| A02Y | `A02Y_ACFCANPUB_20260917_221900_59655158-ACFCANPUB_20260917_222500_59655170` | CANape 值表信号存储形态回归 |
| A66T | `A66T_ACFCANPUB_20260917_151500_59654310-ACFCANPUB_20260917_154000_59654360` | 16 路 CAN 大文件；含 391.5s 记录空洞，统计 t 轴按自产轴契约验收 |
| AHT | `AHT_ACFCANPUB_20260317_210430_59125089-ACFCAN_20260317_210930_59125099` | 大文件性能样例 |

**硬门（全部通过才算 PASS）**

- 解码信号值：`isclose`（atol=1e-6，rtol=1e-6，`equal_nan=True`）
- 解码信号时间戳：逐位一致
- `1s` 统计组 t 轴：沿用 `tools/mdf_compare.py` 规则（含 A66T 记录空洞的自产轴契约）

文件头、组序、存储形态、统计逐点计数等仅进报告层，**不**决定 golden 成败。

**性能门（整段墙钟）**

转换路径与 GUI 一致（`convert_one`：按平台加载 DBC、`raw_export=False`、
`parallel=True`、`stats_export=True`）。上限 = 登记墙钟 + `min(2s, 登记墙钟的 10%)`
（登记值见 `tests/test_canoe_parity.py` 的 `_WALL_S`）。超时失败时 pytest 报
实际秒数与上限。

| 样本 | 登记墙钟 | 上限 |
|------|---------:|-----:|
| A19G1 | 5.01 s | 5.51 s |
| A02Y | 5.64 s | 6.20 s |
| A66T | 13.76 s | 15.14 s |
| AHT | 15.15 s | 16.67 s |
