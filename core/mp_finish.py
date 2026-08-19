"""多进程并行解码：单遍扫描后按桶分派 finish（方案 G，G2 架构，per-bucket 变体）。

设计见 docs/2026-08-06-blf-mdf-parallel-decode-plan.md §5 + §12 原型执行记录。
per-channel 原型实测净省 1.71s（< 决策门 2s），关键路径 = CAN3 单通道全链路
（5.5s 解码 + 30MB 桶 pickle + 大结果回传）；改为按 (通道, 桶) 分派后，
CAN3 的 47 桶摊到全部 worker，最大单桶 6 万帧 ≈ 0.8s 解码（桶分布实测）。

确定性保证（§5.6，per-bucket 追加）：
- 系列顺序 = 桶插入顺序（feed 时 setdefault 首次出现序）；父进程按
  `dec.buckets` 插入序组装 → 与串行 finish 的 buckets 迭代序一致；
- 每桶结果 (series, unknown) 与串行 `ChannelDecoder.finish()` 内对应桶
  逐位一致（同一函数：_finish_bucket_vectorized / _decode_bucket_reference）；
- 统计合并（§5.5）：feed 期计数在父进程；finish 期未知 = Σ桶子进程返回。

任务参数（IPC 最小化）：vectorized 路径不使用 dbc 参数（报文定义在
bucket.md），仅含非向量化报文的通道随任务传 DbcDef（回退路径需要
cantools Database）——样例 10 通道全向量化，零 DBC 重复 pickle。
失败回退：任务异常/池损坏 → 未完成桶原地串行 finish，输出与全串行一致。
"""
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

from core.blf_reader import ConversionCancelled
from core.decoder import (Bucket, DecodeStats, _decode_bucket_reference,
                          _finish_bucket_vectorized, _msg_vectorizable)


def _finish_bucket_worker(channel: int, arb: int, bucket: Bucket, dbc):
    """子进程入口：单桶 finish（= ChannelDecoder.finish() 内同一函数）。

    返回 (channel, arb, series 列表, unknown_frames, sorted(unknown_ids),
    桶解码耗时秒)。耗时在 worker 内实测（perf_counter 包住解码本身，
    不含 pickle/传输）——timings 语义 = 每通道累计工作量，而非调度跨度。
    stats 从空开始（feed 期计数在父进程，见 finish_all 合并规则）。
    """
    t0 = time.perf_counter()
    stats = DecodeStats()
    bucket.to_array()   # feed/blocks 相位 → 数组相位（幂等；原 _normalize_bucket 职责内化）
    md = bucket.md
    if not _msg_vectorizable(md):
        # 回退路径需要 cantools Database（_decode_bucket_reference 用 dbc.db）
        series = _decode_bucket_reference(dbc, channel, bucket, stats)
    else:
        series = _finish_bucket_vectorized(dbc, channel, bucket, stats)
    return channel, arb, series, stats.unknown_frames, sorted(stats.unknown_ids), \
        time.perf_counter() - t0


def _noop():
    """预热任务：触发 ProcessPoolExecutor 惰性 spawn 全部 worker。"""
    return None


def resolve_workers(workers: int | None, n_channels: int,
                    env_override: bool = True) -> int:
    """worker 数唯一决策点（L1 收口）：显式 workers → cpu 数，再对通道数
    取 min；env BLF_MP_WORKERS 覆盖仅产品路径（make_pool）启用，
    finish_all 内部建池（测试/串行兜底）保持不读 env——语义不变。"""
    n = workers if workers is not None else (os.cpu_count() or 1)
    if env_override:
        env = os.environ.get("BLF_MP_WORKERS")
        if env:
            n = int(env)
    return min(n, n_channels)


def decode_progress(fraction: float) -> float:
    """解码进度带 10→90（串行/并行共用，L1 收口）：10 + 80 × 完成比例。"""
    return 10 + 80 * fraction


def warm_up(pool: ProcessPoolExecutor, n_workers: int) -> None:
    """扫描期预热：每个 worker 槽提交一个 no-op 任务并等待，让全部 worker
    的 spawn + import（numpy/cantools）耗时藏在扫描期（实测 8 worker
    ~1.7s）。注意只提交 1 个任务时 ProcessPoolExecutor 惰性只拉起 1 个
    worker，其余 spawn 会落到首个真实任务提交时（H1 后解码墙钟实测
    ~1.7s 虚增）——必须按已知 worker 数全量预热。"""
    futures = [pool.submit(_noop) for _ in range(n_workers)]
    for f in futures:
        f.result()


def make_pool(channels: list[int],
              workers: int | None = None) -> ProcessPoolExecutor:
    """创建并预热进程池（worker 数 = resolve_workers 唯一决策，env
    BLF_MP_WORKERS 覆盖）。convert 在 feed 循环前调用（预热藏在扫描期）；
    池由调用方负责 shutdown。
    """
    n = resolve_workers(workers, len(channels))
    pool = ProcessPoolExecutor(max_workers=n)
    warm_up(pool, n)
    return pool


def bucket_bytes(decoders, channels: list[int]) -> int:
    """桶内存估算（阈值回退用，见 §5.7）：Σ Bucket.memory_estimate()（三相位）。"""
    total = 0
    for ch in channels:
        for b in decoders[ch].buckets.values():
            total += b.memory_estimate()
    return total


def _needs_dbc(decoders, channels: list[int]) -> set[int]:
    """含非向量化报文（mux 选择信号带变换/choices）的通道 → 任务需 DbcDef。"""
    return {ch for ch in channels
            if any(not _msg_vectorizable(md) for md in
                   decoders[ch].dbc.messages.values())}


def _assert_series_equal(a, b) -> str | None:
    """两个 SignalSeries 逐项对比 → 差异描述或 None（相等）。

    nan-aware（float 通道 mux 非活跃为 nan）；dtype 与采样逐位。
    """
    if (a.message_name, a.channel, a.node) != (b.message_name, b.channel, b.node):
        return f"头 {a.message_name}/{b.message_name}"
    if a.signal_names != b.signal_names:
        return "信号名"
    if not np.array_equal(a.timestamps, b.timestamps):
        return "时间戳"
    for n in a.signal_names:
        va, vb = a.values[n], b.values[n]
        if va.dtype != vb.dtype:
            return f"{n} dtype {va.dtype} vs {vb.dtype}"
        # float 通道含 nan（mux 非活跃），equal_nan 才可判等；bytes/整型直接逐位
        equal = np.array_equal(va, vb, equal_nan=True) \
            if va.dtype.kind == "f" else np.array_equal(va, vb)
        if not equal:
            return f"{n} 值"
    return None


def finish_all(decoders: dict[int, "ChannelDecoder"], channels: list[int],
               progress_cb=None, workers: int | None = None,
               pool: ProcessPoolExecutor | None = None,
               verify: bool = False,
               cancel_cb=None,
               timings: dict[int, float] | None = None) \
        -> dict[int, tuple[list, DecodeStats]]:
    """按桶并行 finish 全部通道 → {ch: (series 列表, 合并后 DecodeStats)}。

    - 调用方可在扫描前创建池并 warm_up（spawn 成本藏在扫描期）；
      pool=None 时内部创建（无预热，主要供测试/串行兜底对照用）。
    - progress_cb(stage, percent)：每桶完成时实时回调一次（percent =
      decode_progress(累计完成帧数 / 总帧数)，随 as_completed 单调递增）；
      失败桶在串行兜底完成后同样上报。修复：旧实现把全部上报推迟到
      所有桶完成后按通道补齐 → 解码期间进度条停住、结束瞬间跳到 90%。
    - verify=True：组装结果与父进程本地串行 finish 全量对拍（测试用）。
    - 失败回退：任务异常/池损坏 → 未完成桶原地串行 finish，结果与全串行一致。
    - cancel_cb() 可选：每桶完成时检查（收集循环 + 串行兜底循环），置位即
      raise ConversionCancelled——注意必须在通用 except Exception 之前捕获，否则
      取消异常会被当成池故障吞掉、转串行兜底重解（取消静默失效）。
    - timings 可选（GUI 日志用）：传入 dict 时填充 {ch: 秒}，语义 = 该通道
      各桶解码的累计工作量（worker 内实测每桶耗时求和，含串行兜底桶）——
      并行下各通道互相重叠，是 CPU 工作而非墙钟跨度；零桶通道填 0.0；
      ConversionCancelled 抛出时不收尾（随异常丢弃）。
    """
    if not channels:
        return {}
    need_dbc = _needs_dbc(decoders, channels)
    n_workers = resolve_workers(workers, len(channels), env_override=False)
    owned = pool is None
    if owned:
        pool = ProcessPoolExecutor(max_workers=n_workers)

    # ── 任务清单（桶插入序 = 系列顺序）──
    order: dict[int, list[int]] = {ch: [] for ch in channels}   # ch -> [arb]
    tasks: list[tuple[int, int, Bucket, int]] = []              # (ch, arb, 桶, 帧数)
    for ch in channels:
        for arb in decoders[ch].buckets:
            b = decoders[ch].buckets[arb]
            order[ch].append(arb)
            tasks.append((ch, arb, b, b.n_frames))

    # ── LPT 分派：按桶帧数降序贪心进当前最轻 worker ──
    loads = [0] * n_workers
    bins: list[list[tuple[int, int, Bucket, int]]] = [[] for _ in range(n_workers)]
    for task in sorted(tasks, key=lambda t: -t[3]):
        w = min(range(n_workers), key=lambda i: loads[i])
        bins[w].append(task)
        loads[w] += task[3]

    # ── 提交 + 收集 ──
    results: dict[tuple[int, int], tuple[list, int, list[int]]] = {}
    failed: list[tuple[int, int, Bucket]] = []
    total_frames = sum(t[3] for t in tasks)
    done_frames = 0
    # 每通道累计解码工作量（timings 请求时）：worker 内实测桶耗时求和
    ch_work: dict[int, float] = {}

    def _report(ch: int, bucket: Bucket) -> None:
        """每桶完成上报：percent = 10 + 80 × 累计帧数 / 总帧数（单调）。"""
        nonlocal done_frames
        done_frames += bucket.n_frames
        if progress_cb and total_frames:
            progress_cb(f"解码 CAN{ch}",
                        decode_progress(done_frames / total_frames))

    try:
        futures = {}
        for w, tlist in enumerate(bins):
            for ch, arb, bucket, _ in tlist:
                dbc = decoders[ch].dbc if ch in need_dbc else None
                f = pool.submit(_finish_bucket_worker, ch, arb, bucket, dbc)
                futures[f] = (ch, arb)
        for f in as_completed(futures):
            # 取消检查点：每桶完成时（in-flight 桶由 finally 的
            # shutdown(cancel_futures=True) 收尾，运行中桶至多 ~1s）
            if cancel_cb is not None and cancel_cb():
                raise ConversionCancelled()
            ch, arb = futures[f]
            try:
                _, _, series, unk_frames, unk_ids, dur = f.result()
            except Exception:
                failed.append((ch, arb, decoders[ch].buckets[arb]))
                continue
            ch_work[ch] = ch_work.get(ch, 0.0) + dur
            results[(ch, arb)] = (series, unk_frames, unk_ids)
            _report(ch, decoders[ch].buckets[arb])
    except ConversionCancelled:
        # 必须先于通用 except Exception 捕获：取消不能走「池故障→串行兜底」
        raise
    except Exception:
        # BrokenProcessPool 等：未收齐的桶全部转串行
        done = set(results)
        for ch in channels:
            for arb in decoders[ch].buckets:
                if (ch, arb) not in done:
                    failed.append((ch, arb, decoders[ch].buckets[arb]))
    finally:
        if owned:
            pool.shutdown(wait=True, cancel_futures=True)

    # ── 串行兜底（结果与全串行一致，兜底桶同样上报进度）──
    for ch, arb, bucket in failed:
        # 取消检查点：串行兜底逐桶
        if cancel_cb is not None and cancel_cb():
            raise ConversionCancelled()
        _, _, series, unk_frames, unk_ids, dur = \
            _finish_bucket_worker(ch, arb, bucket, decoders[ch].dbc)
        results[(ch, arb)] = (series, unk_frames, unk_ids)
        ch_work[ch] = ch_work.get(ch, 0.0) + dur
        _report(ch, bucket)

    if verify:
        for ch in channels:
            local, _ = decoders[ch].finish()   # 父进程串行 finish（对拍用）
            built: list = []
            for arb in order[ch]:
                built.extend(results[(ch, arb)][0])
            if len(local) != len(built):
                raise AssertionError(
                    f"CAN{ch}: 系列数 {len(built)} vs {len(local)}")
            for sa, sb in zip(built, local):
                diff = _assert_series_equal(sa, sb)
                if diff:
                    raise AssertionError(f"CAN{ch} 系列不一致: {diff}")

    # ── 每通道累计解码工作量（timings 请求时）：零桶通道填 0.0 ──
    if timings is not None:
        for ch in channels:
            timings[ch] = ch_work.get(ch, 0.0)

    # ── 按桶序组装 + 合并 feed 期计数（§5.5）──
    merged: dict[int, tuple[list, DecodeStats]] = {}
    for ch in channels:
        series: list = []
        unk_frames = 0
        unk_ids: set[int] = set()
        for arb in order[ch]:
            s, uf, ui = results[(ch, arb)]
            series.extend(s)
            unk_frames += uf
            unk_ids.update(ui)
        feed = decoders[ch].stats
        stats = DecodeStats(
            total_frames=feed.total_frames,
            unknown_frames=feed.unknown_frames + unk_frames,
            unknown_ids=feed.unknown_ids | unk_ids,
        )
        merged[ch] = (series, stats)
    return merged
