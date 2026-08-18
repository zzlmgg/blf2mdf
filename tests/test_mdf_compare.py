"""对拍共享模块黄金测试：合成 MDF，穿两个公开入口（唯一 seam）。"""
from datetime import datetime, timezone

import numpy as np
import pytest
from asammdf import MDF, Signal

from tools.mdf_compare import (compare_files_identical, compare_files_reference,
                               STAT_NAMES, DEFAULT_DIMS)

REF_LAYOUT = (22, {"StdData": 4, "StdDataRate": 5, "ExtData": 6, "ExtDataRate": 7,
                   "StdRemote": 8, "StdRemoteRate": 9, "ExtRemote": 10,
                   "ExtRemoteRate": 11, "ErrorFrames": 12, "ErrorFrameRate": 13})

# 固定文件头起始时间：asammdf 新建文件默认盖章写入时刻（两文件必然不同）；
# core/mdf_writer 的 start_time 来自转换源而非写入时刻，此处镜像该语义。
_FIXED_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _write_mdf(path, groups, comment="t"):
    """groups: list[(acq, [(name, samples, dtype), ...])]；t 通道自动加（arange 秒）。"""
    with MDF(version="4.10") as mdf:
        for acq, chans in groups:
            n = len(chans[0][1])
            ts = np.arange(n, dtype=np.float64)
            t_idx = next((i for i, (nm, _, _) in enumerate(chans) if nm == "t"), None)
            if t_idx is not None:
                # 显式 t 通道承载组时间轴：其采样即该组 timestamps（组基准 = 首信号 timestamps）
                ts = np.asarray(chans[t_idx][1], dtype=np.float64)
            sigs = []
            for name, samples, dtype in chans:
                arr = np.asarray(samples, dtype=dtype)
                kwargs = {}
                if arr.dtype.kind in ("S", "O", "U"):
                    kwargs["encoding"] = "utf-8"  # asammdf 8.x 字符串通道需显式 encoding
                sigs.append(Signal(samples=arr, name=name, timestamps=ts, **kwargs))
            if t_idx is None:
                sigs.append(Signal(samples=ts, name="t", timestamps=ts))
            mdf.append(sigs, comment="", acq_name=acq)
        mdf.header.start_time = _FIXED_START
        mdf.header.comment = comment
        mdf.save(path, overwrite=True)  # asammdf 8.x save 强制 .mf4 后缀 → 用例用 .mf4 路径


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
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups)
    assert compare_files_identical(p1, p2) == []


def test_identical_single_value_change(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups[0][1][0] = ("SigA", [1.0, 2.0, 99.0], np.float64)
    _write_mdf(p2, groups)
    diffs = compare_files_identical(p1, p2)
    assert any("SigA" in d and "采样不一致" in d for d in diffs)


def test_identical_tolerance_boundary(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups[0][1][0] = ("SigA", [1.0 + 1e-11, 2.0, 3.0], np.float64)
    _write_mdf(p2, groups)
    assert compare_files_identical(p1, p2)  # 1e-11 > 1e-12 有效容差 → 报
    groups[0][1][0] = ("SigA", [1.0 + 1e-13, 2.0, 3.0], np.float64)
    _write_mdf(p2, groups)
    assert compare_files_identical(p1, p2) == []  # 1e-13 ≤ 1e-12 → 过


def test_identical_group_order(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups.reverse()
    _write_mdf(p2, groups)
    assert any("组名不同" in d for d in compare_files_identical(p1, p2))


def test_identical_channel_order(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    g1 = groups[0]
    g1[1].reverse()
    _write_mdf(p2, groups)
    assert any("通道序不同" in d for d in compare_files_identical(p1, p2))


def test_identical_header_comment(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups, comment="a")
    _write_mdf(p2, groups, comment="b")
    diffs = compare_files_identical(p1, p2)
    assert any("header.comment" in d for d in diffs)


def test_identical_stats_t_axis(tmp_path):
    n = 20
    ts = np.arange(n, dtype=np.float64)
    base = [("1s", [("StdData", np.full(n, 1.0, np.float64), np.float64)])]
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, base)
    # 改 t 轴：以偏移后的 t 通道直接构造文件 2（asammdf r+ 就地修改样本不落盘）
    shifted = [("1s", [("StdData", np.full(n, 1.0, np.float64), np.float64),
                       ("t", ts + 0.5, np.float64)])]
    _write_mdf(p2, shifted)
    assert any("t 轴不一致" in d for d in compare_files_identical(p1, p2,
                                                                  dims=frozenset({"stats"})))


# ---- reference：信号集匹配 / 文本 / 容差 / 长度 ----

def test_reference_group_order_tolerated(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups.reverse()
    _write_mdf(p2, groups)
    assert compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT) == []


def test_reference_text_width_normalized(tmp_path):
    groups = [("G1", [("Tx", np.array([b"hello", b"world"], dtype="S256"), "S256")])]
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups[0][1][0] = ("Tx", np.array([b"hello", b"world"], dtype="S8"), "S8")
    _write_mdf(p2, groups)
    assert compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT) == []


def test_reference_values_tolerance(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups[0][1][0] = ("SigA", [1.0 + 1e-7, 2.0, 3.0], np.float64)
    _write_mdf(p2, groups)
    assert compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT) == []  # ≤1e-6
    groups[0][1][0] = ("SigA", [1.0 + 1e-5, 2.0, 3.0], np.float64)
    _write_mdf(p2, groups)
    assert compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT) != []


def test_reference_length_mismatch(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    # 少一点：整组缩短（asammdf Signal 校验样本/时间戳等长，单信号无法短一截）
    groups[0] = ("G1", [("SigA", [1.0, 2.0], np.float64), ("SigB", [10, 20], np.int64)])
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
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, ours)
    # 逐点：改参考 ch0 StdData（ch0 块内偏移 4 → ref[4]）第 5 点（>1e-9）。
    # asammdf r+ 就地修改样本不落盘，故在写入前构造差异。
    ts = np.arange(npts, dtype=np.float64)
    vals = np.full(npts, 1.0, np.float64)
    vals[5] += 1e-7
    ref[4] = ("1s", [("StdData", vals, np.float64), ("t", ts, np.float64)])
    _write_mdf(p2, ref)
    diffs = compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT)
    assert any("ch0 StdData" in d and "1 点不一致" in d for d in diffs)
    assert any("ch1" in d for d in diffs) is False  # ch1 未被误报


def test_reference_stats_t_axis(tmp_path):
    nch, npts = 2, 20
    ours, _ = _stat_groups(nch, npts)
    _, ref = _stat_groups(nch, npts, t_off=0.1)  # 参考 t 轴整体偏移
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, ours)
    _write_mdf(p2, ref)
    diffs = compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT)
    assert any("统计 t 轴" in d for d in diffs)


# ---- 契约校验 ----

def test_layout_validation(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups)
    with pytest.raises(ValueError):
        compare_files_reference(p1, p2, stats_ref_layout=(22, {"StdData": 4}))  # 缺项
    with pytest.raises(ValueError):
        compare_files_reference(p1, p2, stats_ref_layout=(4, {"StdData": 5}))  # 越界


def test_invalid_dims(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups)
    with pytest.raises(ValueError):
        compare_files_identical(p1, p2, dims=frozenset({"foo"}))
