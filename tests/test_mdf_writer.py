import numpy as np
import pytest
from asammdf import MDF

from core.decoder import SignalSeries
from core.mdf_writer import RawGroup, write_mdf


def _series(channel, node, msg_name, names_units, ts, vals):
    return SignalSeries(
        channel=channel, message_name=msg_name, node=node,
        signal_names=[n for n, _ in names_units],
        timestamps=np.array(ts, dtype=np.float64),
        values={n: np.array(v, dtype=np.float64) for n, v in vals.items()},
        units=dict(names_units),
    )


def test_write_and_readback_signal_groups(tmp_path):
    s1 = _series(1, "ECU1", "MsgA", [("Speed", "km/h"), ("Temp", "degC")],
                 [1.0, 2.0], {"Speed": [10.0, 20.0], "Temp": [25.0, 26.0]})
    s2 = _series(1, "ECU2", "MsgB", [("Volt", "V")],
                 [0.5], {"Volt": [12.6]})
    out = tmp_path / "out.mdf"
    write_mdf([s1, s2], [], str(out))

    m = MDF(str(out))
    group_names = {g.channel_group.acq_name for g in m.groups}
    assert group_names == {"MsgA", "MsgB"}
    # 修复项 6：主时间通道名与 CANoe 一致为 "t"（asammdf 默认 "time"）
    for g in m.groups:
        names = [c.name for c in g.channels]
        assert names[0] == "t", f"主时间通道应为 't'，实际 {names}"
        assert "time" not in names
    # 每组成员均含主通道 t；跨组同名，get 需指定 group
    assert np.allclose(m.get("t", group=0).samples, [1.0, 2.0])
    assert np.allclose(m.get("t", group=1).samples, [0.5])
    speed = m.get("Speed")
    assert speed is not None and speed.unit == "km/h"
    assert np.allclose(speed.samples, [10.0, 20.0])
    assert np.allclose(speed.timestamps, [1.0, 2.0])
    assert np.allclose(m.get("Temp").samples, [25.0, 26.0])
    assert np.allclose(m.get("Volt").samples, [12.6])


def test_write_abs_start_time_metadata(tmp_path):
    """修复项 2：abs_start_epoch 写入 MDF 头部 start_time（naive UTC 整秒，
    与 CANoe 参考逐字段一致：abs_time/time_flags/tz_offset）；
    已存相对时间戳数据不受影响。"""
    from datetime import datetime

    s = _series(1, "ECU1", "MsgA", [("Speed", "km/h")], [0.005, 1.005],
                {"Speed": [10.0, 20.0]})
    out = tmp_path / "meta.mdf"
    write_mdf([s], [], str(out), abs_start_epoch=1784716800.005)

    m = MDF(str(out))
    assert m.header.start_time == datetime(2026, 7, 22, 10, 40)
    assert m.header.abs_time == 1784716800000000000
    assert m.header.time_flags == 0x1 and m.header.tz_offset == 0
    assert np.allclose(m.get("t", group=0).samples, [0.005, 1.005])


def test_write_without_abs_start_keeps_default_header(tmp_path):
    """不传 abs_start_epoch（现有调用方）：不写特定 start_time（保持默认值）。"""
    s = _series(1, "ECU1", "MsgA", [("Speed", "km/h")], [1.0], {"Speed": [10.0]})
    out = tmp_path / "plain.mdf"
    write_mdf([s], [], str(out))
    m = MDF(str(out))
    assert m.header.abs_time != 1784716800000000000


def test_write_mixed_dtypes_roundtrip(tmp_path):
    """修复项 3+8：每信号按自身 dtype 写出（整型原值/|S 文本/float64），读回 dtype 不变。"""
    ts = np.array([1.0, 2.0, 3.0], dtype=np.float64)
    s = SignalSeries(
        channel=1, message_name="MsgT", node="ECU",
        signal_names=["Cnt", "State", "Phys"],
        timestamps=ts,
        values={
            "Cnt": np.array([1, 2, 3], dtype=np.uint8),
            "State": np.array([b"on", b"off", b"on"]),        # |S3
            "Phys": np.array([10.5, 20.5, 30.5], dtype=np.float64),
        },
        units={"Cnt": "", "State": "state", "Phys": "V"},
    )
    out = tmp_path / "mixed.mdf"
    write_mdf([s], [], str(out))

    m = MDF(str(out))
    cnt = m.get("Cnt")
    assert cnt.samples.dtype == np.uint8 and cnt.samples.tolist() == [1, 2, 3]
    st = m.get("State")
    assert st.samples.dtype.kind == "S"
    assert bytes(st.samples[0]).rstrip(b"\x00") == b"on"
    assert st.samples.tolist() == [b"on", b"off", b"on"]
    phys = m.get("Phys")
    assert phys.samples.dtype == np.float64
    assert np.allclose(phys.samples, [10.5, 20.5, 30.5])
    assert st.unit == "state" and phys.unit == "V"


def test_compression_shrinks_output(tmp_path):
    """修复项 7：save 开启压缩；未压缩时仅信号+主通道即 ~8 MB，压缩后应远小于。"""
    n = 500_000
    ts = np.linspace(0.0, 1.0, n)
    s = _series(1, "ECU1", "MsgA", [("Speed", "km/h")], ts,
                {"Speed": np.full(n, 10.0)})
    out = tmp_path / "compressed.mdf"
    write_mdf([s], [], str(out))
    size = out.stat().st_size
    assert size < 1_000_000, f"文件应压缩到 <1MB，实际 {size / 1e6:.2f} MB"
    m = MDF(str(out))
    speed = m.get("Speed")
    assert len(speed.samples) == n
    assert np.allclose(speed.samples, 10.0)


def test_compression_level_matches_canoe(tmp_path):
    """修复项 9：压缩级别对齐 CANoe（asammdf 默认 COMPRESSION_LEVEL=1，
    渐变数据实测 301KB；level 9 后 137KB。真实样例：7.13MB → 5.44MB ≈ CANoe 5.65MB）。"""
    n = 200_000
    ts = np.linspace(0.0, 10.0, n)
    v = np.linspace(0.0, 1.0, n)  # 渐变值：低熵但非恒定，能区分 deflate 级别
    s = _series(1, "ECU1", "MsgA", [("Speed", "km/h")], ts, {"Speed": v})
    out = tmp_path / "lvl.mdf"
    write_mdf([s], [], str(out))
    size = out.stat().st_size
    assert size < 200_000, f"压缩级别未对齐 CANoe（应 <200KB，实际 {size / 1e3:.0f} KB）"
    m = MDF(str(out))
    speed = m.get("Speed")
    assert len(speed.samples) == n
    assert np.allclose(speed.samples, v)


def test_write_raw_group(tmp_path):
    n = 2
    rg = RawGroup(
        channel=2,
        timestamps=np.array([1.0, 2.0]),
        ids=np.array([0x123, 0x456], dtype=np.uint32),
        dlcs=np.array([2, 1], dtype=np.uint8),
        data_array=np.array([[0x01, 0x02], [0x03, 0x00]], dtype=np.uint8),
        is_extended=np.array([False, True]),
        is_fd=np.array([False, False]),
    )
    out = tmp_path / "raw.mdf"
    write_mdf([], [rg], str(out))

    m = MDF(str(out))
    assert {g.channel_group.acq_name for g in m.groups} == {"Raw::CAN2"}
    # 修复项 6：主时间通道名为 "t"；原冗余 "Time" 通道移除（数据由主通道承载）
    assert [c.name for c in m.groups[0].channels] == \
        ["t", "ID", "DLC", "Data", "IsExtended", "IsFD"]
    assert np.allclose(m.get("t").samples, [1.0, 2.0])
    ids = m.get("ID")
    assert ids is not None and np.issubdtype(ids.samples.dtype, np.integer)
    assert ids.samples.tolist() == [0x123, 0x456]
    assert m.get("DLC").samples.tolist() == [2, 1]
    assert m.get("Data").samples.tolist() == [[1, 2], [3, 0]]
    assert m.get("IsExtended").samples.tolist() == [0, 1]
    assert m.get("IsFD").samples.tolist() == [0, 0]


def test_same_message_across_channels_dedupe(tmp_path):
    """同名报文跨通道：首组无前缀，后续组加 CAN<ch>:: 前缀兜底。"""
    s1 = _series(1, "ECU1", "MsgA", [("Speed", "km/h")], [1.0], {"Speed": [10.0]})
    s2 = _series(5, "ECU2", "MsgA", [("Accel", "m/s2")], [2.0], {"Accel": [3.0]})
    out = tmp_path / "dedupe.mdf"
    write_mdf([s1, s2], [], str(out))
    m = MDF(str(out))
    names = sorted(g.channel_group.acq_name for g in m.groups)
    assert names == ["CAN5::MsgA", "MsgA"]
