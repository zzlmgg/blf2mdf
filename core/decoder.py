"""帧流 → 按报文聚合的信号物理值。"""
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

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
        try:
            decoded = dbc.db.decode_message(fr.arbitration_id, fr.data)
        except KeyError:
            stats.unknown_frames += 1
            stats.unknown_ids.add(fr.arbitration_id)
            continue
        md = dbc.messages[fr.arbitration_id]
        bucket = buckets.setdefault(
            fr.arbitration_id,
            {"ts": [], "values": {s.name: [] for s in md.signals}},
        )
        bucket["ts"].append(fr.ts_seconds)
        for s in md.signals:
            bucket["values"][s.name].append(
                decoded.get(s.name, float("nan"))
            )

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
