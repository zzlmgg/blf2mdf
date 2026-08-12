"""BLF 读取：通道枚举（轻量探测/完整解析）+ 按通道流式产帧。"""
from dataclasses import dataclass
from typing import Iterator


class ScanCancelled(Exception):
    """读取被用户取消（GUI 取消按钮置位后，读取循环抛出）。"""


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


def list_channels(path: str, progress_cb=None) -> list[int]:
    """枚举 BLF 中的通道（完整逐帧解析，参考实现/测试与工具用）。

    GUI 输入阶段请用 probe_channels（对象头级轻量行走，5-20 倍更快且
    支持取消）。progress_cb 语义同 probe_channels：每读完一个日志容器
    回调一次（0-100，基于文件字节位置）。
    """
    import os

    import can

    channels = set()
    total = os.path.getsize(path)
    last = -1
    with can.BLFReader(path) as reader:
        for msg in reader:
            channels.add(int(msg.channel))
            if progress_cb is not None and total:
                pos = reader.file.tell()
                if pos != last:
                    last = pos
                    progress_cb(pos * 100.0 / total)
    return sorted(channels)


def probe_channels(path: str, progress_cb=None, cancel_cb=None) -> list[int]:
    """轻量通道枚举：只走容器→对象头，不构造 Message、不解析 payload。

    输入阶段专用（GUI 加载 BLF）：86MB/565 万帧样例 list_channels 实测
    41s，本函数只解对象头里的 channel 字段，预计 5-20 倍加速（需实测）。
    行走结构复刻 python-can 的 BLFReader.__iter__/_parse_container/
    _parse_data（can/io/blf.py），复用其常量与 struct——channel 均为各
    消息结构体的首字段（文件 1-based，读出减 1 变 0-based，与 python-can
    一致）；跨容器对象用尾部缓冲衔接（_tail 语义）；未知对象类型按
    obj_size 跳过；未知压缩/损坏结构抛 BLFParseError（与 list_channels
    同语义）。

    progress_cb(percent) 逐容器字节进度（0-100）；cancel_cb() 每 1024 个
    对象检查一次，返回 True 即 raise ScanCancelled（GUI 取消按钮置位）。
    """
    import os
    import struct
    import zlib

    from can.io.blf import (
        BLFParseError,
        CAN_ERROR_EXT,
        CAN_ERROR_EXT_STRUCT,
        CAN_FD_MESSAGE,
        CAN_FD_MESSAGE_64,
        CAN_FD_MSG_64_STRUCT,
        CAN_FD_MSG_STRUCT,
        CAN_MESSAGE,
        CAN_MESSAGE2,
        CAN_MSG_STRUCT,
        FILE_HEADER_STRUCT,
        LOG_CONTAINER,
        LOG_CONTAINER_STRUCT,
        NO_COMPRESSION,
        OBJ_HEADER_BASE_STRUCT,
        OBJ_HEADER_V1_STRUCT,
        OBJ_HEADER_V2_STRUCT,
        ZLIB_DEFLATE,
    )

    def _channel_of(data: bytes, pos: int, obj_type: int) -> int | None:
        """消息对象头中的 channel（0-based）；非消息对象返回 None。"""
        if obj_type in (CAN_MESSAGE, CAN_MESSAGE2):
            return CAN_MSG_STRUCT.unpack_from(data, pos)[0] - 1
        if obj_type == CAN_ERROR_EXT:
            return CAN_ERROR_EXT_STRUCT.unpack_from(data, pos)[0] - 1
        if obj_type == CAN_FD_MESSAGE:
            return CAN_FD_MSG_STRUCT.unpack_from(data, pos)[0] - 1
        if obj_type == CAN_FD_MESSAGE_64:
            return CAN_FD_MSG_64_STRUCT.unpack_from(data, pos)[0] - 1
        return None

    def _walk(data: bytes, channels: set[int]) -> bytes:
        """容器内对象行走（= python-can _parse_data 轻量版）。

        返回未解析尾部（跨容器对象，字节留给下一容器衔接）——复刻
        _parse_container 的 _tail 语义：容器不足时从当前对象起点保留。
        """
        max_pos = len(data)
        pos = 0
        n = 0
        while True:
            obj_start = pos
            try:
                pos = data.index(b"LOBJ", pos, pos + 8)
            except ValueError:
                if pos + 8 > max_pos:
                    return data[obj_start:]
                raise BLFParseError("Could not find next object") from None
            try:
                _, _, header_version, obj_size, obj_type = \
                    OBJ_HEADER_BASE_STRUCT.unpack_from(data, pos)
            except struct.error:
                return data[obj_start:]  # 容器末尾不足一个对象头
            next_pos = pos + obj_size
            if next_pos > max_pos:
                return data[obj_start:]  # 对象跨容器，剩余留给下一容器
            n += 1
            if n % 1024 == 0 and cancel_cb is not None and cancel_cb():
                raise ScanCancelled()
            pos += OBJ_HEADER_BASE_STRUCT.size
            if header_version == 1:
                pos += OBJ_HEADER_V1_STRUCT.size
            elif header_version == 2:
                pos += OBJ_HEADER_V2_STRUCT.size
            else:
                pos = next_pos
                continue  # 未知对象头版本：整体跳过（同 python-can）
            try:
                ch = _channel_of(data, pos, obj_type)
            except struct.error:
                return data[obj_start:]  # 对象头截断（同 python-can 的容器级兜底）
            if ch is not None:
                channels.add(ch)
            pos = next_pos

    total = os.path.getsize(path)
    last = -1
    channels = set()
    tail = b""
    with open(path, "rb") as f:
        header = FILE_HEADER_STRUCT.unpack(f.read(FILE_HEADER_STRUCT.size))
        if header[0] != b"LOGG":
            raise BLFParseError("Unexpected file format")
        f.seek(header[1])  # 跳过文件头剩余（python-can: read(header[1] - 56)）
        while True:
            base = f.read(OBJ_HEADER_BASE_STRUCT.size)
            if not base:
                break  # EOF
            signature, _, _, obj_size, obj_type = \
                OBJ_HEADER_BASE_STRUCT.unpack(base)
            if signature != b"LOBJ":
                raise BLFParseError()
            obj_data = f.read(obj_size - OBJ_HEADER_BASE_STRUCT.size)
            f.read(obj_size % 4)  # 容器尾部填充字节
            if progress_cb is not None and total:
                pos = f.tell()
                if pos != last:
                    last = pos
                    progress_cb(pos * 100.0 / total)
            if obj_type != LOG_CONTAINER:
                continue  # 非容器对象：跳过（同 python-can __iter__）
            method, _ = LOG_CONTAINER_STRUCT.unpack_from(obj_data)
            container_data = obj_data[LOG_CONTAINER_STRUCT.size:]
            if method == NO_COMPRESSION:
                data = container_data
            elif method == ZLIB_DEFLATE:
                data = zlib.decompress(container_data)
            else:
                continue  # 未知压缩方法：跳过（同 python-can）
            if tail:
                data = tail + data
                tail = b""
            tail = _walk(data, channels)
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


def iter_all_messages(path: str, progress_cb=None, cancel_cb=None) -> Iterator[Frame]:
    """单遍全量帧流（每帧带 channel 字段，供单遍扫描管线路由用）。

    性能优化（方案 A）：convert 只做一次全文件遍历，把帧路由到各通道
    解码器与统计收集器，替代按通道逐遍重复解析整个文件（python-can 的
    BLFReader 无索引，每次迭代都要从头解析全部帧）。

    progress_cb(percent: float) 可选：每读完一个日志容器回调一次
    （0-100，基于文件字节位置，与 list_channels 同一语义）——大文件
    读取占转换耗时大头，进度条需在读取期间持续前进而不是停在 5%。

    cancel_cb() 可选：每 1024 帧检查一次，返回 True 即 raise
    ScanCancelled（转换阶段取消，GUI 取消按钮置位）。
    """
    import os

    import can

    total = os.path.getsize(path)
    last = -1
    n = 0
    with can.BLFReader(path) as reader:
        for msg in reader:
            yield _decode(msg)
            n += 1
            if n % 1024 == 0 and cancel_cb is not None and cancel_cb():
                raise ScanCancelled()
            if progress_cb is not None and total:
                pos = reader.file.tell()
                if pos != last:
                    last = pos
                    progress_cb(pos * 100.0 / total)


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
