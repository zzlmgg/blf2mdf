"""MDF 4.10 写出：信号组 + 原始帧组。"""
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from asammdf import MDF, Signal

from core.decoder import SignalSeries


@dataclass
class RawGroup:
    channel: int
    timestamps: np.ndarray   # float64 (N,)
    ids: np.ndarray          # uint32 (N,)
    dlcs: np.ndarray         # uint8 (N,)
    data_array: np.ndarray   # uint8 (N, L)，L = 该通道最大 DLC
    is_extended: np.ndarray  # bool (N,)
    is_fd: np.ndarray        # bool (N,)


def write_mdf(signal_series_list: list[SignalSeries],
              raw_groups: list[RawGroup], out_path: str) -> None:
    # asammdf 8.8：append 无 group_name 参数，组名 = ChannelGroup.acq_name；
    # 每次 append 新建一组，同一组的所有信号须一次传入（列表）。
    mdf = MDF(version="4.10")
    used_groups = set()

    for s in signal_series_list:
        group = f"Signal::{s.node}"
        if group in used_groups:
            group = f"CAN{s.channel}::Signal::{s.node}"
        used_groups.add(group)
        mdf.append(
            [
                Signal(
                    samples=s.values[name].astype(np.float64),
                    timestamps=s.timestamps,
                    name=name,
                    unit=s.units.get(name, ""),
                )
                for name in s.signal_names
            ],
            acq_name=group,
        )

    for rg in raw_groups:
        group = f"Raw::CAN{rg.channel}"
        ts = rg.timestamps.astype(np.float64)
        mdf.append(
            [
                Signal(samples=ts, timestamps=ts, name="Time", unit="s"),
                Signal(samples=rg.ids.astype(np.uint32), timestamps=ts, name="ID"),
                Signal(samples=rg.dlcs.astype(np.uint8), timestamps=ts, name="DLC"),
                Signal(samples=rg.data_array, timestamps=ts, name="Data"),
                Signal(samples=rg.is_extended.astype(np.uint8), timestamps=ts,
                       name="IsExtended"),
                Signal(samples=rg.is_fd.astype(np.uint8), timestamps=ts,
                       name="IsFD"),
            ],
            acq_name=group,
        )

    mdf.save(out_path, overwrite=True)  # asammdf 8.8 的 save 强制 .mf4 后缀
    os.replace(Path(out_path).with_suffix(".mf4"), out_path)
