"""深度对比两个 MDF 文件：文件头、通道组结构、信号数据逐点比较。

用法: python tools/compare_mdf_deep.py <ref.mdf> <ours.mdf>
"""
import sys

import numpy as np
from asammdf import MDF


def load_summary(path):
    mdf = MDF(path)
    info = {
        "path": path,
        "version": mdf.version,
        "start_time": mdf.header.start_time,
        "abs_time_ns": getattr(mdf.header, "abs_time", None),
        "tz_offset": getattr(mdf.header, "tz_offset", None),
        "flags": getattr(mdf.header, "flags", None),
        "author": mdf.header.author,
        "department": mdf.header.department,
        "project": mdf.header.project,
        "subject": mdf.header.subject,
        "comment": mdf.header.comment,
        "groups": [],
    }
    for gi, g in enumerate(mdf.groups):
        acq_name = getattr(g.channel_group, "acq_name", None) or f"group{gi}"
        grp = {"gi": gi, "acq_name": acq_name, "channels": {}}
        for ch in g.channels:
            grp["channels"][ch.name] = {
                "unit": ch.unit,
                "dtype": str(ch.data_type),
            }
        info["groups"].append(grp)
    return info, mdf


def compare(reference_path, ours_path):
    ref, ref_mdf = load_summary(reference_path)
    ours, ours_mdf = load_summary(ours_path)
    diffs = []

    # ---- 文件头 ----
    print("=" * 72)
    print("文件头对比")
    print("=" * 72)
    for field in ("version", "start_time", "abs_time_ns", "tz_offset", "flags",
                  "author", "department", "project", "subject", "comment"):
        rv, ov = ref[field], ours[field]
        mark = "OK " if rv == ov else "DIFF"
        print(f"[{mark}] {field:14s} ref={rv!r}")
        if rv != ov:
            print(f"           ours={ov!r}")
            diffs.append(f"header.{field}: {rv!r} vs {ov!r}")

    # ---- 组结构 ----
    print()
    print("=" * 72)
    print("通道组结构")
    print("=" * 72)
    ref_groups = {g["acq_name"]: g for g in ref["groups"]}
    ours_groups = {g["acq_name"]: g for g in ours["groups"]}
    ref_keys, ours_keys = set(ref_groups), set(ours_groups)
    print(f"组数: ref={len(ref_keys)} ours={len(ours_keys)}")
    only_ref = ref_keys - ours_keys
    only_ours = ours_keys - ref_keys
    if only_ref:
        print(f"仅 ref 有的组: {sorted(only_ref)}")
        diffs.append(f"groups only in ref: {sorted(only_ref)}")
    if only_ours:
        print(f"仅 ours 有的组: {sorted(only_ours)}")
        diffs.append(f"groups only in ours: {sorted(only_ours)}")

    for key in sorted(ref_keys & ours_keys):
        rg, og = ref_groups[key], ours_groups[key]
        rc, oc = rg["channels"], og["channels"]
        if set(rc) == set(oc) and len(rc) > 0 and len(oc) > 0:
            mark = "OK "
        else:
            mark = "DIFF"
        print(f"[{mark}] 组 {key!r}: ref {len(rc)} 通道, ours {len(oc)} 通道")
        if set(rc) != set(oc):
            print(f"    仅 ref: {sorted(set(rc) - set(oc))}")
            print(f"    仅 ours: {sorted(set(oc) - set(rc))}")
            diffs.append(f"group {key}: channel set differs")
        for name in sorted(set(rc) & set(oc)):
            rch, och = rc[name], oc[name]
            marks = []
            if rch["unit"] != och["unit"]:
                marks.append(f"unit {rch['unit']!r} vs {och['unit']!r}")
            if rch["dtype"] != och["dtype"]:
                marks.append(f"dtype {rch['dtype']} vs {och['dtype']}")
            if marks:
                print(f"    [DIFF] {name}: {'; '.join(marks)}")
                diffs.append(f"group {key} ch {name}: {'; '.join(marks)}")

    # ---- 信号数据逐点对比 ----
    print()
    print("=" * 72)
    print("信号数据逐点对比")
    print("=" * 72)
    n_sig_total = n_sig_equal = 0
    for key in sorted(ref_keys & ours_keys):
        rg, og = ref_groups[key], ours_groups[key]
        if set(rg["channels"]) != set(og["channels"]):
            continue
        for name in rg["channels"]:
            n_sig_total += 1
            rs = ref_mdf.get(name, group=rg["gi"])
            os_ = ours_mdf.get(name, group=og["gi"])
            ts_ok = np.array_equal(rs.timestamps, os_.timestamps)
            try:
                val_ok = np.array_equal(rs.samples, os_.samples)
            except Exception as e:
                val_ok = f"compare err: {e}"
            if ts_ok and val_ok is True:
                n_sig_equal += 1
                print(f"[OK ] {key} :: {name}: n={len(rs.samples)}")
            else:
                print(f"[DIFF] {key} :: {name}: n_ref={len(rs.samples)} n_ours={len(os_.samples)}")
                if not ts_ok:
                    idx = int(np.argmax(rs.timestamps != os_.timestamps))
                    print(f"      timestamps 首个差异 @{idx}: "
                          f"ref={rs.timestamps[idx]!r} ours={os_.timestamps[idx]!r}")
                    if idx > 0:
                        print(f"      前一点 ref={rs.timestamps[idx-1]!r} ours={os_.timestamps[idx-1]!r}")
                if val_ok is not True:
                    if isinstance(val_ok, str):
                        print(f"      values: {val_ok}")
                    else:
                        idx = int(np.argmax(rs.samples != os_.samples))
                        print(f"      values 首个差异 @{idx}: "
                              f"ref={rs.samples[idx]!r} ours={os_.samples[idx]!r}")
                diffs.append(f"group {key} ch {name}: data differs")

    print()
    print(f"逐点相等信号: {n_sig_equal}/{n_sig_total}")

    print()
    if diffs:
        print(f"发现 {len(diffs)} 处差异:")
        for d in diffs:
            print(f"  - {d}")
        return 1
    print("两个 MDF 完全一致 ✓")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    try:
        sys.exit(compare(sys.argv[1], sys.argv[2]))
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
