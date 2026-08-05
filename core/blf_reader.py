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
    is_remote: bool = False
    is_error: bool = False


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
        is_remote=bool(getattr(msg, "is_remote_frame", False)),
        is_error=bool(getattr(msg, "is_error_frame", False)),
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


def iter_all_messages(path: str) -> Iterator[Frame]:
    """单遍全量帧流（每帧带 channel 字段，供单遍扫描管线路由用）。

    性能优化（方案 A）：convert 只做一次全文件遍历，把帧路由到各通道
    解码器与统计收集器，替代按通道逐遍重复解析整个文件（python-can 的
    BLFReader 无索引，每次迭代都要从头解析全部帧）。
    """
    import can

    with can.BLFReader(path) as reader:
        for msg in reader:
            yield _decode(msg)


def scan_channels(path: str) -> dict[int, tuple]:
    """一次全文件扫描：每通道的统计输入（修复项 4）。

    返回 {channel: (相对时间戳 float64, is_extended, is_remote, is_error)}，
    数组等长；未出现的通道不在字典中。供总线统计收集使用（避免逐通道
    重复全文件扫描）。
    """
    from collections import defaultdict

    import can
    import numpy as np

    data = defaultdict(lambda: ([], [], [], []))
    with can.BLFReader(path) as reader:
        start = int(reader.start_timestamp)
        for msg in reader:
            ts, ext, rem, err = data[int(msg.channel)]
            ts.append(float(msg.timestamp) - start)
            ext.append(bool(msg.is_extended_id))
            rem.append(bool(getattr(msg, "is_remote_frame", False)))
            err.append(bool(getattr(msg, "is_error_frame", False)))
    return {
        ch: (np.asarray(ts, dtype=np.float64), np.asarray(ext, dtype=bool),
             np.asarray(rem, dtype=bool), np.asarray(err, dtype=bool))
        for ch, (ts, ext, rem, err) in data.items()
    }
