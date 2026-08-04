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


def iter_messages(path: str, channel: int) -> Iterator[Frame]:
    import can

    with can.BLFReader(path) as reader:
        for msg in reader:
            if int(msg.channel) != channel:
                continue
            yield _decode(msg)
