"""全量对比：按信号集合匹配组 → 采样数/时间/首尾值/单位，再抽样数值对比。"""
import sys
from collections import defaultdict

import numpy as np
from asammdf import MDF

TIME_CHANNELS = {"t", "time"}


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


def main(path_a, path_b):
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


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
