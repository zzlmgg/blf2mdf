"""BLF 读取：通道枚举 + 按通道流式产帧。"""
from dataclasses import dataclass
from typing import Iterator


@dataclass
class Frame:
    channel: int
    ts_seconds: float
    arbitration_id: int
    is_extended: bool
    is_fd: bool
    dlc: int
    data: bytes


def _decode(msg) -> Frame:
    """python-can Message → Frame（若探测后改用 blf 包，仅需改写本函数）。"""
    return Frame(
        channel=int(msg.channel),
        ts_seconds=float(msg.timestamp),
        arbitration_id=int(msg.arbitration_id),
        is_extended=bool(msg.is_extended_id),
        is_fd=bool(getattr(msg, "is_fd", False)),
        dlc=int(msg.dlc),
        data=bytes(msg.data),
    )


def list_channels(path: str) -> list[int]:
    import can

    channels = set()
    with can.BLFReader(path) as reader:
        for msg in reader:
            channels.add(int(msg.channel))
    return sorted(channels)


def read_start_time(path: str) -> float:
    """BLF 文件头记录的测量开始时间（UTC 秒）。

    修复项 2：CANoe 导出以该时刻归零（实测参考 _T058.mdf 的 t 轴
    = 帧绝对时间 − 文件头 start_timestamp，与哪些帧被解码无关——
    样例中首解码帧晚于测量开始 2ms，若以首帧归零会整体偏移 2ms）。
    python-can 写出的 BLF 该值为首帧时间（截断 3ms），语义等价。
    """
    import can

    with can.BLFReader(path) as reader:
        return float(reader.start_timestamp)


def iter_messages(path: str, channel: int) -> Iterator[Frame]:
    import can

    with can.BLFReader(path) as reader:
        for msg in reader:
            if int(msg.channel) != channel:
                continue
            yield _decode(msg)
