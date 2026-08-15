"""BLF 读取：通道枚举（轻量探测/完整解析）+ 按通道流式产帧。

帧时间戳语义（修复项）：Frame.ts_seconds 是相对 BLF 文件头测量开始
时刻的整数秒（int(start_timestamp)×1e9）的偏移，整数 ns 构造
（float(ms_part + rel_ns) × 1e-9，与 CANoe t 轴同构）。python-can 读
路径 timestamp = float(Decimal(rel)*1e-9) + start_timestamp 在
~1.78e9s 量级的 float64 加法会把整数 ns 帧时刻舍入到 238ns 网格
（ulp = 2^-22s，实测 A19G1 信号 t 轴偏离 CANoe +72.5~73.5ns）；相对
化后偏移 ≤ 记录时长，float64 精确（整数 ns ≤ 2^53），一次 ×1e-9 舍入
与 stats.align_timestamps 的 ns 路径同构。
"""
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

    容器枚举复用 _iter_containers（与 _iter_frames 同源，H1 Step 0 抽取）。

    progress_cb(percent) 逐容器字节进度（0-100）；cancel_cb() 每 1024 个
    对象检查一次，返回 True 即 raise ScanCancelled（GUI 取消按钮置位）。
    """
    import struct

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
        OBJ_HEADER_BASE_STRUCT,
        OBJ_HEADER_V1_STRUCT,
        OBJ_HEADER_V2_STRUCT,
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

    channels = set()
    tail = b""
    for _, data in _iter_containers(path, progress_cb=progress_cb):
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


def _ms_part_ns(start_ns: int) -> int:
    """SYSTEMTIME 毫秒整数 → 相对整数秒的毫秒部分（0..999_999_999 ns）。"""
    return start_ns - (start_ns // 1_000_000_000) * 1_000_000_000


def _iter_containers(path: str, progress_cb=None) -> Iterator[tuple[int, bytes]]:
    """BLF 容器枚举：逐容器产出 (start_ns, 解压后容器数据字节)。

    自 _iter_frames 的文件头/容器循环原样抽取（H1 Step 0，行为不变）：
    LOGG 校验、seek(header[1])、基础头 16B 读入 + signature 校验、
    obj_size%4 填充字节、非 LOG_CONTAINER 跳过、NO_COMPRESSION/ZLIB 解压、
    未知压缩跳过。start_ns = 文件头 SYSTEMTIME 绝对 ns 整数（每容器重复
    产出，调用方据此推 ms_part）。尾部衔接由调用方负责（行走器返回尾部
    字节，下一容器拼接——见 _walk_container）。progress_cb(percent) 逐
    容器字节进度（0-100，与 list_channels 同一语义）。
    """
    import os
    import struct
    import zlib

    from can.io.blf import (
        BLFParseError,
        FILE_HEADER_STRUCT,
        LOG_CONTAINER,
        LOG_CONTAINER_STRUCT,
        NO_COMPRESSION,
        OBJ_HEADER_BASE_STRUCT,
        ZLIB_DEFLATE,
    )

    with open(path, "rb") as f:
        header = FILE_HEADER_STRUCT.unpack(f.read(FILE_HEADER_STRUCT.size))
        if header[0] != b"LOGG":
            raise BLFParseError("Unexpected file format")
        start_ns = _systemtime_ns(header)
        f.seek(header[1])  # 跳过文件头剩余（python-can: read(header[1] - 56)）
        total = os.path.getsize(path)
        last = -1
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
            yield start_ns, data


def _walk_container(data: bytes, ms_part: int,
                    cancel_cb=None) -> tuple[list[Frame], bytes]:
    """容器内对象行走（python-can _parse_data 语义），返回 (帧, 尾部)。

    自 _iter_frames 原样抽取（H1 Step 0，行为不变）。尾部 = 容器末尾
    不足一个对象头 / 跨容器对象的剩余字节，留给下一容器衔接
    （_parse_container 的 _tail 语义）。cancel_cb() 每 1024 个对象检查
    一次，置位即 raise ScanCancelled（GUI 取消按钮）。
    """
    import struct

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
        CAN_MSG_EXT,
        CAN_MSG_STRUCT,
        OBJ_HEADER_BASE_STRUCT,
        OBJ_HEADER_V1_STRUCT,
        OBJ_HEADER_V2_STRUCT,
        REMOTE_FLAG,
        dlc2len,
    )

    max_pos = len(data)
    pos = 0
    frames = []
    n = 0
    while True:
        obj_start = pos
        try:
            pos = data.index(b"LOBJ", pos, pos + 8)
        except ValueError:
            if pos + 8 > max_pos:
                return frames, data[obj_start:]
            raise BLFParseError("Could not find next object") from None
        try:
            _, header_size, header_version, obj_size, obj_type = \
                OBJ_HEADER_BASE_STRUCT.unpack_from(data, pos)
        except struct.error:
            return frames, data[obj_start:]  # 容器末尾不足一个对象头
        if pos + obj_size > max_pos:
            return frames, data[obj_start:]  # 对象跨容器，留给下一容器
        n += 1
        if n % 1024 == 0 and cancel_cb is not None and cancel_cb():
            raise ScanCancelled()
        pos += OBJ_HEADER_BASE_STRUCT.size
        if header_version == 1:
            flags, _, _, rel = OBJ_HEADER_V1_STRUCT.unpack_from(data, pos)
            hsz = OBJ_HEADER_V1_STRUCT.size
        elif header_version == 2:
            flags, _, _, rel = OBJ_HEADER_V2_STRUCT.unpack_from(data, pos)
            hsz = OBJ_HEADER_V2_STRUCT.size
        else:
            pos = obj_start + obj_size
            continue  # 未知对象头版本：整体跳过（同 python-can）
        rel_ns = rel * 10_000 if flags == 1 else rel  # 10µs / 1ns 单位
        try:
            if obj_type in (CAN_MESSAGE, CAN_MESSAGE2):
                m = CAN_MSG_STRUCT.unpack_from(data, pos + hsz)
                frames.append(Frame(
                    channel=m[0] - 1,
                    ts_seconds=float(ms_part + rel_ns) * 1e-9,
                    arbitration_id=m[3] & 0x1FFFFFFF,
                    is_extended=bool(m[3] & CAN_MSG_EXT),
                    is_fd=False,
                    dlc=m[2],
                    data=m[4][:m[2]],
                    is_remote=bool(m[1] & REMOTE_FLAG),
                ))
            elif obj_type == CAN_ERROR_EXT:
                m = CAN_ERROR_EXT_STRUCT.unpack_from(data, pos + hsz)
                frames.append(Frame(
                    channel=m[0] - 1,
                    ts_seconds=float(ms_part + rel_ns) * 1e-9,
                    arbitration_id=m[7] & 0x1FFFFFFF,
                    is_extended=bool(m[7] & CAN_MSG_EXT),
                    is_fd=False,
                    dlc=m[5],
                    data=m[9][:m[5]],
                    is_error=True,
                ))
            elif obj_type == CAN_FD_MESSAGE:
                m = CAN_FD_MSG_STRUCT.unpack_from(data, pos + hsz)
                frames.append(Frame(
                    channel=m[0] - 1,
                    ts_seconds=float(ms_part + rel_ns) * 1e-9,
                    arbitration_id=m[3] & 0x1FFFFFFF,
                    is_extended=bool(m[3] & CAN_MSG_EXT),
                    is_fd=bool(m[6] & 0x1),
                    dlc=dlc2len(m[2]),
                    data=m[8][:m[7]],  # valid_bytes 截断
                    is_remote=bool(m[1] & REMOTE_FLAG),
                ))
            elif obj_type == CAN_FD_MESSAGE_64:
                m = CAN_FD_MSG_64_STRUCT.unpack_from(data, pos + hsz)
                # 数据长度（python-can :issue:`1905`：valid_bytes 可能大于
                # 实际可用数据，补零对齐 CANoe/binlog.dll 语义）
                data_field_length = min(
                    m[2],  # valid_bytes
                    (m[13] or obj_size) - header_size -
                    CAN_FD_MSG_64_STRUCT.size,
                )
                msg_off = pos + hsz + CAN_FD_MSG_64_STRUCT.size
                msg_data = data[msg_off:msg_off + data_field_length]
                frames.append(Frame(
                    channel=m[0] - 1,
                    ts_seconds=float(ms_part + rel_ns) * 1e-9,
                    arbitration_id=m[4] & 0x1FFFFFFF,
                    is_extended=bool(m[4] & CAN_MSG_EXT),
                    is_fd=bool(m[6] & 0x1000),
                    dlc=dlc2len(m[1]),
                    data=msg_data.ljust(m[2], b"\x00"),
                    is_remote=bool(m[6] & 0x0010),
                ))
        except struct.error:
            return frames, data[obj_start:]  # 对象数据区截断（同 python-can）
        pos = obj_start + obj_size


def _iter_frames(path: str, progress_cb=None, cancel_cb=None) -> Iterator[Frame]:
    """手写容器行走 → Frame 流（时间戳整数 ns 构造，见模块 docstring）。

    逐帧语义与 python-can 的 BLFReader.__iter__ 一致（channel/id/ext/
    remote/error/fd/dlc/data、跨容器对象尾部衔接、未知对象跳过、容器
    级 struct.error 兜底），唯一差异：时间戳不走 float64 大数加法，
    而是 SYSTEMTIME 毫秒整数 + 对象头相对整数（1ns 或 flags==1 的 10µs
    单位）→ (ms_part + rel_ns) × 1e-9 一次舍入，与 CANoe 同构。

    容器枚举与容器内行走见 _iter_containers/_walk_container（H1 Step 0
    自本函数原样抽取，行为不变）。

    progress_cb(percent: float) 可选：每读完一个日志容器回调一次
    （0-100，基于文件字节位置，与 list_channels 同一语义）。
    cancel_cb() 可选：每 1024 个消息对象检查一次（GUI 取消按钮置位）。
    """
    tail = b""
    ms_part = None
    for start_ns, data in _iter_containers(path, progress_cb=progress_cb):
        if ms_part is None:
            ms_part = _ms_part_ns(start_ns)
        if tail:
            data = tail + data
            tail = b""
        frames, tail = _walk_container(data, ms_part, cancel_cb)
        yield from frames


def _systemtime_ns(header) -> int:
    """BLF 文件头 SYSTEMTIME（毫秒精度）→ 绝对 ns 整数。

    与 python-can systemtime_to_timestamp 同一 SYSTEMTIME 来源（毫秒
    整数），但保留整数语义——float64 在 1.78e18ns 量级只有 256ns 网格。
    """
    import datetime

    st = header[14:22]  # (year, month, dow, day, hour, min, sec, ms)
    dt = datetime.datetime(
        st[0], st[1], st[3], st[4], st[5], st[6], st[7] * 1000,
        tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1e9)


def iter_messages(path: str, channel: int) -> Iterator[Frame]:
    for fr in _iter_frames(path):
        if fr.channel != channel:
            continue
        yield fr


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
    yield from _iter_frames(path, progress_cb=progress_cb, cancel_cb=cancel_cb)


def scan_channels(path: str) -> dict[int, tuple]:
    """一次全文件扫描：每通道的统计输入（修复项 4）。

    返回 {channel: (相对时间戳 float64, is_extended, is_remote, is_error)}，
    数组等长；未出现的通道不在字典中。时间戳 = Frame 的整数 ns 构造
    （见模块 docstring）。供总线统计收集使用（避免逐通道重复全文件扫描）。
    """
    from collections import defaultdict

    import numpy as np

    data = defaultdict(lambda: ([], [], [], []))
    for fr in _iter_frames(path):
        ts, ext, rem, err = data[fr.channel]
        ts.append(fr.ts_seconds)
        ext.append(fr.is_extended)
        rem.append(fr.is_remote)
        err.append(fr.is_error)
    return {
        ch: (np.asarray(ts, dtype=np.float64), np.asarray(ext, dtype=bool),
             np.asarray(rem, dtype=bool), np.asarray(err, dtype=bool))
        for ch, (ts, ext, rem, err) in data.items()
    }
