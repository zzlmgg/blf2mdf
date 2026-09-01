import itertools

import pytest

from core.blf_reader import (
    Frame,
    ConversionCancelled,
    iter_all_messages,
    iter_messages,
    list_channels,
    probe_channels,
)
from conftest import sample_blf


def _multi_container_blf(path, frames=3000):
    """多容器合成 BLF（~9000 帧 × ~35B ≈ 300KB → python-can 容器上限内多个
    容器），制造跨容器读取路径；返回预期通道列表 [1, 2, 3]。
    """
    import can

    with can.BLFWriter(str(path)) as w:
        for i in range(frames):
            for ch in (1, 2, 3):
                w.on_message_received(can.Message(
                    arbitration_id=0x100 + i % 3, data=b"\xAA\xBB\xCC",
                    channel=ch, timestamp=1784716800.0 + i))
    return [1, 2, 3]


def _big_blf(path, frames=2500):
    """≥1024 帧的单通道合成 BLF（取消检查点每 1024 帧触发一次）。"""
    import can

    with can.BLFWriter(str(path)) as w:
        for i in range(frames):
            w.on_message_received(can.Message(
                arbitration_id=0x100 + i % 3, data=b"\xAA",
                channel=1, timestamp=1784716800.0 + i))


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
    # ts_seconds 语义 = 相对测量开始整数秒（整数 ns 构造，与 CANoe 同构）：
    # 整数秒 start + 首帧偏移 0 → 0.0
    assert frames[0].ts_seconds == 0.0
    assert isinstance(frames[0], Frame)


def test_list_channels_reports_monotonic_progress(tmp_path):
    """list_channels 的 progress_cb 按文件字节位置单调推进，最终 100%。

    修复项：大 BLF 读取期间 GUI 需真实进度（每读完一个日志容器回调一次，
    基于 file.tell() / 文件大小）。
    """
    import can

    p = tmp_path / "prog.blf"
    with can.BLFWriter(str(p)) as w:
        for i in range(3):
            w.on_message_received(can.Message(
                arbitration_id=0x100 + i, data=b"\x01\x02",
                channel=1 + i, timestamp=1784716800.0 + i))
    values = []
    assert list_channels(str(p), progress_cb=values.append) == [1, 2, 3]
    assert values, "至少一次进度回调"
    assert values[-1] == 100.0, "读完应报 100%"
    assert all(0.0 <= v <= 100.0 for v in values)
    assert values == sorted(values), "进度应单调不降"


def test_list_channels_on_sample():
    blf = sample_blf()
    if blf is None:
        pytest.skip("无样例 BLF 文件")
    channels = list_channels(str(blf))
    assert channels, "样例 BLF 应至少有一个通道"
    print("样例 BLF 通道:", channels)


def test_iter_all_messages_reports_monotonic_progress(tmp_path):
    """iter_all_messages 的 progress_cb 按文件字节位置单调推进，最终 100%。

    修复项：转换读取阶段（占大文件耗时大头）进度条需真实前进——逐容器
    回调（与 list_channels 同一语义），而不是停在 5% 几十秒。
    """
    import can

    p = tmp_path / "prog2.blf"
    with can.BLFWriter(str(p)) as w:
        for i in range(3):
            w.on_message_received(can.Message(
                arbitration_id=0x100 + i, data=b"\x01\x02",
                channel=1 + i, timestamp=1784716800.0 + i))
    values = []
    frames = list(iter_all_messages(str(p), progress_cb=values.append))
    assert len(frames) == 3
    assert values, "至少一次进度回调"
    assert values[-1] == 100.0, "读完应报 100%"
    assert all(0.0 <= v <= 100.0 for v in values)
    assert values == sorted(values), "进度应单调不降"


def test_iter_all_messages_matches_iter_messages(tmp_path):
    """单遍全量流 = 各通道按通道过滤流之和（方案 A 路由正确性）。"""
    import can

    p = tmp_path / "multi.blf"
    with can.BLFWriter(str(p)) as w:
        for ch, arb, ts in ((1, 0x123, 1784716800.0), (2, 0x456, 1784716801.0),
                            (1, 0x789, 1784716802.0), (3, 0xABC, 1784716803.0)):
            w.on_message_received(can.Message(arbitration_id=arb, data=b"\x01",
                                              channel=ch, timestamp=ts))
    all_frames = list(iter_all_messages(str(p)))
    assert len(all_frames) == 4
    assert [f.channel for f in all_frames] == [1, 2, 1, 3], "全量流应按文件序"
    for ch in (1, 2, 3):
        subset = [f for f in all_frames if f.channel == ch]
        assert subset == list(iter_messages(str(p), ch)), \
            f"通道 {ch} 单遍流与过滤流应逐帧一致"


def test_iter_all_messages_accepts_unaligned_objects(unaligned_blf):
    """合法对象 padding 不得累积成搜索窗口漂移。"""
    import can

    expected = [(0, 0x100, b"\x01"), (1, 0x101, b"\x02")]
    frames = list(iter_all_messages(str(unaligned_blf)))
    got = [(frame.channel, frame.arbitration_id, frame.data) for frame in frames]
    with can.BLFReader(str(unaligned_blf)) as reader:
        reference = [
            (message.channel, message.arbitration_id, bytes(message.data))
            for message in reader
        ]
    assert got == reference == expected


def test_iter_messages_fields_on_sample():
    blf = sample_blf()
    if blf is None:
        pytest.skip("无样例 BLF 文件")
    chans = list_channels(str(blf))
    frames = list(itertools.islice(iter_messages(str(blf), chans[0]), 200))
    assert frames, "第一个通道应产出帧"
    assert all(f.channel == chans[0] for f in frames)
    ts = [f.ts_seconds for f in frames]
    # 容差 50ms：CANoe 多源交织记录时帧时间戳偶有 ~23ms 级倒挂（实测
    # A19G1 样例前 200 帧 39 处，最大 23ms）。reader 按文件序直通保序，
    # 容差只滤掉数据级毛刺，仍能抓住真正的时间戳打乱。
    assert all(ts[i] >= ts[i - 1] - 0.05 for i in range(1, len(ts))), \
        "时间戳应大体非递减（容差 50ms）"
    assert all(f.ts_seconds >= 0 for f in frames)
    assert all(0 <= f.dlc <= 64 and len(f.data) == f.dlc for f in frames)
    assert any(f.is_fd for f in frames) or True  # 打印 FD 帧占比，不强制
    fd_count = sum(1 for f in frames if f.is_fd)
    print(f"前 200 帧中 CANFD: {fd_count}")


# ---- 轻量通道探测 probe_channels ----

def test_probe_channels_matches_list_channels(tmp_path):
    """probe_channels（对象头级行走）== list_channels（完整解析）——
    多容器文件下通道集合逐位一致。"""
    p = tmp_path / "multi.blf"
    expected = _multi_container_blf(p)
    assert probe_channels(str(p)) == expected
    assert probe_channels(str(p)) == list_channels(str(p))


def test_probe_channels_accepts_unaligned_objects(unaligned_blf):
    """通道探测与参考解析器同样接受连续对象 padding。"""
    assert probe_channels(str(unaligned_blf)) == [0, 1]


def test_probe_channels_on_sample():
    """样例 BLF：探测与完整解析通道集合一致（覆盖 CANoe 真实文件布局，
    含跨容器对象/尾部衔接——合成 BLF 的 writer 不在消息间跨容器）。"""
    blf = sample_blf()
    if blf is None:
        pytest.skip("无样例 BLF 文件")
    assert probe_channels(str(blf)) == list_channels(str(blf))


def test_probe_channels_reports_monotonic_progress(tmp_path):
    """probe_channels 的 progress_cb 按文件字节位置单调推进，最终 100%。"""
    p = tmp_path / "prog3.blf"
    _multi_container_blf(p)
    values = []
    assert probe_channels(str(p), progress_cb=values.append) == [1, 2, 3]
    assert values, "至少一次进度回调"
    assert values[-1] == 100.0, "读完应报 100%"
    assert values == sorted(values), "进度应单调不降"


def test_probe_channels_cancel(tmp_path):
    """cancel_cb 置位 → raise ConversionCancelled；恒 False → 正常完成。"""
    p = tmp_path / "cancel1.blf"
    _big_blf(p)  # ≥1024 帧，检查点在 1024 帧处触发
    with pytest.raises(ConversionCancelled):
        probe_channels(str(p), cancel_cb=lambda: True)
    assert probe_channels(str(p), cancel_cb=lambda: False), \
        "cancel_cb 恒 False 不应取消"


def test_iter_all_messages_cancel(tmp_path):
    """iter_all_messages 的 cancel_cb 同款语义（转换读取阶段用）。"""
    p = tmp_path / "cancel2.blf"
    _big_blf(p)
    with pytest.raises(ConversionCancelled):
        list(iter_all_messages(str(p), cancel_cb=lambda: True))
    frames = list(iter_all_messages(str(p), cancel_cb=lambda: False))
    assert len(frames) >= 1024, "恒 False 不应取消"


def _raw_rel_ns(path):
    """读 BLF 首容器消息对象的对象头 rel 整数（验证读路径还原的值）。"""
    import struct
    import zlib

    from can.io.blf import (
        CAN_MESSAGE,
        CAN_MESSAGE2,
        FILE_HEADER_STRUCT,
        LOG_CONTAINER,
        LOG_CONTAINER_STRUCT,
        NO_COMPRESSION,
        OBJ_HEADER_BASE_STRUCT,
        OBJ_HEADER_V1_STRUCT,
        OBJ_HEADER_V2_STRUCT,
        ZLIB_DEFLATE,
    )

    with open(path, "rb") as f:
        header = FILE_HEADER_STRUCT.unpack(f.read(FILE_HEADER_STRUCT.size))
        f.seek(header[1])
        base = f.read(OBJ_HEADER_BASE_STRUCT.size)
        _, _, _, obj_size, obj_type = OBJ_HEADER_BASE_STRUCT.unpack(base)
        od = f.read(obj_size - OBJ_HEADER_BASE_STRUCT.size)
        method, _ = LOG_CONTAINER_STRUCT.unpack_from(od)
        cd = od[LOG_CONTAINER_STRUCT.size:]
        data = zlib.decompress(cd) if method == ZLIB_DEFLATE else cd
    pos = 0
    out = []
    while True:
        try:
            pos = data.index(b"LOBJ", pos, pos + 8)
        except ValueError:
            break
        if pos + 8 > len(data):
            break
        _, _, hv, osize, otype = OBJ_HEADER_BASE_STRUCT.unpack_from(data, pos)
        if otype not in (CAN_MESSAGE, CAN_MESSAGE2):
            break
        p = pos + OBJ_HEADER_BASE_STRUCT.size
        if hv == 1:
            flags, _, _, rel = OBJ_HEADER_V1_STRUCT.unpack_from(data, p)
        else:
            flags, _, _, rel = OBJ_HEADER_V2_STRUCT.unpack_from(data, p)
        out.append(rel if flags != 1 else rel * 10_000)
        pos += osize
    return out


def test_ts_seconds_integer_ns_construction(tmp_path):
    """非整数秒 start：ts_seconds = (SYSTEMTIME 毫秒部分 + 对象头 rel)×1e-9。

    根因（修复项）：python-can 读路径 timestamp = float(Decimal(rel)*1e-9)
    + start_timestamp，float64 加法在 ~1.78e9s 量级把整数 ns 帧时刻
    舍入到 238ns 网格（实测 A19G1 偏离 CANoe +72.5~73.5ns）。CANoe
    用整数运算：t = (SYSTEMTIME_ms 整数 + rel 整数) − int(S)×1e9。
    本测试断言读回的 ts_seconds 忠实还原对象头整数 rel（读路径不再
    引入任何浮点舍入）。
    """
    import can

    p = tmp_path / "subms.blf"
    start = 1784716800.624  # 非整数秒（毫秒 624，SYSTEMTIME 写入）
    offsets = [0.0, 0.123456789, 0.5, 1.000000001, 99.846363]
    with can.BLFWriter(str(p)) as w:
        for rel_s in offsets:
            w.on_message_received(can.Message(
                arbitration_id=0x123, data=b"\xAA\xBB",
                channel=1, timestamp=start + rel_s))
    frames = list(iter_all_messages(str(p)))
    rels = _raw_rel_ns(str(p))
    assert len(frames) == len(rels)
    # 注：python-can 写路径 rel = int((start+rel_s − start)×1e9) 是 float
    # 减法（写路径固有 238ns 网格，非本测试对象）；读路径必须逐位还原
    # 对象头整数，并按 CANoe 同构公式构造（ms_part 从毫秒整数推：
    # int(start) 的 float64 减法不可靠——1784716800.624 在 238ns 网格）
    ms_part = (int(round(start * 1000)) % 1000) * 1_000_000  # 624_000_000
    for i, (f, rel) in enumerate(zip(frames, rels)):
        expected = float(ms_part + rel) * 1e-9
        assert f.ts_seconds == expected, \
            f"帧 {i} ts_seconds={f.ts_seconds!r} 应还原对象头 rel={rel} 的整数构造 {expected!r}"


def test_ts_seconds_integer_ns_construction_integer_start(tmp_path):
    """整数秒 start（AHT 场景）：整数 ns 构造与 float 路径一致，不回归。"""
    import can

    p = tmp_path / "intstart.blf"
    offsets = [0.0, 1.0, 2.5, 10.125]
    with can.BLFWriter(str(p)) as w:
        for rel_s in offsets:
            w.on_message_received(can.Message(
                arbitration_id=0x123, data=b"\xAA",
                channel=1, timestamp=1784716800.0 + rel_s))
    frames = list(iter_all_messages(str(p)))
    for i, f in enumerate(frames):
        expected = float(int(round(offsets[i] * 1e9))) * 1e-9
        assert f.ts_seconds == expected, \
            f"帧 {i} 整数秒 start 下应精确等于 {expected!r}"
