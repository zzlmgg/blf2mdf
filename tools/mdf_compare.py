"""对拍判定共享模块：自产 vs 自产 / 自产 vs CANoe 参考。

两个公开入口（差异为空列表 = 一致）：
- compare_files_identical(ref_path, ours_path, *, dims=..., atol=1e-12, rtol=0.0)
    自产 vs 自产：按组序号对齐，组序一致是硬性要求；逐通道精确逐位；
    浮点用「有效容差 atol」：nanmax 差 ≤ atol 视为一致（rtol 仅接口统一，不使用）。
- compare_files_reference(ref_path, ours_path, *, stats_ref_layout, dims=..., atol=1e-6, rtol=1e-6)
    自产 vs CANoe 参考：按信号集合匹配组；等长全量 isclose(equal_nan=True)；
    文本定宽重铸比较；信号时间戳逐位（需求线硬契约）；统计组逐点验收与 t 轴细查
    需要显式传入参考统计布局。

stats_ref_layout: tuple[int, dict[str, int]] —— (参考每通道统计项数, {统计项名: 组内偏移})，
必须覆盖 STAT_NAMES 全部 10 项且偏移 ∈ [0, 块大小)；违反则抛 ValueError（错位不静默）。
"""
from __future__ import annotations

import numpy as np
from asammdf import MDF

DIMS = frozenset({"header", "structure", "values", "stats"})
DEFAULT_DIMS = frozenset({"header", "structure", "values", "stats"})

# 自产 1s 统计组布局：通道 × 10 项（组序 = 本元组顺序）
STAT_NAMES = ("StdData", "StdDataRate", "ExtData", "ExtDataRate",
              "StdRemote", "StdRemoteRate", "ExtRemote", "ExtRemoteRate",
              "ErrorFrames", "ErrorFrameRate")

_TIME_CHANNELS = {"t", "time"}
_HEADER_FIELDS = ("version", "start_time", "abs_time", "tz_offset", "flags",
                  "author", "department", "project", "subject", "comment")
_CN_FIELDS = ("unit", "data_type", "bit_count", "bit_resolution", "flags", "conversion")


def _norm_bytes(v):
    """文本采样归一化：U→S、尾随 \\x00 剥离（compare_two_mdf.norm 语义）。"""
    v = np.asarray(v)
    if v.dtype.kind == "U":
        v = v.astype("S")
    if v.dtype.kind == "S":
        return np.char.rstrip(v, b"\x00")
    return v


def _text_equal(sa, sb):
    """定宽文本相等（deep_compare_latest 语义）：归一化后重铸为共同宽度再比。"""
    sa, sb = _norm_bytes(sa), _norm_bytes(sb)
    wa = sa.dtype.itemsize if sa.dtype.kind == "S" else 1
    wb = sb.dtype.itemsize if sb.dtype.kind == "S" else 1
    w = max(wa, wb)
    return np.array_equal(sa.astype(f"|S{w}"), sb.astype(f"|S{w}"))


def _load(mdf):
    """提取组骨架：acq_name / 通道名序 / 信号集 / 通道元数据。"""
    groups = []
    for gi, g in enumerate(mdf.groups):
        cg = g.channel_group
        names = [c.name for c in g.channels]
        groups.append({
            "gi": gi,
            "acq": cg.acq_name or "",
            "names": names,
            "signals": frozenset(n for n in names if n not in _TIME_CHANNELS),
            "meta": {c.name: {"unit": c.unit,
                              "data_type": int(c.data_type),
                              "bit_count": c.bit_count,
                              "bit_resolution": getattr(c, "bit_resolution", None),
                              "flags": getattr(c, "flags", None),
                              "conversion": str(c.conversion)}
                     for c in g.channels},
        })
    return groups


def _header_diffs(a, b):
    out = []
    ha, hb = a.header, b.header
    for f in _HEADER_FIELDS:
        rv, ov = getattr(ha, f, None), getattr(hb, f, None)
        if rv != ov:
            out.append(f"header.{f}: {rv!r} vs {ov!r}")
    return out


def _channel_meta_diffs(ma, mb, label):
    """同名通道元数据差异（通道名已对齐时调用）。"""
    out = []
    for name in ma:
        ca, cb = ma[name], mb[name]
        for f in _CN_FIELDS:
            if ca[f] != cb[f]:
                out.append(f"{label}.{name} {f}: {ca[f]!r} vs {cb[f]!r}")
    return out


def _structure_diffs_identical(ga, gb):
    out = []
    if len(ga) != len(gb):
        out.append(f"组数不同: {len(ga)} vs {len(gb)}")
    for gi, (a, b) in enumerate(zip(ga, gb)):
        if a["acq"] != b["acq"]:
            out.append(f"组 {gi}: 组名不同 {a['acq']!r} vs {b['acq']!r}")
        if a["names"] != b["names"]:
            out.append(f"组 {gi} {a['acq']!r}: 通道序不同 {a['names'][:8]}... vs {b['names'][:8]}...")
            continue
        out += _channel_meta_diffs(a["meta"], b["meta"], f"组 {gi} {a['acq']!r}")
    return out


def _values_diffs_identical(a, b, ga, gb, atol):
    out = []
    for gi, (g_a, g_b) in enumerate(zip(ga, gb)):
        if g_a["names"] != g_b["names"]:
            continue
        for name in g_a["names"]:
            label = f"组 {gi} {g_a['acq']!r}.{name}"
            sa_raw = np.asarray(a.get(name, group=g_a["gi"]).samples)
            sb_raw = np.asarray(b.get(name, group=g_b["gi"]).samples)
            if sa_raw.dtype != sb_raw.dtype:
                out.append(f"{label}: dtype {sa_raw.dtype} vs {sb_raw.dtype}")
                continue
            sa, sb = _norm_bytes(sa_raw), _norm_bytes(sb_raw)
            if np.array_equal(sa, sb):
                continue
            if np.issubdtype(sa.dtype, np.floating):
                diff = float(np.nanmax(np.abs(sa - sb))) if sa.size else 0.0
                if np.isnan(diff) or diff > atol:
                    out.append(f"{label}: 采样不一致 maxdiff={diff}")
            else:
                out.append(f"{label}: 采样不一致 ({sa.size} vs {sb.size})")
    return out


def _stats_diffs_identical(a, b, ga, gb):
    """显式统计组判定：'1s' 组数 + t 轴精确（自产 vs 自产）。"""
    out = []
    ones_a = [g for g in ga if g["acq"] == "1s"]
    ones_b = [g for g in gb if g["acq"] == "1s"]
    if len(ones_a) != len(ones_b):
        out.append(f"1s 组数: {len(ones_a)} vs {len(ones_b)}")
    for i, (x, y) in enumerate(zip(ones_a, ones_b)):
        ta = np.asarray(a.get("t", group=x["gi"]).samples)
        tb = np.asarray(b.get("t", group=y["gi"]).samples)
        if not np.array_equal(ta, tb):
            out.append(f"1s 组 {i}: t 轴不一致")
    return out


def _structure_diffs_reference(ga, gb):
    out = []
    ia = {g["signals"]: g for g in ga if g["acq"] != "1s"}
    ib = {g["signals"]: g for g in gb if g["acq"] != "1s"}
    for names, g_a in ia.items():
        g_b = ib.get(names)
        if g_b is None:
            out.append(f"未匹配组(自产): {g_a['acq']!r} ({len(names)}信号)")
            continue
        label = f"组 {g_a['acq']!r}"
        if g_a["acq"] != g_b["acq"]:
            out.append(f"组名: 自产 {g_a['acq']!r} vs 参考 {g_b['acq']!r}")
        if len(g_a["names"]) != len(g_b["names"]):
            out.append(f"{label}: 通道数 {len(g_a['names'])} vs {len(g_b['names'])}")
        na = [n for n in g_a["names"] if n not in _TIME_CHANNELS]
        nb = [n for n in g_b["names"] if n not in _TIME_CHANNELS]
        if na != nb:
            out.append(f"{label}: 组内信号序不同 {na[:8]}... vs {nb[:8]}...")
        out += _channel_meta_diffs(g_a["meta"], g_b["meta"], label)
    miss_b = [g for g in gb if g["acq"] != "1s" and g["signals"] not in ia]
    for g in miss_b:
        out.append(f"未匹配组(参考): {g['acq']!r} ({len(g['signals'])}信号)")
    return out


def _values_diffs_reference(a, b, ga, gb, atol, rtol):
    out = []
    ia = {g["signals"]: g for g in ga if g["acq"] != "1s"}
    ib = {g["signals"]: g for g in gb if g["acq"] != "1s"}
    for names, g_a in ia.items():
        g_b = ib.get(names)
        if g_b is None:
            continue
        for name in g_a["signals"]:
            label = f"组 {g_a['acq']!r}.{name}"
            sig_a = a.get(name, group=g_a["gi"])
            sig_b = b.get(name, group=g_b["gi"])
            sa = np.asarray(sig_a.samples)
            sb = np.asarray(sig_b.samples)
            if len(sa) != len(sb):
                out.append(f"{label}: 长度 {len(sa)} vs {len(sb)}")
                continue
            # 信号时间戳逐位：CANoe 与自产同为整数 ns×1e-9 构造，逐位一致是需求线硬契约
            ta = np.asarray(sig_a.timestamps)
            tb = np.asarray(sig_b.timestamps)
            if not np.array_equal(ta, tb):
                n = int(np.count_nonzero(ta != tb))
                out.append(f"{label}: 时间戳不一致 ({n} 点)")
            if sa.dtype.kind in "OSU":
                if not _text_equal(sa, sb):
                    out.append(f"{label}: 文本不一致")
                continue
            if (np.issubdtype(sa.dtype, np.integer)
                    and np.issubdtype(sb.dtype, np.integer)):
                if not np.array_equal(sa.astype(np.int64), sb.astype(np.int64)):
                    out.append(f"{label}: 整型不一致")
                continue
            bad = int(np.sum(~np.isclose(sa.astype(np.float64), sb.astype(np.float64),
                                         atol=atol, rtol=rtol, equal_nan=True)))
            if bad:
                out.append(f"{label}: {bad} 点不一致")
    return out


def _stats_diffs_reference(a, b, ga, gb, layout):
    block_size, offsets = layout
    out = []
    ones_a = [g for g in ga if g["acq"] == "1s"]
    ones_b = [g for g in gb if g["acq"] == "1s"]
    if not ones_a or not ones_b:
        # 任一侧无统计组：只做组数比较（两侧都有时布局检查才有意义）
        if len(ones_a) != len(ones_b):
            out.append(f"1s 组数: 自产 {len(ones_a)} vs 参考 {len(ones_b)}")
        return out
    if len(ones_a) % len(STAT_NAMES):
        out.append(f"1s 组数(自产): {len(ones_a)} 非 {len(STAT_NAMES)} 的整数倍")
        return out
    nch = len(ones_a) // len(STAT_NAMES)
    if len(ones_b) < nch * block_size:
        out.append(f"1s 组数(参考): {len(ones_b)} < {nch * block_size}")
        return out
    # 自产统计布局一致性：每通道块 10 组，组内非时间信号序须等于 STAT_NAMES
    # （自产每组 1 个统计信号；仅结构真正漂移时报出）
    for ch in range(nch):
        seq = [next((n for n in ones_a[ch * len(STAT_NAMES) + i]["names"]
                     if n not in _TIME_CHANNELS), None)
               for i in range(len(STAT_NAMES))]
        if seq != list(STAT_NAMES):
            out.append(f"1s 组内信号序(自产): ch{ch} 与 STAT_NAMES 不符")
            break
    # 统计 t 轴：自产 ch0 StdData vs 参考 ch0 StdData（layout 显式偏移）。
    # 容差 1ns = 整数 ns 网格分辨率：起点带毫秒残值时两侧 float64 运算产生
    # ULP 级表示噪声（A19G1 实测 ≤3e-5ns），超出网格分辨率才算真实差异。
    ta = np.asarray(a.get("StdData", group=ones_a[0]["gi"]).timestamps)
    tb = np.asarray(b.get("StdData", group=ones_b[offsets["StdData"]]["gi"]).timestamps)
    if len(ta) != len(tb):
        out.append(f"统计 t 轴: 自产 n={len(ta)} vs 参考 n={len(tb)}")
    elif np.any(np.abs(ta - tb) > 1e-9):
        n = int(np.count_nonzero(ta != tb))
        out.append(f"统计 t 轴: {n} 点不一致 "
                   f"maxdiff={np.abs(ta - tb).max() * 1e9:.0f}ns")
    # 逐点验收：自产 ch×10+i ↔ 参考 ch×block+offset(name)
    for ch in range(nch):
        for i, name in enumerate(STAT_NAMES):
            g_a = ones_a[ch * len(STAT_NAMES) + i]
            g_b = ones_b[ch * block_size + offsets[name]]
            va = np.asarray(a.get(name, group=g_a["gi"]).samples)
            vb = np.asarray(b.get(name, group=g_b["gi"]).samples)
            if len(va) != len(vb):
                out.append(f"ch{ch} {name}: 采样数 {len(va)} vs {len(vb)}")
                continue
            if np.issubdtype(va.dtype, np.integer):
                bad = int(np.sum(va != vb))
            else:
                bad = int(np.sum(np.abs(va - vb) > 1e-9))
            if bad:
                out.append(f"ch{ch} {name}: {bad} 点不一致")
    return out


def _validate_layout(layout):
    block_size, offsets = layout
    missing = set(STAT_NAMES) - set(offsets)
    if missing:
        raise ValueError(f"stats_ref_layout 缺统计项: {sorted(missing)}")
    for k, v in offsets.items():
        if v < 0 or v >= block_size:
            raise ValueError(f"stats_ref_layout 偏移越界: {k}={v} (块大小 {block_size})")


def _check_dims(dims):
    bad = set(dims) - DIMS
    if bad:
        raise ValueError(f"非法维度: {sorted(bad)}（合法: {sorted(DIMS)}）")


def compare_files_identical(ref_path, ours_path, *, dims=DEFAULT_DIMS,
                            atol=1e-12, rtol=0.0):
    """自产 vs 自产：按组序号对齐，精确逐位（compare_two_mdf 语义升级）。"""
    _check_dims(dims)
    with MDF(ref_path) as a, MDF(ours_path) as b:
        ga, gb = _load(a), _load(b)
        out = []
        if "header" in dims:
            out += _header_diffs(a, b)
        if "structure" in dims:
            out += _structure_diffs_identical(ga, gb)
        if "values" in dims:
            out += _values_diffs_identical(a, b, ga, gb, atol)
        if "stats" in dims:
            out += _stats_diffs_identical(a, b, ga, gb)
        return out


def compare_files_reference(ref_path, ours_path, *, stats_ref_layout,
                            dims=DEFAULT_DIMS, atol=1e-6, rtol=1e-6):
    """自产 vs CANoe 参考：按信号集合匹配 + 等长全量容差 + 统计逐点验收。"""
    _check_dims(dims)
    _validate_layout(stats_ref_layout)
    with MDF(ref_path) as a, MDF(ours_path) as b:
        ga, gb = _load(a), _load(b)
        out = []
        if "header" in dims:
            out += _header_diffs(a, b)
        if "structure" in dims:
            out += _structure_diffs_reference(ga, gb)
        if "values" in dims:
            out += _values_diffs_reference(a, b, ga, gb, atol, rtol)
        if "stats" in dims:
            out += _stats_diffs_reference(a, b, ga, gb, stats_ref_layout)
        return out
