"""方案C 向量化解码测试：位提取单元 + 与逐帧参考实现对拍。"""
import struct

import numpy as np
import pytest

from cantools.database.errors import DecodeError
from cantools.database.namedsignalvalue import NamedSignalValue

from core.blf_reader import Frame
from core.decoder import (DecodeStats, SignalSeries, _clamped_int_array,
                          _extract_bits, _extract_signal, _int_dtype,
                          _pad_to_64, _signal_kind)
from core.dbc_loader import DbcDef, SignalDef, load, normalize_id


def _ref_bits(data: bytes, pos: int, length: int, byte_order: str) -> int:
    """位级参考（独立于实现）：bit 0 = 首字节 MSB 的大端位流语义。"""
    if byte_order == "little_endian":
        # 小端位 i = 字节 i//8 的 LSB 起第 i%8 位（修正 2：逐位求和）
        return sum(((data[(pos + t) // 8] >> ((pos + t) % 8)) & 1) << t
                   for t in range(length))
    big = int.from_bytes(data, "big")
    n = len(data) * 8
    return (big >> (n - pos - length)) & ((1 << length) - 1)


def _frame_data(n_frames: int, n_bytes: int, rng) -> np.ndarray:
    return rng.integers(0, 256, size=(n_frames, n_bytes), dtype=np.uint8)


@pytest.mark.parametrize("byte_order", ["big_endian", "little_endian"])
def test_extract_bits_random_matches_bit_reference(byte_order):
    """随机 (数据, 位置, 长度) 逐位对拍位级参考（含跨界、>64 位截断）。"""
    rng = np.random.default_rng(20260806)
    for n_bytes in (1, 2, 8, 9, 24, 64):
        for _ in range(200):
            n = int(rng.integers(1, 6))
            data = _frame_data(n, n_bytes, rng)
            total = n_bytes * 8
            pos = int(rng.integers(0, total))
            length = int(rng.integers(1, min(129, total - pos + 1)))
            data64 = _pad_to_64(data).view("<u8")
            # 修正 5：>64 位信号取低 64 位，大端从 pos+length-64 起（小端从 pos 起）；
            # 仅 length>64 时调整（length≤64 时 pos+length-64 为负，会错取字边界）
            ext_pos = pos + (length - 64) if byte_order == "big_endian" and length > 64 else pos
            got = _extract_bits(data64, ext_pos, min(length, 64), byte_order)
            ref = np.array([
                _ref_bits(bytes(row), pos, length, byte_order) & ((1 << 64) - 1)
                for row in data], dtype=np.uint64)
            assert np.array_equal(got, ref), \
                f"{byte_order} n_bytes={n_bytes} pos={pos} len={length}"


def test_extract_signal_signed_and_float():
    """符号扩展与浮点位模式（int64/float32/float64 view）。"""
    rng = np.random.default_rng(7)
    data = _frame_data(4, 8, rng)
    data64 = _pad_to_64(data).view("<u8")
    for sd in [
        SignalDef(name="s", start_bit=0, length=12, scale=1.0, offset=0.0,
                  unit="", is_signed=True, byte_order="big_endian"),
        SignalDef(name="f", start_bit=16, length=32, scale=1.0, offset=0.0,
                  unit="", is_float=True, byte_order="little_endian"),
    ]:
        raw = _extract_signal(data64, sd)
        if sd.is_signed:
            ref = []
            for row in data:
                # 修正 3：start_bit=0 大端 → 位流位 7（sawtooth）
                v = _ref_bits(bytes(row), 7, 12, "big_endian")
                if v & (1 << 11):
                    v -= 1 << 12
                ref.append(v)
            assert raw.dtype == np.int64
            assert raw.tolist() == ref
        else:
            ref = [struct.unpack("<f", bytes(row[2:6]))[0] for row in data]
            assert raw.dtype == np.float32
            assert np.allclose(raw, ref)


def test_extract_signal_truncates_over_64_bits():
    """>64 位信号：取低 64 位（Task 5 截断语义）。"""
    sd = SignalDef(name="big", start_bit=7, length=512, scale=1.0, offset=0.0,
                   unit="", byte_order="big_endian")  # 512 位整帧
    # 修正 4：低 64 位 = 位流末尾 = 末 8 字节，非零字节置于尾部
    data = np.array([[0] * 56 + list(range(8))] * 2, dtype=np.uint8)
    data64 = _pad_to_64(data).view("<u8")
    got = _extract_signal(data64, sd)
    low = int.from_bytes(bytes(range(8))[::-1], "little")  # 末字（低 64 位）
    assert got.dtype == np.uint64
    assert got.tolist() == [low, low]


# ── Task 3：逐帧 cantools 参考实现（= 方案C前的 ChannelDecoder 语义，对拍基准）──

def reference_decode(frames, dbc: DbcDef, channel: int):
    stats = DecodeStats()
    buckets = {}
    for fr in frames:
        stats.total_frames += 1
        arb = normalize_id(fr.arbitration_id, fr.is_extended)
        try:
            decoded = dbc.db.decode_message(arb, fr.data)
        except (KeyError, DecodeError):
            stats.unknown_frames += 1
            stats.unknown_ids.add(fr.arbitration_id)
            continue
        md = dbc.messages.get(arb)
        if md is None:
            stats.unknown_frames += 1
            stats.unknown_ids.add(fr.arbitration_id)
            continue
        b = buckets.setdefault(arb, {"ts": [], "values": {s.name: [] for s in md.signals}})
        b["ts"].append(fr.ts_seconds)
        for s in md.signals:
            b["values"][s.name].append(decoded.get(s.name, float("nan")))
    series = []
    for arb, b in buckets.items():
        md = dbc.messages[arb]
        values = {}
        for s in md.signals:
            vals = b["values"][s.name]
            kind = _signal_kind(s, vals)
            if kind == "text":
                values[s.name] = np.asarray([
                    str(v).encode("utf-8") if isinstance(v, NamedSignalValue) else b""
                    for v in vals])
            elif kind == "int":
                values[s.name] = _clamped_int_array(vals, _int_dtype(s.length, s.is_signed))
            else:
                values[s.name] = np.asarray(
                    [float("nan") if isinstance(v, NamedSignalValue) else v
                     for v in vals], dtype=np.float64)
        series.append(SignalSeries(
            channel=channel, message_name=md.name, node=md.sender_node,
            signal_names=[s.name for s in md.signals],
            timestamps=np.asarray(b["ts"], dtype=np.float64),
            values=values, units={s.name: s.unit for s in md.signals}))
    return series, stats


def _assert_series_equal(sa, sb):
    assert len(sa) == len(sb), f"系列数 {len(sa)} vs {len(sb)}"
    for a, b in zip(sa, sb):
        assert a.message_name == b.message_name
        assert a.signal_names == b.signal_names
        assert a.units == b.units
        assert np.array_equal(a.timestamps, b.timestamps)
        for name in a.signal_names:
            va, vb = a.values[name], b.values[name]
            assert va.dtype == vb.dtype, f"{name}: {va.dtype} vs {vb.dtype}"
            if va.dtype.kind == "S":
                assert va.tolist() == vb.tolist(), name
            elif va.dtype.kind == "f":
                eq = np.isnan(va) & np.isnan(vb)
                np.testing.assert_allclose(va[~eq], vb[~eq], rtol=0, atol=0), name
            else:
                assert np.array_equal(va, vb), name


def _random_frames(rng, n, known_ids=()):
    ids = list(known_ids) + [int(rng.integers(300, 400)) for _ in range(max(0, n - len(known_ids)))]
    out = []
    for i in range(n):
        if rng.random() < 0.2:
            # 短帧/超长帧混合
            dlc = int(rng.integers(0, 5))
            data = bytes(rng.integers(0, 256, size=dlc))
        else:
            data = bytes(rng.integers(0, 256, size=8))
        out.append(Frame(channel=1, ts_seconds=float(i), arbitration_id=ids[i % len(ids)],
                         is_extended=bool(rng.random() < 0.1), is_fd=False, dlc=len(data),
                         data=data))
    return out


def _checked_positions(start: int, length: int, byte_order: str,
                       msg_bits: int) -> list[int]:
    """cantools 42.0.3 Message._check_signal 的位映射（start_bit() 见
    cantools/database/utils.py: 大端 anchor = 8*(start//8) + (7 - start%8)；
    小端经字节块反转）。生成器按此校验空间标记占用，保证 DBC 不被
    "signals are overlapping" 拒绝。

    重要（cantools 42.0.3 dbc.py:1599 实测）：DBC 文本字节序标记与 cantools
    内部 byte_order 相反——@1 → little_endian，@0 → big_endian。brief 原式
    按 LSB-first 物理位集标记且假设 @1=大端，与 cantools 实际校验映射不符，
    生成的大端/小端信号被误判重叠 → 拒绝加载。"""
    if byte_order == "big_endian":
        anchor = 8 * (start // 8) + (7 - start % 8)
        if anchor + length > msg_bits:
            return []
        return list(range(anchor, anchor + length))
    signal_bits = ["x"] * length + [None] * start
    if len(signal_bits) > msg_bits:
        return []
    if len(signal_bits) < msg_bits:
        signal_bits = (msg_bits - len(signal_bits)) * [None] + signal_bits
    rebits = []
    for i in range(0, len(signal_bits), 8):
        rebits = signal_bits[i:i + 8] + rebits
    return [i for i, v in enumerate(rebits) if v == "x"]


def _random_signal_dbc(rng, frame_len=8, n_signals=6):
    """单字节内不重叠的随机信号 DBC（大端/小端/符号/变换/choices 混合）。

    修正（brief 原式三处缺陷，均会使测试无法运行/无法通过）：
    ① `if not free`：np.ndarray 多元素 bool 歧义 → ValueError；
    ② len_max 恒为 0（start 必为空位，~occ[start:] 首元素恒 0）→ rng.integers(1,1) ValueError；
    ③ 占用位集必须按 cantools 校验空间标记（_checked_positions）且字节序标记
       与 cantools 内部语义对齐：@1 = little_endian，@0 = big_endian
       （cantools 42.0.3 dbc.py:1599 实测，与通常 DBC 文档直觉相反）——
       否则大端/小端信号被误判不重叠 → cantools 报 overlapping 拒绝加载。"""
    occ = np.zeros(frame_len * 8, dtype=bool)
    lines = [f'BO_ 100 RND: {frame_len} ECU']
    sgs, vals = [], []
    for i in range(n_signals):
        free = np.flatnonzero(~occ)
        if free.size == 0:
            break
        start = int(rng.choice(free))
        bo = "@1" if rng.random() < 0.5 else "@0"
        # cantools 42.0.3：@1 → little_endian，@0 → big_endian（与 DBC 文本直觉相反）
        order = "little_endian" if bo == "@1" else "big_endian"
        max_len = 0
        for cand in range(1, 9):
            mapped = _checked_positions(start, cand, order, frame_len * 8)
            if not mapped or not all(not occ[p] for p in mapped):
                break
            max_len = cand
        if max_len == 0:          # 校验空间内起点位已被占 → 重选起点
            continue
        length = int(rng.integers(1, max_len + 1))
        for p in _checked_positions(start, length, order, frame_len * 8):
            occ[p] = True
        signed = "+" if rng.random() < 0.3 else "-"
        scale = float(rng.choice([1.0, 1.0, 1.0, 2.0, 10.0, 0.5]))
        offset = float(rng.choice([0.0, 0.0, 0.0, -40.0, 5.0]))
        sgs.append(f" SG_ S{i} : {start}|{length}{bo}{signed} ({scale},{offset}) [0|255] \"\" ECU")
        if rng.random() < 0.3:
            keys = sorted(rng.choice(1 << length, size=min(3, 1 << length), replace=False).tolist())
            vals.append(f"VAL_ 100 S{i} " + " ".join(f"{k} \"v{k}\"" for k in keys) + " ;")
    return "\n".join(['VERSION ""', "", "NS_ :", "", "BS_:", "", "BU_: ECU", ""]
                     + lines + sgs + vals)


def test_vectorized_matches_reference_random(tmp_path):
    """随机信号 DBC × 随机帧：向量化 vs 逐帧参考全量对拍（dtype/值/nan/统计）。"""
    from core.decoder import decode_channel
    rng = np.random.default_rng(20260807)
    for trial in range(30):
        frame_len = int(rng.choice([8, 8, 16, 32]))
        dbc_txt = _random_signal_dbc(rng, frame_len)
        p = tmp_path / f"r{trial}.dbc"
        p.write_text(dbc_txt, encoding="utf-8")
        dbc = load(str(p))
        frames = _random_frames(rng, int(rng.integers(10, 120)), known_ids=[100])
        sr, st = reference_decode(frames, dbc, 1)
        vr, vt = decode_channel(iter(frames), dbc, 1)
        assert vt.total_frames == st.total_frames
        assert vt.unknown_frames == st.unknown_frames
        assert vt.unknown_ids == st.unknown_ids
        _assert_series_equal(vr, sr)


# ── Task 4：mux 单级向量化对拍 ──

MUX_DBC = '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 200 MUX: 8 ECU
 SG_ Mux M : 0|8@1+ (1,0) [0|255] "" ECU
 SG_ SigA m0 : 8|8@1+ (1,0) [0|255] "" ECU
 SG_ SigB m1 : 8|8@1+ (1,0) [0|255] "" ECU
 SG_ Always : 16|8@1+ (1,0) [0|255] "" ECU
'''


def test_vectorized_mux_matches_reference_random(tmp_path):
    """mux 随机帧（含无子组 mux 值/短帧/未知 ID）对拍参考实现。"""
    from core.decoder import decode_channel
    rng = np.random.default_rng(4242)
    p = tmp_path / "mux.dbc"
    p.write_text(MUX_DBC, encoding="utf-8")
    dbc = load(str(p))
    for trial in range(40):
        frames = []
        for i in range(80):
            arb = int(rng.choice([200, 200, 200, 300]))
            if arb == 300:
                data = bytes(rng.integers(0, 256, size=8))
            elif rng.random() < 0.15:
                data = bytes(rng.integers(0, 256, size=int(rng.integers(0, 8))))
            else:
                mux = int(rng.choice([0, 1, 2, 3]))   # 2/3 无子组 → 未知帧
                data = bytes([mux]) + bytes(rng.integers(0, 256, size=7))
            frames.append(Frame(channel=1, ts_seconds=float(i), arbitration_id=arb,
                                is_extended=False, is_fd=False, dlc=len(data), data=data))
        sr, st = reference_decode(frames, dbc, 1)
        vr, vt = decode_channel(iter(frames), dbc, 1)
        assert vt.unknown_frames == st.unknown_frames and vt.unknown_ids == st.unknown_ids
        _assert_series_equal(vr, sr)


# ── Task 5：0x7DF 512 位溢出截断 + 小端 >64 位截断修正 ──

BIG_DBC = '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 2015 DIAG: 64 ECU
 SG_ Payload : 7|512@0+ (1,0) [0|0] "" ECU

BO_ 2018 CNT: 8 ECU
 SG_ Cnt : 0|8@0+ (1,0) [0|255] "" ECU

BO_ 2016 SIGNED: 8 ECU
 SG_ BigS : 7|64@0- (1,0) [0|0] "" ECU
'''


def test_overflow_512bit_signal_truncates(tmp_path):
    """0x7DF 修复：512 位信号值超 uint64 → 按模截断低 64 位，不崩溃；
    向量化与参考实现截断结果一致。"""
    from core.decoder import decode_channel
    p = tmp_path / "big.dbc"
    p.write_text(BIG_DBC, encoding="utf-8")
    dbc = load(str(p))
    # 高位置位：整帧 0xFF...，低 64 位 = 末 8 字节（= 0xAB...AB）
    data = bytes([0xFF] * 56 + [0xAB] * 8)
    frames = [Frame(channel=1, ts_seconds=1.0, arbitration_id=2015,
                    is_extended=False, is_fd=True, dlc=64, data=data)]
    sr, st = reference_decode(frames, dbc, 1)
    vr, vt = decode_channel(iter(frames), dbc, 1)
    assert vt.unknown_frames == st.unknown_frames == 0
    payload = vr[0].values["Payload"]
    assert payload.dtype == np.uint64
    assert payload.tolist() == [int.from_bytes(data[56:64], "big")]  # 低 64 位 = 末 8 字节
    _assert_series_equal(vr, sr)


def test_overflow_signed_64bit_wraps(tmp_path):
    """有符号 64 位信号全范围回绕：int64 view（补码）。"""
    from core.decoder import decode_channel
    p = tmp_path / "s64.dbc"
    p.write_text(BIG_DBC, encoding="utf-8")
    dbc = load(str(p))
    data = (2**64 - 1).to_bytes(8, "big")  # 0xFFFF... = int64 -1
    frames = [Frame(channel=1, ts_seconds=1.0, arbitration_id=2016,
                    is_extended=False, is_fd=False, dlc=8, data=data)]
    sr, st = reference_decode(frames, dbc, 1)
    vr, _ = decode_channel(iter(frames), dbc, 1)
    assert vr[0].values["BigS"].dtype == np.int64
    assert vr[0].values["BigS"].tolist() == [-1]
    _assert_series_equal(vr, sr)


def test_overflow_512bit_little_endian_truncates(tmp_path):
    """修正 2：小端 512 位信号低 64 位 = 从 start_bit 起 64 位（LSB 在 pos，pos 不动），
    参考 _clamped_int_array 按模 2^64 截断，两侧必须一致。"""
    from core.decoder import decode_channel
    p = tmp_path / "bigl.dbc"
    p.write_text('''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 2017 DIAGL: 64 ECU
 SG_ PayloadL : 0|512@1+ (1,0) [0|0] "" ECU
''', encoding="utf-8")
    dbc = load(str(p))
    data = bytes([0xAB] * 8) + bytes([0xFF] * 56)   # 低 64 位 = 前 8 字节（小端 LSB-first）
    frames = [Frame(channel=1, ts_seconds=1.0, arbitration_id=2017,
                    is_extended=False, is_fd=True, dlc=64, data=data)]
    sr, st = reference_decode(frames, dbc, 1)
    vr, vt = decode_channel(iter(frames), dbc, 1)
    assert vt.unknown_frames == st.unknown_frames == 0
    payload = vr[0].values["PayloadL"]
    assert payload.dtype == np.uint64
    assert payload.tolist() == [int.from_bytes(data[0:8], "little")]  # = 0xABABABABABABABAB
    _assert_series_equal(vr, sr)
