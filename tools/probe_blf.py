import can
from pathlib import Path

path = str(sorted(Path("inputs/blf").glob("*.blf"))[0])  # 在项目根目录运行
types, channels, fd = {}, set(), 0
n = 0
try:
    with can.BLFReader(path) as reader:
        for msg in reader:
            n += 1
            channels.add(msg.channel)
            if getattr(msg, "is_fd", False):
                fd += 1
            if n <= 5:
                print("sample:", msg)
    print(f"总帧数(前扫): {n}")
    print("通道:", sorted(channels))
    print("CANFD 帧数:", fd)
except Exception as e:
    print("BLF 读取失败:", type(e).__name__, e)
