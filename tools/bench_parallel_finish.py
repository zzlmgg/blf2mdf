"""方案 G 第 1 步原型：真实样例「扫描 + 并行 finish」决策数据。

产出（方案文档 §6 步骤 1）：
- 扫描+feed 墙钟（基线对照）
- spawn 预热耗时（藏在扫描期）
- 桶 pickle 总量（IPC 传输量，对照 §5.4 估算）
- 每通道帧数 + 串行/并行 finish 时间（CAN3 关键路径）
- 串行 finish 总墙钟 vs 并行 finish 总墙钟（净省）
- 并行 vs 串行输出全量对拍（等价性校验，防止计时跑在错误实现上）

决策门（§1）：并行 finish 净省 ≥ 2s 才继续。

用法：python tools/bench_parallel_finish.py [workers]
"""
import os
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from conftest import DBC_DIR, sample_blf          # noqa: E402
from test_golden import BINDING                    # noqa: E402
from core import mp_finish                        # noqa: E402
from core.blf_reader import iter_all_messages     # noqa: E402
from core.decoder import ChannelDecoder           # noqa: E402
from core.dbc_loader import load                  # noqa: E402


def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 \
        else min(os.cpu_count() or 1, len(BINDING))
    blf = sample_blf()
    if blf is None:
        raise SystemExit("无样例 BLF")
    bindings = {}
    for ch, dbc in BINDING.items():
        p = DBC_DIR / dbc
        if p.exists():
            bindings[ch] = load(str(p))
    channels = sorted(bindings)
    print(f"绑定通道: {channels}   worker 数: {workers}")

    # ── 1. 建池 + 预热（生产路径：在扫描前，spawn 藏在扫描期）──
    t0 = time.perf_counter()
    pool = ProcessPoolExecutor(max_workers=workers)
    mp_finish.warm_up(pool)
    t_spawn = time.perf_counter() - t0
    print(f"\n1) 池创建 + spawn/import 预热: {t_spawn:.2f}s（藏在扫描期）")

    # ── 2. 扫描 + feed（与 convert 单遍扫描相同的路由逻辑）──
    decoders = {ch: ChannelDecoder(d, ch) for ch, d in bindings.items()}
    t0 = time.perf_counter()
    for fr in iter_all_messages(str(blf)):
        dec = decoders.get(fr.channel)
        if dec is not None:
            dec.feed(fr)
    t_scan = time.perf_counter() - t0
    print(f"2) 扫描+feed: {t_scan:.2f}s")

    # ── 3. IPC 传输量（桶 pickle 字节）──
    bytes_total = sum(len(pickle.dumps(dec.buckets)) for dec in decoders.values())
    frames = {ch: sum(len(b["ts"]) for b in dec.buckets.values())
              for ch, dec in decoders.items()}
    print(f"3) 桶 pickle 总量: {bytes_total/1e6:.1f} MB（全通道 {sum(frames.values()):,} 帧）")

    # ── 4. 并行 finish（生产路径时序：feed 完立即提交）──
    t0 = time.perf_counter()
    done_at = {}

    def cb(stage, pct):
        done_at[stage] = time.perf_counter() - t0

    par = mp_finish.finish_all(decoders, channels, progress_cb=cb, pool=pool)
    t_par = time.perf_counter() - t0
    # per-bucket：完成回调在组装阶段触发（每通道一次），首通道完成 ≈
    # 最重桶任务链路（解码+回传）的代理；总墙钟 = 全部桶任务 + 组装。
    first = min(done_at.values(), default=float("nan"))
    print(f"4) 并行 finish 墙钟: {t_par:.2f}s（首通道完成于 {first:.2f}s）")

    # ── 5. 串行 finish（同一批 decoders，与并行输出对比）──
    t0 = time.perf_counter()
    ser = {}
    for ch in channels:
        ser[ch] = decoders[ch].finish()
    t_ser = time.perf_counter() - t0
    print(f"5) 串行 finish 墙钟: {t_ser:.2f}s")

    # ── 6. 等价性校验（并行 vs 串行全量对拍）──
    bad = 0
    for ch in channels:
        (p_series, p_stats), (s_series, s_stats) = par[ch], ser[ch]
        if len(p_series) != len(s_series):
            print(f"  CAN{ch}: 系列数 {len(p_series)} vs {len(s_series)}")
            bad += 1
            continue
        for sa, sb in zip(p_series, s_series):
            diff = mp_finish._assert_series_equal(sa, sb)
            if diff:
                print(f"  CAN{ch}: {diff}")
                bad += 1
        if (p_stats.total_frames != s_stats.total_frames
                or p_stats.unknown_frames != s_stats.unknown_frames
                or p_stats.unknown_ids != s_stats.unknown_ids):
            print(f"  CAN{ch}: stats 不一致 {p_stats} vs {s_stats}")
            bad += 1
    print(f"6) 等价性校验: {'全部一致' if bad == 0 else f'{bad} 处差异'}")

    # ── 7. 决策数据 ──
    net = t_ser - t_par
    print(f"\n=== 决策数据 ===")
    print(f"串行 finish {t_ser:.2f}s → 并行 finish {t_par:.2f}s（净省 {net:.2f}s）")
    print(f"spawn 预热 {t_spawn:.2f}s 已隐藏在扫描期 {t_scan:.2f}s")
    print(f"每通道帧数: {frames}")
    verdict = "PASS 通过，继续实施" if net >= 2 else "FAIL 未达，中止回退"
    print(f"决策门（净省 ≥ 2s）: {verdict}")
    pool.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    main()
