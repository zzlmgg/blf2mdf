"""转换编排：多通道聚合 → 单个 MDF。"""
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from core import blf_reader, mdf_writer, mp_finish
from core.blf_reader import ScanCancelled
from core.blf_vector import iter_container_frames
from core.decoder import Bucket, ChannelDecoder
from core.dbc_loader import DbcDef, classify_batch, message_table, normalize_ids
from core import stats as stats_mod


def _check_cancel(cancel_cb) -> None:
    """转换检查点：cancel_cb 置位 → raise ScanCancelled（GUI 取消按钮）。"""
    if cancel_cb is not None and cancel_cb():
        raise ScanCancelled()


# ── H1 Step 3：向量化单遍扫描（读入路由 + 桶装配）──
def _lookup(keys: np.ndarray, vals: np.ndarray) -> np.ndarray:
    """排序键二分：vals 是否在表内（searchsorted + 判等，O(n log m)）。"""
    if len(keys) == 0:
        return np.zeros(len(vals), dtype=bool)
    pos = np.searchsorted(keys, vals)
    ok = pos < len(keys)
    safe = np.where(ok, pos, 0)
    return ok & (keys[safe] == vals)


def _bucket_block(cf, sel) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """容器内帧子集（bool 掩码或整数索引）→ 桶块 (ts, lens, (N, L) data)。

    packed（回退/旧路径）：data8 定跨距块，按 data_off/data_len 自洽寻址；
    scattered（H7b 快路径）：data8 = 容器字节视图，data_off = 容器内绝对
    偏移，行尾补零按 glen（含 FD64 截断 ljust）掩码——载荷只拷一次。
    row/col 摊平 gather 实测 ~3.7s 已弃（H2a 教训：大数据路径禁散点索引）；
    定宽单次 take 为固有成本（载荷必须离开容器缓冲进桶）。
    """
    lens = cf.data_len[sel]
    n = len(lens)
    if n == 0:
        return cf.ts[sel], lens, np.zeros((0, 0), dtype=np.uint8)
    L = int(lens.max())
    idt = np.int32 if cf.data8.size < 2 ** 31 else np.int64
    src2 = cf.data_off[sel].astype(idt)[:, None] + np.arange(L, dtype=idt)
    block = np.take(cf.data8, src2, mode="clip")   # 越界位 → 掩码清零兜底
    if cf.scattered:
        gsel = cf.glen[sel]
        if not bool(np.all(gsel == L)):   # 全满桶跳过掩码（同现优化）
            block[np.arange(L)[None, :] >= gsel[:, None]] = 0
    else:
        if not bool(np.all(lens == L)):
            block[np.arange(L)[None, :] >= lens[:, None]] = 0
    return cf.ts[sel], lens, block


def _read_vectorized(blf_path, decoders, raw_chs, stats_export, stats_bufs,
                     raw_chunks, known_keys, read_cb, cancel_cb) -> None:
    """H1 向量化单遍扫描（计划 §9.1.3）：逐容器路由解码桶/统计/原始帧。

    直接填充 decoders[ch].buckets（数组桶）、decoders[ch].stats（feed 期
    计数语义）、stats_bufs 块列表、raw_chunks 块列表——下游（finish/统计
    聚合/raw 组装）输出与旧 feed 循环逐位一致。
    """
    info = {ch: message_table(dec.dbc) for ch, dec in decoders.items()}
    for cf in iter_container_frames(blf_path, progress_cb=read_cb,
                                    cancel_cb=cancel_cb):
        n = len(cf.channel)
        if n == 0:
            continue
        arb_norm = normalize_ids(cf.arb, cf.is_ext)
        # ── 统计块（仅 STAT_CHANNELS 被聚合消费；与旧路径逐帧追加等价）──
        if stats_export:
            for ch in stats_mod.STAT_CHANNELS:
                m = cf.channel == ch
                if m.any():
                    t, e, r, er = stats_bufs[ch]
                    t.append(cf.ts[m])
                    e.append(cf.is_ext[m])
                    r.append(cf.is_remote[m])
                    er.append(cf.is_error[m])
        # ── 解码桶（逐通道路由）──
        for ch, dec in decoders.items():
            m = cf.channel == ch
            idx_m = np.flatnonzero(m)
            cnt = len(idx_m)
            if cnt == 0:
                continue
            dec.stats.total_frames += cnt
            norm = arb_norm[idx_m]
            found, valid = classify_batch(info[ch], norm, cf.data_len[idx_m])
            bad = cnt - int(valid.sum())
            if bad:
                dec.stats.unknown_frames += bad
                dec.stats.unknown_ids.update(cf.arb[idx_m][~valid].tolist())
            if not valid.any():
                continue
            keys, _, mds = info[ch]
            # 桶分组：首现序 = 桶插入序（feed setdefault 首次出现序）
            vkeys = norm[valid]
            uniq, first_i = np.unique(vkeys, return_index=True)
            for k in uniq[np.argsort(first_i, kind="stable")]:
                grp = valid & (norm == k)
                sel = idx_m[grp]
                arb = int(k)
                md = mds[int(np.searchsorted(keys, k))]
                ts, lens, block = _bucket_block(cf, sel)
                b = dec.buckets.get(arb)
                if b is None:
                    dec.buckets[arb] = Bucket.from_blocks(
                        arb, int(cf.arb[sel][0]), md, block, ts, lens)
                else:
                    b.add_block(block, ts, lens)      # 参数序按澄清 ③ 修订（block 在前）
        # ── 原始帧块（raw_export 未绑定通道；known_ids 过滤在块内完成）──
        for ch in raw_chs:
            m = cf.channel == ch
            idx_m = np.flatnonzero(m)
            if len(idx_m) == 0:
                continue
            sel = idx_m
            if known_keys is not None:
                found = _lookup(known_keys, arb_norm[idx_m])
                bad = int((~found).sum())
                if bad:
                    raw_chunks[ch][1] += bad
                    raw_chunks[ch][2].update(cf.arb[idx_m][~found].tolist())
                sel = idx_m[found]
            if len(sel):
                ts, lens, block = _bucket_block(cf, sel)
                raw_chunks[ch][0].append(
                    (ts, cf.arb[sel], cf.dlc[sel], lens, block,
                     cf.is_ext[sel], cf.is_fd[sel]))
    # 桶装配：blocks 相位 → 数组相位（读入后一次成型，下游 finish/阈值计按数组消费）
    for dec in decoders.values():
        for b in dec.buckets.values():
            b.to_array()


def _block_rows(block: np.ndarray, lens: np.ndarray) -> np.ndarray:
    """块 (N, L) 按帧长摊平 → 变长行拼串 (M,) uint8（row/col 技巧）。"""
    n = len(lens)
    if n == 0:
        return np.zeros(0, dtype=np.uint8)
    row = np.repeat(np.arange(n), lens)
    col = np.arange(int(lens.sum())) \
        - np.repeat(np.concatenate(([0], np.cumsum(lens)[:-1])), lens)
    return block[row, col]


def _fill_var_rows(data: np.ndarray, lens: np.ndarray, flat: np.ndarray) -> None:
    """(N, L) 零数组按变长行填充：row/col 散点（flat = 各行前 lens 字节拼串）。"""
    row = np.repeat(np.arange(len(lens)), lens)
    col = np.arange(len(row)) \
        - np.repeat(np.concatenate(([0], np.cumsum(lens)[:-1])), lens)
    data[row, col] = flat


def _assemble_raw(blocks, channel: int, unknown_frames: int, unknown_ids: set):
    """向量化原始帧块 → RawGroup（= 旧逐帧收集实现语义，H1 已替换）。

    data_array 行宽 = max(dlc)（帧 dlc 原值：经典帧可为 >8 原值，FD 帧
    为 dlc2len 值），填充按帧 data 长度——与旧逐帧收集实现逐位一致。
    """
    if not blocks:
        return (
            mdf_writer.RawGroup(
                channel=channel,
                timestamps=np.empty(0, dtype=np.float64),
                ids=np.empty(0, dtype=np.uint32),
                dlcs=np.empty(0, dtype=np.uint8),
                data_array=np.zeros((0, 1), dtype=np.uint8),
                is_extended=np.empty(0, dtype=bool),
                is_fd=np.empty(0, dtype=bool),
            ),
            unknown_frames, unknown_ids)
    ts = np.concatenate([b[0] for b in blocks])
    ids = np.concatenate([b[1] for b in blocks])
    dlcs = np.concatenate([b[2] for b in blocks])
    lens = np.concatenate([b[3] for b in blocks])
    is_ext = np.concatenate([b[5] for b in blocks])
    is_fd = np.concatenate([b[6] for b in blocks])
    L = int(dlcs.max())
    data_array = np.zeros((len(ts), L), dtype=np.uint8)
    if len(ts):
        _fill_var_rows(data_array, lens,
                       np.concatenate([_block_rows(b[4], b[3]) for b in blocks]))
    return (
        mdf_writer.RawGroup(
            channel=channel,
            timestamps=ts,
            ids=ids,
            dlcs=dlcs,
            data_array=data_array,
            is_extended=is_ext,
            is_fd=is_fd,
        ),
        unknown_frames,
        unknown_ids,
    )


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


def convert(blf_path: str, bindings: dict[int, DbcDef | None], out_path: str,
            progress_cb=None, raw_export: bool = False,
            stats_export: bool = True, parallel: bool = False,
            cancel_cb=None) -> ConversionResult:
    """多通道 BLF → 单个 MDF。

    raw_export=False（默认，与 CANoe 一致）：未绑定 DBC 的通道不导出原始帧，
    只保留解码信号组；True 时未绑定通道收集为 Raw::CANn 组（见 _assemble_raw）。

    stats_export=True（默认，与 CANoe 一致）：输出 1s 总线统计组（修复项 4
    阶段 1，10 项 × 16 通道，覆盖 0-15 全部通道与 DBC 绑定无关）。

    progress_cb(stage, percent) 进度刻度（percent 单调不降）：
    读取 BLF 5→10（逐容器字节位置）、解码 CANn 10→90（并行按桶/串行按
    通道）、聚合统计 CANn 92→95（逐通道）、写 MDF 95、完成 100。

    cancel_cb() 可选：读取（每 1024 帧）、解码（串行逐通道/并行每桶）、
    写 MDF 前后检查，置位即 raise ScanCancelled；写 MDF 期间（asammdf
    无取消钩子）点取消 → 删除已写完整输出后抛出（失败清理在 write_mdf
    内部——无残留契约，见 CONTEXT.md）。
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
    raw_chs = {ch for ch, dbc in bindings.items() if dbc is None} if raw_export else set()
    stats_bufs = defaultdict(lambda: ([], [], [], []))  # ch -> (ts, ext, remote, err)

    # 原始通道过滤键（向量化二分用排序数组）
    known_keys_arr = np.asarray(sorted(known_ids), dtype=np.uint32) \
        if known_ids is not None else None
    # 向量化原始块收集（ch -> [块列表, 丢弃帧数, 被丢弃帧原始 id 集合]）
    raw_chunks = {ch: [[], 0, set()] for ch in raw_chs}

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
        # H1 向量化单遍扫描：路由解码桶/统计/原始帧（数组桶 + 块列表，
        # 见 _read_vectorized；产物与旧 feed 循环逐位一致）
        _read_vectorized(blf_path, decoders, list(raw_chunks),
                         stats_export, stats_bufs, raw_chunks,
                         known_keys_arr, read_cb, cancel_cb)
        # 统计块列表 → 单数组（下游聚合代码不变）
        for ch, (t, e, r, er) in list(stats_bufs.items()):
            if not t:
                continue
            stats_bufs[ch] = (
                np.concatenate(t) if t else np.empty(0, dtype=np.float64),
                np.concatenate(e) if e else np.empty(0, dtype=bool),
                np.concatenate(r) if r else np.empty(0, dtype=bool),
                np.concatenate(er) if er else np.empty(0, dtype=bool))
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
                    blocks, uf, ui = raw_chunks[ch]
                    raw, unk_frames, unk_ids = _assemble_raw(blocks, ch, uf, ui)
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
                if len(ts):
                    global_end = max(global_end, float(np.max(ts)))
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
        # 无残留契约在 write_mdf 内部（失败清理本次半成品，见 CONTEXT.md）；
        # converter 不重复清理（deletion test：writer 改临时文件策略不连带 converter）
        mdf_writer.write_mdf(all_series, raw_groups, out_path,
                             abs_start_seconds=abs_start_time,
                             stats_groups=stats_groups)
        # 取消检查点：写入期间（asammdf 无取消钩子）点取消 → 删除完整产物
        #（写成功后 .mf4 已被 replace 消费，无半成品残留；删 out_path 是
        #「取消 = 放弃本次转换」的 converter 业务语义，不属于 writer 失败契约）
        if cancel_cb is not None and cancel_cb():
            Path(out_path).unlink(missing_ok=True)
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
