"""方案 G：多进程并行解码（per-bucket finish）回归测试。

覆盖（docs/2026-08-06-blf-mdf-parallel-decode-plan.md §7）：
1. 合成数据：并行 finish_all vs 串行全量逐点等价（含 stats 合并）；
2. 真实样例：convert(parallel=True) vs parallel=False 输出逐组全量一致（golden）；
3. 确定性：同一 decoders 两次并行 finish 组序/stats 一致（LPT 幂等）；
4. 回退：池创建失败 → 自动串行，输出与串行一致；
5. 短路：单通道不建池；
6. 合并语义：未知/短帧计数在并行与串行严格等价。
"""
import can
import numpy as np
import pytest
from asammdf import MDF

from core import mp_finish
from core.converter import convert
from core.decoder import ChannelDecoder
from core.dbc_loader import load

from conftest import DBC_DIR, sample_blf


# ── 合成数据辅助 ──

def _feed_synthetic(decoders: dict[int, ChannelDecoder]) -> None:
    """每个通道 feed：前 3 个已知报文 × 50 帧 + 未知 ID 3 帧 + 已知短帧 2 帧。"""
    for ch, dec in decoders.items():
        keys = list(dec.dbc.messages.keys())
        for k in keys[:3]:
            m = dec.dbc.messages[k]
            for i in range(50):
                dec.feed(_frame(ch, k, m.frame_length, b"\x01", float(i) * 0.1))
        for i in range(3):
            dec.feed(_frame(ch, 0x7FF, 8, b"\x02", float(i)))
        for i in range(2):
            # 已知 ID 短帧：长度不足 frame_length → feed 即计未知、不入桶
            dec.feed(_frame(ch, keys[0], 8, b"\x03", float(i)))


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


@pytest.fixture
def synthetic_decoders():
    d3 = load(str(DBC_DIR / "VDCCCU_CANFD2.dbc"))
    d6 = load(str(DBC_DIR / "VDCCCU_CANFD3.dbc"))
    decoders = {3: ChannelDecoder(d3, 3), 6: ChannelDecoder(d6, 6)}
    _feed_synthetic(decoders)
    return decoders


def _mini_blf(tmp_path, decoders) -> str:
    """合成 BLF：各通道前 2 报文 × 20 帧 + 未知 ID 2 帧（python-can 写出）。"""
    path = tmp_path / "mini.blf"
    with can.BLFWriter(str(path), channel=16) as w:
        for ch, dec in decoders.items():
            for k in list(dec.dbc.messages.keys())[:2]:
                m = dec.dbc.messages[k]
                for i in range(20):
                    w.on_message_received(can.Message(
                        timestamp=float(i) * 0.1,
                        arbitration_id=k & 0x7FFFFFFF,
                        is_extended_id=bool(k & 0x80000000),
                        is_fd=True,          # 样例报文为 64 字节 CANFD（dlc>8 需 FD 对象）
                        dlc=m.frame_length,
                        data=b"\x01" * m.frame_length,
                        channel=ch,
                    ))
            for i in range(2):
                w.on_message_received(can.Message(
                    timestamp=float(i), arbitration_id=0x7FF,
                    dlc=8, data=b"\x02" * 8, channel=ch,
                ))
    return str(path)


def _assert_series_list_equal(par_series, ser_series, ch):
    assert len(par_series) == len(ser_series), \
        f"CAN{ch}: 系列数 {len(par_series)} vs {len(ser_series)}"
    for sa, sb in zip(par_series, ser_series):
        diff = mp_finish._assert_series_equal(sa, sb)
        assert diff is None, f"CAN{ch}: {diff}"


def _assert_stats_equal(a, b, ch):
    assert a.total_frames == b.total_frames, f"CAN{ch}: total"
    assert a.unknown_frames == b.unknown_frames, f"CAN{ch}: unknown"
    assert a.unknown_ids == b.unknown_ids, f"CAN{ch}: ids"


# ── 1. 合成数据全量等价 ──

def test_parallel_matches_serial_synthetic(synthetic_decoders):
    channels = sorted(synthetic_decoders)
    par = mp_finish.finish_all(synthetic_decoders, channels)
    ser = {ch: synthetic_decoders[ch].finish() for ch in channels}
    for ch in channels:
        _assert_series_list_equal(par[ch][0], ser[ch][0], ch)
        _assert_stats_equal(par[ch][1], ser[ch][1], ch)


# ── 2. 真实样例双跑逐组全量一致（golden）──

def _mdf_equal(a_path, b_path) -> list[str]:
    """逐组全量对比 → 差异列表（空 = 一致）。bytes 采样去尾零比较。"""
    a, b = MDF(a_path), MDF(b_path)
    diffs = []
    if len(a.groups) != len(b.groups):
        return [f"组数 {len(a.groups)} vs {len(b.groups)}"]
    for gi, (ga, gb) in enumerate(zip(a.groups, b.groups)):
        na, nb = ga.channel_group.acq_name, gb.channel_group.acq_name
        if na != nb:
            diffs.append(f"组 {gi}: {na!r} vs {nb!r}")
            continue
        ca = [c.name for c in ga.channels]
        cb = [c.name for c in gb.channels]
        if ca != cb:
            diffs.append(f"组 {gi} {na}: 通道序 {ca[:6]} vs {cb[:6]}")
            continue
        for chn in ca:
            va = np.asarray(a.get(chn, group=gi).samples)
            vb = np.asarray(b.get(chn, group=gi).samples)
            if va.dtype != vb.dtype:
                diffs.append(f"组 {gi} {na}.{chn}: dtype")
                continue
            if va.dtype.kind == "S":
                ok = np.array_equal(np.char.rstrip(va.astype("|S256"), b"\x00"),
                                    np.char.rstrip(vb.astype("|S256"), b"\x00"))
            else:
                ok = np.array_equal(va, vb, equal_nan=va.dtype.kind == "f")
            if not ok:
                diffs.append(f"组 {gi} {na}.{chn}: 采样不一致")
    return diffs


@pytest.mark.golden
def test_parallel_matches_serial_real_blf(tmp_path):
    blf = sample_blf()
    if blf is None:
        pytest.skip("无样例 BLF")
    bindings = {}
    for ch, dbc_name in ((1, "VDCPublic_CANFD1.dbc"), (3, "VDCCCU_CANFD2.dbc"),
                         (6, "VDCCCU_CANFD3.dbc")):
        p = DBC_DIR / dbc_name
        if p.exists():
            bindings[ch] = load(str(p))
    assert len(bindings) >= 2, "样例 DBC 不可用"
    out_ser = tmp_path / "par_serial.mdf"
    out_par = tmp_path / "par_parallel.mdf"
    convert(str(blf), bindings, str(out_ser), parallel=False)
    convert(str(blf), bindings, str(out_par), parallel=True)
    diffs = _mdf_equal(str(out_par), str(out_ser))
    assert not diffs, f"并行 vs 串行输出差异:\n" + "\n".join(diffs[:20])


# ── 3. 确定性：同一 decoders 两次并行 finish 组序/stats 一致 ──

def test_parallel_deterministic(synthetic_decoders):
    channels = sorted(synthetic_decoders)
    pool = mp_finish.make_pool(channels, workers=2)
    try:
        r1 = mp_finish.finish_all(synthetic_decoders, channels, pool=pool)
        r2 = mp_finish.finish_all(synthetic_decoders, channels, pool=pool)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    for ch in channels:
        names1 = [s.message_name for s in r1[ch][0]]
        names2 = [s.message_name for s in r2[ch][0]]
        assert names1 == names2, f"CAN{ch}: 两次运行组序不一致"
        _assert_stats_equal(r1[ch][1], r2[ch][1], ch)


# ── 4. 池创建失败 → 自动串行 ──

def test_fallback_serial_on_pool_failure(tmp_path, monkeypatch):
    from core import converter as conv_mod

    d3 = load(str(DBC_DIR / "VDCCCU_CANFD2.dbc"))
    d6 = load(str(DBC_DIR / "VDCCCU_CANFD3.dbc"))
    decoders = {3: ChannelDecoder(d3, 3), 6: ChannelDecoder(d6, 6)}
    _feed_synthetic(decoders)
    blf = _mini_blf(tmp_path, decoders)

    def boom(*_a, **_k):
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(conv_mod.mp_finish, "make_pool", boom)
    out = tmp_path / "fallback.mdf"
    result = convert(blf, {3: d3, 6: d6}, str(out), parallel=True)
    # 自动回退串行：正常完成且计数与串行一致
    assert {s.channel for s in result.summaries if s.bound} == {3, 6}
    for s in result.summaries:
        if s.bound:
            assert s.decoded_frames > 0 and s.unknown_frames > 0
    out_ser = tmp_path / "fallback_ser.mdf"
    convert(blf, {3: d3, 6: d6}, str(out_ser), parallel=False)
    assert _mdf_equal(str(out), str(out_ser)) == []


# ── 5. 单通道短路（不建池）──

def test_single_channel_no_pool(tmp_path, monkeypatch):
    from core import converter as conv_mod

    calls = []
    monkeypatch.setattr(conv_mod.mp_finish, "make_pool",
                        lambda *a, **k: calls.append(1) or None)
    d3 = load(str(DBC_DIR / "VDCCCU_CANFD2.dbc"))
    dec = ChannelDecoder(d3, 3)
    _feed_synthetic({3: dec})
    blf = _mini_blf(tmp_path, {3: dec})
    out = tmp_path / "single.mdf"
    result = convert(blf, {3: d3}, str(out), parallel=True)
    assert not calls, "单通道不应创建进程池"
    assert result.summaries[0].bound and result.summaries[0].decoded_frames > 0


# ── 6. 合并语义：未知/短帧计数并行与串行严格等价 ──

def test_unknown_merge_equivalence(synthetic_decoders):
    channels = sorted(synthetic_decoders)
    par = mp_finish.finish_all(synthetic_decoders, channels)
    ser = {ch: synthetic_decoders[ch].finish() for ch in channels}
    for ch in channels:
        # 未知帧来源：未知 ID（3）+ 短帧（2）；unknown_ids 只含未知 ID 与
        # mux 坏值帧的 id（短帧帧源相同 id 已含，set 等价性由测试 1 覆盖）
        assert par[ch][1].unknown_frames == ser[ch][1].unknown_frames
        assert par[ch][1].unknown_ids == ser[ch][1].unknown_ids
        assert par[ch][1].total_frames == ser[ch][1].total_frames
