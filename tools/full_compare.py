"""全量对比：自产 vs CANoe 参考（报告薄壳：判定委托 tools/mdf_compare）。

用法：
    python tools/full_compare.py <自产.mdf> <参考.mdf> \
        --stats-ref-block 22 --stats-ref-idx "StdData:4,StdDataRate:5,..." \
        [--skip-{header,structure,values,stats}] [--outdir outputs/mdf_compare] [--no-report]
--stats-ref-block / --stats-ref-idx 必填：参考文件统计组布局（每通道项数 + 项名→组内偏移）。
退出码 0 = 判定一致；非 0 = 存在判定差异（报告仍写入）。
"""
import argparse
import contextlib
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
from asammdf import MDF

from mdf_compare import compare_files_reference, STAT_NAMES  # 同目录脚本运行

_TIME_CHANNELS = {"t", "time"}
_DIMS = ("header", "structure", "values", "stats")


class _Tee:
    """同时写多个流（终端 + 报告文件）。"""

    def __init__(self, *files):
        self.files = files

    def write(self, s):
        for f in self.files:
            f.write(s)

    def flush(self):
        for f in self.files:
            f.flush()


def _index_groups(mdf):
    """group_index: frozenset(信号名) -> [(gi, acq, nch)]（报告用；'1s' 跳过）。"""
    idx = defaultdict(list)
    for gi, g in enumerate(mdf.groups):
        acq = g.channel_group.acq_name or ""
        if acq == "1s":
            continue
        names = frozenset(c.name for c in g.channels if c.name not in _TIME_CHANNELS)
        idx[names].append((gi, acq, len(g.channels)))
    return idx


def _summarize_signal(mdf, gi, name):
    try:
        sig = mdf.get(name, group=gi)
    except Exception:
        return None
    samples = np.asarray(sig.samples)
    ts = np.asarray(sig.timestamps)
    n = samples.size
    if n == 0:
        return (0, None, None, None, None, str(sig.unit))
    return (n, float(ts[0]), float(ts[-1]), samples[0], samples[-1], str(sig.unit))


def _report(path_a, path_b):
    """报告部分（采样数/时间范围/dtype 抽查/文本详查/抽样数值）——只报告，不判定。"""
    with MDF(path_a) as a, MDF(path_b) as b:
        ia, ib = _index_groups(a), _index_groups(b)
        print(f"匹配组数: {len(ia)} vs {len(ib)}")
        matched = sum(1 for names in ia if names in ib)
        print(f"按信号名集合完全匹配的组: {matched}/{len(ia)}")
        miss_a = [gs[0] for names, gs in ia.items() if names not in ib]
        print(f"未匹配组（自产 {len(miss_a)} 个）: {[g[1] for g in miss_a[:5]]}")
        miss_b = [gs[0] for names, gs in ib.items() if names not in ia]
        print(f"未匹配组（参考 {len(miss_b)} 个）: {[g[1] for g in miss_b[:5]]}")
        # 采样数/时间范围（每组取首信号，前 15 组）
        print("\n=== 采样数/时间范围对比（每组取第一个信号，前 15 组）===")
        checked = 0
        for names, gs in ia.items():
            hit = ib.get(names)
            if not hit or checked >= 15:
                continue
            s_name = sorted(names)[0]
            sa = _summarize_signal(a, gs[0][0], s_name)
            sb = _summarize_signal(b, hit[0][0], s_name)
            if sa is None or sb is None or sa[0] == 0 or sb[0] == 0:
                continue
            checked += 1
            flag = "" if sa[0] == sb[0] else "  <== 采样数不同"
            print(f"  {s_name}: 自产 n={sa[0]} t=[{sa[1]:.3f},{sa[2]:.3f}]"
                  f" 参考 n={sb[0]} t=[{sb[1]:.3f},{sb[2]:.3f}]{flag}")
        print(f"  (共对比 {checked} 组)")
        # 枚举文本详查（≤10 文本信号 × 前 200 点，时间对齐；只报告不判定）
        print("\n=== 枚举文本逐值详查（≤10 文本信号 × 前 200 点）===")
        txt_sigs = 0
        for names, gs in ia.items():
            hit = ib.get(names)
            if not hit or txt_sigs >= 10:
                continue
            for name in sorted(names):
                if txt_sigs >= 10:
                    break
                try:
                    siga = a.get(name, group=gs[0][0])
                    sigb = b.get(name, group=hit[0][0])
                except Exception:
                    continue
                va, ta = np.asarray(siga.samples), np.asarray(siga.timestamps)
                vb, tb = np.asarray(sigb.samples), np.asarray(sigb.timestamps)
                if va.size == 0 or vb.size == 0 or va.dtype.kind not in "OSU":
                    continue
                offset = float(ta[0]) - float(tb[0])
                ta2 = ta - offset
                m, bad = 0, 0
                for i in range(min(200, len(tb))):
                    idx = int(np.argmin(np.abs(ta2 - tb[i])))
                    if abs(ta2[idx] - tb[i]) > 1e-3:
                        continue
                    if bytes(va[idx]).rstrip(b"\x00") != bytes(vb[i]).rstrip(b"\x00"):
                        bad += 1
                    m += 1
                print(f"  {name} ({va.dtype}): {'一致' if bad == 0 else f'{bad}/{m} 点不一致'}")
                txt_sigs += 1
        if txt_sigs == 0:
            print("  （匹配组中无文本信号）")
        # 抽样数值（前 8 信号 × 前 200 点，时间对齐；只报告不判定）
        print("\n=== 抽样数值对比（前 8 个共同信号，各取前 200 点）===")
        npairs = 0
        for names, gs in ia.items():
            if npairs >= 8:
                break
            hit = ib.get(names)
            if not hit:
                continue
            s_name = sorted(names)[0]
            sa = _summarize_signal(a, gs[0][0], s_name)
            sb = _summarize_signal(b, hit[0][0], s_name)
            if sa is None or sb is None or sa[0] == 0 or sb[0] == 0:
                continue
            siga = a.get(s_name, group=gs[0][0])
            sigb = b.get(s_name, group=hit[0][0])
            va, ta = np.asarray(siga.samples), np.asarray(siga.timestamps)
            vb, tb = np.asarray(sigb.samples), np.asarray(sigb.timestamps)
            if not (np.issubdtype(va.dtype, np.number)
                    and np.issubdtype(vb.dtype, np.number)):
                continue
            offset = float(ta[0]) - float(tb[0])
            ta2 = ta - offset
            m, bad = 0, 0
            for i in range(min(200, len(tb))):
                idx = int(np.argmin(np.abs(ta2 - tb[i])))
                if abs(ta2[idx] - tb[i]) > 1e-3:
                    continue
                if abs(float(va[idx]) - float(vb[i])) > max(1e-6, 1e-6 * abs(float(vb[i]))):
                    bad += 1
                m += 1
            print(f"  {s_name} (offset={offset:.3f}s): {'一致' if bad == 0 else f'{bad}/{m} 点不一致'}")
            npairs += 1


def _parse_layout(block_s, idx_s):
    offsets = {}
    for pair in idx_s.split(","):
        k, v = pair.split(":")
        offsets[k.strip()] = int(v.strip())
    return int(block_s), offsets


def main(argv=None):
    p = argparse.ArgumentParser(description="全量对比两个 MDF（自产 vs CANoe 参考）")
    p.add_argument("path_a", help="自产 MDF 路径")
    p.add_argument("path_b", help="参考 MDF 路径")
    p.add_argument("--stats-ref-block", type=int, required=True,
                   help="参考文件每通道统计项数（如 22），必填")
    p.add_argument("--stats-ref-idx", required=True,
                   help="参考统计项偏移表，如 'StdData:4,StdDataRate:5,...'，必填")
    for d in _DIMS:
        p.add_argument(f"--skip-{d}", action="store_true", help=f"关闭 {d} 维度")
    p.add_argument("--outdir", default="outputs/mdf_compare",
                   help="报告输出目录（默认 outputs/mdf_compare）")
    p.add_argument("--no-report", action="store_true",
                   help="只打印终端，不写报告文件")
    args = p.parse_args(argv)
    layout = _parse_layout(args.stats_ref_block, args.stats_ref_idx)
    dims = frozenset(d for d in _DIMS if not getattr(args, f"skip_{d}"))

    if args.no_report:
        _report(args.path_a, args.path_b)
    else:
        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name_a, name_b = Path(args.path_a).stem, Path(args.path_b).stem
        report = outdir / f"compare_{name_a}_vs_{name_b}_{ts}.md"
        with open(report, "w", encoding="utf-8") as fp:
            fp.write(f"# MDF 对比报告\n\n- 自产: `{args.path_a}`\n- 参考: `{args.path_b}`\n"
                     f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            tee = _Tee(sys.stdout, fp)
            with contextlib.redirect_stdout(tee):
                _report(args.path_a, args.path_b)
        print(f"\n[报告已写入] {report}")

    diffs = compare_files_reference(args.path_a, args.path_b,
                                    stats_ref_layout=layout, dims=dims)
    print(f"\n=== 判定（{', '.join(sorted(dims))}）: "
          f"{'全部一致' if not diffs else f'{len(diffs)} 处差异'} ===")
    for line in diffs:
        print(f"  - {line}")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
