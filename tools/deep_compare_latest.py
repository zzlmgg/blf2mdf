"""深度结构对比：最新自产输出 vs CANoe 参考（组序/通道序/元数据/全量数值）。"""
import sys
import numpy as np
from asammdf import MDF

TIME_CHANNELS = {"t", "time"}


def dump_structure(mdf, label):
    print(f"\n########## {label}: {mdf.version} / groups={len(mdf.groups)}")
    print(f"header.start_time = {mdf.header.start_time!r}")
    print(f"header.comment = {mdf.header.comment!r}")
    print(f"header.author = {mdf.header.author!r}")
    print(f"header.department = {mdf.header.department!r}")
    print(f"header.project = {mdf.header.project!r}")
    print(f"header.subject = {mdf.header.subject!r}")
    print(f"header.abs_time = {mdf.header.abs_time!r}  flags={mdf.header.flags}  tz={mdf.header.tz_offset}")
    n_stat = 0
    for gi, g in enumerate(mdf.groups):
        cg = g.channel_group
        names = [c.name for c in g.channels]
        tnames = [c.name for c in g.channels if c.name in TIME_CHANNELS]
        if (cg.acq_name or "") == "1s":
            n_stat += 1
            continue
        print(f"  G{gi:3d} {cg.acq_name!r} channels({len(names)})={names[:6]}{'...' if len(names) > 6 else ''} "
              f"t_pos={names.index(tnames[0]) if tnames else '-'}")
    print(f"  ... 1s 统计组数: {n_stat}")


def full_value_compare(a, b):
    """匹配组全信号全长度数值对比。"""
    ia = {frozenset(c.name for c in g.channels if c.name not in TIME_CHANNELS): gi
          for gi, g in enumerate(a.groups) if (g.channel_group.acq_name or "") != "1s"}
    ib = {frozenset(c.name for c in g.channels if c.name not in TIME_CHANNELS): gi
          for gi, g in enumerate(b.groups) if (g.channel_group.acq_name or "") != "1s"}
    total_bad, total_checked, sigs = 0, 0, 0
    for names, gia in ia.items():
        gib = ib.get(names)
        if gib is None:
            continue
        for name in names:
            sa, sb = a.get(name, group=gia), b.get(name, group=gib)
            va, vb = np.asarray(sa.samples), np.asarray(sb.samples)
            if va.dtype.kind in "OSU":
                if va.dtype.kind == "U":
                    va = va.astype("S").astype(f"|S{max(va.dtype.itemsize, 1)}")
                # |Sn 定宽可能不同：rstrip 后比较
                av = np.char.rstrip(va.astype(f"|S{max(va.dtype.itemsize, vb.dtype.itemsize)}"), b"\x00")
                bv = np.char.rstrip(vb.astype(f"|S{max(va.dtype.itemsize, vb.dtype.itemsize)}"), b"\x00")
                bad = int(np.sum(av != bv)) if len(va) == len(vb) else -1
            else:
                if len(va) != len(vb):
                    bad = -1
                elif np.issubdtype(va.dtype, np.integer) and np.issubdtype(vb.dtype, np.integer):
                    bad = int(np.sum(va.astype(np.int64) != vb.astype(np.int64)))
                else:
                    bad = int(np.sum(~np.isclose(va.astype(np.float64), vb.astype(np.float64),
                                                 atol=1e-6, rtol=1e-6, equal_nan=True)))
            total_checked += 1
            sigs += 1
            if bad != 0:
                total_bad += max(bad, 1)
                print(f"  MISMATCH {name}: n={len(va)}/{len(vb)} bad={bad} "
                      f"dtype={va.dtype}/{vb.dtype} first=[{va[0] if len(va) else None}|{vb[0] if len(vb) else None}]")
    print(f"\n全量数值对比: {sigs} 信号 {total_checked} 组内全部检查, 不一致信号数={total_bad}")


def stats_deep(a, b):
    """统计组结构对比：组序、组内信号序、t 轴。"""
    ones_a = [(gi, g) for gi, g in enumerate(a.groups) if (g.channel_group.acq_name or "") == "1s"]
    ones_b = [(gi, g) for gi, g in enumerate(b.groups) if (g.channel_group.acq_name or "") == "1s"]
    print(f"\n1s 组: 自产 {len(ones_a)}（G{ones_a[0][0]}..G{ones_a[-1][0]}） vs 参考 {len(ones_b)}（G{ones_b[0][0]}..G{ones_b[-1][0]}）")
    print(f"参考 1s 组内信号序: {[c.name for c in ones_b[0][1].channels]}")
    print(f"自产 1s 组内信号序: {[c.name for c in ones_a[0][1].channels]}")
    # 参考统计组序（每 22 一组，看通道顺序）
    first22 = [(gi, g) for gi, g in ones_b[:22]]
    print(f"参考 1s 前 22 组的 时间通道: {[g.channels[0].name for _, g in first22]}")
    # t 轴对比
    ta = np.asarray(a.get("StdData", group=ones_a[0][0]).timestamps)
    # 参考 1s 组：ch×22+idx，StdData 在 idx 4（ch=0 → 组 4）
    tb = np.asarray(b.get("StdData", group=ones_b[4][0]).timestamps)
    print(f"统计 t 轴: 自产 n={len(ta)} [0]={ta[0]} [-1]={ta[-1]} | 参考 n={len(tb)} [0]={tb[0]} [-1]={tb[-1]}")


def order_compare(a, b):
    """匹配组内信号顺序对比 + 时间通道元数据对比。"""
    ia = {frozenset(c.name for c in g.channels if c.name not in TIME_CHANNELS): gi
          for gi, g in enumerate(a.groups) if (g.channel_group.acq_name or "") != "1s"}
    ib = {frozenset(c.name for c in g.channels if c.name not in TIME_CHANNELS): gi
          for gi, g in enumerate(b.groups) if (g.channel_group.acq_name or "") != "1s"}
    order_diff = []
    for names, gia in ia.items():
        gib = ib.get(names)
        if gib is None:
            continue
        na = [c.name for c in a.groups[gia].channels if c.name not in TIME_CHANNELS]
        nb = [c.name for c in b.groups[gib].channels if c.name not in TIME_CHANNELS]
        if na != nb:
            order_diff.append((names, na, nb))
    print(f"\n组内信号顺序不一致的组: {len(order_diff)}/{len(ia)}")
    for names, na, nb in order_diff[:15]:
        print(f"  {sorted(names)[:3]}...")
        print(f"    自产: {na[:8]}{'...' if len(na) > 8 else ''}")
        print(f"    参考: {nb[:8]}{'...' if len(nb) > 8 else ''}")
    # 时间通道元数据（取一个匹配组）
    first = next(iter(ia))
    gia, gib = ia[first], ib[first]
    ca = [c for c in a.groups[gia].channels if c.name in TIME_CHANNELS][0]
    cb = [c for c in b.groups[gib].channels if c.name in TIME_CHANNELS][0]
    print(f"\n时间通道元数据（组 {sorted(first)[:3]}...）:")
    for c, lab in ((ca, "自产"), (cb, "参考")):
        print(f"  {lab}: name={c.name} type={c.data_type} bit_res={getattr(c, 'bit_resolution', None)} "
              f"unit={c.unit!r} flags={getattr(c, 'flags', None)}")
    # 信号通道元数据抽查（首信号）
    sa = a.groups[gia].channels[0 if ca != a.groups[gia].channels[0] else 1]
    sb = b.groups[gib].channels[0 if cb != b.groups[gib].channels[0] else 1]
    print(f"  首信号通道: 自产 {sa.name} type={sa.data_type} unit={sa.unit!r} | "
          f"参考 {sb.name} type={sb.data_type} unit={sb.unit!r}")


if __name__ == "__main__":
    a = MDF(sys.argv[1])
    b = MDF(sys.argv[2])
    dump_structure(a, "自产")
    dump_structure(b, "参考")
    print("\n" + "=" * 100)
    stats_deep(a, b)
    print("=" * 100)
    order_compare(a, b)
    print("=" * 100)
    full_value_compare(a, b)
    a.close()
    b.close()
