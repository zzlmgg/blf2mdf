"""全量对比：按信号集合匹配组 → 采样数/时间/首尾值/单位，再抽样数值对比。

用法：
    python tools/full_compare.py <自产.mdf> <参考.mdf> [--outdir outputs/mdf_compare] [--no-report]
默认同时打印到终端，并把详细报告写入 outputs/mdf_compare/compare_<时间戳>.md。
"""
import argparse
import contextlib
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
from asammdf import MDF


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

TIME_CHANNELS = {"t", "time"}

# 修复项 4：阶段 1 统计项（10 项）与参考组内偏移（参考每组 22 项）
STAT_NAMES = ("StdData", "StdDataRate", "ExtData", "ExtDataRate",
              "StdRemote", "StdRemoteRate", "ExtRemote", "ExtRemoteRate",
              "ErrorFrames", "ErrorFrameRate")
REF_STAT_IDX = {"StdData": 4, "StdDataRate": 5, "ExtData": 6, "ExtDataRate": 7,
                "StdRemote": 8, "StdRemoteRate": 9, "ExtRemote": 10,
                "ExtRemoteRate": 11, "ErrorFrames": 12, "ErrorFrameRate": 13}


def compare_stats(a, b):
    """1s 总线统计组对比（修复项 4 验收：160 组 × 601 点逐秒一致）。

    自产组序 = 通道 × 统计项（'1s' 组在文件尾部）；参考组序 = 通道 × 22 项（头部）。
    """
    print("\n=== 1s 总线统计组对比（修复项 4，阶段 1：10 项 × 16 通道）===")
    ones_a = [gi for gi, g in enumerate(a.groups)
              if (g.channel_group.acq_name or "") == "1s"]
    ones_b = [gi for gi, g in enumerate(b.groups)
              if (g.channel_group.acq_name or "") == "1s"]
    print(f"  '1s' 组数: 自产 {len(ones_a)} vs 参考 {len(ones_b)}（参考另含 12 项未实现统计）")
    if len(ones_a) != 160:
        print("  （自产非 160 组，跳过逐点对比）")
        return
    bad_total = 0
    for ch in range(16):
        for i, name in enumerate(STAT_NAMES):
            ga = ones_a[ch * 10 + i]
            gb = ones_b[ch * 22 + REF_STAT_IDX[name]]
            va = np.asarray(a.get(name, group=ga).samples)
            vb = np.asarray(b.get(name, group=gb).samples)
            if len(va) != len(vb):
                bad_total += 1
                print(f"  ch{ch} {name}: 采样数 {len(va)} vs {len(vb)}")
                continue
            if np.issubdtype(va.dtype, np.integer):
                bad = int(np.sum(va != vb))
            else:
                bad = int(np.sum(np.abs(va - vb) > 1e-9))
            if bad:
                bad_total += bad
                print(f"  ch{ch} {name}: {bad} 点不一致")
    if bad_total == 0:
        print("  （160 组 × 601 点逐点全部一致）")
    else:
        print(f"  （共 {bad_total} 点不一致）")


def index_groups(mdf):
    """group_index: frozenset(信号名) -> [(gi, acq, nch, channels)]"""
    idx = defaultdict(list)
    for gi, g in enumerate(mdf.groups):
        acq = g.channel_group.acq_name or ""
        if acq == "1s":
            continue
        names = frozenset(c.name for c in g.channels if c.name not in TIME_CHANNELS)
        idx[names].append((gi, acq, len(g.channels)))
    return idx


def summarize_signal(mdf, gi, name):
    try:
        sig = mdf.get(name, group=gi)
    except Exception:
        return None
    samples = np.asarray(sig.samples)
    ts = np.asarray(sig.timestamps)
    n = samples.size
    if n == 0:
        return (0, None, None, None, None, str(sig.unit))
    return (n, float(ts[0]), float(ts[-1]),
            samples[0], samples[-1], str(sig.unit))


def _run(path_a, path_b):
    with MDF(path_a) as a, MDF(path_b) as b:
        ia = index_groups(a)
        ib = index_groups(b)
        print(f"匹配组数: {len(ia)} vs {len(ib)}")

        # 1) 组匹配（按信号名集合）
        matched = 0
        mismatch = []
        for names, gs in ia.items():
            hit = ib.get(names)
            if hit:
                matched += 1
            else:
                mismatch.append((names, gs))
        print(f"按信号名集合完全匹配的组: {matched}/{len(ia)}")

        # 1.5) 组名对比（匹配组之间的 acq_name；修复项 1 验收：组名与参考报文名一致）
        print("\n=== 组名对比（按信号集合匹配的组）===")
        name_diff = 0
        for names, gs in ia.items():
            hit = ib.get(names)
            if not hit:
                continue
            for gi, acq, nch in gs:
                if acq != hit[0][1]:
                    name_diff += 1
                    print(f"  自产 {acq!r} vs 参考 {hit[0][1]!r}")
        if name_diff == 0:
            print("  （匹配组组名全部一致）")

        print(f"\n=== 未匹配组（自产侧 {len(mismatch)} 个）示例 ===")
        for names, gs in mismatch[:10]:
            print(f"  自产 {gs[0][1]} ({len(names)}信号): {sorted(names)[:6]}...")

        # 2) 未匹配组（参考侧）
        miss_b = [g for names, gs in ib.items() if names not in ia]
        print(f"\n=== 未匹配组（参考侧 {len(miss_b)} 个）示例 ===")
        for gi, acq, nch in miss_b[:10]:
            names = [c.name for c in b.groups[gi].channels if c.name not in TIME_CHANNELS]
            print(f"  参考 {acq} ({len(names)}信号): {names[:6]}...")

        # 3) 组内信号名对比（匹配组之间）
        print("\n=== 匹配组间信号差异 ===")
        diff_count = 0
        for names, gs in ia.items():
            hit = ib.get(names)
            if not hit:
                continue
            # 组内通道数对比（含 time 通道）
            nch_a = gs[0][2]
            nch_b = hit[0][2]
            if nch_a != nch_b:
                diff_count += 1
                print(f"  自产 {gs[0][1]} ({nch_a}ch) vs 参考 {hit[0][1]} ({nch_b}ch)")
        if diff_count == 0:
            print("  （无通道数差异）")

        # 4) 采样数与时间范围对比（匹配组，取第一个信号）
        print("\n=== 采样数/时间范围对比（每组取第一个信号）===")
        checked = 0
        for names, gs in ia.items():
            hit = ib.get(names)
            if not hit:
                continue
            s_name = sorted(names)[0]
            sa = summarize_signal(a, gs[0][0], s_name)
            sb = summarize_signal(b, hit[0][0], s_name)
            if sa is None or sb is None or sa[0] == 0 or sb[0] == 0:
                continue
            checked += 1
            if checked <= 15 or sa[0] != sb[0]:
                flag = "" if (sa[0] == sb[0]) else "  <== 采样数不同"
                print(f"  {s_name}: 自产 n={sa[0]} t=[{sa[1]:.3f},{sa[2]:.3f}]"
                      f" 参考 n={sb[0]} t=[{sb[1]:.3f},{sb[2]:.3f}]{flag}")
        print(f"  (共对比 {checked} 组)")

        # 5.5) 逐信号 dtype 对比（修复项 8 验收：整型/浮点/枚举三类各抽查 ≤20 信号）
        print("\n=== dtype 对比（匹配组逐信号，按类抽查各 ≤20）===")
        kind_seen = {"int": 0, "float": 0, "text": 0}
        dtype_mismatch = []
        scanned = 0

        def _kind_of(dt):
            return ("text" if dt.kind in "OSU" else
                    "int" if np.issubdtype(dt, np.integer) else "float")

        done = False
        for names, gs in ia.items():
            hit = ib.get(names)
            if not hit:
                continue
            for name in sorted(names):
                if all(kind_seen[k] >= 20 for k in kind_seen):
                    done = True
                    break
                try:
                    sa = a.get(name, group=gs[0][0])
                    sb = b.get(name, group=hit[0][0])
                except Exception:
                    continue
                va, vb = np.asarray(sa.samples), np.asarray(sb.samples)
                if va.size == 0 or vb.size == 0:
                    continue
                kind = _kind_of(va.dtype)
                if kind_seen[kind] >= 20:
                    continue
                kind_seen[kind] += 1
                scanned += 1
                if va.dtype != vb.dtype:
                    dtype_mismatch.append((name, str(va.dtype), str(vb.dtype)))
            if done:
                break
        print(f"  （共抽查 {scanned} 信号：整型 {kind_seen['int']}、浮点 {kind_seen['float']}、"
              f"文本 {kind_seen['text']}）")
        if dtype_mismatch:
            for name, da, db in dtype_mismatch[:20]:
                print(f"  MISMATCH {name}: 自产 {da} vs 参考 {db}")
        else:
            print("  （抽查信号 dtype 全部与参考一致）")

        # 5.6) 枚举文本逐值对比（修复项 3：按时间对齐逐点比对 ≤10 信号 × 前 200 点）
        print("\n=== 枚举文本逐值对比（抽查 ≤10 文本信号 × 前 200 点）===")
        txt_checked, txt_bad, txt_sigs = 0, 0, 0
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
                status = "一致" if bad == 0 else f"{bad}/{m} 点不一致"
                print(f"  {name} ({va.dtype}): {status}")
                txt_sigs += 1
                txt_checked += m
                txt_bad += bad
        if txt_sigs == 0:
            print("  （匹配组中无文本信号）")
        elif txt_bad == 0:
            print(f"  （{txt_sigs} 个文本信号逐值全部一致，共比对 {txt_checked} 点）")

        # 5) 抽样数值对比
        print("\n=== 抽样数值对比（前 8 个共同信号，各取前 200 点）===")
        npairs = 0
        for names, gs in ia.items():
            if npairs >= 8:
                break
            hit = ib.get(names)
            if not hit:
                continue
            s_name = sorted(names)[0]
            sa = summarize_signal(a, gs[0][0], s_name)
            sb = summarize_signal(b, hit[0][0], s_name)
            if sa is None or sb is None or sa[0] == 0 or sb[0] == 0:
                continue
            siga = a.get(s_name, group=gs[0][0])
            sigb = b.get(s_name, group=hit[0][0])
            va, ta = np.asarray(siga.samples), np.asarray(siga.timestamps)
            vb, tb = np.asarray(sigb.samples), np.asarray(sigb.timestamps)
            if not (np.issubdtype(va.dtype, np.number) and np.issubdtype(vb.dtype, np.number)):
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
            status = "一致" if bad == 0 else f"{bad}/{m} 点不一致"
            print(f"  {s_name} (offset={offset:.3f}s): {status}")
            npairs += 1

        # 6) 1s 总线统计组对比（修复项 4）
        compare_stats(a, b)


def main():
    parser = argparse.ArgumentParser(description="全量对比两个 MDF（自产 vs CANoe 参考）")
    parser.add_argument("path_a", help="自产 MDF 路径")
    parser.add_argument("path_b", help="参考 MDF 路径")
    parser.add_argument("--outdir", default="outputs/mdf_compare",
                        help="报告输出目录（默认 outputs/mdf_compare）")
    parser.add_argument("--no-report", action="store_true",
                        help="只打印终端，不写报告文件")
    args = parser.parse_args()

    if args.no_report:
        _run(args.path_a, args.path_b)
        return

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name_a = Path(args.path_a).stem
    name_b = Path(args.path_b).stem
    report = outdir / f"compare_{name_a}_vs_{name_b}_{ts}.md"
    with open(report, "w", encoding="utf-8") as fp:
        fp.write(f"# MDF 对比报告\n\n- 自产: `{args.path_a}`\n- 参考: `{args.path_b}`\n"
                 f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        tee = _Tee(sys.stdout, fp)
        with contextlib.redirect_stdout(tee):
            _run(args.path_a, args.path_b)
    print(f"\n[报告已写入] {report}")


if __name__ == "__main__":
    main()
