import itertools

import pytest

from core.blf_reader import Frame, iter_messages, list_channels
from conftest import sample_blf


def test_synthetic_blf_roundtrip(tmp_path):
    """不依赖样例文件：python-can 写的小 BLF 能枚举通道并过滤。"""
    import can

    p = tmp_path / "tiny.blf"
    # python-can 对 1990 年以前的头部时间戳置零，故用真实 Unix 秒
    with can.BLFWriter(str(p)) as w:
        w.on_message_received(can.Message(arbitration_id=0x123, data=b"\x01\x02",
                                          channel=1, timestamp=1784716800.0))
        w.on_message_received(can.Message(arbitration_id=0x456, data=b"\x03",
                                          channel=2, timestamp=1784716802.0))
    assert list_channels(str(p)) == [1, 2]
    frames = list(iter_messages(str(p), 1))
    assert len(frames) == 1
    assert frames[0].arbitration_id == 0x123
    assert frames[0].data == b"\x01\x02"
    assert frames[0].ts_seconds == 1784716800.0
    assert isinstance(frames[0], Frame)


def test_list_channels_on_sample():
    blf = sample_blf()
    if blf is None:
        pytest.skip("无样例 BLF 文件")
    channels = list_channels(str(blf))
    assert channels, "样例 BLF 应至少有一个通道"
    print("样例 BLF 通道:", channels)


def test_iter_messages_fields_on_sample():
    blf = sample_blf()
    if blf is None:
        pytest.skip("无样例 BLF 文件")
    chans = list_channels(str(blf))
    frames = list(itertools.islice(iter_messages(str(blf), chans[0]), 200))
    assert frames, "第一个通道应产出帧"
    assert all(f.channel == chans[0] for f in frames)
    ts = [f.ts_seconds for f in frames]
    assert ts == sorted(ts), "时间戳应非递减"
    assert all(f.ts_seconds >= 0 for f in frames)
    assert all(0 <= f.dlc <= 64 and len(f.data) == f.dlc for f in frames)
    assert any(f.is_fd for f in frames) or True  # 打印 FD 帧占比，不强制
    fd_count = sum(1 for f in frames if f.is_fd)
    print(f"前 200 帧中 CANFD: {fd_count}")
