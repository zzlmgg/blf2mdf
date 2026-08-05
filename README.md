# blf2mdf

把车上采集的 Vector .blf 日志结合 .dbc 信号矩阵转换为 .mdf（MDF 4.10）测量文件。

## 使用

运行 `conda run -n blfmdf python main.py`：

1. 选择 .blf 文件（自动列出其中记录的总线通道）
2. "添加 DBC…" 导入 .dbc 矩阵
3. 为每个通道选择一个 DBC；不选则导出原始帧
4. 点"转换"，输出单个 .mdf（所有通道数据）

未绑定 DBC 的通道导出原始帧时，仅保留本次转换中任一已绑定 DBC 有报文定义的帧；
其余帧（DBC 中无对应信号名的未知信号）在摘要中计数并丢弃。完全不使用 DBC 时不过滤。

## 开发

依赖：`pip install -r requirements.txt`（Python 3.12，conda 环境 blfmdf）
测试：`conda run -n blfmdf python -m pytest`
金标准对比：`conda run -n blfmdf python -m pytest -m golden`
