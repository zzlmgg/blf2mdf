"""CAN3/CAN6 桶大小分布（per-bucket 变体可行性依据）。

方案 G 第 1 步原型补充测量：per-channel 并行净省 1.71s < 决策门 2s，
关键路径 = CAN3 单通道全链路。per-bucket 变体若把 CAN3 56 桶摊到多
worker，可打破关键路径——这里先量化桶分布验证理论空间。

用法：python tools/bench_bucket_dist.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from conftest import DBC_DIR, sample_blf                  # noqa: E402
from test_golden import BINDING                            # noqa: E402
from core.blf_reader import iter_all_messages             # noqa: E402
from core.decoder import ChannelDecoder                   # noqa: E402
from core.dbc_loader import load                          # noqa: E402

blf = sample_blf()
for ch in (3, 6):
    dec = ChannelDecoder(load(str(DBC_DIR / BINDING[ch])), ch)
    for fr in iter_all_messages(str(blf)):
        if fr.channel == ch:
            dec.feed(fr)
    sizes = sorted((len(b["ts"]) for b in dec.buckets.values()), reverse=True)
    n = len(sizes)
    total = sum(sizes)
    # 前 8 大桶 + 累计占比
    print(f"\nCAN{ch}: {n} 桶, {total:,} 帧")
    cum = 0
    for i, s in enumerate(sizes[:10], 1):
        cum += s
        print(f"  桶 {i:>3}: {s:>7,} 帧 ({cum/total:5.1%} 累计)")
    # 按 8 worker LPT 的期望墙钟（帧数加权，解码 ~13µs/帧 参考 CAN3 5.5s/43万）
    loads = [0] * 8
    for s in sizes:
        w = min(range(8), key=lambda i: loads[i])
        loads[w] += s
    print(f"  8 worker LPT 后最大负载: {max(loads):,} 帧 (vs 串行 {total:,})")
