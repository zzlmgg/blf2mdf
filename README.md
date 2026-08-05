# blf2mdf

把车上采集的 Vector .blf 日志结合 .dbc 信号矩阵转换为 .mdf（MDF 4.10）测量文件。

## 使用

运行 `conda run -n blfmdf python main.py`：

1. 选择 .blf 文件（自动列出其中记录的总线通道）
2. "添加 DBC…" 导入 .dbc 矩阵
3. 为每个通道选择一个 DBC；不选则该通道不参与解码
4. （可选）勾选「导出未绑定通道的原始帧」后再点"转换"，输出单个 .mdf（所有通道数据）

未绑定 DBC 的通道**默认不导出原始帧**（与 CANoe 导出一致）；勾选「导出未绑定通道的
原始帧」后，未绑定通道以 `Raw::CANn` 组导出，仅保留本次转换中任一已绑定 DBC 有
报文定义的帧；其余帧（DBC 中无对应信号名的未知信号）在摘要中计数并丢弃。
完全不使用 DBC 时不过滤。

时间基准与 CANoe 导出一致：时间通道 `t` 为**相对时间**（以 BLF 文件头的测量
开始时间归零）；绝对测量起始时间（整秒，UTC）写入 MDF 头部 `start_time`，
两者相加可得绝对时间戳。

输出包含与 CANoe 一致的 `1s` 总线统计组（默认开启，10 项 × 全部 16 个通道：
StdData/ExtData/StdRemote/ExtRemote/ErrorFrames 及各自 Rate，逐秒聚合，
周期 1s；未绑定/无数据通道输出全 0）。统计语义按 CANoe 参考文件实测校准
（首窗 1.1s、帧时间戳毫秒网格等，见 `docs/2026-08-05-mdf-differences-fix-plan.md`
§11 修复项 4 执行记录）。

## 开发

依赖：`pip install -r requirements.txt`（Python 3.12，conda 环境 blfmdf）
测试：`conda run -n blfmdf python -m pytest`
金标准对比：`conda run -n blfmdf python -m pytest -m golden`
