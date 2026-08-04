import math

import pytest

from core.decoder import SignalSeries, decode_channel
from core.dbc_loader import load

INLINE_DBC = '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 100 ABC: 8 ECU
 SG_ Speed : 0|16@1+ (0.01,0) [0|655.35] "km/h" ECU
 SG_ Temp : 16|8@1+ (1,-40) [-40|215] "degC" ECU

BO_ 200 ABC: 8 ECU
 SG_ Mux M : 0|8@1+ (1,0) [0|255] "" ECU
 SG_ SigA m0 : 8|8@1+ (1,0) [0|255] "" ECU
 SG_ SigB m1 : 8|8@1+ (1,0) [0|255] "" ECU
'''

DBC_TXT = "\n".join(INLINE_DBC.splitlines())


def _frames():
    """构造 3 帧：Speed=0x03E8(→10.0 km/h) Temp=0x50(→40.0°C)；一个 mux 帧；一个未知 ID。"""
    from core.blf_reader import Frame

    f1 = Frame(channel=1, ts_seconds=1.0, arbitration_id=100,
               is_extended=False, is_fd=False, dlc=8,
               data=bytes([0xE8, 0x03, 0x50, 0, 0, 0, 0, 0]))
    f2 = Frame(channel=1, ts_seconds=2.0, arbitration_id=200,
               is_extended=False, is_fd=False, dlc=8,
               data=bytes([0x00, 0x05, 0x07, 0, 0, 0, 0, 0]))
    f3 = Frame(channel=1, ts_seconds=3.0, arbitration_id=999,
               is_extended=False, is_fd=False, dlc=8,
               data=bytes(8))
    # mux 帧在前：两报文同名 "ABC"，by_name 取同名最后一个系列，须让 Speed/Temp 系列殿后
    return [f2, f1, f3]


def test_decode_physical_values(tmp_path):
    p = tmp_path / "t.dbc"
    p.write_text(DBC_TXT, encoding="utf-8")
    dbc = load(str(p))
    series, stats = decode_channel(iter(_frames()), dbc, channel=1)
    assert stats.total_frames == 3
    assert stats.unknown_frames == 1
    assert stats.unknown_ids == {999}
    assert len(series) == 2
    by_name = {s.message_name: s for s in series}
    abc100 = by_name["ABC"]
    assert abc100.channel == 1 and abc100.node == "ECU"
    assert abc100.signal_names == ["Speed", "Temp"]
    assert abc100.timestamps.tolist() == [1.0]
    assert abc100.values["Speed"][0] == pytest.approx(10.0)
    assert abc100.values["Temp"][0] == pytest.approx(40.0)
    assert abc100.units["Speed"] == "km/h"
    assert abc100.units["Temp"] == "degC"


def test_mux_inactive_signal_is_nan(tmp_path):
    p = tmp_path / "t.dbc"
    p.write_text(DBC_TXT, encoding="utf-8")
    dbc = load(str(p))
    series, _ = decode_channel(iter(_frames()), dbc, channel=1)
    abc200 = next(s for s in series if s.message_name == "ABC"
                  and s.signal_names == ["Mux", "SigA", "SigB"])
    assert abc200.values["SigA"][0] == pytest.approx(5.0)
    assert math.isnan(abc200.values["SigB"][0])
    assert abc200.values["Mux"][0] == pytest.approx(0.0)
    assert len(abc200.signal_names) == len(abc200.values)
    n = len(abc200.timestamps)
    assert all(len(v) == n for v in abc200.values.values()), "组内等长"


def test_no_matching_frames(tmp_path):
    p = tmp_path / "t.dbc"
    p.write_text(DBC_TXT, encoding="utf-8")
    dbc = load(str(p))
    series, stats = decode_channel(iter([]), dbc, channel=1)
    assert series == [] and stats.total_frames == 0
