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


def test_short_dlc_known_id_counts_as_unknown(tmp_path):
    """已知 ID 但 DLC 短于 DBC 长度：DecodeError 计入未知帧，不中断解码。"""
    from core.blf_reader import Frame

    p = tmp_path / "t.dbc"
    p.write_text(DBC_TXT, encoding="utf-8")
    dbc = load(str(p))
    short = Frame(channel=1, ts_seconds=0.5, arbitration_id=100,
                  is_extended=False, is_fd=False, dlc=2,
                  data=bytes([0xE8, 0x03]))
    good = Frame(channel=1, ts_seconds=1.0, arbitration_id=100,
                 is_extended=False, is_fd=False, dlc=8,
                 data=bytes([0xE8, 0x03, 0x50, 0, 0, 0, 0, 0]))
    series, stats = decode_channel(iter([short, good]), dbc, channel=1)
    assert stats.total_frames == 2
    assert stats.unknown_frames == 1
    assert stats.unknown_ids == {100}
    assert len(series) == 1
    assert series[0].timestamps.tolist() == [1.0]


def test_enum_signal_named_choice_unwraps_to_numeric(tmp_path):
    """VAL_ 枚举信号：NamedSignalValue 解包为数值（.value）存入 float64 通道。"""
    from core.blf_reader import Frame

    enum_dbc = '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 300 ECU: 8 ABC
 SG_ State : 0|8@1+ (1,0) [0|255] "state" ECU

VAL_ 300 State 0 "off" 1 "on" ;
'''
    p = tmp_path / "t.dbc"
    p.write_text(enum_dbc, encoding="utf-8")
    dbc = load(str(p))
    on = Frame(channel=1, ts_seconds=1.0, arbitration_id=300,
               is_extended=False, is_fd=False, dlc=8,
               data=bytes([0x01, 0, 0, 0, 0, 0, 0, 0]))
    other = Frame(channel=1, ts_seconds=2.0, arbitration_id=300,
                  is_extended=False, is_fd=False, dlc=8,
                  data=bytes([0x02, 0, 0, 0, 0, 0, 0, 0]))
    series, stats = decode_channel(iter([on, other]), dbc, channel=1)
    assert stats.total_frames == 2 and stats.unknown_frames == 0
    assert series[0].values["State"].tolist() == pytest.approx([1.0, 2.0])
    assert isinstance(series[0].values["State"][0], float)  # 数值而非 NamedSignalValue


def test_extended_id_normalization(tmp_path):
    """扩展帧 id 归一化：29 位原始 id 与 EFF 位约定对齐（4 场景）。"""
    from core.blf_reader import Frame

    ext_dbc = '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 256 M1: 8 ECU
 SG_ A : 0|8@1+ (1,0) [0|255] "" ECU

BO_ 2147483904 M2: 8 ECU
 SG_ B : 0|8@1+ (1,0) [0|255] "" ECU

BO_ 2147488308 M3: 8 ECU
 SG_ C : 0|8@1+ (1,0) [0|255] "" ECU
'''
    # 2147483904 = 0x80000100（扩展 0x100），2147488308 = 0x80001234（扩展 0x1234 > 0x7FF）
    p = tmp_path / "t.dbc"
    p.write_text(ext_dbc, encoding="utf-8")
    dbc = load(str(p))
    f_ext_small = Frame(channel=1, ts_seconds=1.0, arbitration_id=0x100,
                        is_extended=True, is_fd=False, dlc=8,
                        data=bytes([0x07, 0, 0, 0, 0, 0, 0, 0]))
    f_ext_big = Frame(channel=1, ts_seconds=2.0, arbitration_id=0x1234,
                      is_extended=True, is_fd=False, dlc=8,
                      data=bytes([0x08, 0, 0, 0, 0, 0, 0, 0]))
    f_std = Frame(channel=1, ts_seconds=3.0, arbitration_id=0x100,
                  is_extended=False, is_fd=False, dlc=8,
                  data=bytes([0x01, 0, 0, 0, 0, 0, 0, 0]))
    f_eff = Frame(channel=1, ts_seconds=4.0, arbitration_id=0x80000100,
                  is_extended=True, is_fd=False, dlc=8,
                  data=bytes([0x02, 0, 0, 0, 0, 0, 0, 0]))
    series, stats = decode_channel(iter([f_ext_small, f_ext_big, f_std, f_eff]),
                                   dbc, channel=1)
    assert stats.total_frames == 4 and stats.unknown_frames == 0
    by_name = {s.message_name: s for s in series}
    # (1)(4) 扩展 0x100 帧与已带 EFF 位帧都落到 M2
    assert by_name["M2"].values["B"].tolist() == pytest.approx([7.0, 2.0])
    # (2) 扩展 id > 0x7FF 正常解码
    assert by_name["M3"].values["C"].tolist() == pytest.approx([8.0])
    # (3) 标准/扩展共用原始 id 0x100：各归各的系列，无覆盖/NaN 污染
    assert by_name["M1"].values["A"].tolist() == pytest.approx([1.0])
