from pathlib import Path

import numpy as np
import pytest
from asammdf import MDF

from core.blf_reader import ConversionCancelled
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
                     raw_export=True, stats_export=False)

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
    result = convert(blf, {1: load(dbc_path), 2: None}, str(out), stats_export=False)

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
                     raw_export=False, stats_export=False)
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
    # 细粒度修复：读取阶段按字节位置多次回调（5→10 爬升），而非停在 5%；
    # 全程 percent 单调不降（跨阶段，含解码/统计逐段上报）
    read_pcts = [p for s, p in calls if s == "读取 BLF"]
    assert read_pcts, "读取阶段应有进度回调"
    assert read_pcts == sorted(read_pcts)
    assert max(read_pcts) <= 10.0, "读取阶段应落在 5→10 区间"
    pcts = [p for _, p in calls]
    assert pcts == sorted(pcts), "进度应单调不降"
    assert len(set(s for s, _ in calls if s.startswith("聚合统计"))) >= 2, \
        "统计阶段应按通道逐段上报（92→95）"


def test_convert_no_matching_frames_warns(tmp_path, blf_and_dbc):
    blf, dbc_path = blf_and_dbc
    out = tmp_path / "empty.mdf"
    result = convert(blf, {1: None, 3: load(dbc_path)}, str(out),
                     raw_export=True, stats_export=False)
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
    result = convert(blf, {1: None}, str(out), raw_export=True, stats_export=False)
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
                     raw_export=True, stats_export=False)
    s4 = next(s for s in result.summaries if s.channel == 4)
    assert s4.raw_frames == 0 and s4.unknown_frames == 1
    assert s4.warning == "全部原始帧未匹配 DBC"
    m = MDF(str(out))
    assert {g.channel_group.acq_name for g in m.groups} == {"ABC"}  # Raw::CAN4 空组不写入


def test_convert_no_channels_raises(tmp_path, blf_and_dbc):
    blf, _ = blf_and_dbc
    with pytest.raises(ValueError):
        convert(blf, {}, str(tmp_path / "x.mdf"))


def test_convert_write_failure_propagates(tmp_path, blf_and_dbc, monkeypatch):
    """写 MDF 失败：convert 上抛且不重复清理——无残留契约归 write_mdf
    （2026-08-18 spec D），清理语义由 writer 层测试守护，converter 不复述
    中间文件命名（deletion test：writer 改临时文件策略不连带 converter）。"""
    blf, dbc_path = blf_and_dbc
    out = tmp_path / "out.mdf"

    def boom(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr("core.converter.mdf_writer.write_mdf", boom)
    with pytest.raises(OSError):
        convert(blf, {1: load(dbc_path)}, str(out))


def test_convert_stats_export_default_ones_groups(tmp_path, blf_and_dbc):
    """修复项 4：stats_export 默认开——输出 10 项 × 16 通道 = 160 个 '1s' 统计组，
    覆盖 0-15 全部通道（无数据通道全 0），与 DBC 绑定无关。"""
    blf, dbc_path = blf_and_dbc
    out = tmp_path / "out.mdf"
    convert(blf, {1: load(dbc_path)}, str(out))
    m = MDF(str(out))
    ones = [g for g in m.groups if g.channel_group.acq_name == "1s"]
    assert len(ones) == 160
    # 统计组在文件尾部（解码组 1 个 + 统计 160 个），组序 = 通道 × 统计项：
    # ch1 的 StdData = 组 1 + 1*10 + 0；ch1（绑定，3 帧 @ 0/1/2s）累计 C[1]=帧'<1.109=2
    std = m.get("StdData", group=1 + 10)
    assert std.samples.dtype == np.int32
    assert std.samples.tolist()[:3] == [0, 2, 3]
    # ch15（无数据通道）的 StdDataRate 全 0（组 1 + 15*10 + 1）
    rate = m.get("StdDataRate", group=1 + 15 * 10 + 1)
    assert np.all(rate.samples == 0.0)


def test_convert_stats_export_off_no_ones_groups(tmp_path, blf_and_dbc):
    """stats_export=False：不输出 '1s' 统计组。"""
    blf, dbc_path = blf_and_dbc
    out = tmp_path / "out.mdf"
    convert(blf, {1: load(dbc_path)}, str(out), stats_export=False)
    m = MDF(str(out))
    assert {g.channel_group.acq_name for g in m.groups} == {"ABC"}


def test_convert_stats_t_axis_last_point_full_precision(tmp_path):
    """修复：统计组 t 轴末点 = 未舍入的全局末帧时刻（与 CANoe 全精度一致）。

    回归背景：converter 曾把 global_end round 到 1ms 再传（末点 254.42639749 →
    254.426，差 0.4ms）；CANoe 的 t 轴末点保留全精度（实测 A19G1 参考文件
    254.42639749 vs 我们 254.426）。窗数不受影响：ceil(未舍入) 与 ceil(1ms 舍入) 一致。
    """
    import can

    blf = tmp_path / "subms.blf"
    with can.BLFWriter(str(blf)) as w:
        w.on_message_received(can.Message(arbitration_id=100, is_extended_id=False,
                                          data=bytes([0xE8, 0x03, 0, 0, 0, 0, 0, 0]),
                                          channel=1, timestamp=1784716800.0))
        # 末帧时间戳带亚毫秒精度：1784716805.123456 → 相对 = 5.123456
        w.on_message_received(can.Message(arbitration_id=100, is_extended_id=False,
                                          data=bytes([0x88, 0x13, 0, 0, 0, 0, 0, 0]),
                                          channel=1, timestamp=1784716805.123456))
    dbc = tmp_path / "t.dbc"
    dbc.write_text(INLINE_DBC, encoding="utf-8")
    out = tmp_path / "out.mdf"
    convert(blf, {1: load(dbc)}, str(out))

    m = MDF(str(out))
    ones = [g for g in m.groups if g.channel_group.acq_name == "1s"]
    assert len(ones) == 160
    std = m.get("StdData", group=1 + 10)  # ch1 StdData（组 1 = 解码组）
    t = np.asarray(std.timestamps)
    assert len(t) == 7, t                     # ceil(5.123456) = 6 窗 + 首点
    assert t[-2] == 5.009
    # 末点保留全精度（仅容忍 BLF 写入/读回的浮点舍入，不允许 1ms 截断）
    assert t[-1] == pytest.approx(5.123456, abs=1e-6), t[-1]


# ---- 转换取消 ----

def test_convert_cancel_preset_no_output(tmp_path, blf_and_dbc):
    """cancel_cb 预置 True：转换在任何检查点（读取后）即抛 ConversionCancelled，
    输出文件与半成品 .mf4 均不残留。"""
    blf, dbc_path = blf_and_dbc
    out = tmp_path / "cancelled.mdf"
    with pytest.raises(ConversionCancelled):
        convert(blf, {1: load(dbc_path)}, str(out), cancel_cb=lambda: True)
    assert not out.exists(), "取消后不应残留输出文件"
    assert not Path(str(out).replace(".mdf", ".mf4")).exists(), \
        "取消后不应残留半成品 .mf4"


def test_convert_cancel_mid_read(tmp_path):
    """读取阶段中途取消：≥1024 帧文件，cancel_cb 在第 3 次检查（3072 帧处）
    置位 → 抛出且无输出。"""
    import can

    blf = tmp_path / "big_cancel.blf"
    with can.BLFWriter(str(blf)) as w:
        for i in range(3500):
            w.on_message_received(can.Message(
                arbitration_id=100, is_extended_id=False,
                data=bytes([0xE8, 0x03, 0, 0, 0, 0, 0, 0]),
                channel=1, timestamp=1784716800.0 + i))
    dbc = tmp_path / "t.dbc"
    dbc.write_text(INLINE_DBC, encoding="utf-8")
    out = tmp_path / "mid_cancel.mdf"
    state = {"calls": 0}

    def cancel():
        state["calls"] += 1
        return state["calls"] >= 3  # 1024、2048 帧处 False，3072 帧处 True

    with pytest.raises(ConversionCancelled):
        convert(blf, {1: load(dbc)}, str(out), cancel_cb=cancel)
    assert not out.exists()


def test_convert_parallel_cancel_during_finish(tmp_path, blf_and_dbc):
    """并行解码（finish_all 桶收集循环）中取消：cancel_cb 在读取后检查点放行、
    首个桶完成时置位 → ConversionCancelled 从 finish_all 上抛（不被 except Exception
    吞掉转串行重解），输出不残留。"""
    blf, dbc_path = blf_and_dbc
    out = tmp_path / "par_cancel.mdf"
    state = {"calls": 0}

    def cancel():
        state["calls"] += 1
        return state["calls"] >= 2  # 读取后检查点（第 1 次）放行，桶完成时（第 2 次）取消

    with pytest.raises(ConversionCancelled):
        convert(blf, {1: load(dbc_path), 2: load(dbc_path)}, str(out),
                parallel=True, cancel_cb=cancel)
    assert not out.exists()


def test_convert_cancel_after_write_discards_output(tmp_path, blf_and_dbc,
                                                    monkeypatch):
    """写后取消分支（2026-08-18 spec D5 唯一剩余清理）：write_mdf 成功返回后
    cancel_cb 置位 → 完整产物被删除 + ConversionCancelled。删除完整产物是
    「取消 = 放弃本次转换」的 converter 业务语义（CONTEXT.md：取消不是失败）。

    取消条件 = write_mdf 已完成的标志（状态机，非检查点计数）：前面所有
    检查点（读取后/解码/写前）均放行，第一个写后检查点必然置位——精确命中
    写后分支，fixture 通道数变化不破坏本测试。written 守护：若写后检查点
    被移除，convert 正常返回、pytest.raises 失败（测试立即暴露）。"""
    import core.converter

    blf, dbc_path = blf_and_dbc
    out = tmp_path / "post_cancel.mdf"
    state = {"written": False}
    orig_write = core.converter.mdf_writer.write_mdf

    def tracked_write(*args, **kwargs):
        state["written"] = True
        return orig_write(*args, **kwargs)

    monkeypatch.setattr("core.converter.mdf_writer.write_mdf", tracked_write)

    def cancel():
        return state["written"]

    with pytest.raises(ConversionCancelled):
        convert(blf, {1: load(dbc_path)}, str(out), cancel_cb=cancel)
    assert state["written"], "取消必须发生在 write_mdf 完成后（否则测试空过）"
    assert not out.exists(), "取消 = 放弃本次转换：完整产物应被删除"


# ---- 阶段计时（GUI 日志用）----

def test_convert_reports_stage_timings(tmp_path, blf_and_dbc):
    """串行 convert 返回 timings：标签顺序 读入 BLF → 解码 CANn → 统计聚合
    → 写 MDF → 总耗时；只含已绑定通道的解码项；所有值 >= 0。
    串行逐通道耗时本身即墙钟，无并行路径的「解码墙钟」行。"""
    blf, dbc_path = blf_and_dbc
    out = tmp_path / "timed.mdf"
    result = convert(blf, {1: load(dbc_path), 2: None}, str(out))

    labels = [label for label, _ in result.timings]
    assert labels[0] == "读入 BLF" and labels[-1] == "总耗时", labels
    assert "解码 CAN1" in labels, labels
    assert "解码 CAN2" not in labels, "未绑定通道无解码项"
    assert "解码墙钟（并行）" not in labels, "串行路径无并行墙钟行"
    assert "统计聚合" in labels and "写 MDF" in labels, labels
    values = {label: t for label, t in result.timings}
    assert all(v >= 0.0 for v in values.values())
    assert values["总耗时"] >= values["读入 BLF"]


def test_convert_cancel_discards_timings(tmp_path, blf_and_dbc):
    """取消路径：ConversionCancelled 上抛，timings 随异常丢弃（GUI 取消日志不带耗时）。"""
    blf, dbc_path = blf_and_dbc
    with pytest.raises(ConversionCancelled):
        convert(blf, {1: load(dbc_path)}, str(tmp_path / "x.mdf"),
                cancel_cb=lambda: True)


# ---- H1 Step 4：FD/扩展/变长向量化读入结构测试（A/B 开关已删除）----

INLINE_DBC_FD = '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 512 ABC2: 8 ECU
 SG_ Speed2 : 0|16@1+ (0.01,0) [0|655.35] "km/h" ECU

BO_ 768 MSG300: 8 ECU
 SG_ Dummy3 : 0|8@1+ (1,0) [0|255] "" ECU
'''


@pytest.fixture()
def blf_and_dbc_fd(tmp_path):
    """FD 帧 + 变长载荷 + 扩展帧夹具：exercises 桶块拼接（lens 8→24 pad）、
    原始组行宽（FD dlc2len 48 / 经典 dlc 1）、扩展帧归一化键。"""
    import can

    blf = tmp_path / "fd.blf"
    with can.BLFWriter(str(blf)) as w:
        # 0x200（标准 FD）：8 字节（入桶，lens=8）
        w.on_message_received(can.Message(arbitration_id=0x200, is_extended_id=False,
                                          is_fd=True, data=bytes(range(8)),
                                          channel=3, timestamp=1784716800.0))
        # 0x200（标准 FD）：24 字节（同桶追加，lens=24，触发 data 列宽 pad）
        w.on_message_received(can.Message(arbitration_id=0x200, is_extended_id=False,
                                          is_fd=True, data=bytes(range(24)),
                                          channel=3, timestamp=1784716801.0))
        # 0x200（扩展帧）：归一化键含 EFF 位 → 与标准帧不同桶，且无 DBC 定义
        w.on_message_received(can.Message(arbitration_id=0x200, is_extended_id=True,
                                          data=b"\x01\x02", channel=3,
                                          timestamp=1784716802.0))
        # 通道 5 原始：0x300 FD 48 字节（行宽 = dlc2len 48）+ 0x200 短帧
        # （经典 dlc 原值 1）；0x300 在 DBC 中有定义 → 不被过滤
        w.on_message_received(can.Message(arbitration_id=0x300, is_extended_id=False,
                                          is_fd=True, data=bytes(range(48)),
                                          channel=5, timestamp=1784716803.0))
        w.on_message_received(can.Message(arbitration_id=0x200, is_extended_id=False,
                                          data=b"\xAA", channel=5,
                                          timestamp=1784716804.0))
    dbc = tmp_path / "t_fd.dbc"
    dbc.write_text(INLINE_DBC_FD, encoding="utf-8")
    return str(blf), str(dbc)


def test_convert_fd_bucketing_and_raw_assembly(tmp_path, blf_and_dbc_fd):
    """FD/扩展/变长帧经向量化单遍扫描：解码桶（8→24 pad 后同桶两样本）、
    扩展帧归未知、原始组行宽 max(dlc2len)=48 且短帧补零。"""
    blf, dbc_path = blf_and_dbc_fd
    out = tmp_path / "fd_out.mdf"
    result = convert(blf, {3: load(dbc_path), 5: None}, str(out),
                     raw_export=True, stats_export=False)

    by_ch = {s.channel: s for s in result.summaries}
    s3 = by_ch[3]
    assert s3.bound and s3.decoded_frames == 2
    assert s3.unknown_frames == 1 and s3.unknown_ids == 1   # 扩展帧 0x200 无定义
    s5 = by_ch[5]
    assert not s5.bound and s5.raw_frames == 2
    assert s5.unknown_frames == 0 and s5.unknown_ids == 0   # 0x300 在 DBC 有定义

    m = MDF(str(out))
    gi = next(i for i, g in enumerate(m.groups)
              if g.channel_group.acq_name == "Raw::CAN5")
    assert {g.channel_group.acq_name for g in m.groups} == {"ABC2", "Raw::CAN5"}
    assert np.allclose(m.get("Speed2").samples, [2.56, 2.56])  # 0x0100 × 0.01
    assert np.allclose(m.get("t", group=0).samples, [0.0, 1.0])
    assert m.get("ID", group=gi).samples.tolist() == [0x300, 0x200]
    assert m.get("DLC", group=gi).samples.tolist() == [48, 1]
    data = np.asarray(m.get("Data", group=gi).samples)
    assert data.shape == (2, 48)
    assert data[0].tolist() == list(range(48))
    assert data[1][0] == 0xAA and not data[1][1:].any()      # 短帧补零
    assert m.get("IsFD", group=gi).samples.tolist() == [1, 0]
    assert np.allclose(m.get("t", group=gi).samples, [3.0, 4.0])
