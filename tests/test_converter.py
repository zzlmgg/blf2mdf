import numpy as np
import pytest
from asammdf import MDF

from core.converter import convert
from core.dbc_loader import load

INLINE_DBC = '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 100 ABC: 8 ECU
 SG_ Speed : 0|16@1+ (0.01,0) [0|655.35] "km/h" ECU
'''


@pytest.fixture()
def blf_and_dbc(tmp_path):
    import can

    blf = tmp_path / "two_ch.blf"
    with can.BLFWriter(str(blf)) as w:
        # 通道 1：匹配 DBC 的帧（python-can 4.6.1 Message 默认 is_extended_id=True，须显式标准帧）
        w.on_message_received(can.Message(arbitration_id=100, is_extended_id=False,
                                          data=bytes([0xE8, 0x03, 0, 0, 0, 0, 0, 0]),
                                          channel=1, timestamp=1784716800.0))
        w.on_message_received(can.Message(arbitration_id=100, is_extended_id=False,
                                          data=bytes([0x88, 0x13, 0, 0, 0, 0, 0, 0]),
                                          channel=1, timestamp=1784716801.0))
        # 通道 1：未知 ID
        w.on_message_received(can.Message(arbitration_id=999, is_extended_id=False,
                                          data=bytes(8), channel=1, timestamp=1784716802.0))
        # 通道 2：原始帧
        w.on_message_received(can.Message(arbitration_id=0x456, is_extended_id=False,
                                          data=b"\xAA\xBB", channel=2, timestamp=1784716803.0))
    dbc = tmp_path / "t.dbc"
    dbc.write_text(INLINE_DBC, encoding="utf-8")
    return str(blf), str(dbc)


def test_convert_mixed_channels(tmp_path, blf_and_dbc):
    blf, dbc_path = blf_and_dbc
    out = tmp_path / "out.mdf"
    result = convert(blf, {1: load(dbc_path), 2: None}, str(out))

    assert result.duration_seconds == pytest.approx(3.0)  # 1784716800.0s → 1784716803.0s
    by_ch = {s.channel: s for s in result.summaries}
    s1 = by_ch[1]
    assert s1.bound and s1.decoded_frames == 2
    assert s1.signal_count == 1
    assert s1.unknown_frames == 1 and s1.unknown_ids == 1
    assert s1.warning == ""
    s2 = by_ch[2]
    assert not s2.bound and s2.raw_frames == 1

    m = MDF(str(out))
    speed = m.get("Speed")
    assert np.allclose(speed.samples, [10.0, 50.0])   # 0x03E8→10.0, 0x1388→50.0
    assert {g.channel_group.acq_name for g in m.groups} == {"Signal::ECU", "Raw::CAN2"}
    data = m.get("Data")
    assert data.samples.tolist() == [[0xAA, 0xBB]]


def test_convert_progress_callback(tmp_path, blf_and_dbc):
    blf, dbc_path = blf_and_dbc
    calls = []
    convert(blf, {1: load(dbc_path), 2: None}, str(tmp_path / "p.mdf"),
            progress_cb=lambda stage, pct: calls.append((stage, pct)))
    assert calls, "应至少有一次回调"
    assert calls[-1][0] == "完成"
    assert "写 MDF" in [s for s, _ in calls]


def test_convert_no_matching_frames_warns(tmp_path, blf_and_dbc):
    blf, dbc_path = blf_and_dbc
    out = tmp_path / "empty.mdf"
    result = convert(blf, {1: None, 3: load(dbc_path)}, str(out))
    s3 = next(s for s in result.summaries if s.channel == 3)
    assert s3.warning == "该通道无匹配帧"
    m = MDF(str(out))
    assert {g.channel_group.acq_name for g in m.groups} == {"Raw::CAN1"}  # 通道 3 空组不写入


def test_convert_no_channels_raises(tmp_path, blf_and_dbc):
    blf, _ = blf_and_dbc
    with pytest.raises(ValueError):
        convert(blf, {}, str(tmp_path / "x.mdf"))


def test_convert_write_failure_cleans_partial_files(tmp_path, blf_and_dbc, monkeypatch):
    """写 MDF 失败：异常上抛，且 <out>.mf4 半成品与 out_path 都被清理。"""
    blf, dbc_path = blf_and_dbc
    out = tmp_path / "out.mdf"
    mf4 = tmp_path / "out.mf4"

    def boom(*args, **kwargs):
        # 模拟 asammdf save 写出 .mf4 后、rename 前失败
        mf4.write_bytes(b"partial")
        raise OSError("simulated write failure")

    monkeypatch.setattr("core.converter.mdf_writer.write_mdf", boom)
    with pytest.raises(OSError):
        convert(blf, {1: load(dbc_path)}, str(out))
    assert not out.exists(), "out_path 不应残留"
    assert not mf4.exists(), "半成品 .mf4 应被删除"
