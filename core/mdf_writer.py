"""MDF 4.10 写出：信号组 + 原始帧组 + 总线统计组。"""
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from asammdf import MDF, Signal

from core.decoder import SignalSeries
from core.stats import ChannelStats, STAT_NAMES

# 修复项 9：asammdf 默认 COMPRESSION_LEVEL=1（zlib 最快、压缩率最低，
# 真实样例 7.13MB vs CANoe 5.65MB）；提升到 9 与 CANoe 高压缩输出一致
# （实测同数据 5.44MB ≈ 5.65MB，耗时 ~4s 无感）。save 不暴露该参数，
# 只能改 v4_blocks 模块常量（DZ 块构造时读取）。
import asammdf.blocks.v4_blocks as _v4_blocks
_v4_blocks.COMPRESSION_LEVEL = 9


@dataclass
class RawGroup:
    channel: int
    timestamps: np.ndarray   # float64 (N,)
    ids: np.ndarray          # uint32 (N,)
    dlcs: np.ndarray         # uint8 (N,)
    data_array: np.ndarray   # uint8 (N, L)，L = 该通道最大 DLC
    is_extended: np.ndarray  # bool (N,)
    is_fd: np.ndarray        # bool (N,)


# asammdf v4_constants.SYNC_TYPE_TIME：主时间通道同步类型，单位 "s"
_MASTER_TIME = ("t", 1)


def write_mdf(signal_series_list: list[SignalSeries],
              raw_groups: list[RawGroup], out_path: str,
              abs_start_epoch: float | None = None,
              stats_groups: list[ChannelStats] | None = None) -> None:
    # asammdf 8.8：append 无 group_name 参数，组名 = ChannelGroup.acq_name；
    # 每次 append 新建一组，同一组的所有信号须一次传入（列表）。
    # 主时间通道名由首信号的 master_metadata 决定（默认 "time"），
    # 统一传 ("t", SYNC_TYPE_TIME) 与 CANoe 一致（修复项 6）。
    mdf = MDF(version="4.10")
    if abs_start_epoch is not None:
        # 修复项 2：MDF 头部 start_time = 绝对测量起始（naive UTC 整秒）。
        # asammdf setter 对 naive datetime 按 UTC 计算 abs_time 并置
        # FLAG_HD_LOCAL_TIME / tz_offset=0——与 CANoe 参考 _T058.mdf 逐字段一致
        # （实测 roundtrip：abs_time/flags/tz 全同；时间戳数据不受影响）。
        mdf.header.start_time = (
            datetime.fromtimestamp(int(abs_start_epoch), tz=timezone.utc)
            .replace(tzinfo=None))
    used_groups = set()

    for s in signal_series_list:
        # 组名 = 报文名（与 CANoe 一致）；跨通道同名报文加 CAN<ch>:: 前缀兜底
        group = s.message_name
        if group in used_groups:
            group = f"CAN{s.channel}::{s.message_name}"
        used_groups.add(group)
        signals = []
        for name in s.signal_names:
            samples = s.values[name]
            kwargs = {}
            if samples.dtype.kind in ("S", "O", "U"):
                # 枚举文本：|Sn 定宽 bytes（修复项 3）；asammdf 需 encoding 元数据
                kwargs["encoding"] = "utf-8"
            signals.append(Signal(
                samples=samples,           # 按解码 dtype 原样写出（整型/文本/float64，修复项 8）
                timestamps=s.timestamps,
                name=name,
                unit=s.units.get(name, ""),
                **kwargs,
            ))
        signals[0].master_metadata = _MASTER_TIME
        # 方案 E：组内各信号时间戳为同一数组对象（构造保证），
        # common_timebase=True 跳过 asammdf 逐信号 O(N) array_equal 比较
        # （默认路径比较全同后同样取 t_，输出逐位一致）。
        mdf.append(signals, acq_name=group, common_timebase=True)

    for rg in raw_groups:
        group = f"Raw::CAN{rg.channel}"
        ts = rg.timestamps.astype(np.float64)
        # 时间戳数据由主时间通道 t 承载，不再单独建 Time 通道
        mdf.append(
            [
                Signal(samples=rg.ids.astype(np.uint32), timestamps=ts, name="ID",
                       master_metadata=_MASTER_TIME),
                Signal(samples=rg.dlcs.astype(np.uint8), timestamps=ts, name="DLC"),
                Signal(samples=rg.data_array, timestamps=ts, name="Data"),
                Signal(samples=rg.is_extended.astype(np.uint8), timestamps=ts,
                       name="IsExtended"),
                Signal(samples=rg.is_fd.astype(np.uint8), timestamps=ts,
                       name="IsFD"),
            ],
            acq_name=group,
            common_timebase=True,
        )

    # 修复项 4：总线统计 1s 组（阶段 1，CANoe 语义：10 项 × 16 通道，组名 '1s'，
    # 每组 = 统计信号 + 主时间通道 t）。写在文件尾部（不改变既有组序；
    # full_compare 按信号名匹配组，不依赖组序）。
    for cs in stats_groups or []:
        for name in STAT_NAMES:
            mdf.append(
                [Signal(samples=cs.values[name], timestamps=cs.t, name=name,
                        master_metadata=_MASTER_TIME)],
                acq_name="1s",
            )

    # compression=2（转置 + deflate）：参考 CANoe 高压缩输出（修复项 7，计划实测 326 MB → 4 MB 量级）；
    # 压缩透明，读回自动解压。asammdf 8.8 的 save 强制 .mf4 后缀。
    mdf.save(out_path, overwrite=True, compression=2)
    os.replace(Path(out_path).with_suffix(".mf4"), out_path)
