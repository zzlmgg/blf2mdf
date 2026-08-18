"""对拍共享模块黄金测试：合成 MDF，穿两个公开入口（唯一 seam）。"""
from datetime import datetime, timezone

import numpy as np
import pytest
from asammdf import MDF, Signal

from mdf_factory import _write_mdf, _simple, REF_LAYOUT
from tools.mdf_compare import (compare_files_identical, compare_files_reference,
                               STAT_NAMES, DEFAULT_DIMS)
from core.stats import STAT_NAMES as CORE_STAT_NAMES


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


# ---- identical / reference：NaN 语义（两侧）----

def test_identical_nan_semantics(tmp_path):
    """identical float 路径用 nanmax（跳过 NaN）：同位置 NaN 与单侧 NaN 均不报（契约现状）。

    sa - sb 含 NaN 时 nanmax 取非 NaN 最大值 → diff=0.0 ≤ atol → 不报。
    与 reference 的 equal_nan=True 语义区分（见下两用例）。
    """
    g_nan = [("G1", [("SigA", [1.0, np.nan, 3.0], np.float64),
                     ("SigB", [10, 20, 30], np.int64)]),
             ("G2", [("SigC", [b"x", b"y", b"z"], "S8")])]
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, g_nan)
    _write_mdf(p2, g_nan)  # 同位置 NaN
    assert compare_files_identical(p1, p2) == []
    _write_mdf(p1, g_nan)
    _write_mdf(p2, groups)  # 单侧 NaN
    assert compare_files_identical(p1, p2) == []


def test_reference_equal_nan_same_position(tmp_path):
    """reference isclose(equal_nan=True)：同位置 NaN 不误报（本应相等判为差异）。"""
    g_nan = [("G1", [("SigA", [1.0, np.nan, 3.0], np.float64),
                     ("SigB", [10, 20, 30], np.int64)]),
             ("G2", [("SigC", [b"x", b"y", b"z"], "S8")])]
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, g_nan)
    _write_mdf(p2, g_nan)
    assert compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT) == []


def test_reference_equal_nan_one_side(tmp_path):
    """reference：单侧 NaN → N 点不一致（不误合并为相等）。"""
    g_nan = [("G1", [("SigA", [1.0, np.nan, 3.0], np.float64),
                     ("SigB", [10, 20, 30], np.int64)]),
             ("G2", [("SigC", [b"x", b"y", b"z"], "S8")])]
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, g_nan)
    _write_mdf(p2, groups)
    diffs = compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT)
    assert any("1 点不一致" in d for d in diffs)


# ---- reference：整型 / identical：dtype / 两入口：文本内容差异 ----

def test_reference_integer_mismatch(tmp_path):
    """整型信号差异走 array_equal 整型路径 → 整型不一致（不落浮点容差路径被掩盖）。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups[0][1][1] = ("SigB", [10, 99, 30], np.int64)
    _write_mdf(p2, groups)
    diffs = compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT)
    assert any("整型不一致" in d for d in diffs)


def test_identical_dtype_mismatch(tmp_path):
    """identical：dtype 不匹配显式报出（dtype 漂移不静默）。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups[0][1][0] = ("SigA", [1.0, 2.0, 3.0], np.float32)
    _write_mdf(p2, groups)
    diffs = compare_files_identical(p1, p2)
    assert any("dtype" in d for d in diffs)


def test_identical_text_content_diff(tmp_path):
    """identical 文本内容差异 → 采样不一致（文本通道按归一化后内容判定）。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups[1][1][0] = ("SigC", [b"x", b"NO", b"z"], "S8")
    _write_mdf(p2, groups)
    diffs = compare_files_identical(p1, p2)
    assert any("采样不一致" in d for d in diffs)


def test_reference_text_content_diff(tmp_path):
    """reference 文本内容差异 → 文本不一致。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups[1][1][0] = ("SigC", [b"x", b"NO", b"z"], "S8")
    _write_mdf(p2, groups)
    diffs = compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT)
    assert any("文本不一致" in d for d in diffs)


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


# ---- identical：通道元数据（structure 维度隔离）----

def test_identical_structure_data_type(tmp_path):
    """SigA float64(dt=4) vs int64(dt=2)：data_type 单字段差异（bit_count 同为 64）。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, [("G1", [("SigA", [1, 2, 3], np.int64),
                            ("SigB", [10, 20, 30], np.int64)]),
                    ("G2", [("SigC", [b"x", b"y", b"z"], "S8")])])
    diffs = compare_files_identical(p1, p2, dims=frozenset({"structure"}))
    assert any("data_type" in d for d in diffs)


def test_identical_structure_bit_count(tmp_path):
    """SigA float64(64bit) vs float32(32bit)：bit_count 单字段差异（data_type 同为 4）。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, [("G1", [("SigA", [1.0, 2.0, 3.0], np.float32),
                            ("SigB", [10, 20, 30], np.int64)]),
                    ("G2", [("SigC", [b"x", b"y", b"z"], "S8")])])
    diffs = compare_files_identical(p1, p2, dims=frozenset({"structure"}))
    assert any("bit_count" in d for d in diffs)


def test_identical_structure_unit(tmp_path):
    """unit：chan_mut 就地改 SigA 的 unit（asammdf Channel.unit 可写且落盘）。

    channels[0] 是自动生成的 'time' 主通道，数据通道从 channels[1] 起（SigA）。
    """
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups,
               chan_mut=lambda m: setattr(m.groups[0].channels[1], "unit", "km/h"))
    diffs = compare_files_identical(p1, p2, dims=frozenset({"structure"}))
    assert any("unit" in d for d in diffs)


def test_identical_structure_flags(tmp_path):
    """flags：chan_mut 就地改 SigA 的 CN 块 flags 字段（可写且落盘）。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups,
               chan_mut=lambda m: setattr(m.groups[0].channels[1], "flags", 1))
    diffs = compare_files_identical(p1, p2, dims=frozenset({"structure"}))
    assert any("flags" in d for d in diffs)


def test_identical_structure_conversion(tmp_path):
    """conversion：默认 None vs 显式转换对象（str 不同即报）；对象读回后 str 含地址等字段。

    注意：读回的转换对象是"空"转换（val_param_nr=0，参数未落盘），不缩放采样值——
    但本用例仅开 structure 维度，不涉值比较。
    """
    from asammdf.blocks.v4_blocks import ChannelConversion
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups,
               chan_mut=lambda m: setattr(m.groups[0].channels[1], "conversion",
                                          ChannelConversion(P1=2.0)))
    diffs = compare_files_identical(p1, p2, dims=frozenset({"structure"}))
    assert any("conversion" in d for d in diffs)


def test_identical_structure_bit_resolution_noop(tmp_path):
    """MDF4 Channel 无 bit_resolution 属性（实测 <absent>）→ 模块 getattr 恒 None，永不报。

    以真实差异对（bit_count 差异）验证：差异确实存在但 bit_resolution 不出现。
    """
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, [("G1", [("SigA", [1.0, 2.0, 3.0], np.float32),
                            ("SigB", [10, 20, 30], np.int64)]),
                    ("G2", [("SigC", [b"x", b"y", b"z"], "S8")])])
    diffs = compare_files_identical(p1, p2, dims=frozenset({"structure"}))
    assert diffs
    assert all("bit_resolution" not in d for d in diffs)


def test_identical_header_comment(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups, comment="a")
    _write_mdf(p2, groups, comment="b")
    diffs = compare_files_identical(p1, p2)
    assert any("header.comment" in d for d in diffs)


@pytest.mark.parametrize("field, value", [
    ("start_time", datetime(2026, 1, 2, tzinfo=timezone.utc)),
    # abs_time 是 HD 原始 int ns 字段；start_time 由它派生 → 该用例会相伴出现 start_time 差异（不查互斥）
    ("abs_time", int(datetime(2026, 1, 3, tzinfo=timezone.utc).timestamp() * 10**9)),
    ("tz_offset", 480),
    ("flags", 3),
    # author 等四字段落盘进 HD comment XML → 会连带 header.comment 差异（不查互斥）
    ("author", "author2"),
    ("department", "dept2"),
    ("project", "proj2"),
    ("subject", "subj2"),
])
def test_identical_header_field_diff(tmp_path, field, value):
    """构造单字段头部差异 → 断言报出字段名。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups, header={field: value})
    diffs = compare_files_identical(p1, p2)
    assert any(f"header.{field}" in d for d in diffs)


def test_identical_header_version_noop(tmp_path):
    """MDF4 HeaderBlock 无 version 属性（实测 AttributeError）→ 模块 getattr 恒 None，永不报。

    以真实差异对（flags 差异）验证：差异确实存在但 version 不出现。
    """
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups, header={"flags": 3})
    diffs = compare_files_identical(p1, p2)
    assert diffs
    assert all("header.version" not in d for d in diffs)


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


def test_identical_stats_value_diff(tmp_path):
    """identical 1s 组逐点数值差异：default dims 下由 values 维度捕获（1s 组同样参与采样比较）。"""
    n = 20
    base = [("1s", [("StdData", np.full(n, 1.0, np.float64), np.float64)])]
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, base)
    vals = np.full(n, 1.0, np.float64)
    vals[5] = 2.0
    _write_mdf(p2, [("1s", [("StdData", vals, np.float64)])])
    diffs = compare_files_identical(p1, p2)
    assert any("采样不一致" in d for d in diffs)


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


# ---- dims 关闭语义（两入口各一对）----

def test_identical_dims_off(tmp_path):
    """dims 关闭：关闭维度不报、其余维度仍报（identical 入口）。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups[0][1][0] = ("SigA", [1.0, 2.0, 99.0], np.float64)
    _write_mdf(p2, groups, comment="diff-comment")
    diffs = compare_files_identical(p1, p2, dims=frozenset({"header"}))
    assert any("header.comment" in d for d in diffs)
    assert all("采样不一致" not in d for d in diffs)
    diffs = compare_files_identical(p1, p2, dims=frozenset({"values"}))
    assert any("采样不一致" in d for d in diffs)
    assert all("header.comment" not in d for d in diffs)


def test_reference_dims_off(tmp_path):
    """dims 关闭：关闭维度不报、其余维度仍报（reference 入口）。"""
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    groups[0][1][0] = ("SigA", [1.0, 2.0, 99.0], np.float64)
    _write_mdf(p2, groups, comment="diff-comment")
    diffs = compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT,
                                    dims=frozenset({"header"}))
    assert any("header.comment" in d for d in diffs)
    assert all("不一致" not in d for d in diffs)
    diffs = compare_files_reference(p1, p2, stats_ref_layout=REF_LAYOUT,
                                    dims=frozenset({"values"}))
    assert any("点不一致" in d for d in diffs)
    assert all("header.comment" not in d for d in diffs)


def test_invalid_dims(tmp_path):
    _, groups, _ = _simple()
    p1, p2 = tmp_path / "a.mf4", tmp_path / "b.mf4"
    _write_mdf(p1, groups)
    _write_mdf(p2, groups)
    with pytest.raises(ValueError):
        compare_files_identical(p1, p2, dims=frozenset({"foo"}))


def test_stat_names_locked_to_core():
    """STAT_NAMES 双副本相等性锁定：生产写出布局（core.stats）与 oracle 消费端
    （tools.mdf_compare）任一侧漂移 → 本用例变红，对拍不再静默失真。"""
    assert STAT_NAMES == CORE_STAT_NAMES
