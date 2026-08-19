# blf2mdf

把车上采集的 Vector .blf 日志结合 .dbc 信号矩阵转换为 .mdf（MDF 4.10）测量文件。

## 使用

运行 `C:\ProgramData\Anaconda3\envs\blfmdf\python.exe main.py`，启动
PySide6/QSS 的紧凑 Windows 桌面面板：

1. 浏览或拖入 `.blf` 文件，后台轻量扫描其中记录的总线通道（对象头级
   通道探测，带进度条、可取消）。
2. 选择 ccu3.0 项目自动载入/匹配 DBC，或点击 DBC 标题旁绿色 `+` 手动添加。
3. 在 `Channel ↔ DBC` 中检查或调整绑定；不绑定的通道状态为“不导出”。
4. 确认自动生成的 `<BLF 文件名>_t.mdf` 输出路径（可手动修改或浏览），点击
   “开始转换”；转换带进度条、并行解码（内存超阈值自动回退串行）、可中途
   取消，完成后可打开完整转换摘要。

未绑定 DBC 的通道**不导出原始帧**（与当前 CANoe 对比基线一致）；界面固定使用
`raw_export=False`，不提供会改变这一语义的开关。

时间基准与 CANoe 逐位一致：时间通道 `t` 为**相对时间**，帧时间戳按**整数 ns
构造**（BLF 头 SYSTEMTIME 毫秒整数 + 对象头相对整数，`× 1e-9` 一次舍入，与
CANoe t 轴同构）；t 轴零点 = 测量开始的**整数秒**，头部 `start_time`/
`abs_time` 保留 SYSTEMTIME 毫秒小数，两者相加可得绝对时间戳。经 A19G1 /
AHT 两份参考文件全量对比，信号时间戳与值均与 CANoe **逐位一致**（704 +
925 信号，见 `docs/2026-08-13-mdf-canoe-comparison-v4.md`）。

输出包含与 CANoe 一致的 `1s` 总线统计组（默认开启，10 项 × 全部 16 个通道：
StdData/ExtData/StdRemote/ExtRemote/ErrorFrames 及各自 Rate，逐秒聚合，
周期 1s；未绑定/无数据通道输出全 0）。统计语义按 CANoe 参考文件实测校准
（首窗 1.1s、9ms 输出时刻偏置、帧时间戳毫秒网格，见 `core/stats.py`
docstring）。已知差异：CANoe 共 22 项统计，其余 12 项（Busload、Bursts、
ChipState 等）未实现；非毫秒网格记录（如 A19G1）的统计计数与 CANoe 差
1~2 帧——CANoe 统计基于自身测量时钟域，BLF 无对应信息、不可复现（毫秒
网格记录如 AHT 则一致），详见 `docs/2026-08-13-mdf-canoe-comparison-v4.md`。

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
测试：`C:\ProgramData\Anaconda3\envs\blfmdf\python.exe -m pytest`
金标准对比：`C:\ProgramData\Anaconda3\envs\blfmdf\python.exe -m pytest -m golden`
