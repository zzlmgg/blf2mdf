"""转换编排：多通道聚合 → 单个 MDF。"""
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from core import blf_reader, mdf_writer, mp_finish
from core.blf_reader import ScanCancelled
from core.decoder import ChannelDecoder
from core.dbc_loader import DbcDef
from core import stats as stats_mod


def _check_cancel(cancel_cb) -> None:
    """转换检查点：cancel_cb 置位 → raise ScanCancelled（GUI 取消按钮）。"""
    if cancel_cb is not None and cancel_cb():
        raise ScanCancelled()

# 方案 G：并行解码的桶内存阈值（估算，超阈值回退串行，env 可覆盖；见 §5.7）
_MP_MEM_THRESHOLD = float(os.environ.get("BLF_MP_MEM_THRESHOLD", 1.5e9))


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
    # 各阶段耗时（按时间顺序：读入 BLF → [并行] 解码墙钟（并行）→
    # 解码 CANn → 统计聚合 → 写 MDF → 总耗时）。串行「解码 CANn」为
    # 该通道墙钟；并行「解码 CANn」为该通道累计解码工作量（各通道互相
    # 重叠，之和可大于解码墙钟）。取消/异常路径不返回（随异常丢弃）。
    timings: list[tuple[str, float]] = field(default_factory=list)


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
            progress_cb=None, raw_export: bool = False,
            stats_export: bool = True, parallel: bool = False,
            cancel_cb=None) -> ConversionResult:
    """多通道 BLF → 单个 MDF。

    raw_export=False（默认，与 CANoe 一致）：未绑定 DBC 的通道不导出原始帧，
    只保留解码信号组；True 时未绑定通道收集为 Raw::CANn 组（见 _collect_raw）。

    stats_export=True（默认，与 CANoe 一致）：输出 1s 总线统计组（修复项 4
    阶段 1，10 项 × 16 通道，覆盖 0-15 全部通道与 DBC 绑定无关）。

    progress_cb(stage, percent) 进度刻度（percent 单调不降）：
    读取 BLF 5→10（逐容器字节位置）、解码 CANn 10→90（并行按桶/串行按
    通道）、聚合统计 CANn 92→95（逐通道）、写 MDF 95、完成 100。

    cancel_cb() 可选：读取（每 1024 帧）、解码（串行逐通道/并行每桶）、
    写 MDF 前后检查，置位即 raise ScanCancelled；写 MDF 期间（asammdf
    无取消钩子）点取消 → 删除已写输出与半成品 .mf4 后抛出。
    """
    channels = sorted(bindings)
    if not channels:
        raise ValueError("未选择任何通道")
    total = len(channels)
    all_series, raw_groups, summaries = [], [], []
    min_ts, max_ts = float("inf"), float("-inf")
    # 各阶段计时（GUI 日志用）：总耗时入口 → 读入 → [并行] 解码墙钟 →
    # 解码×n → 聚合 → 写。串行「解码 CANn」= 该通道墙钟（逐通道顺序执行）；
    # 并行「解码 CANn」= 该通道累计工作量（finish_all 内 worker 实测求和），
    # 与「解码墙钟（并行）」行并存使日志可对账。
    t_total = time.perf_counter()
    timings: list[tuple[str, float]] = []

    def note_range(ts_arr):
        nonlocal min_ts, max_ts
        if len(ts_arr):
            min_ts = min(min_ts, float(ts_arr[0]))
            max_ts = max(max_ts, float(ts_arr[-1]))

    # 原始通道过滤：本次转换中所有已绑定 DBC 的报文 ID 并集；
    # 无 DBC 参与时不过滤（known_ids=None）。
    dbcs = [d for d in bindings.values() if d is not None]
    known_ids = set().union(*(d.messages.keys() for d in dbcs)) if dbcs else None

    # 时间基准对齐 CANoe（修复项 2）：以 BLF 文件头测量开始时间归零
    # （与解码帧无关，实测 CANoe 基准 = 文件头 start_timestamp；
    # 若用最早解码帧会整体偏移——样例首解码帧晚于测量开始 2ms）；
    # t 轴零点 = 起始的整数秒（CANoe 语义，实测首帧 t=0.628791498 反推）。
    # Frame.ts_seconds 已按整数 ns 构造相对该整数秒（见 blf_reader 模块
    # docstring：float64 大数加法有 238ns 网格舍入，整数构造与 CANoe 同构），
    # 此处不再做 float - int 相对化。
    # 绝对起始时间（含小数秒，BLF 头 SYSTEMTIME 毫秒精度）全精度传给
    # write_mdf 写入 MDF 头部 start_time/abs_time。
    abs_start_time = blf_reader.read_start_time(blf_path)

    # 性能优化（方案 A）：单遍全文件扫描——一次遍历完成 解码输入路由 +
    # 统计输入收集，替代按通道逐遍重复解析整个文件（旧实现每绑定通道
    # 一遍 + scan_channels 一遍，实测 11 遍 × ~13s ≈ 58% 转换耗时）。
    # 逐帧语义与旧流程完全一致：解码走 ChannelDecoder（= 原 decode_channel
    # 逻辑），统计输入逐通道列表（= 原 scan_channels 输出）。
    decoders = {ch: ChannelDecoder(dbc, ch)
                for ch, dbc in bindings.items() if dbc is not None}
    raw_bufs = {ch: [] for ch, dbc in bindings.items() if dbc is None} \
        if raw_export else {}
    stats_bufs = defaultdict(lambda: ([], [], [], []))  # ch -> (ts, ext, remote, err)

    # 方案 G：多进程并行解码（可选，默认关）。池在 feed 前创建 + 预热
    # （spawn 成本藏在扫描期，原型实测 8 worker ~1.7s）；单通道短路；
    # 池创建失败（环境/杀软等）→ 自动回退串行，正确性零损失（§5.7）。
    pool = None
    if parallel and len(decoders) >= 2:
        try:
            pool = mp_finish.make_pool(sorted(decoders))
        except Exception:
            pool = None

    try:
        if progress_cb:
            progress_cb("读取 BLF", 5)
        # 读取阶段进度：字节位置 5→10（读取占大文件转换耗时大头——实测
        # 35MB 样例 52s 里 50s 在读取，逐容器上报让进度条全程前进）
        read_cb = (lambda f: progress_cb("读取 BLF", 5 + 5 * f / 100)) \
            if progress_cb else None
        t_read = time.perf_counter()
        for fr in blf_reader.iter_all_messages(blf_path, progress_cb=read_cb,
                                               cancel_cb=cancel_cb):
            ch = fr.channel
            if stats_export:
                t, e, r, er = stats_bufs[ch]
                t.append(fr.ts_seconds)
                e.append(fr.is_extended)
                r.append(fr.is_remote)
                er.append(fr.is_error)
            dec = decoders.get(ch)
            if dec is not None:
                dec.feed(fr)
            elif raw_export and ch in raw_bufs:
                raw_bufs[ch].append(fr)
        timings.append(("读入 BLF", time.perf_counter() - t_read))
        # 取消检查点：读取完毕、解码前（取消则不再启动解码/池回收）
        _check_cancel(cancel_cb)

        # 内存阈值回退（方案 G §5.7）：feed 后桶内存估算超阈值 → 转串行。
        # 池已预热但未提交任务，shutdown 无副作用。
        if pool is not None and \
                mp_finish.bucket_bytes(decoders, sorted(decoders)) > _MP_MEM_THRESHOLD:
            pool.shutdown(wait=True, cancel_futures=True)
            pool = None

        # ── 解码阶段：并行（方案 G，per-bucket）或串行（现状语义）──
        if pool is not None:
            ch_times: dict[int, float] = {}
            t_decode = time.perf_counter()
            results = mp_finish.finish_all(decoders, sorted(decoders),
                                           progress_cb=progress_cb, pool=pool,
                                           cancel_cb=cancel_cb,
                                           timings=ch_times)
            # 解码阶段墙钟：读入 + 本行 + 聚合 + 写 ≈ 总耗时（日志可对账）
            timings.append(("解码墙钟（并行）", time.perf_counter() - t_decode))
            for ch in sorted(decoders):
                # 该通道累计解码工作量；零桶通道无桶任务，finish_all 兜底 0.0
                timings.append((f"解码 CAN{ch}", ch_times.get(ch, 0.0)))
        else:
            results = {}
            for i, ch in enumerate(sorted(decoders)):
                if progress_cb:
                    progress_cb(f"解码 CAN{ch}", 10 + 80 * i / total)
                # 取消检查点：串行解码逐通道
                _check_cancel(cancel_cb)
                t_ch = time.perf_counter()
                results[ch] = decoders[ch].finish()
                timings.append((f"解码 CAN{ch}", time.perf_counter() - t_ch))

        for i, ch in enumerate(channels):
            dbc = bindings.get(ch)
            if dbc is not None:
                series, stats = results[ch]
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
                if raw_export:
                    raw, unk_frames, unk_ids = _collect_raw(raw_bufs[ch], ch, known_ids)
                    if len(raw.timestamps):
                        note_range(raw.timestamps)
                        raw_groups.append(raw)
                    summary = ChannelSummary(
                        channel=ch, bound=False, raw_frames=len(raw.timestamps),
                        unknown_frames=unk_frames, unknown_ids=len(unk_ids),
                    )
                    if unk_frames and not len(raw.timestamps):
                        summary.warning = "全部原始帧未匹配 DBC"
                else:
                    # 修复项 5：原始帧导出默认关闭（与 CANoe 一致）——
                    # 未绑定通道不存储帧数据（单遍扫描中仅路由，不收集）。
                    summary = ChannelSummary(channel=ch, bound=False)
            summaries.append(summary)

        # 时间戳对齐 CANoe（修复：输入已是整数 ns 构造的相对值，见
        # blf_reader 模块 docstring；align_timestamps 对 ms 网格文件仍
        # round 到 1ms 整数（幂等），任意 ns 精度文件保留整数 ns 原值）
        for s in all_series:
            s.timestamps = stats_mod.align_timestamps(s.timestamps)
        for rg in raw_groups:
            rg.timestamps = stats_mod.align_timestamps(rg.timestamps)

        # 修复项 4：总线统计 1s 组（阶段 1）——覆盖 0-15 全部通道（与 DBC 绑定无关），
        # 无帧通道输出全 0；输入已在单遍扫描中收集（stats_bufs），
        # BLF 中不存在的通道直接全 0。
        stats_groups = []
        if stats_export:
            t_stats = time.perf_counter()
            if progress_cb:
                progress_cb("聚合总线统计", 92)
            global_end = 0.0
            for ch in stats_mod.STAT_CHANNELS:
                ts = stats_bufs.get(ch, ((), None, None, None))[0]
                if ts:
                    global_end = max(global_end, max(ts))
            n_stats = len(stats_mod.STAT_CHANNELS)
            for i, ch in enumerate(stats_mod.STAT_CHANNELS):
                # 逐通道上报 92→95：聚合 16 通道实测 ~1.4s，不再停在 92%
                if progress_cb:
                    progress_cb(f"聚合统计 CAN{ch}",
                                92 + 3 * (i + 1) / n_stats)
                t, e, r, er = stats_bufs.get(ch, ((), (), (), ()))
                stats_groups.append(stats_mod.aggregate_channel(
                    np.asarray(t, dtype=np.float64),
                    np.asarray(e, dtype=bool),
                    np.asarray(r, dtype=bool),
                    np.asarray(er, dtype=bool),
                    global_end))
            timings.append(("统计聚合", time.perf_counter() - t_stats))

        if progress_cb:
            progress_cb("写 MDF", 95)
        # 取消检查点：写 MDF 前（取消则不写，无输出残留）
        _check_cancel(cancel_cb)
        t_write = time.perf_counter()
        try:
            mdf_writer.write_mdf(all_series, raw_groups, out_path,
                                 abs_start_seconds=abs_start_time,
                                 stats_groups=stats_groups)
        except Exception:
            # write_mdf 先写 <out>.mf4 再 rename 成 out_path（见 mdf_writer.py）：
            # save 失败留 .mf4，rename 失败两者都在，半成品都要清。
            for p in (out_path, Path(out_path).with_suffix(".mf4")):
                if os.path.exists(p):
                    os.remove(p)
            raise
        # 取消检查点：写入期间（asammdf 无取消钩子）点取消 → 清理已写输出
        if cancel_cb is not None and cancel_cb():
            for p in (out_path, Path(out_path).with_suffix(".mf4")):
                if os.path.exists(p):
                    os.remove(p)
            raise ScanCancelled()
        timings.append(("写 MDF", time.perf_counter() - t_write))
        if progress_cb:
            progress_cb("完成", 100)
        timings.append(("总耗时", time.perf_counter() - t_total))
        return ConversionResult(
            summaries=summaries,
            duration_seconds=(max_ts - min_ts) if min_ts <= max_ts else 0.0,
            timings=timings,
        )
    finally:
        # 池生命周期：正常/异常路径均回收（feed 异常、write 异常等）
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
