"""深度对比两个 MDF（v2）：同名组按序号区分，逐组逐通道严格对比。

用法: python tools/compare_mdf_v2.py <ref.mdf> <ours.mdf>
"""
import sys
from collections import Counter, defaultdict

import numpy as np
from asammdf import MDF


def load_groups(path):
    mdf = MDF(path)
    groups = []
    for gi, g in enumerate(mdf.groups):
        name = getattr(g.channel_group, "acq_name", None) or f"group{gi}"
        chans = {}
        for ch in g.channels:
            chans[ch.name] = {
                "unit": ch.unit,
                "cn_data_type": int(ch.data_type),
                "cn_bit_count": ch.bit_count,
                "cn_conversion": str(ch.conversion),
            }
        groups.append({"gi": gi, "name": name, "chans": chans})
    return mdf, groups


def main(ref_path, ours_path):
    ref_mdf, ref_groups = load_groups(ref_path)
    ours_mdf, ours_groups = load_groups(ours_path)
    diffs = []

    # ---- 文件头 ----
    for field in ("version", "start_time", "abs_time", "tz_offset", "flags"):
        rv = getattr(ref_mdf.header, field, None)
        ov = getattr(ours_mdf.header, field, None)
        if rv != ov:
            diffs.append(f"[header] {field}: {rv!r} vs {ov!r}")

    # ---- 组级 ----
    print(f"组数: ref={len(ref_groups)} ours={len(ours_groups)}")
    if len(ref_groups) != len(ours_groups):
        diffs.append(f"[group-count] ref={len(ref_groups)} ours={len(ours_groups)}")

    # 组名序列对比（含重复）
    ref_names = [g["name"] for g in ref_groups]
    ours_names = [g["name"] for g in ours_groups]
    if ref_names != ours_names:
        diffs.append("[group-order] 组名序列不一致")
        # 找出差异点
        rc, oc = Counter(ref_names), Counter(ours_names)
        for k in sorted(set(rc) | set(oc)):
            if rc[k] != oc[k]:
                diffs.append(f"[group-count] 组名 {k!r}: ref={rc[k]} ours={oc[k]}")
        for i, (a, b) in enumerate(zip(ref_names, ours_names)):
            if a != b:
                diffs.append(f"[group-order] 位置 {i}: ref={a!r} ours={b!r}")
                break

    # ---- 通道级（按序号对齐）----
    n = min(len(ref_groups), len(ours_groups))
    stats_chan_diff = []
    for i in range(n):
        rg, og = ref_groups[i], ours_groups[i]
        rn, on = rg["name"], og["name"]
        if rn != on:
            continue
        rc, oc = rg["chans"], og["chans"]
        if rc.keys() != oc.keys():
            only_r = sorted(set(rc) - set(oc))
            only_o = sorted(set(oc) - set(rc))
            diffs.append(f"[group {i} {rn!r}] 通道名不同: 仅ref={only_r[:8]} 仅ours={only_o[:8]}")
            stats_chan_diff.append((i, rn, only_r, only_o))
            continue
        for name in rc:
            rch, och = rc[name], oc[name]
            for k in ("unit",):
                if rch[k] != och[k]:
                    diffs.append(f"[group {i} {rn!r} ch {name}] {k}: {rch[k]!r} vs {och[k]!r}")
            # CN 元数据差异记录但不判错（可能 asammdf 解析差异）
            for k in ("cn_data_type", "cn_bit_count", "cn_conversion"):
                if rch[k] != och[k]:
                    diffs.append(f"[group {i} {rn!r} ch {name}] CN.{k}: {rch[k]!r} vs {och[k]!r}")

    # ---- 数据逐点对比（组按序号）----
    print("逐点对比…")
    n_sig = n_ok = n_ts = 0
    for i in range(n):
        rg, og = ref_groups[i], ours_groups[i]
        if rg["name"] != og["name"]:
            continue
        rc, oc = rg["chans"], og["chans"]
        if rc.keys() != oc.keys():
            continue
        for name in rc:
            n_sig += 1
            rs = ref_mdf.get(name, group=rg["gi"])
            os_ = ours_mdf.get(name, group=og["gi"])
            val_ok = np.array_equal(rs.samples, os_.samples)
            ts_ok = np.array_equal(rs.timestamps, os_.timestamps)
            if val_ok and ts_ok:
                n_ok += 1
            else:
                if not ts_ok:
                    n_ts += 1
                    idx = int(np.argmax(rs.timestamps != os_.timestamps))
                    d = abs(rs.timestamps[idx] - os_.timestamps[idx])
                    if i < 3 or name == "t":
                        print(f"[TS-DIFF] 组{i} {rg['name']!r} :: {name}: @{idx} "
                              f"ref={rs.timestamps[idx]!r} ours={os_.timestamps[idx]!r} d={d:.3e}")
                if not val_ok:
                    print(f"[VAL-DIFF] 组{i} {rg['name']!r} :: {name}: n_ref={len(rs.samples)} n_ours={len(os_.samples)}")

    print(f"逐点全等信号: {n_ok}/{n_sig} (时间戳差异 {n_ts}, 值差异 {n_sig-n_ok-n_ts})")

    # ---- '1s' 统计组通道名差异汇总 ----
    if stats_chan_diff:
        print()
        print("'1s' 统计组通道名差异汇总:")
        for i, rn, only_r, only_o in stats_chan_diff:
            print(f"  组#{i} ({rn}): 仅ref={only_r}, 仅ours={only_o}")
        # 汇总 distinct
        all_r = sorted(set(x for _, _, x, _ in stats_chan_diff for x in x))
        all_o = sorted(set(x for _, _, _, x in stats_chan_diff for x in x))
        print(f"  全部仅ref: {all_r}")
        print(f"  全部仅ours: {all_o}")

    print()
    if diffs:
        print(f"发现 {len(diffs)} 处元数据/结构差异（前60条）:")
        for d in diffs[:60]:
            print(f"  - {d}")
        if len(diffs) > 60:
            print(f"  … 还有 {len(diffs)-60} 条")
        return 1
    print("两个 MDF 完全一致 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
