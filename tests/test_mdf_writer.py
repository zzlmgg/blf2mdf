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
    assert group_names == {"Signal::ECU1", "Signal::ECU2"}
    speed = m.get("Speed")
    assert speed is not None and speed.unit == "km/h"
    assert np.allclose(speed.samples, [10.0, 20.0])
    assert np.allclose(speed.timestamps, [1.0, 2.0])
    assert np.allclose(m.get("Temp").samples, [25.0, 26.0])
    assert np.allclose(m.get("Volt").samples, [12.6])


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
    ids = m.get("ID")
    assert ids is not None and np.issubdtype(ids.samples.dtype, np.integer)
    assert ids.samples.tolist() == [0x123, 0x456]
    assert m.get("DLC").samples.tolist() == [2, 1]
    assert m.get("Data").samples.tolist() == [[1, 2], [3, 0]]
    assert np.allclose(m.get("Time").samples, [1.0, 2.0])
    assert m.get("IsExtended").samples.tolist() == [0, 1]
    assert m.get("IsFD").samples.tolist() == [0, 0]


def test_same_node_across_channels_dedupe(tmp_path):
    s1 = _series(1, "ECU1", "MsgA", [("Speed", "km/h")], [1.0], {"Speed": [10.0]})
    s2 = _series(5, "ECU1", "MsgC", [("Accel", "m/s2")], [2.0], {"Accel": [3.0]})
    out = tmp_path / "dedupe.mdf"
    write_mdf([s1, s2], [], str(out))
    m = MDF(str(out))
    names = sorted(g.channel_group.acq_name for g in m.groups)
    assert names == ["CAN5::Signal::ECU1", "Signal::ECU1"]
