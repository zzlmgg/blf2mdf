"""深度对比两个 MDF：组信息 + 信号级采样数/时间范围/首尾值。"""
import sys
from collections import defaultdict

import numpy as np
from asammdf import MDF


def signal_summary(mdf, gi, name):
    """返回 (n_samples, t0, t1, first_val, last_val) 或 None。"""
    try:
        sig = mdf.get(name, group=gi)
    except Exception:
        return None
    if sig is None:
        return None
    samples = np.asarray(sig.samples)
    ts = np.asarray(sig.timestamps)
    n = samples.size
    if n == 0:
        return (0, None, None, None, None)
    return (n, float(ts[0]), float(ts[-1]), samples[0], samples[-1])


def dump(path, label):
    with MDF(path) as mdf:
        print(f"### {label} groups={len(mdf.groups)}")
        sig_total = 0
        for gi, g in enumerate(mdf.groups):
            acq = g.channel_group.acq_name or ""
            nch = len(g.channels)
            try:
                cyc = g.cycles_nr
            except Exception:
                cyc = -1
            chnames = [c.name for c in g.channels]
            sig_total += cyc
            # 只打印精简信息
            print(f"  g[{gi:3d}] '{acq}' nch={nch} cycles={cyc} ch={chnames[:6]}{'...' if nch>6 else ''}")
        print(f"  total samples(cycles sum)={sig_total}")


if __name__ == "__main__":
    dump(sys.argv[1], sys.argv[1])
