"""帧流 → 按报文聚合的信号物理值。"""
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

from cantools.database.errors import DecodeError
from cantools.database.namedsignalvalue import NamedSignalValue

from core.blf_reader import Frame
from core.dbc_loader import DbcDef, SignalDef


@dataclass
class SignalSeries:
    channel: int
    message_name: str
    node: str
    signal_names: list[str]
    timestamps: np.ndarray          # float64 (N,)
    # 每信号 dtype 由 DBC 定义决定（修复项 3+8，与 CANoe 一致）：
    #   float64 物理值 / 最小整型原值（uint8..int64）/ |Sn 枚举文本 bytes
    values: dict[str, np.ndarray] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)


def _signal_kind(sd: SignalDef, vals: list) -> str:
    """CANoe 最小存储类型规则（参考 _T058.mdf 全量核对：3724 信号 0 反例）：
    - 物理变换（factor≠1 / offset≠0 / 浮点）：默认 float64 物理值；但有 choices 且
      **观察到的值全部在表内**（全为 NamedSignalValue）→ 文本（实测
      HVAC_RearTempSelect 全 31→|S7；HVAC_DriverTempSelect 混表外 0 → float64，
      表内值存 nan）；
    - 无物理变换且有 choices → 文本（表外原始值存空字节，实测 FanPWMSt）；
    - 其余 → 原始整型（最小 dtype）。"""
    if sd.is_float or sd.scale != 1.0 or sd.offset != 0.0:
        if sd.choices and all(isinstance(v, NamedSignalValue) for v in vals):
            return "text"
        return "float"
    if sd.choices:
        return "text"
    return "int"


def _int_dtype(length: int, is_signed: bool) -> np.dtype:
    """按位宽取最小整型 dtype（修复项 8）：8→8 位，9..16→16 位，17..32→32 位，>32→64 位。"""
    if is_signed:
        return np.dtype(np.int8 if length <= 8 else np.int16 if length <= 16
                        else np.int32 if length <= 32 else np.int64)
    return np.dtype(np.uint8 if length <= 8 else np.uint16 if length <= 16
                    else np.uint32 if length <= 32 else np.uint64)


@dataclass
class DecodeStats:
    total_frames: int = 0
    unknown_frames: int = 0
    unknown_ids: set[int] = field(default_factory=set)


def decode_channel(frames: Iterator[Frame], dbc: DbcDef, channel: int):
    stats = DecodeStats()
    buckets = {}  # msg_id -> {"ts": [], "values": {name: []}}
    for fr in frames:
        stats.total_frames += 1
        arb = fr.arbitration_id | (0x80000000 if fr.is_extended else 0)
        try:
            decoded = dbc.db.decode_message(arb, fr.data)
        except (KeyError, DecodeError):
            stats.unknown_frames += 1
            stats.unknown_ids.add(fr.arbitration_id)
            continue
        md = dbc.messages.get(arb)
        if md is None:
            # cantools 对 >0x7FF 的 id 无条件置 EFF 位：畸形标准帧（id 超出 11 位）
            # 可能成功解码到同原始 id 的扩展报文，但 loader 键（含 EFF 位）不含此键
            # → 按未知帧计数，不中断解码。
            stats.unknown_frames += 1
            stats.unknown_ids.add(fr.arbitration_id)
            continue
        bucket = buckets.setdefault(
            arb,
            {"ts": [], "values": {s.name: [] for s in md.signals}},
        )
        bucket["ts"].append(fr.ts_seconds)
        # 先原样收集解码值（NamedSignalValue / 原始整型 / 物理 float / nan）；
        # 存储类型在收齐后按"观察到的值是否全在表内"决定（见 _signal_kind）。
        for s in md.signals:
            bucket["values"][s.name].append(decoded.get(s.name, float("nan")))

    series = []
    for msg_id, b in buckets.items():
        md = dbc.messages[msg_id]
        timestamps = np.asarray(b["ts"], dtype=np.float64)
        values = {}
        for s in md.signals:
            vals = b["values"][s.name]
            kind = _signal_kind(s, vals)
            if kind == "text":
                # 表内值：DBC value table 文本 verbatim（UTF-8 bytes，含分号/尾空格）；
                # 表外原始值：CANoe 存空字节（实测 FanPWMSt）。bytes 列表 → |S 定宽（=最长观察值）
                values[s.name] = np.asarray([
                    str(v).encode("utf-8") if isinstance(v, NamedSignalValue) else b""
                    for v in vals])
            elif kind == "int":
                # 原始整型（最小 dtype）；mux 非活跃信号 cantools 返回 nan → 按原始 0 存储
                values[s.name] = np.asarray(
                    [v if isinstance(v, (int, np.integer)) else 0 for v in vals],
                    dtype=_int_dtype(s.length, s.is_signed))
            else:
                # float64 物理值；物理变换+choices 信号（存在表外值）的表内值存 nan
                # （CANoe 实测：HVAC_DriverTempSelect 表内 31 → nan，表外 0 → 18.0）
                values[s.name] = np.asarray(
                    [float("nan") if isinstance(v, NamedSignalValue) else v
                     for v in vals], dtype=np.float64)
        units = {s.name: s.unit for s in md.signals}
        series.append(
            SignalSeries(
                channel=channel,
                message_name=md.name,
                node=md.sender_node,
                signal_names=[s.name for s in md.signals],
                timestamps=timestamps,
                values=values,
                units=units,
            )
        )
    return series, stats
