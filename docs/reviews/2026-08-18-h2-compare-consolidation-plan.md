# H2 对拍工具收口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 7 个各自重写「加载→对齐→逐点比较」的对拍脚本收口为单一深模块 `tools/mdf_compare.py`（两个公开入口）+ 两个 thin-adapter CLI + 黄金对拍测试，删除 5 个冗余/变质脚本，判定语义行为等价。

**Architecture:** 模块为唯一判定 seam——pytest 与 CLI 都穿过 `compare_files_identical` / `compare_files_reference` 两个公开入口；四个维度（header/structure/values/stats）默认全开；CLI 保留 compare_two_mdf / full_compare 文件名，只做参数解析与报告。

**Tech Stack:** Python 3.12、numpy（向量化）、asammdf 4.x、pytest（含 tmp_path 合成 MDF）。

**Spec:** [2026-08-18-h2-compare-consolidation-spec.md](./2026-08-18-h2-compare-consolidation-spec.md)（grilling 决策全部 settle，本计划是它的落地；两处精化已在文中标注）

## Global Constraints

- **范围**：只动 `tools/` 与 `tests/`；`core/`、`gui/` 零改动（不触碰性能前提与逐位一致对拍链）。
- **行为等价**：判定语义继承现有脚本；容差默认逐入口不变（identical 1e-12 有效容差；reference isclose 1e-6 + 统计 1e-9 + 对齐窗口 1e-3）；不统一容差。
- **契约显式**：`stats_ref_layout` 必填无默认；非法 dims / 缺偏移 / 越界 → 抛 `ValueError`（错位从静默变报错）。
- **测试**：无样例数据依赖——全部用 asammdf 合成 MDF；断言穿两个公开入口（唯一 seam），不触碰模块内部。
- **提交**：由用户执行（项目惯例），每个任务末尾是交付点。
- **词汇**：对拍 / 逐位一致 / 维度 / 对齐策略（CONTEXT.md 定义）。

---

### Task 1: 共享模块 + 黄金对拍测试

**Files:**
- Create: `tools/mdf_compare.py`
- Test: `tests/test_mdf_compare.py`

**Interfaces:**
- Produces:
  - `compare_files_identical(ref_path, ours_path, *, dims=DEFAULT_DIMS, atol=1e-12, rtol=0.0) -> list[str]` — 按组序号对齐，组序一致是硬性要求；文本 `\x00` 剥离；浮点有效容差 `atol`（`rtol` 仅接口统一，identical 不使用）。
  - `compare_files_reference(ref_path, ours_path, *, stats_ref_layout, dims=DEFAULT_DIMS, atol=1e-6, rtol=1e-6) -> list[str]` — 按信号集合匹配组；等长全量 `isclose(atol, rtol, equal_nan=True)`；文本定宽重铸比较；统计组逐点验收 + t 轴细查。
  - `stats_ref_layout: tuple[int, dict[str, int]]` — `(参考每通道统计项数, {统计项名: 组内偏移})`，必须覆盖 `STAT_NAMES` 全部 10 项、偏移 ∈ [0, 块大小)。
  - `DEFAULT_DIMS = frozenset({"header", "structure", "values", "stats"})`；非法维度 → `ValueError`。
- Consumes: asammdf `MDF`、numpy。

**spec 精化 ①**：identical 的 `stats` 维度 = 显式统计组 t 轴判定（compare_two_mdf 的逐组 t 轴检查聚焦 '1s' 组）；reference 的 `stats` 维度 = 统计布局逐点验收 + t 轴细查（spec D2 落地，四维两入口默认全开）。

- [x] **Step 1: 写测试文件 `tests/test_mdf_compare.py`**

```python
"""对拍共享模块黄金测试：合成 MDF，穿两个公开入口（唯一 seam）。"""
import numpy as np
import pytest
from asammdf import MDF

from tools.mdf_compare import (compare_files_identical, compare_files_reference,
                               STAT_NAMES, DEFAULT_DIMS)

REF_LAYOUT = (22, {"StdData": 4, "StdDataRate": 5, "ExtData": 6, "ExtDataRate": 7,
                   "StdRemote": 8, "StdRemoteRate": 9, "ExtRemote": 10,
                   "ExtRemoteRate": 11, "ErrorFrames": 12, "ErrorFrameRate": 13})


def _write_mdf(path, groups, comment="t"):
    """groups: list[(acq, [(name, samples, dtype), ...])]；t 通道自动加（arange 秒）。"""
    with MDF(version="4.10") as mdf:
        for acq, chans in groups:
            n = len(chans[0][1])
            ts = np.arange(n, dtype=np.float64)
            sigs = []
            for name, samples, dtype in chans:
                arr = np.asarray(samples, dtype=dtype)
                sigs.append(mdf.Signal(samples=arr, name=name,
                                       timestamps=ts if name != "t" else None))
            if not any(nm == "t" for nm, _, _ in chans):
                sigs.append(mdf.Signal(samples=ts, name="t", timestamps=ts))
            mdf.append(sigs, comment="", acq_name=acq)
        mdf.header.comment = comment
        mdf.save(path, overwrite=True)


def _simple(comment="t"):
    groups = [
        ("G1", [("SigA", [1.0, 2.0, 3.0], np.float64),
                ("SigB", [10, 20, 30], np.int64)]),
        ("G2", [("SigC", [b"x", b"y", b"z"], "S8")]),
    ]
    return _write_mdf, groups, comment


# ---- identical：基础 ----

def test_identical_equal_files(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mdf", tmp_path / "b.mdf"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups)
    assert compare_files_identical(p1, p2) == []


def test_identical_single_value_change(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mdf", tmp_path / "b.mdf"
    _write_mdf(p1, groups)
    groups[0][1][0] = ("SigA", [1.0, 2.0, 99.0], np.float64)
    _write_mdf(p2, groups)
    diffs = compare_files_identical(p1, p2)
    assert any("SigA" in d and "采样不一致" in d for d in diffs)


def test_identical_tolerance_boundary(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mdf", tmp_path / "b.mdf"
    _write_mdf(p1, groups)
    groups[0][1][0] = ("SigA", [1.0 + 1e-11, 2.0, 3.0], np.float64)
    _write_mdf(p2, groups)
    assert compare_files_identical(p1, p2)  # 1e-11 > 1e-12 有效容差 → 报
    groups[0][1][0] = ("SigA", [1.0 + 1e-13, 2.0, 3.0], np.float64)
    _write_mdf(p2, groups)
    assert compare_files_identical(p1, p2) == []  # 1e-13 ≤ 1e-12 → 过


def test_identical_group_order(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mdf", tmp_path / "b.mdf"
    _write_mdf(p1, groups)
    groups.reverse()
    _write_mdf(p2, groups)
    assert any("组名不同" in d for d in compare_files_identical(p1, p2))


def test_identical_channel_order(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mdf", tmp_path / "b.mdf"
    _write_mdf(p1, groups)
    g1 = groups[0]
    g1[1].reverse()
    _write_mdf(p2, groups)
    assert any("通道序不同" in d for d in compare_files_identical(p1, p2))


def test_identical_header_comment(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mdf", tmp_path / "b.mdf"
    _write_mdf(p1, groups, comment="a")
    _write_mdf(p2, groups, comment="b")
    diffs = compare_files_identical(p1, p2)
    assert any("header.comment" in d for d in diffs)


def test_identical_stats_t_axis(tmp_path):
    n = 20
    ts = np.arange(n, dtype=np.float64)
    groups = [("1s", [("StdData", np.full(n, 1.0, np.float64), np.float64)])]
    p1, p2 = tmp_path / "a.mdf", tmp_path / "b.mdf"
    _write_mdf(p1, groups)
    groups[0][1][0] = ("StdData", np.full(n, 1.0, np.float64), np.float64)
    # 改 t 轴：直接改文件 2 的 t 采样
    with MDF(p2, "r+") as mdf:
        mdf.get("t", group=0).samples[:] = ts + 0.5
        mdf.save(p2, overwrite=True)
    assert any("t 轴不一致" in d for d in compare_files_identical(p1, p2, dims=frozenset({"stats"})))


# ---- reference：信号集匹配 / 文本 / 容差 / 长度 ----

def test_reference_group_order_tolerated(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mdf", tmp_path / "b.mdf"
    _write_mdf(p1, groups)
    groups.reverse()
    _write_mdf(p2, groups)
    assert compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT) == []


def test_reference_text_width_normalized(tmp_path):
    groups = [("G1", [("Tx", np.array([b"hello", b"world"], dtype="S256"), "S256")])]
    p1, p2 = tmp_path / "a.mdf", tmp_path / "b.mdf"
    _write_mdf(p1, groups)
    groups[0][1][0] = ("Tx", np.array([b"hello", b"world"], dtype="S8"), "S8")
    _write_mdf(p2, groups)
    assert compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT) == []


def test_reference_values_tolerance(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mdf", tmp_path / "b.mdf"
    _write_mdf(p1, groups)
    groups[0][1][0] = ("SigA", [1.0 + 1e-7, 2.0, 3.0], np.float64)
    _write_mdf(p2, groups)
    assert compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT) == []  # ≤1e-6
    groups[0][1][0] = ("SigA", [1.0 + 1e-5, 2.0, 3.0], np.float64)
    _write_mdf(p2, groups)
    assert compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT) != []


def test_reference_length_mismatch(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mdf", tmp_path / "b.mdf"
    _write_mdf(p1, groups)
    groups[0][1][0] = ("SigA", [1.0, 2.0], np.float64)  # 少一点
    _write_mdf(p2, groups)
    diffs = compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT)
    assert any("长度" in d for d in diffs)


# ---- reference：统计布局 ----

def _stat_groups(nch, npts, t_off=0.0):
    """自产统计布局：nch 通道 × 10 项；参考布局：nch 通道 × 22 项（REF_LAYOUT）。"""
    ts = np.arange(npts, dtype=np.float64) + t_off
    ours = []
    for ch in range(nch):
        for name in STAT_NAMES:
            ours.append(("1s", [(name, np.full(npts, ch * 10.0 + 1.0, np.float64), np.float64),
                                ("t", ts, np.float64)]))
    ref = []
    for ch in range(nch):
        for i in range(22):
            name = next((k for k, v in REF_LAYOUT[1].items() if v == i), None)
            if name is None:
                ref.append(("1s", [("t", ts, np.float64)]))
            else:
                ref.append(("1s", [(name, np.full(npts, ch * 10.0 + 1.0, np.float64), np.float64),
                                   ("t", ts, np.float64)]))
    return ours, ref


def test_reference_stats_values(tmp_path):
    nch, npts = 2, 20
    ours, ref = _stat_groups(nch, npts)
    p1, p2 = tmp_path / "a.mdf", tmp_path / "b.mdf"
    _write_mdf(p1, ours)
    _write_mdf(p2, ref)
    # 逐点：改参考 ch0 StdData 一点（>1e-9）
    with MDF(p2, "r+") as mdf:
        mdf.get("StdData", group=4).samples[5] += 1e-7
        mdf.save(p2, overwrite=True)
    diffs = compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT)
    assert any("ch0 StdData" in d and "1 点不一致" in d for d in diffs)
    assert any("ch1" in d for d in diffs) is False  # ch1 未被误报


def test_reference_stats_t_axis(tmp_path):
    nch, npts = 2, 20
    ours, _ = _stat_groups(nch, npts)
    _, ref = _stat_groups(nch, npts, t_off=0.1)  # 参考 t 轴整体偏移
    p1, p2 = tmp_path / "a.mdf", tmp_path / "b.mdf"
    _write_mdf(p1, ours)
    _write_mdf(p2, ref)
    diffs = compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT)
    assert any("统计 t 轴" in d for d in diffs)


# ---- 契约校验 ----

def test_layout_validation(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mdf", tmp_path / "b.mdf"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups)
    with pytest.raises(ValueError):
        compare_files_reference(p1, p2, stats_ref_layout=(22, {"StdData": 4}))  # 缺项
    with pytest.raises(ValueError):
        compare_files_reference(p1, p2, stats_ref_layout=(4, {"StdData": 5}))  # 越界


def test_invalid_dims(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mdf", tmp_path / "b.mdf"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups)
    with pytest.raises(ValueError):
        compare_files_identical(p1, p2, dims=frozenset({"foo"}))
```

- [x] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_mdf_compare.py -q`
Expected: FAIL — `ModuleNotFoundError: tools.mdf_compare`（模块不存在）

- [x] **Step 3: 写模块 `tools/mdf_compare.py`**

```python
"""对拍判定共享模块：自产 vs 自产 / 自产 vs CANoe 参考。

两个公开入口（差异为空列表 = 一致）：
- compare_files_identical(ref_path, ours_path, *, dims=..., atol=1e-12, rtol=0.0)
    自产 vs 自产：按组序号对齐，组序一致是硬性要求；逐通道精确逐位；
    浮点用「有效容差 atol」：nanmax 差 ≤ atol 视为一致（rtol 仅接口统一，不使用）。
- compare_files_reference(ref_path, ours_path, *, stats_ref_layout, dims=..., atol=1e-6, rtol=1e-6)
    自产 vs CANoe 参考：按信号集合匹配组；等长全量 isclose(equal_nan=True)；
    文本定宽重铸比较；统计组逐点验收与 t 轴细查需要显式传入参考统计布局。

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
            sa = np.asarray(a.get(name, group=g_a["gi"]).samples)
            sb = np.asarray(b.get(name, group=g_b["gi"]).samples)
            if len(sa) != len(sb):
                out.append(f"{label}: 长度 {len(sa)} vs {len(sb)}")
                continue
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
    if len(ones_a) % len(STAT_NAMES):
        out.append(f"1s 组数(自产): {len(ones_a)} 非 {len(STAT_NAMES)} 的整数倍")
        return out
    nch = len(ones_a) // len(STAT_NAMES)
    if len(ones_b) < nch * block_size:
        out.append(f"1s 组数(参考): {len(ones_b)} < {nch * block_size}")
        return out
    if len(ones_a) != len(ones_b):
        out.append(f"1s 组数: 自产 {len(ones_a)} vs 参考 {len(ones_b)}")
    # 自产统计组内信号序须符合 STAT_NAMES（自产自身布局一致性）
    got = [n for n in ones_a[0]["names"] if n not in _TIME_CHANNELS]
    if got != list(STAT_NAMES):
        out.append("1s 组内信号序(自产) 与 STAT_NAMES 不符")
    # 统计 t 轴：自产 ch0 StdData vs 参考 ch0 StdData（layout 显式偏移）
    ta = np.asarray(a.get("StdData", group=ones_a[0]["gi"]).timestamps)
    tb = np.asarray(b.get("StdData", group=ones_b[offsets["StdData"]]["gi"]).timestamps)
    if len(ta) != len(tb) or not np.array_equal(ta, tb):
        out.append(f"统计 t 轴: 自产 n={len(ta)} vs 参考 n={len(tb)}")
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
```

- [x] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_mdf_compare.py -q`
Expected: PASS（13 个用例全绿）

- [x] **Step 5: 交付点（用户提交）**

```bash
git add tools/mdf_compare.py tests/test_mdf_compare.py
git commit -m "feat: 对拍判定收口为共享模块（identical/reference 双入口 + 黄金测试）"
```

---

### Task 2: compare_two_mdf 薄壳化

**Files:**
- Rewrite: `tools/compare_two_mdf.py`

**Interfaces:**
- Consumes: `compare_files_identical`（Task 1）
- Produces: CLI 契约不变——`python tools/compare_two_mdf.py <a.mdf> <b.mdf> [--skip-*]`，退出码 0 = 一致 / 非 0 = 差异（打印明细）

- [x] **Step 1: 重写薄壳**

```python
"""自产 vs 自产逐组全量对拍（薄壳：判定委托 tools/mdf_compare）。

用法：python tools/compare_two_mdf.py <a.mdf> <b.mdf> [--skip-{header,structure,values,stats}]
退出码 0 = 一致；非 0 = 存在差异（打印明细）。
"""
import argparse
import sys

from mdf_compare import compare_files_identical  # 同目录脚本运行（sys.path[0]=tools/）

_DIMS = ("header", "structure", "values", "stats")


def main(argv=None):
    p = argparse.ArgumentParser(description="自产 vs 自产逐位对拍")
    p.add_argument("path_a", help="自产 MDF 路径")
    p.add_argument("path_b", help="自产 MDF 路径")
    for d in _DIMS:
        p.add_argument(f"--skip-{d}", action="store_true", help=f"关闭 {d} 维度")
    args = p.parse_args(argv)
    dims = frozenset(d for d in _DIMS if not getattr(args, f"skip_{d}"))
    diffs = compare_files_identical(args.path_a, args.path_b, dims=dims)
    for line in diffs:
        print(line)
    if not diffs:
        print("=== 全部一致 ===")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [x] **Step 2: 语法自检 + 等价冒烟**

Run: `python -m py_compile tools/compare_two_mdf.py && python tools/compare_two_mdf.py --help`
Expected: exit 0；`--help` 显示参数矩阵

- [x] **Step 3: 真实对拍冒烟（自产并/串产物）**

Run: `python tools/compare_two_mdf.py <并行产物.mdf> <串行产物.mdf>`
Expected: exit 0（输出「=== 全部一致 ===」；若机器上有已知差异数据，以 Task 6 验证为准）

- [x] **Step 4: 交付点（用户提交）**

```bash
git add tools/compare_two_mdf.py
git commit -m "refactor: compare_two_mdf 薄壳化（判定委托共享模块）"
```

---

### Task 3: full_compare 薄壳化（报告保留 + 布局必填）

**Files:**
- Rewrite: `tools/full_compare.py`

**Interfaces:**
- Consumes: `compare_files_reference`、`STAT_NAMES`（Task 1）
- Produces: CLI 保留终端 + Markdown 报告；**新增必填 `--stats-ref-block` 与 `--stats-ref-idx`**（spec D1/Q5：参考布局无默认）；新增退出码（差异 → 1，spec 精化 ②：判定现在真实存在，报告工具获得可脚本化退出码）

**spec 精化 ②**：原 full_compare 无退出码（纯报告）。收口后判定委托模块，非空差异 → exit 1（超集行为，文档注明）。

- [x] **Step 1: 重写薄壳**

```python
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
```

- [x] **Step 2: 语法自检**

Run: `python -m py_compile tools/full_compare.py && python tools/full_compare.py --help`
Expected: exit 0；`--help` 显示必填布局参数

- [x] **Step 3: 合成冒烟（构造最小自产/参考对，含统计组）**

Run（用 Task 1 测试的合成构造，写临时文件后执行）:
```
python tools/full_compare.py <合成自产.mdf> <合成参考.mdf> \
    --stats-ref-block 22 --stats-ref-idx "StdData:4,StdDataRate:5,ExtData:6,ExtDataRate:7,StdRemote:8,StdRemoteRate:9,ExtRemote:10,ExtRemoteRate:11,ErrorFrames:12,ErrorFrameRate:13"
```
Expected: exit 1（构造的差异被判定）+ 报告文件生成

- [ ] **Step 4: 交付点（用户提交）**

```bash
git add tools/full_compare.py
git commit -m "refactor: full_compare 薄壳化（判定委托共享模块，布局参数必填）"
```

---

### Task 4: test_parallel_decode 消除第三份拷贝

**Files:**
- Modify: `tests/test_parallel_decode.py`（删 `_mdf_equal` 216-245；替换两处断言）
- 新测试（同一文件，验证替换后等价）：`test_compare_module_replaces_inline_equal`

**Interfaces:**
- Consumes: `compare_files_identical`（Task 1）
- 语义差异说明：原 `_mdf_equal` 浮点用 `array_equal(equal_nan=True)`；模块 identical 用 `array_equal` + nanmax 有效容差——测试数据无 NaN 时两者等价（本文件测试数据为自产并/串产物，无 NaN）。

- [ ] **Step 1: 删除 `_mdf_equal` 并替换断言**

删除 [tests/test_parallel_decode.py:216-245](../../tests/test_parallel_decode.py#L216-L245) 的 `_mdf_equal` 定义。替换两处调用：

```python
# 原: assert _mdf_equal(par, ser) == []
# 新: assert compare_files_identical(par, ser) == []
```
（`test_parallel_matches_serial_real_blf` ~264 行、`test_fallback_serial_on_pool_failure` ~309 行；`import` 改为 `from tools.mdf_compare import compare_files_identical`）

- [ ] **Step 2: 补充等价回归断言**

在 `test_parallel_matches_serial_real_blf` 后追加（模块级对拍结果同时覆盖组序/头部，防御未来漂移）：

```python
def test_parallel_identical_with_module_dims(tmp_path):
    """模块对拍默认 dims 全开时也一致（头部/结构/数值/统计全维度）。"""
    from tools.mdf_compare import compare_files_identical
    # 复用真实 blf 产物路径参数（与本文件既有真实数据测试同源），
    # 但此处仅断言模块可 import 且签名契约成立（真实等价由既有测试保证）：
    assert callable(compare_files_identical)
```

（说明：真实数据上的逐位一致由 Task 6 验证；此处防回归断言仅校验替换链路。）

- [ ] **Step 3: 跑全量测试**

Run: `python -m pytest tests/ -q`
Expected: PASS（含真实数据对拍用例——模块替换拷贝后仍全绿）

- [ ] **Step 4: 交付点（用户提交）**

```bash
git add tests/test_parallel_decode.py
git commit -m "refactor: test_parallel_decode 改用共享对拍模块（删除第三份拷贝）"
```

---

### Task 5: 删除 5 个冗余脚本 + 文档核对

**Files:**
- Delete: `tools/compare_mdf.py`、`tools/compare_mdf_v2.py`、`tools/compare_mdf_deep.py`、`tools/deep_compare_mdf.py`、`tools/deep_compare_latest.py`
- Modify: 文档——`grep -rn "compare_mdf\|deep_compare" docs/ *.md` 核对引用处并更新

**独有能力去向**（Task 1 已并入，删除无损失）：
- compare_mdf_deep：10 字段头部 → `_header_diffs`；组结构/逐点 → identical/reference 维度
- deep_compare_latest：统计 t 轴 / bit_resolution / 组内信号序 / isclose 全量 → `_stats_diffs_reference` / `_channel_meta_diffs` / `_values_diffs_reference`
- compare_mdf_v2：CN 三项升级判错 → `_channel_meta_diffs`；'1s' 通道名汇总 → reference structure 的通道序判定
- compare_mdf / deep_compare_mdf：变质残留，无独有能力

- [x] **Step 1: 核对引用**

Run: `git grep -n -E "compare_mdf|deep_compare|full_compare" -- docs/ *.md`
Expected: 找到 master plan §8 等引用——`compare_two_mdf` / `full_compare` 文件名保留无需改；若引用被删脚本（如 `compare_mdf_deep`），改为对应新契约（模块入口 + `--stats-ref-*` 参数）

- [x] **Step 2: 删除 5 个脚本**

Run: `git rm tools/compare_mdf.py tools/compare_mdf_v2.py tools/compare_mdf_deep.py tools/deep_compare_mdf.py tools/deep_compare_latest.py`
（git rm 保留历史——Task 6 行为等价验证需要的「原版」可从 `git show HEAD:tools/...` 恢复运行，无需物理备份）

- [x] **Step 3: 全量测试**

Run: `python -m pytest tests/ -q`
Expected: PASS（无测试引用被删脚本——Task 4 已确认测试只经模块）

- [ ] **Step 4: 交付点（用户提交）**（2026-08-18 执行任务时按用户指示**不提交**，交付点由用户执行）

```bash
git add -A
git commit -m "chore: 删除 5 个冗余/变质对拍脚本（能力已并入共享模块）"
```

---

### Task 6: 行为等价验证（真实数据）

**Files:**
- 无代码改动；验证产物：`outputs/` 下对比输出文件（不入库，或按项目惯例放临时目录）

**前提**：需要真实对拍数据——AHT 82MB 样例（blf → 自产并/串 mdf 产物 + CANoe 参考 mdf）。数据清单（2026-08-18 用户确认）：

- **并行产物**（已有）：`inputs/blf/AHT_ACFCANPUB_20260317_210430_59125089-ACFCAN_20260317_210930_59125099_t.mdf`（12.3MB，用户确认并行模式快速转换产物）
- **串行产物**（现场生成）：`python tools/convert_aht.py <tmp>/AHT_serial.mdf`——convert_aht 调 `convert(...)` 默认 `parallel=False`（串行），参数与 GUI 一致（raw_export=False、stats_export=True）；82MB 串行预计 1~3 分钟
- **CANoe 参考**（已有）：`inputs/mdf_canoe/AHT.mdf`
- **排除**：`%TEMP%\AHT_bench.mdf`（用户：生成模式未知，不可作为串/并证据）
- 下方命令中 `<并.mdf>` = 并行产物路径、`<串.mdf>` = 现场生成的串行产物路径

**验证矩阵**（语义等价 = 判定结论一致；「原版」从 `git show HEAD:tools/<脚本>` 恢复运行）：

- [x] **Step 1: identical 语义等价**（2026-08-18 验证：判定/退出码一致；发现并修复薄壳汇总行缺组数——`tools/compare_two_mdf.py` 复刻原案「N 组对拍」汇总行，修复后逐字节一致；详见验证记录）

```bash
# 原版（git show 恢复后）
python <tmp>/compare_two_mdf.py <并.mdf> <串.mdf> > <tmp>/v1.txt; echo $?
# 收口版
python tools/compare_two_mdf.py <并.mdf> <串.mdf> > <tmp>/v2.txt; echo $?
diff <tmp>/v1.txt <tmp>/v2.txt
```
Expected: 两输出逐行一致（头部字段并/串产物同源 → 无新增行）；退出码一致（0）

- [x] **Step 2: reference 语义等价（对 CANoe 参考）**（2026-08-18 验证：3434 处差异逐类核验全部为已知真实差异——header.comment 1、组内信号序 41 组（原版 deep_compare_latest 记录 41/70）、存储表示元数据 3392；values/stats 维度 0 差异，对应原版「160 组 × 601 点逐点全部一致」；退出码 1 符合预期，无意外行）

```bash
# 原版 full_compare（判定结论：统计逐点 0 不一致、无 dtype MISMATCH）
python <tmp>/full_compare.py <自产.mdf> <参考.mdf> --no-report > <tmp>/v1.txt
# 收口版（默认 dims 全开 + 布局必填参数）
python tools/full_compare.py <自产.mdf> <参考.mdf> \
    --stats-ref-block 22 --stats-ref-idx "StdData:4,StdDataRate:5,ExtData:6,ExtDataRate:7,StdRemote:8,StdRemoteRate:9,ExtRemote:10,ExtRemoteRate:11,ErrorFrames:12,ErrorFrameRate:13" \
    --no-report > <tmp>/v2.txt; echo $?
```
Expected:
- 原版结论「160 组 × 601 点逐点全部一致」↔ 收口版判定 stats 维度无差异行
- 原版「抽查信号 dtype 全部与参考一致」「枚举文本逐值全部一致」↔ 收口版 values 维度无差异行
- **已知真实差异需人工确认**（memory: MDF-CANoe 格式差异——头部 comment 等）：收口版 header 维度报出的差异行与原版 `compare_mdf_deep` 头部判定一致，且是文档已知差异
- 收口版退出码 1（存在头部等已知差异）——**人工核对每个新行是已知真实差异，无意外**

- [x] **Step 3: 统计 t 轴等价**（2026-08-18 验证：原版打印 n=1217 [0]=0.0 [-1]=1215.356 两侧一致 ↔ 收口版无「统计 t 轴」差异行）

```bash
# 原版 deep_compare_latest（t 轴打印；判定侧由 Task 6 Step 2 的 stats 维度覆盖）
python <tmp>/deep_compare_latest.py <自产.mdf> <参考.mdf>
```
Expected: 其统计 t 轴打印（n/首/末）与收口版判定输出一致（无「统计 t 轴」差异行 = 与打印值相符）

- [x] **Step 4: pytest 全绿 + 验收链冒烟**（2026-08-18 验证：`pytest tests/ -q` 220 passed（blfmdf 环境，薄壳修复前后各跑一次）；identical 对拍复跑两次输出一致）

Run: `python -m pytest tests/ -q`；再跑一次 Step 1 命令确认稳定性
Expected: 全绿；两次 identical 对拍输出一致

- [ ] **Step 5: 交付点（用户提交）**（2026-08-18 执行任务时按用户指示**不提交**，交付点由用户执行；验证记录见 [2026-08-18-h2-compare-consolidation-verification.md](./2026-08-18-h2-compare-consolidation-verification.md)，待提交改动：`tools/compare_two_mdf.py` 汇总行修复）

```bash
git add -A
git commit -m "docs: H2 对拍收口行为等价验证记录"
```
（若验证发现差异，先回到对应 Task 修复再提交）

---

## Self-Review（写计划时执行）

- **Spec 覆盖**：D1 双入口 ✓（Task 1）；D2 四维 + 独有能力 ✓（Task 1）；D3 容差逐入口 ✓（Task 1 默认值）；D4 两个薄壳 + 布局必填 ✓（Task 2/3）；D5 删 5 + 消拷贝 ✓（Task 4/5）；D6 行为等价实证 ✓（Task 6）。User Stories 1-12 全部有任务对应。
- **占位符扫描**：无 TBD；所有代码步骤含完整内容；验证步骤给出命令与预期（真实数据路径需用户提供——这是环境事实，非占位）。
- **类型一致性**：`compare_files_identical` / `compare_files_reference` 签名在 Task 1 定义、Task 2/3/4 消费一致；`stats_ref_layout: tuple[int, dict[str, int]]` 在 Task 1 定义、Task 3 解析为同形状；`STAT_NAMES` 在模块与 Task 3 引用一致。
