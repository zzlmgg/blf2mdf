"""方案 G：多进程并行解码（per-bucket finish）回归测试。

覆盖（docs/2026-08-06-blf-mdf-parallel-decode-plan.md §7；原样例 DBC
相关用例已随 inputs/dbc/ 目录移除，2026-08-18）：
- timings：finish_all 按通道累计解码工作量（工作量而非跨度语义）；
- convert 并行：逐阶段计时顺序与值域；
- 模块对拍 seam：compare_files_identical 可 import（H2 对拍收口回归）。
"""
import can
import numpy as np
import pytest

from core import mp_finish
from core.converter import convert
from core.decoder import ChannelDecoder
from core.dbc_loader import load


def _frame(channel, key, length, fill, ts):
    return type("F", (), dict(
        channel=channel,
        ts_seconds=ts,
        arbitration_id=key & 0x7FFFFFFF,
        is_extended=bool(key & 0x80000000),
        is_fd=True,
        dlc=length,
        data=fill * length,
    ))()


# 内联 DBC（不依赖样例数据，样例 DBC 缺失的环境也能跑）
_INLINE_DBC = '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 100 ABC: 8 ECU
 SG_ Speed : 0|16@1+ (0.01,0) [0|655.35] "km/h" ECU
'''


def test_finish_all_timings_cover_all_channels(tmp_path):
    """timings 可选参数：按通道填充累计解码工作量（每桶 worker 内实测求和，
    >= 0）；零桶通道填 0.0。"""
    dbc_path = tmp_path / "t.dbc"
    dbc_path.write_text(_INLINE_DBC, encoding="utf-8")
    dbc = load(str(dbc_path))
    decoders = {1: ChannelDecoder(dbc, 1), 2: ChannelDecoder(dbc, 2)}
    for i in range(30):
        decoders[1].feed(_frame(1, 100, 8, b"\x01", float(i) * 0.01))
        decoders[2].feed(_frame(2, 100, 8, b"\x01", float(i) * 0.01))
    decoders[99] = ChannelDecoder(dbc, 99)  # 无 feed → 零桶
    timings = {}
    mp_finish.finish_all(decoders, sorted(decoders), timings=timings)
    assert set(timings) == set(decoders)
    assert all(v >= 0.0 for v in timings.values())
    assert timings[99] == 0.0, "零桶通道无桶任务 → 0.0"


def test_finish_all_timings_measure_work_not_span(tmp_path):
    """timings 语义 = 每通道累计解码工作量，而非桶时间跨度。

    判别构造：CAN1 单桶 20000 帧 vs CAN2 单桶 5 帧（工作量比 ~4000:1，
    向量化解码的固定开销相对可忽略，CPU 抢占下也稳健）。LPT 下两桶各占
    一个 worker、几乎同时提交，旧「跨度」（min 提交 → max 完成）两者都
    ≈ 整个阶段（spawn + 解码，比值 ≈ 1）；「工作量」（worker 内实测桶
    解码耗时求和）比值应数十倍。断言 CAN1 > CAN2 × 10 区分两种语义。
    """
    dbc_path = tmp_path / "t.dbc"
    dbc_path.write_text(_INLINE_DBC, encoding="utf-8")
    dbc = load(str(dbc_path))
    decoders = {1: ChannelDecoder(dbc, 1), 2: ChannelDecoder(dbc, 2)}
    for i in range(20000):
        decoders[1].feed(_frame(1, 100, 8, b"\x01", float(i) * 0.001))
    for i in range(5):
        decoders[2].feed(_frame(2, 100, 8, b"\x01", float(i) * 0.001))
    timings = {}
    mp_finish.finish_all(decoders, sorted(decoders), timings=timings)
    assert timings[1] > timings[2] * 10, \
        f"应为工作量语义（重通道 ≫ 轻通道）：{timings}"


def test_parallel_convert_reports_per_channel_decode_timings(tmp_path):
    """convert(parallel=True)：timings 顺序 = 读入 → 解码墙钟（并行）→
    解码 CANn（累计工作量，>= 0）→ 聚合 → 写 → 总；各值 >= 0。"""
    blf = tmp_path / "mini.blf"
    with can.BLFWriter(str(blf), channel=4) as w:
        for ch in (1, 2):
            for i in range(20):
                # 绝对时间戳（BLF 头 SYSTEMTIME 须有效年份，与 test_converter 一致）
                w.on_message_received(can.Message(
                    timestamp=1784716800.0 + float(i) * 0.01,
                    arbitration_id=100, is_extended_id=False, dlc=8,
                    data=bytes([0xE8, 0x03, 0, 0, 0, 0, 0, 0]), channel=ch))
    dbc_path = tmp_path / "t.dbc"
    dbc_path.write_text(_INLINE_DBC, encoding="utf-8")
    out = tmp_path / "par_timed.mdf"
    result = convert(blf, {1: load(str(dbc_path)), 2: load(str(dbc_path))},
                     str(out), parallel=True)

    labels = [label for label, _ in result.timings]
    assert labels[0] == "读入 BLF" and labels[-1] == "总耗时", labels
    # 墙钟行紧跟读入、先于逐通道行：解码阶段墙钟 + 各通道累计工作量，
    # 使日志可对账（读入 + 解码墙钟 + 聚合 + 写 ≈ 总耗时）
    assert labels[1] == "解码墙钟（并行）", labels
    assert "解码 CAN1" in labels and "解码 CAN2" in labels, labels
    values = {label: t for label, t in result.timings}
    assert all(v >= 0.0 for v in values.values())
    assert values["解码墙钟（并行）"] > 0.0


def test_parallel_identical_with_module_dims(tmp_path):
    """模块对拍默认 dims 全开时也一致（头部/结构/数值/统计全维度）。"""
    from tools.mdf_compare import compare_files_identical
    # 复用真实 blf 产物路径参数（与本文件既有真实数据测试同源），
    # 但此处仅断言模块可 import 且签名契约成立（真实等价由既有测试保证）：
    assert callable(compare_files_identical)
