"""帧流 → 按报文聚合的信号物理值。"""
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

from cantools.database.errors import DecodeError
from cantools.database.namedsignalvalue import NamedSignalValue

from core.blf_reader import Frame
from core.dbc_loader import DbcDef


@dataclass
class SignalSeries:
    channel: int
    message_name: str
    node: str
    signal_names: list[str]
    timestamps: np.ndarray          # float64 (N,)
    values: dict[str, np.ndarray] = field(default_factory=dict)  # float64 (N,)
    units: dict[str, str] = field(default_factory=dict)


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
        for s in md.signals:
            v = decoded.get(s.name, float("nan"))
            if isinstance(v, NamedSignalValue):
                v = v.value
            bucket["values"][s.name].append(v)

    series = []
    for msg_id, b in buckets.items():
        md = dbc.messages[msg_id]
        timestamps = np.asarray(b["ts"], dtype=np.float64)
        values = {k: np.asarray(v, dtype=np.float64) for k, v in b["values"].items()}
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
