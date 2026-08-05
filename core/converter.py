"""转换编排：多通道聚合 → 单个 MDF。"""
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core import blf_reader, mdf_writer
from core.decoder import decode_channel
from core.dbc_loader import DbcDef


@dataclass
class ChannelSummary:
    channel: int
    bound: bool
    decoded_frames: int = 0
    signal_count: int = 0
    unknown_frames: int = 0
    unknown_ids: int = 0
    raw_frames: int = 0
    warning: str = ""


@dataclass
class ConversionResult:
    summaries: list[ChannelSummary]
    duration_seconds: float


def _normalize_id(fr) -> int:
    """原始帧 id → 含 EFF 位的归一化键，与 dbc_loader.messages 的键契约一致。"""
    return fr.arbitration_id | (0x80000000 if fr.is_extended else 0)


def _collect_raw(frames, channel: int,
                 known_ids: set[int] | None) -> tuple[mdf_writer.RawGroup, int, set[int]]:
    """收集原始帧组；known_ids 非 None 时只保留 DBC 中有报文定义的帧。

    返回 (组, 丢弃帧数, 被丢弃帧的原始 id 集合)。无 DBC 参与（known_ids=None）
    时不过滤，保持原始导出的全量语义。
    """
    ts, ids, dlcs, datas, is_ext, is_fd = [], [], [], [], [], []
    unknown_frames = 0
    unknown_ids = set()
    max_dlc = 0
    for fr in frames:
        if known_ids is not None and _normalize_id(fr) not in known_ids:
            unknown_frames += 1
            unknown_ids.add(fr.arbitration_id)
            continue
        ts.append(fr.ts_seconds)
        ids.append(fr.arbitration_id)
        dlcs.append(fr.dlc)
        datas.append(fr.data)
        is_ext.append(bool(fr.is_extended))
        is_fd.append(bool(fr.is_fd))
        max_dlc = max(max_dlc, fr.dlc)
    n = len(ts)
    data_array = np.zeros((n, max_dlc), dtype=np.uint8) if n else np.zeros((0, 1), dtype=np.uint8)
    for i, d in enumerate(datas):
        data_array[i, : len(d)] = np.frombuffer(d, dtype=np.uint8)
    return (
        mdf_writer.RawGroup(
            channel=channel,
            timestamps=np.asarray(ts, dtype=np.float64),
            ids=np.asarray(ids, dtype=np.uint32),
            dlcs=np.asarray(dlcs, dtype=np.uint8),
            data_array=data_array,
            is_extended=np.asarray(is_ext, dtype=bool),
            is_fd=np.asarray(is_fd, dtype=bool),
        ),
        unknown_frames,
        unknown_ids,
    )


def convert(blf_path: str, bindings: dict[int, DbcDef | None], out_path: str,
            progress_cb=None) -> ConversionResult:
    channels = sorted(bindings)
    if not channels:
        raise ValueError("未选择任何通道")
    total = len(channels)
    all_series, raw_groups, summaries = [], [], []
    min_ts, max_ts = float("inf"), float("-inf")

    def note_range(ts_arr):
        nonlocal min_ts, max_ts
        if len(ts_arr):
            min_ts = min(min_ts, float(ts_arr[0]))
            max_ts = max(max_ts, float(ts_arr[-1]))

    # 原始通道过滤：本次转换中所有已绑定 DBC 的报文 ID 并集；
    # 无 DBC 参与时不过滤（known_ids=None）。
    dbcs = [d for d in bindings.values() if d is not None]
    known_ids = set().union(*(d.messages.keys() for d in dbcs)) if dbcs else None

    for i, ch in enumerate(channels):
        if progress_cb:
            progress_cb(f"处理 CAN{ch}", 10 + 80 * i / total)
        dbc = bindings.get(ch)
        frames = blf_reader.iter_messages(blf_path, ch)
        if dbc is not None:
            series, stats = decode_channel(frames, dbc, ch)
            for s in series:
                note_range(s.timestamps)
            summary = ChannelSummary(
                channel=ch, bound=True,
                decoded_frames=stats.total_frames - stats.unknown_frames,
                signal_count=sum(len(s.signal_names) for s in series),
                unknown_frames=stats.unknown_frames,
                unknown_ids=len(stats.unknown_ids),
            )
            if not series:
                summary.warning = "该通道无匹配帧"
            all_series.extend(series)
        else:
            raw, unk_frames, unk_ids = _collect_raw(frames, ch, known_ids)
            if len(raw.timestamps):
                note_range(raw.timestamps)
                raw_groups.append(raw)
            summary = ChannelSummary(
                channel=ch, bound=False, raw_frames=len(raw.timestamps),
                unknown_frames=unk_frames, unknown_ids=len(unk_ids),
            )
            if unk_frames and not len(raw.timestamps):
                summary.warning = "全部原始帧未匹配 DBC"
        summaries.append(summary)

    if progress_cb:
        progress_cb("写 MDF", 95)
    try:
        mdf_writer.write_mdf(all_series, raw_groups, out_path)
    except Exception:
        # write_mdf 先写 <out>.mf4 再 rename 成 out_path（见 mdf_writer.py）：
        # save 失败留 .mf4，rename 失败两者都在，半成品都要清。
        for p in (out_path, Path(out_path).with_suffix(".mf4")):
            if os.path.exists(p):
                os.remove(p)
        raise
    if progress_cb:
        progress_cb("完成", 100)
    return ConversionResult(
        summaries=summaries,
        duration_seconds=(max_ts - min_ts) if min_ts <= max_ts else 0.0,
    )
