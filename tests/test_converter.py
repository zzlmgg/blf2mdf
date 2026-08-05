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
        # 通道 2：原始帧（一个 DBC 已知 ID 0x100、一个未知 ID 0x456）
        w.on_message_received(can.Message(arbitration_id=100, is_extended_id=False,
                                          data=b"\xAA\xBB", channel=2, timestamp=1784716803.0))
        w.on_message_received(can.Message(arbitration_id=0x456, is_extended_id=False,
                                          data=b"\xCC\xDD", channel=2, timestamp=1784716804.0))
        # 通道 4：只有未知 ID 的原始帧
        w.on_message_received(can.Message(arbitration_id=0x777, is_extended_id=False,
                                          data=b"\xEE", channel=4, timestamp=1784716805.0))
    dbc = tmp_path / "t.dbc"
    dbc.write_text(INLINE_DBC, encoding="utf-8")
    return str(blf), str(dbc)


def test_convert_mixed_channels(tmp_path, blf_and_dbc):
    blf, dbc_path = blf_and_dbc
    out = tmp_path / "out.mdf"
    result = convert(blf, {1: load(dbc_path), 2: None}, str(out),
                     raw_export=True)

    assert result.duration_seconds == pytest.approx(3.0)  # 1784716800.0s → 1784716803.0s（0x456 帧被过滤不入组）
    by_ch = {s.channel: s for s in result.summaries}
    s1 = by_ch[1]
    assert s1.bound and s1.decoded_frames == 2
    assert s1.signal_count == 1
    assert s1.unknown_frames == 1 and s1.unknown_ids == 1
    assert s1.warning == ""
    s2 = by_ch[2]
    assert not s2.bound and s2.raw_frames == 1
    assert s2.unknown_frames == 1 and s2.unknown_ids == 1  # 0x456 无 DBC 定义被过滤

    m = MDF(str(out))
    speed = m.get("Speed")
    assert np.allclose(speed.samples, [10.0, 50.0])   # 0x03E8→10.0, 0x1388→50.0
    assert {g.channel_group.acq_name for g in m.groups} == {"ABC", "Raw::CAN2"}
    data = m.get("Data")
    assert data.samples.tolist() == [[0xAA, 0xBB]]    # 只保留 DBC 已知 ID 的帧
    assert m.get("ID").samples.tolist() == [100]


def test_convert_raw_export_off_by_default(tmp_path, blf_and_dbc):
    """修复项 5：原始帧导出默认关闭（与 CANoe 一致）——未绑定通道不产出
    Raw:: 组，摘要 raw_frames=0；输出组数 = 解码组数。"""
    blf, dbc_path = blf_and_dbc
    out = tmp_path / "no_raw.mdf"
    result = convert(blf, {1: load(dbc_path), 2: None}, str(out))

    by_ch = {s.channel: s for s in result.summaries}
    s2 = by_ch[2]
    assert not s2.bound and s2.raw_frames == 0 and s2.unknown_frames == 0
    m = MDF(str(out))
    assert {g.channel_group.acq_name for g in m.groups} == {"ABC"}
    # 解码数据不受影响
    assert np.allclose(m.get("Speed").samples, [10.0, 50.0])


def test_convert_raw_export_off_skips_collection(tmp_path, blf_and_dbc):
    """raw_export=False 显式指定：未绑定通道完全跳过原始帧收集（不迭代帧）。"""
    blf, dbc_path = blf_and_dbc
    out = tmp_path / "no_raw2.mdf"
    result = convert(blf, {1: load(dbc_path), 2: None}, str(out),
                     raw_export=False)
    s2 = next(s for s in result.summaries if s.channel == 2)
    assert s2.raw_frames == 0 and s2.unknown_frames == 0
    m = MDF(str(out))
    assert {g.channel_group.acq_name for g in m.groups} == {"ABC"}


def test_convert_relative_timestamps_and_start_time(tmp_path, blf_and_dbc):
    """修复项 2：时间基准对齐 CANoe——时间轴相对（全局首帧归零），
    绝对起始时间写入 MDF 头部 start_time（naive UTC 整秒，与参考 _T058.mdf 一致）。"""
    from datetime import datetime

    blf, dbc_path = blf_and_dbc
    out = tmp_path / "rel.mdf"
    convert(blf, {1: load(dbc_path), 2: None}, str(out), raw_export=True)

    m = MDF(str(out))
    # 解码组：最早帧 1784716800.0 归零 → 0.0 / 1.0（原绝对时间戳）
    assert np.allclose(m.get("t", group=0).samples, [0.0, 1.0])
    # 原始帧组：1784716803.0 保留帧 → 3.0（0x456 未知 ID 帧被过滤，跨组同一基准）
    assert np.allclose(m.get("t", group=1).samples, [3.0])
    # MDF 头部绝对起始时间：floor(min_ts) = 2026-07-22 10:40:00（UTC 整秒，与 CANoe 一致）
    assert m.header.start_time == datetime(2026, 7, 22, 10, 40)
    assert m.header.abs_time == 1784716800000000000


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
    result = convert(blf, {1: None, 3: load(dbc_path)}, str(out),
                     raw_export=True)
    s3 = next(s for s in result.summaries if s.channel == 3)
    assert s3.warning == "该通道无匹配帧"
    # 通道 1 原始帧：DBC 存在时按 ID 过滤（ID 100 保留 ×2，ID 999 丢弃）
    s1 = next(s for s in result.summaries if s.channel == 1)
    assert s1.raw_frames == 2 and s1.unknown_frames == 1 and s1.unknown_ids == 1
    m = MDF(str(out))
    assert {g.channel_group.acq_name for g in m.groups} == {"Raw::CAN1"}  # 通道 3 空组不写入
    assert m.get("ID").samples.tolist() == [100, 100]


def test_convert_raw_without_dbc_keeps_all_frames(tmp_path, blf_and_dbc):
    """无任何 DBC 参与转换：原始导出不过滤，全部帧保留。"""
    blf, _ = blf_and_dbc
    out = tmp_path / "all_raw.mdf"
    result = convert(blf, {1: None}, str(out), raw_export=True)
    s1 = result.summaries[0]
    assert s1.raw_frames == 3  # ID 100 ×2 + ID 999
    assert s1.unknown_frames == 0
    m = MDF(str(out))
    assert m.get("ID").samples.tolist() == [100, 100, 999]


def test_convert_raw_all_unknown_frames_skipped(tmp_path, blf_and_dbc):
    """原始通道帧全部无 DBC 定义：过滤后为空，组不写入并给出警告。"""
    blf, dbc_path = blf_and_dbc
    out = tmp_path / "filtered.mdf"
    result = convert(blf, {4: None, 1: load(dbc_path)}, str(out),
                     raw_export=True)
    s4 = next(s for s in result.summaries if s.channel == 4)
    assert s4.raw_frames == 0 and s4.unknown_frames == 1
    assert s4.warning == "全部原始帧未匹配 DBC"
    m = MDF(str(out))
    assert {g.channel_group.acq_name for g in m.groups} == {"ABC"}  # Raw::CAN4 空组不写入


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
