# blf2mdf

把车上采集的 Vector .blf 日志结合 .dbc 信号矩阵转换为 .mdf（MDF 4.10）测量文件。

## 使用

双击 `blf2mdf.exe`（或 `conda run -n blfmdf python main.py`）：

1. 选择 .blf 文件（自动列出其中记录的总线通道）
2. "添加 DBC…" 导入 .dbc 矩阵
3. 为每个通道选择一个 DBC；不选则导出原始帧
4. 点"转换"，输出单个 .mdf（所有通道数据）

## 开发

依赖：`pip install -r requirements.txt`（Python 3.12，conda 环境 blfmdf）
测试：`conda run -n blfmdf python -m pytest`
金标准对比：`conda run -n blfmdf python -m pytest -m golden`

## 打包

```bash
conda run -n blfmdf pyinstaller --noconfirm --onefile --windowed --name blf2mdf --collect-all PySide6 main.py
```
