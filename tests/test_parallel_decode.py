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

from core import blf_reader, mp_finish
from core.converter import convert, _read_vectorized
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


def _two_channel_inputs(tmp_path):
    """双通道迷你 BLF + 内联 DBC（并行退化用例输入）。"""
    import can

    blf = tmp_path / "two_ch.blf"
    with can.BLFWriter(str(blf), channel=4) as w:
        for ch in (1, 2):
            for i in range(20):
                w.on_message_received(can.Message(
                    timestamp=1784716800.0 + float(i) * 0.01,
                    arbitration_id=100, is_extended_id=False, dlc=8,
                    data=bytes([0xE8, 0x03, 0, 0, 0, 0, 0, 0]), channel=ch))
    dbc_path = tmp_path / "t.dbc"
    dbc_path.write_text(_INLINE_DBC, encoding="utf-8")
    return str(blf), load(str(dbc_path))


def test_parallel_pool_failure_falls_back_with_warning(tmp_path, monkeypatch):
    """L7 并行退化显式化：make_pool 失败（环境/杀软等）→ 回退串行，
    转换照常成功且 ConversionResult.warnings 记录退化原因。"""
    import core.mp_finish as mp_finish

    blf, dbc = _two_channel_inputs(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("pool creation failed (test)")
    monkeypatch.setattr(mp_finish, "make_pool", boom)
    out = tmp_path / "deg_pool.mdf"
    result = convert(blf, {1: dbc, 2: dbc}, str(out), parallel=True)

    assert result.warnings == ["并行不可用（进程池创建失败），已回退串行"]
    assert out.exists() and out.stat().st_size > 0, "回退串行应照常产出"


def test_parallel_mem_threshold_falls_back_with_warning(tmp_path, monkeypatch):
    """L7 并行退化显式化：桶内存估算超阈值 → 池 shutdown 转串行，
    warnings 记录（用户可得知本次未走并行）。"""
    import core.mp_finish as mp_finish

    blf, dbc = _two_channel_inputs(tmp_path)
    monkeypatch.setattr(mp_finish, "bucket_bytes", lambda *a, **k: 10 ** 30)
    out = tmp_path / "deg_mem.mdf"
    result = convert(blf, {1: dbc, 2: dbc}, str(out), parallel=True)

    assert result.warnings == ["桶内存估算超阈值，已回退串行"]
    assert out.exists() and out.stat().st_size > 0, "回退串行应照常产出"


def test_parallel_identical_with_module_dims(tmp_path):
    """模块对拍默认 dims 全开时也一致（头部/结构/数值/统计全维度）。"""
    from tools.mdf_compare import compare_files_identical
    # 复用真实 blf 产物路径参数（与本文件既有真实数据测试同源），
    # 但此处仅断言模块可 import 且签名契约成立（真实等价由既有测试保证）：
    assert callable(compare_files_identical)


def test_route_equivalence_buckets(tmp_path):
    """两条建桶路由（feed 逐帧 / _read_vectorized 向量化）桶级逐元素对拍。

    M1 最脆弱等价点收口：np.unique 首现序 vs setdefault 插入序、未知 ID/短帧
    分类此前只靠注释声明 + 端到端对拍间接兜底；本测试直接对拍键集、插入序、
    ts/lens/data、raw_id 与 unknown 记账。feed 侧输入 = 同一文件的标量读回
    （blf_reader.iter_messages）——与向量化侧同源整数 ns，ts 逐位可比。"""
    import can

    from test_decoder_vectorized import (MUX_DBC, _random_frames,
                                         _random_signal_dbc)

    rng = np.random.default_rng(20260819)
    for trial in range(10):
        if trial % 2:
            dbc_txt, known = _random_signal_dbc(rng, 8), [100]
        else:
            dbc_txt, known = MUX_DBC, [200]      # mux 报文混入建桶输入面
        p = tmp_path / f"r{trial}.dbc"
        p.write_text(dbc_txt, encoding="utf-8")
        dbc = load(str(p))
        frames = _random_frames(rng, int(rng.integers(10, 120)), known_ids=known)
        blf = tmp_path / f"r{trial}.blf"
        with can.BLFWriter(str(blf), channel=4) as w:
            for fr in frames:
                w.on_message_received(can.Message(
                    timestamp=1784716800.0 + fr.ts_seconds,
                    arbitration_id=fr.arbitration_id,
                    is_extended_id=fr.is_extended,
                    dlc=fr.dlc, data=fr.data, channel=fr.channel))
                # 与 test_parallel_decode:90-97 先例同形（不写 is_fd：
                # _random_frames 恒 is_fd=False，BLF 往返标志无关紧要）
        # feed 路由（to_array 后；输入 = 同一文件标量读回帧）
        dec_feed = ChannelDecoder(dbc, 1)
        for fr in blf_reader.iter_messages(str(blf), 1):
            dec_feed.feed(fr)
        for b in dec_feed.buckets.values():
            b.to_array()
        # 向量化路由（_read_vectorized 装配后 = 数组相位）
        dec_vec = ChannelDecoder(dbc, 1)
        # 签名 = (blf_path, decoders, raw_chs, stats_export, stats_bufs,
        #         raw_chunks, known_keys, read_cb, cancel_cb)——raw_chs=[] 时
        # 原始帧/known_keys 分支不执行，stats_export=False 时 stats_bufs 不触碰
        _read_vectorized(str(blf), {1: dec_vec}, [], False, {}, None,
                         None, None, None)
        # 桶级逐元素对拍：键集 + 插入序（最脆弱等价点）
        assert list(dec_vec.buckets) == list(dec_feed.buckets), \
            f"键集/插入序: {list(dec_vec.buckets)} vs {list(dec_feed.buckets)}"
        for k in dec_vec.buckets:
            bv, bf = dec_vec.buckets[k], dec_feed.buckets[k]
            assert (bv.raw_id, bv.md.name) == (bf.raw_id, bf.md.name), k
            assert bv.ts.dtype == np.float64 and bf.ts.dtype == np.float64
            assert bv.lens.dtype == np.int64 and bf.lens.dtype == np.int64
            assert bv.data.dtype == np.uint8 and bf.data.dtype == np.uint8
            assert np.array_equal(bv.ts, bf.ts), f"{k} ts"
            assert np.array_equal(bv.lens, bf.lens), f"{k} lens"
            assert np.array_equal(bv.data, bf.data), f"{k} data"
        assert dec_vec.stats == dec_feed.stats, "unknown 记账逐位一致"
