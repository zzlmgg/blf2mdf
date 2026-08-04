# BLF → MDF 转换工具（blf2mdf）设计文档

日期：2026-08-04
状态：已确认（分节评审通过）

## 1. 背景与目标

车载 CAN 数据采集产生的原始二进制 .blf 日志（Vector BLF 格式）需要结合 .dbc 信号矩阵转换为人类可解读的 .mdf（MDF 4.10）测量文件。现有转换依赖 Vector CANoe 商业工具，操作繁琐（需打开 CANoe、手动配置）。

**目标**：开发一个自研桌面工具，导入 1 个 .blf 文件和 .dbc 矩阵文件，为该文件记录的各总线通道分配 DBC，一键转换输出包含全部通道数据的单个 .mdf 文件。

**成功标准**：
- 样例文件 `blf/ACFCANPUB_20260722_104000_*.blf` 可完整转换；
- 绑定 DBC 的通道输出解码物理信号，其信号集覆盖参考文件 `mdf/_T058.mdf` 的 Signal 部分，抽样信号数值与参考一致（容差内）、时间跨度一致（10 分钟）；
- 未绑定 DBC 的通道以原始帧数据组导出；
- 产出为单个 exe，可双击使用，无需安装 Python 或依赖 CANoe。

## 2. 需求确认

| 项 | 决策 |
|---|---|
| 动机 | 简化操作流程，替代 CANoe 转换 |
| 交互形式 | 桌面 GUI（PySide6），单窗口 |
| 规模 | 一次转 1 个 BLF；无需批量/队列 |
| 通道模型 | **1 个 BLF 通道 ↔ 1 个 DBC**；DBC 不合并 |
| 导出模型 | **所有通道全部导出**到 1 个 MDF：绑定 DBC → 解码物理信号；未绑定 → 原始帧数据组 |
| 输出内容 | 解码物理信号 + 未绑定通道的原始帧；不含总线统计通道 |
| 输出格式 | MDF 4.10（asammdf 写出，CANape/INCA 可读） |

## 3. 架构

```
┌──────────────────────────────────────┐
│ GUI 层（PySide6 单窗口）             │
│ 文件选择 / DBC 管理 / 通道-DBC 绑定  │
│ 转换按钮 / 进度条 / 结果摘要          │
└───────────────┬──────────────────────┘
                │ 后台线程转换，界面不阻塞
┌───────────────▼──────────────────────┐
│ 转换内核 core/（纯逻辑，不依赖 Qt）  │
│                                      │
│ blf_reader.py  流式读 BLF            │
│   ├ list_channels(path) -> list      │
│   └ iter_messages(path, channel)     │
│ dbc_loader.py  解析单个 DBC          │
│   └ load(path) -> {id: MessageDef}   │
│ decoder.py     帧 → 信号物理值       │
│ mdf_writer.py  组装写出 MDF 4.10     │
└──────────────────────────────────────┘
```

数据流：BLF → 按通道过滤 → 绑定通道逐帧解码 / 未绑定通道透出原始帧 → 按报文聚合 → 写入单个 MDF。

**核心原则**：界面与内核彻底分离；流式处理不整载 BLF 入内存。

## 4. 核心模块规格

### 4.1 blf_reader.py — BLF 读取

依赖 `python-can` 的 BLF reader（备选：`blf` 纯 Python 包；实现时以能正确处理样例文件为准则，两者可互换，接口不变）。

- `list_channels(path) -> list[str]`：扫描 BLF，返回其中出现的通道标识列表（GUI 通道表数据源）。
- `iter_messages(path, channel) -> Iterator[Frame]`：流式产出指定通道的报文帧，仅 CAN/CANFD 报文对象（过滤错误帧、过载帧等非报文对象）。
- `Frame`：`channel, ts_seconds(float), arbitration_id, is_extended, is_fd, dlc, data(bytes)`。
- 时间戳统一换算为秒（float64），与 MDF 时间轴一致。

### 4.2 dbc_loader.py — DBC 解析（单文件，不合并）

依赖 `cantools`。

- `load(path) -> dict[arbitration_id, MessageDef]`。
- `MessageDef = (name, sender_node, signals[], is_extended, frame_length)`。
- 一次只加载一个 DBC；无合并逻辑、无跨文件冲突问题。GUI 导入的多个 DBC 只是备选库，每个通道各选一个绑定。

### 4.3 decoder.py — 帧 → 物理值

- 输入：Frame 流 + 该通道绑定的 DBC 查询表；未绑定通道不走本模块（见 4.4 原始组）。
- 按 `arbitration_id` 查表，用 cantools 的 decode 解出物理值。
- 输出按报文聚合：`SignalSeries(msg_id, name, node, timestamps[], signal_values{signal: array})`。
- 边界：ID 不在表中 → 跳过并计数（摘要报告）；mux 信号非激活帧记 NaN（保证组内等长）；字节序/缩放/偏移/单位由 cantools 处理。

### 4.4 mdf_writer.py — MDF 4.10 写出

依赖 `asammdf`，写 MDF 4.10。

- **信号组**（绑定通道）：一个报文 ID 一个 ChannelGroup，组名 `Signal::<发送节点>`；组内 = 时间通道（float64 秒）+ 每信号一个 Channel（float64，带 unit）。
- **原始组**（未绑定通道）：组名 `Raw::<通道名>`，每帧一行：时间（float64 秒）、`ID`（uint32）、`DLC`（uint8）、`Data`（uint8 数组通道，长度 = 该通道帧的最大 DLC，上限 64，支持 CANFD）、`IsExtended`（uint8）、`IsFD`（uint8）。
- **多通道消歧**：不同通道出现同名 `Signal::<节点>` 组时，加通道前缀，如 `CAN3::Signal::VDC`。
- 完成后返回统计（每通道：报文数、信号数、总帧数、时间跨度、跳过未知帧数）供 GUI 摘要。

## 5. GUI 设计（PySide6 单窗口）

```
┌─ BLF → MDF 转换 ─────────────────────────────────────────┐
│                                                          │
│  BLF 文件   [ C:\...\ACFCANPUB_20260722_104000.blf  ] [浏览]│
│                                                          │
│  DBC 矩阵文件                                             │
│  ┌──────────────────────────────────────────────────┐    │
│  │  VDCPublic_CANFD1.dbc      C:\...\dbc\            │    │
│  │  VDCCCU_CANFD1.dbc         C:\...\dbc\            │    │
│  │  VDCTBOX_CANFD.dbc         C:\...\dbc\            │    │
│  │  ─────────────────────────────────────────────── │    │
│  │  [ 添加 DBC… ]  [ 移除选中 ]                       │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  通道             DBC 矩阵             状态               │
│  ┌─┐ CAN1          [VDCPublic_CANFD1.dbc   ▾]   已绑定    │
│  ┌─┐ CAN2          [不绑定（导出原始帧）    ▾]   原始      │
│  ┌─┐ CAN3          [VDCCCU_CANFD1.dbc      ▾]   已绑定    │
│  ┌─┐ CAN4          [不绑定（导出原始帧）    ▾]   原始      │
│  ┌─┐ CAN5          [不绑定（导出原始帧）    ▾]   原始      │
│                                                          │
│  输出文件 [ C:\...\ACFCANPUB_..._conv.mdf        ] [浏览] │
│                                                          │
│  [ 转 换 ]   ████████░░░░░░ 42%   正在解码 CAN1…          │
│  ── 结果摘要 ──────────────────────────────────           │
│  CAN1 已绑定:  解码 482,120 帧 · 548 个信号 · 未知 3 帧    │
│  CAN2 未绑定:  原始帧 1,204 帧                            │
└──────────────────────────────────────────────────────────┘
```

**交互流程**：
1. 选 BLF → 扫描出通道 → 通道表出现，每行一个 DBC 下拉框，默认"不绑定（导出原始帧）"。
2. "添加 DBC…" → 文件对话框（多选、`*.dbc` 过滤）；导入后出现在列表及所有通道下拉框。"移除选中" → 移除；若该 DBC 正被绑定，对应通道自动回落为"不绑定"。
3. 输出路径默认 = BLF 同目录同名加 `_conv` 后缀，可改。
4. "转换" → 后台线程执行，进度条 + 阶段文字（扫描 BLF / 解码 / 写 MDF）；转换中全部控件禁用。
5. 完成 → 底部摘要按通道报告。

样式：PySide6 + Fusion 主题，浅色为主，统一间距。

## 6. 错误处理与边界

| 场景 | 行为 |
|---|---|
| BLF 打不开 / 格式损坏 | 弹窗报错并指出路径，不启动转换 |
| DBC 语法错误 | 弹窗指出文件与解析错误，该文件不加入列表 |
| BLF 中 0 个通道 | 提示"文件中未找到有效报文数据" |
| 转换中途失败 | 弹窗报错，自动删除半成品 MDF |
| 绑定通道遇未知 ID | 跳过并计数，摘要报告"未知报文 N 帧 / M 个不同 ID" |
| 绑定通道无匹配帧 | 摘要警告"该通道无匹配帧"，该通道组跳过不写 |
| 输出文件已存在 | 弹窗确认覆盖 |
| 内存 | 流式处理 + 按通道数组累积；样例 35MB 无压力 |

## 7. 测试与验证策略

- **金标准对比**（最关键）：样例 BLF + VDCPublic 系列 DBC → 用 asammdf 读参考 `mdf/_T058.mdf` 对比：`Signal::` 通道集合覆盖、抽样信号数值容差内一致、时间跨度一致（对比时忽略参考文件中的 BusStatistic 部分）。
- **单元测试**：13 个 DBC 全部可解析；decoder 构造已知帧验证物理值换算；blf_reader 通道枚举。
- **端到端**：python-can 合成微型多通道 BLF → 完整流程 → 校验 MDF 结构与值。
- **GUI 冒烟**：手动走加载 → 绑定 → 转换全流程。

## 8. 交付与环境

- 技术栈：Python 3.11 + `cantools` + `python-can` + `asammdf` + `PySide6` + `numpy`。
- 项目结构：

```
blf_dbc/
├─ main.py               # GUI 入口
├─ core/                 # 转换内核（不依赖 Qt）
│  ├─ blf_reader.py
│  ├─ dbc_loader.py
│  ├─ decoder.py
│  └─ mdf_writer.py
├─ gui/main_window.py
├─ tests/
└─ requirements.txt
```

- 环境：当前机器无可用 Python（PATH 仅有 WindowsApps 占位符），实现第一步安装 Python 3.11。
- 分发：PyInstaller 打包单文件 `blf2mdf.exe`（约 80-100MB）。
- 版本管理：初始化 git 仓库，数据文件（blf/、mdf/、dbc/、outputs/、raw_inputs.7z）加入 .gitignore。
