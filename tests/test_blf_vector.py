"""H1 向量化解析器属性对拍（core/blf_vector.py，计划 §9.1.1）。

对拍对象 = 现行标量行走 blf_reader.walk_container（oracle，行为与
python-can _parse_data 一致）。容器字节手工构造（不经 python-can writer），
覆盖其 writer 不产生的布局：未知版本、对象体内假阳性、窗口内假阳性、
空洞/垃圾、跨容器截断、损坏头字段、FD64 的 ext_data_offset/说谎字段等。

断言契约：快路径产出与标量逐位全等（帧字段 + 尾部字节）；快路径拒绝
（回退标量）或同型异常均合法；快路径接受且结果不等 / 标量正常而快路径
抛异常 = 失败。
"""
import struct

import numpy as np
import pytest

from core import blf_reader
from core.blf_reader import (ConversionCancelled, iter_containers, ms_part_ns,
                             walk_container)
from core.blf_vector import (_parse_fast, _u16_at, _u16_at_v, _u32_at,
                             _u32_at_v, _u64_at, _u64_at_v,
                             iter_container_frames)

from can.io.blf import (
    BLFParseError,
    CAN_ERROR_EXT,
    CAN_ERROR_EXT_STRUCT,
    CAN_FD_MESSAGE,
    CAN_FD_MESSAGE_64,
    CAN_FD_MSG_64_STRUCT,
    CAN_FD_MSG_STRUCT,
    CAN_MESSAGE,
    CAN_MESSAGE2,
    CAN_MSG_STRUCT,
    OBJ_HEADER_BASE_STRUCT,
    OBJ_HEADER_V1_STRUCT,
    OBJ_HEADER_V2_STRUCT,
    REMOTE_FLAG,
    dlc2len,
)

GLOBAL_MARKER = 96


# ── 构造器：字段约定与 python-can writer 一致 ──
# header_size 字段 = base(16) + 版本头（V1→32，V2→40）；
# obj_size = header_size + len(body)（不含 base 头）；
# body 按 4 字节对齐补零（writer _add_object 语义）——相邻对象头恰落入
# 步行器 8 字节窗口 [e, e+4]。
def _base(hs, ver, osz, otype):
    return OBJ_HEADER_BASE_STRUCT.pack(b"LOBJ", hs, ver, osz, otype)


def _v1(flags=0, rel=0):
    return OBJ_HEADER_V1_STRUCT.pack(flags, 0, 0, rel)


def _v2(flags=0, rel=0):
    return OBJ_HEADER_V2_STRUCT.pack(flags, 0, 0, rel)


def _mk_obj(body, version=1, flags=0, rel=0, obj_type=CAN_MESSAGE,
            header_size=None, obj_size=None, pad=None):
    """对象 = base 头 + 版本头 + body + 对齐 pad；字段可覆盖制造损坏。"""
    vh = _v1(flags, rel) if version == 1 else _v2(flags, rel)
    hs = 16 + len(vh) if header_size is None else header_size
    total = hs + len(body)
    pad = (-len(body)) % 4 if pad is None else pad
    return (_base(hs, version, total if obj_size is None else obj_size,
                  obj_type) + vh + body + b"\x00" * pad)


def _msg(channel, can_id, data, *, flags=0, rel=0, version=1, dlc=None,
         remote=False, obj_type=CAN_MESSAGE, header_size=None, obj_size=None):
    dlc = len(data) if dlc is None else dlc
    body = CAN_MSG_STRUCT.pack(channel + 1, REMOTE_FLAG if remote else 0, dlc,
                               can_id, data.ljust(8, b"\x00"))
    return _mk_obj(body, version, flags, rel, obj_type, header_size, obj_size)


def _err(channel, can_id, data, *, flags=0, rel=0, version=1, dlc=None):
    dlc = len(data) if dlc is None else dlc
    body = CAN_ERROR_EXT_STRUCT.pack(channel + 1, 0, 0, 0, 0, dlc, 0, can_id,
                                     0, data.ljust(8, b"\x00"))
    return _mk_obj(body, version, flags, rel, obj_type=CAN_ERROR_EXT)


def _fd(channel, can_id, data, *, flags=0, rel=0, version=1, dlc=None,
        valid_bytes=None, fd_flags=1, remote=False):
    dlc = len(data) if dlc is None else dlc
    vb = len(data) if valid_bytes is None else valid_bytes
    body = CAN_FD_MSG_STRUCT.pack(channel + 1, REMOTE_FLAG if remote else 0,
                                  dlc, can_id, 0, 0, fd_flags, vb,
                                  data.ljust(64, b"\x00"))
    return _mk_obj(body, version, flags, rel, obj_type=CAN_FD_MESSAGE)


def _fd64(channel, can_id, data, *, flags=0, rel=0, version=1, dlc=None,
          valid_bytes=None, fd_flags=0x1000, remote=False, ext_data_offset=0,
          header_size=None, obj_size=None):
    dlc = 15 if dlc is None else dlc
    vb = len(data) if valid_bytes is None else valid_bytes
    ff = fd_flags | (0x0010 if remote else 0)
    struct_ = CAN_FD_MSG_64_STRUCT.pack(channel + 1, dlc, vb, 0, can_id, 0,
                                        ff, 0, 0, 0, 0, ext_data_offset,
                                        0, 0, 0)
    # 40 字节结构后紧跟数据字节（真实文件布局；dfl 公式按 obj_size 推算）
    return _mk_obj(struct_ + data, version, flags, rel,
                   obj_type=CAN_FD_MESSAGE_64, header_size=header_size,
                   obj_size=obj_size)


# ── 容器级对拍 ──
def _assert_eq(frames, tail, res):
    cf, ftail = res
    assert ftail == tail, "尾部字节不一致"
    got = cf.channel.shape[0]
    assert got == len(frames), f"帧数 {got} != {len(frames)}"
    for i, f in enumerate(frames):
        assert cf.channel[i] == f.channel, f"[{i}] channel"
        assert cf.ts[i] == f.ts_seconds, f"[{i}] ts"
        assert cf.arb[i] == f.arbitration_id, f"[{i}] arb"
        assert bool(cf.is_ext[i]) == bool(f.is_extended), f"[{i}] is_ext"
        assert bool(cf.is_remote[i]) == bool(f.is_remote), f"[{i}] is_remote"
        assert bool(cf.is_error[i]) == bool(f.is_error), f"[{i}] is_error"
        assert bool(cf.is_fd[i]) == bool(f.is_fd), f"[{i}] is_fd"
        assert cf.dlc[i] == f.dlc, f"[{i}] dlc"
        d = cf.payload(i)
        assert d == f.data, f"[{i}] data"


def _check(data, ms_part=0):
    """容器级对拍：快路径（拒绝则回退）= 标量；异常同型。

    返回：快路径接受 → (ContainerFrames, tail)；双方同型抛 / 快路径拒绝
    → None。标量正常而快路径抛异常、或标量抛而快路径产出结果 = 测试失败。
    """
    try:
        frames, tail = walk_container(data, ms_part, None)
    except Exception as e:
        try:
            res = _parse_fast(data, ms_part)
        except Exception as e2:
            assert type(e2) is type(e), (
                f"快路径异常 {type(e2).__name__}: {e2!r} 与标量 "
                f"{type(e).__name__}: {e!r} 不同型")
        else:
            assert res is None, (
                f"标量抛 {type(e).__name__} 时快路径应拒绝或同型抛出")
        return None
    res = _parse_fast(data, ms_part)
    if res is None:
        return None
    _assert_eq(frames, tail, res)
    return res


# ── 语义组合 ──
def test_mixed_four_types_engage_and_match():
    data = b"".join([
        _msg(3, 0x123, b"\x01\x02\x03"),
        _err(1, 0x100, b"\xAA\xBB"),
        _fd(5, 0x1FF, b"\x00" * 24, dlc=9, valid_bytes=24),
        _fd64(7, 0x2AB, b"\x11" * 10, valid_bytes=10),
        _msg(2, 0x80000123, b"", dlc=0),
        _msg(3, 0x456, b"\xCC", remote=True),
        _msg(0, 0x789, b"\xEE" * 8, obj_type=CAN_MESSAGE2),
        _msg(4, 0x8FF, b"\x01", version=2),
        _fd64(8, 0x900, b"", valid_bytes=0, dlc=0),
    ])
    res = _check(data)
    assert res is not None, "全正常容器应命中快路径"
    cf, tail = res
    # 容器尾 padding（fd64(7) 的 body 50 字节 → 4 字节对齐补 2）按 walk
    # 语义留在尾部（= 窗口下界 s 之后的未消费字节）
    assert tail == b"\x00\x00"
    assert len(cf.channel) == 9
    assert cf.arb[0] == 0x123 and not cf.is_ext[0] and cf.channel[0] == 3
    assert cf.channel[1] == 1 and bool(cf.is_error[1])
    assert cf.channel[2] == 5 and bool(cf.is_fd[2]) and cf.dlc[2] == 12
    assert cf.channel[3] == 7 and bool(cf.is_fd[3]) and cf.dlc[3] == 64 \
        and cf.data_len[3] == 10
    assert cf.arb[4] == 0x123 and bool(cf.is_ext[4]) and cf.dlc[4] == 0
    assert bool(cf.is_remote[5])
    assert cf.channel[6] == 0
    assert cf.channel[7] == 4
    assert cf.data_len[8] == 0


def test_ts_flags_units_and_ms_part():
    ms_part = 624_000_000
    data = b"".join([
        _msg(1, 0x100, b"\x01", flags=0, rel=123456789),
        _msg(1, 0x101, b"\x01", flags=1, rel=123456),          # ×10000
        _msg(1, 0x102, b"\x01", version=2, flags=0, rel=42),
        _msg(1, 0x103, b"\x01", version=2, flags=1, rel=7),    # V2 u32==1
    ])
    res = _check(data, ms_part=ms_part)
    assert res is not None
    cf, _ = res
    assert cf.ts[0] == float(ms_part + 123456789) * 1e-9
    assert cf.ts[1] == float(ms_part + 123456 * 10000) * 1e-9
    assert cf.ts[2] == float(ms_part + 42) * 1e-9
    assert cf.ts[3] == float(ms_part + 70000) * 1e-9


def test_unknown_version_skipped():
    u = _base(32, 3, 32 + 16, CAN_MESSAGE) + _v1() + b"\x00" * 16
    data = _msg(1, 0x100, b"\x01") + u + _msg(1, 0x101, b"\x02")
    res = _check(data)
    assert res is not None
    cf, _ = res
    assert len(cf.channel) == 2


def test_unknown_obj_type_skipped():
    g = _base(32, 1, 32 + 16, GLOBAL_MARKER) + _v1() + b"\x00" * 16
    data = _msg(1, 0x100, b"\x01") + g + _msg(2, 0x101, b"\x02")
    res = _check(data)
    assert res is not None
    cf, _ = res
    assert len(cf.channel) == 2


def test_false_positive_in_payload_parsed_identically():
    # H7a 语义翻转："LOBJ" 在对象体内、fp@42 ≡ 2 mod 4（单类扫描不可见）
    # → 候选 {0,48} 全有效 → 接受且与 walk 同解析（旧 4 类扫描把 fp 判为
    # 窗口违约 → 回退；输出一致，仅「拒绝 ↔ 接受」互换）
    data = _msg(1, 0x100, b"ABLOBJCD") + _msg(1, 0x101, b"\x02")
    res = _check(data)
    assert res is not None, "fp 不可见后应接受并与 oracle 同解析"
    cf, _ = res
    assert len(cf.channel) == 2
    assert cf.arb[0] == 0x100 and cf.arb[1] == 0x101


def test_false_positive_in_window_parsed_identically():
    # 窗口内假阳性 fp@50（≡2 mod 4，单类扫描不可见）→ 末探针命中 → 回退
    # （walk 同解析 2 帧；快路径拒绝合法）
    body = CAN_MSG_STRUCT.pack(5, 0, 1, 0x777, b"\x55" + b"\x00" * 7)
    fp = _base(32, 1, 32 + 16, CAN_MESSAGE) + _v1() + body
    data = _msg(1, 0x100, b"\x01") + b"\x00\x00" + fp
    assert _check(data) is None, "末探针应命中并回退"


def test_gap_in_window_0_to_4():
    # k=0/4：第二对象 ≡0 mod 4 → 单类扫描可见，gap ≤ 4 → 接受
    for k in (0, 4):
        data = _msg(1, 0x100, b"\x01") + b"\x00" * k + _msg(1, 0x101, b"\x02")
        res = _check(data)
        assert res is not None, f"空洞 {k} 字节应命中窗口"
    # k=1..3：第二对象错位 → 单类扫描不可见 → 末探针命中 → 回退（walk 同解析）
    for k in (1, 2, 3):
        data = _msg(1, 0x100, b"\x01") + b"\x00" * k + _msg(1, 0x101, b"\x02")
        assert _check(data) is None, f"空洞 {k} 字节：末探针应命中回退"


def test_gap_beyond_window_raises_or_tails():
    for k in (5, 6, 7):
        # 情形 1：对象后 ≥8 字节 → 双双 raise（快路径拒绝回退，回退即 raise）
        data = _msg(1, 0x100, b"\x01") + b"\x00" * k + _msg(1, 0x101, b"\x02")
        assert _check(data) is None
        with pytest.raises(BLFParseError):
            walk_container(data, 0, None)
        # 情形 2：容器在 e_A 后不足 8 字节 → 双双尾部（= 洞字节）
        data2 = _msg(1, 0x100, b"\x01") + b"\x00" * k
        frames, tail = walk_container(data2, 0, None)
        assert tail == b"\x00" * k
        res = _check(data2)
        assert res is not None and res[1] == tail


def test_tail_junk_gte_8_raises():
    data = _msg(1, 0x100, b"\x01") + b"\x00" * 8
    with pytest.raises(BLFParseError):
        walk_container(data, 0, None)
    with pytest.raises(BLFParseError):
        _parse_fast(data, 0)   # valid[last] ∧ e+8 ≤ max_pos → 直接 raise


def test_no_candidates_tail_or_raise():
    for n in range(8):
        data = b"\x00" * n
        frames, tail = walk_container(data, 0, None)
        assert frames == [] and tail == data
        res = _parse_fast(data, 0)
        assert res is not None and res[1] == data \
            and res[0].channel.shape[0] == 0
    for n in (8, 9):
        data = b"\x00" * n
        with pytest.raises(BLFParseError):
            walk_container(data, 0, None)
        with pytest.raises(BLFParseError):
            _parse_fast(data, 0)


def test_prefix_junk_alignment():
    for k in (0, 1, 2, 3, 4):
        data = b"\xAB" * k + _msg(1, 0x100, b"\x01") + _msg(2, 0x101, b"\x02\x03")
        res = _check(data)
        assert res is not None, f"前缀 {k} 字节应命中窗口"
    for k in (5, 6, 7):
        data = b"\xAB" * k + _msg(1, 0x100, b"\x01")
        assert _check(data) is None   # 双双 raise（首候选无法完整落入窗口）


def test_version_header_truncated_last_candidate():
    # 末候选版本头截断（obj_size 恰好装下 base+8 字节）→ 标量未捕获
    # struct.error；快路径条件 5 拒绝回退
    truncated = _base(16, 1, 24, CAN_MESSAGE) + _v1()[:8]
    data = _msg(1, 0x200, b"\x02") + truncated
    assert _check(data) is None
    with pytest.raises(struct.error):
        walk_container(data, 0, None)
    assert _parse_fast(data, 0) is None


def test_message_body_truncated_last_candidate():
    # 消息体截断（版本头完整、消息结构不足）→ 双双：已累积帧 + tail =
    # 截断对象自窗口下界（walk 捕获 struct.error 的容器级兜底语义）
    truncated = _mk_obj(b"\x00" * 8)   # obj_size = 40，实际 16+16+8 字节
    data = _msg(1, 0x200, b"\x02") + truncated
    res = _check(data)
    assert res is not None and res[1] == truncated and len(res[0].channel) == 1
    frames, tail = walk_container(data, 0, None)
    assert len(frames) == 1 and tail == truncated


def test_dlc_out_of_range_classic():
    for dlc in (9, 12, 15, 255):
        data = _msg(1, 0x100, b"\xAA" * 8, dlc=dlc)
        res = _check(data)
        assert res is not None
        cf, _ = res
        assert cf.dlc[0] == dlc and cf.data_len[0] == 8
        assert cf.payload(0) == b"\xAA" * 8


def test_fd_dlc_codes():
    for code, expect in ((0, 0), (8, 8), (9, 12), (15, 64), (16, 64),
                         (255, 64)):
        n = min(expect, 24)
        data = _fd(1, 0x100, b"\xBB" * n, dlc=code, valid_bytes=n)
        res = _check(data)
        assert res is not None
        cf, _ = res
        assert cf.dlc[0] == dlc2len(code) and cf.data_len[0] == n


def test_fd64_valid_bytes_padding():
    # ext_off=0 → span=obj_size：dfl = obj_size − 32 − 40 = len(data)
    data = _fd64(1, 0x100, b"\xCC" * 30, valid_bytes=60, dlc=15)
    res = _check(data)
    assert res is not None
    cf, _ = res
    assert cf.dlc[0] == 64 and cf.data_len[0] == 60
    assert cf.glen[0] == 30
    assert cf.payload(0) == b"\xCC" * 30 + b"\x00" * 30


def test_fd64_ext_data_offset():
    # ext_off 显式：dfl = min(vb, ext_off − 32 − 40) = 40（数据区全长）
    data = _fd64(1, 0x100, b"\xDD" * 40, valid_bytes=60,
                 ext_data_offset=112)
    res = _check(data)
    assert res is not None
    cf, _ = res
    assert cf.payload(0) == b"\xDD" * 40 + b"\x00" * 20
    # ext_off 超长：dfl = vb，切片按容器尾截断 → 40 字节
    data2 = _fd64(1, 0x100, b"\xEE" * 40, valid_bytes=60,
                  ext_data_offset=200)
    res2 = _check(data2)
    assert res2 is not None
    assert res2[0].payload(0) == b"\xEE" * 40 + b"\x00" * 20


def test_fd64_lying_header_size_field():
    # 说谎 header_size 字段（100）→ dfl 负 → 空切片 → 全零（ljust 语义）
    data = (_fd64(1, 0x100, b"\xFF" * 30, valid_bytes=60,
                  ext_data_offset=112, header_size=100, obj_size=112)
            + b"\x00" * 10)
    res = _check(data)
    assert res is not None
    cf, _ = res
    assert cf.data_len[0] == 60
    assert cf.glen[0] == 0
    assert cf.payload(0) == b"\x00" * 60


def test_remote_and_fd_flags():
    data = _msg(1, 0x100, b"\x01", remote=True) + _fd(2, 0x200, b"\x02",
                                                      fd_flags=0)
    res = _check(data)
    assert res is not None
    cf, _ = res
    assert bool(cf.is_remote[0]) and not bool(cf.is_remote[1])
    assert not bool(cf.is_fd[1])
    data2 = _msg(1, 0x100, b"\x01") + _fd64(2, 0x200, b"\x03",
                                            fd_flags=0x0010)
    res2 = _check(data2)
    assert res2 is not None
    cf2, _ = res2
    assert bool(cf2.is_remote[1]) and not bool(cf2.is_fd[1])


def test_rel_guard_exact_boundary():
    ms_part = 123_456_789
    rel_max = 2 ** 53 - ms_part
    res = _check(_msg(1, 0x100, b"\x01", rel=rel_max), ms_part=ms_part)
    assert res is not None
    assert res[0].ts[0] == float(2 ** 53) * 1e-9
    assert _check(_msg(1, 0x100, b"\x01", rel=rel_max + 1),
                  ms_part=ms_part) is None
    rel_max10 = (2 ** 53 - ms_part) // 10000
    res3 = _check(_msg(1, 0x100, b"\x01", flags=1, rel=rel_max10),
                  ms_part=ms_part)
    assert res3 is not None
    assert res3[0].ts[0] == float(ms_part + rel_max10 * 10000) * 1e-9
    assert _check(_msg(1, 0x100, b"\x01", flags=1, rel=rel_max10 + 1),
                  ms_part=ms_part) is None


def test_cross_container_split_all_offsets():
    head = _msg(1, 0x100, b"\x01")
    obj = _fd64(2, 0x2AB, b"\x22" * 30, valid_bytes=30, ext_data_offset=112)
    tail_obj = _msg(3, 0x300, b"\x33")
    for k in range(len(obj) + 1):
        data1 = head + obj[:k]
        data2 = obj[k:] + tail_obj
        _check(data1)
        _check(data2)
        f1, t1 = walk_container(data1, 0, None)
        f2, t2 = walk_container(t1 + data2, 0, None)
        fall, tall = walk_container(data1 + data2, 0, None)
        assert f1 + f2 == fall, f"k={k} 跨容器组合帧不相等"
        assert t2 == tall, f"k={k} 组合尾部不一致"


def test_junk_tail_continuation():
    # 尾部垃圾（非对象字节）衔接下一容器：拼接后从头解析（≤4 字节可命中窗口）
    data1 = _msg(1, 0x100, b"\x01") + b"\xFE" * 3
    data2 = _msg(2, 0x200, b"\x02")
    res = _check(data1)
    assert res is not None and res[1] == b"\xFE" * 3
    f1, t1 = walk_container(data1, 0, None)
    f2, t2 = walk_container(t1 + data2, 0, None)
    assert len(f1) == 1 and len(f2) == 1
    # 衔接后：窗口下界按 obj_start+obj_size 推进（gap 字节未消费）——
    # msg2 末尾 3 字节留在 tail，继续流向下一容器
    assert t2 == b"\x00" * 3
    assert _check(t1 + data2) is not None


# ── 模糊对拍 ──
def test_fuzz_object_mix():
    rng = np.random.default_rng(20260814)
    for _ in range(300):
        parts = []
        for _ in range(int(rng.integers(0, 12))):
            kind = int(rng.integers(0, 8))
            ch = int(rng.integers(0, 40))
            can_id = int(rng.integers(0, 0x20000000))
            data = rng.bytes(int(rng.integers(0, 40)))
            rel = int(rng.integers(0, 2_000_000_000))
            ver = 1 if rng.random() < 0.8 else 2
            fl = int(rng.integers(0, 2))
            if kind == 0:
                parts.append(_msg(ch, can_id, data[:8], rel=rel, version=ver,
                                  flags=fl, dlc=len(data)))
            elif kind == 1:
                parts.append(_err(ch, can_id, data[:8], rel=rel, version=ver,
                                  dlc=len(data)))
            elif kind == 2:
                parts.append(_fd(ch, can_id, data[:64],
                                 dlc=int(rng.integers(0, 20)),
                                 valid_bytes=len(data[:64]),
                                 fd_flags=int(rng.integers(0, 3)),
                                 version=ver))
            elif kind == 3:
                parts.append(_fd64(ch, can_id, data[:40],
                                   valid_bytes=int(rng.integers(0, 80)),
                                   dlc=int(rng.integers(0, 20)),
                                   version=ver))
            elif kind == 4:
                parts.append(_msg(ch, can_id, b"ABLOBJ" + data[:6],
                                  dlc=len(data[:6]) + 5))
            elif kind == 5:
                parts.append(_base(32, 3, 32 + 16, CAN_MESSAGE) + _v1()
                             + b"\x00" * 16)
            elif kind == 6:
                parts.append(b"\x00" * int(rng.integers(0, 5)))
            else:
                parts.append(_msg(ch, can_id, b"LOBJ\x00\x00\x00\x00",
                                  rel=rel, flags=1))
        data = b"".join(parts) + rng.bytes(int(rng.integers(0, 12)))
        _check(data)


def test_fuzz_garbage_with_magic():
    rng = np.random.default_rng(20260815)
    for _ in range(150):
        n = int(rng.integers(0, 400))
        data = bytearray(rng.bytes(n))
        for _ in range(int(rng.integers(0, 4))):
            if n >= 4:
                at = int(rng.integers(0, n - 3))
                data[at:at + 4] = b"LOBJ"
        _check(bytes(data))


# ── 文件级（python-can writer 产物）──
def _write_synthetic(path, frames=600):
    import can
    with can.BLFWriter(str(path)) as w:
        for i in range(frames):
            for ch in (1, 2, 3):
                w.on_message_received(can.Message(
                    arbitration_id=0x100 + i % 5,
                    data=bytes([i % 256]) * (i % 9),
                    channel=ch, timestamp=1784716800.0 + i * 0.001))
    return frames * 3


def _assert_streams_equal(ref, gots):
    it = iter(ref)
    for cf in gots:
        for i in range(len(cf.channel)):
            f = next(it)
            assert f.channel == cf.channel[i]
            assert f.ts_seconds == cf.ts[i]
            assert f.arbitration_id == cf.arb[i]
            assert f.is_extended == bool(cf.is_ext[i])
            assert f.is_fd == bool(cf.is_fd[i])
            assert f.dlc == cf.dlc[i]
            assert f.is_remote == bool(cf.is_remote[i])
            assert f.is_error == bool(cf.is_error[i])
            d = cf.payload(i)
            assert f.data == d
    with pytest.raises(StopIteration):
        next(it)


def test_iter_container_frames_matches_iter_all_messages(tmp_path):
    p = tmp_path / "multi.blf"
    _write_synthetic(p)
    ref = list(blf_reader.iter_all_messages(str(p)))
    got = list(iter_container_frames(str(p)))
    _assert_streams_equal(ref, got)


def test_iter_container_frames_force_fallback_matches(tmp_path):
    p = tmp_path / "multi.blf"
    _write_synthetic(p)
    ref = list(blf_reader.iter_all_messages(str(p)))
    got = list(iter_container_frames(str(p), _force_fallback=True))
    _assert_streams_equal(ref, got)


def test_iter_container_frames_progress_monotonic(tmp_path):
    p = tmp_path / "prog.blf"
    _write_synthetic(p, frames=30)
    values = []
    list(iter_container_frames(str(p), progress_cb=values.append))
    assert values and values[-1] == 100.0
    assert values == sorted(values)


def test_iter_container_frames_cancel(tmp_path):
    p = tmp_path / "cancel.blf"
    _write_synthetic(p, frames=2500)
    with pytest.raises(ConversionCancelled):
        list(iter_container_frames(str(p), cancel_cb=lambda: True))
    got = list(iter_container_frames(str(p), cancel_cb=lambda: False))
    assert got


def test_sample_full_bitwise_and_fast_engagement():
    from conftest import sample_blf
    blf = sample_blf()
    if blf is None:
        pytest.skip("无样例 BLF 文件")
    # 命中率：逐容器复刻 iter_container_frames 的尾部衔接语义统计快路径命中
    ms_part = None
    tail = b""
    total = hits = 0
    for start_ns, data in iter_containers(str(blf)):
        if ms_part is None:
            ms_part = ms_part_ns(start_ns)
        if tail:
            data = tail + data
            tail = b""
        total += 1
        res = _parse_fast(data, ms_part)
        if res is not None:
            hits += 1
            tail = res[1]
        else:
            _, tail = walk_container(data, ms_part, None)
    assert hits == total, f"样例 {hits}/{total} 容器命中快路径"
    # 全量对拍：快路径流 / 强制回退流 vs 现行 iter_all_messages
    ref = list(blf_reader.iter_all_messages(str(blf)))
    _assert_streams_equal(ref, iter_container_frames(str(blf)))
    _assert_streams_equal(ref,
                          iter_container_frames(str(blf), _force_fallback=True))


def test_view_helpers_bitwise_equal_byte_helpers():
    """H7e：对齐位置上视图读与字节 gather 逐位同值（含两侧越界回退位）。"""
    rng = np.random.default_rng(20260816)
    for n in (16, 20, 33, 100):
        data = rng.bytes(n)
        b8 = np.frombuffer(data, dtype=np.uint8)
        v32 = np.frombuffer(data, dtype="<u4", count=n // 4)
        q = np.arange(-4, n + 4, 4, dtype=np.int64)   # 对齐扫掠（两侧越界）
        q = np.concatenate((q, [2 ** 40]))
        assert np.array_equal(_u32_at_v(v32, q, n), _u32_at(b8, q, n))
        assert np.array_equal(_u64_at_v(v32, q, n), _u64_at(b8, q, n))
        assert np.array_equal(_u16_at_v(v32, q, n), _u16_at(b8, q, n))
