import numpy as np
import pytest

from core.dbc_loader import load, normalize_id, normalize_ids

INLINE_DBC = '''VERSION ""

NS_ :
    NS_DESC_
    CM_
    BA_DEF_
    BA_
    VAL_
    CAT_DEF_
    CAT_
    FILTER
    BA_DEF_DEF_
    EV_DATA_
    ENVVAR_DATA_
    SGTYPE_
    SGTYPE_VAL_
    BA_DEF_SGTYPE_
    BA_SGTYPE_
    SIG_TYPE_REF_
    VAL_TABLE_
    SIG_GROUP_
    SIG_VALTYPE_
    SIGTYPE_VALTYPE_
    BO_TX_BU_
    BA_DEF_REL_
    BA_REL_
    BA_DEF_DEF_REL_
    BU_SG_REL_
    BU_EV_REL_
    BU_BO_REL_
    SG_MUL_VAL_

BS_:

BU_: ECU

BO_ 100 ABC: 8 ECU
 SG_ Speed : 0|16@1+ (0.01,0) [0|655.35] "km/h" ECU
 SG_ Temp : 16|8@1+ (1,-40) [-40|215] "degC" ECU
'''


def test_load_inline_dbc(tmp_path):
    p = tmp_path / "t.dbc"
    p.write_text(INLINE_DBC, encoding="utf-8")
    dbc = load(str(p))
    assert 100 in dbc.messages
    md = dbc.messages[100]
    assert md.name == "ABC"
    assert md.sender_node == "ECU"
    assert md.is_extended is False
    assert md.frame_length == 8
    names = [s.name for s in md.signals]
    assert names == ["Speed", "Temp"]
    speed = md.signals[0]
    assert speed.scale == 0.01 and speed.unit == "km/h"
    assert speed.start_bit == 0 and speed.length == 16
    assert md.signals[1].offset == -40


def test_load_malformed_dbc_raises_with_path(tmp_path):
    p = tmp_path / "bad.dbc"
    p.write_text("garbage line 1\nstill not dbc\n", encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        load(str(p))
    assert str(p) in str(excinfo.value)


def test_load_dbc_with_trailing_nul_padding(tmp_path):
    """固定缓冲导出的 DBC 尾部 NUL 填充应剥离后正常解析（A19G1/CFCAN2 场景：
    尾部 734 个 \\x00，cantools 当文本解析报 Invalid syntax at line 4100）。"""
    p = tmp_path / "padded.dbc"
    p.write_bytes(INLINE_DBC.encode("utf-8") + b"\x00" * 734)
    dbc = load(str(p))
    assert 100 in dbc.messages


def test_signal_metadata_fields(tmp_path):
    """SignalDef 扩展字段：byte_order/多路复用信息（方案C向量化解码需要）。"""
    dbc_txt = '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 100 M1: 8 ECU
 SG_ Sel M : 0|8@1+ (1,0) [0|255] "" ECU
 SG_ A m0 : 8|8@1+ (1,0) [0|255] "" ECU
 SG_ B m1 : 8|8@0+ (1,0) [0|255] "" ECU

BO_ 200 M2: 8 ECU
 SG_ C : 16|16@0+ (1,0) [0|65535] "" ECU

VAL_ 200 C 0 "off" 1 "on" ;
'''
    p = tmp_path / "meta.dbc"
    p.write_text(dbc_txt, encoding="utf-8")
    dbc = load(str(p))
    m1 = dbc.messages[100]
    sel, a, b = m1.signals
    assert sel.is_multiplexer and sel.multiplexer_ids is None
    assert a.multiplexer_ids == [0] and a.byte_order == "little_endian"
    assert b.multiplexer_ids == [1] and b.byte_order == "big_endian"
    m2 = dbc.messages[200]
    assert m2.signals[0].byte_order == "big_endian"
    assert m2.signals[0].choices == {0: "off", 1: "on"}


@pytest.mark.parametrize("raw_id,is_extended,expected", [
    (0x123, False, 0x123),             # 标准帧：EFF 位不置位
    (0x123, True, 0x80000123),         # 扩展帧：EFF 位置位
    (0x1FFFFFFF, True, 0x9FFFFFFF),    # 29 位 id 上限的扩展帧
    (0x80000000, False, 0x80000000),   # 标准帧原始 id 已含 EFF 位模式：OR 0 幂等不清除
    (0x80000001, True, 0x80000001),    # 扩展帧 EFF 位已置位：OR 幂等
])
def test_normalize_id(raw_id, is_extended, expected):
    """归一化键 = 原始 id 并入 EFF 位（dbc.messages 键的单一来源）。"""
    assert normalize_id(raw_id, is_extended) == expected


def test_normalize_ids_vectorized():
    """numpy 批量版与标量逐位一致，dtype 保持 uint32。"""
    arbs = np.array([0x123, 0x123, 0x1FFFFFFF, 0x80000000, 0x80000001], np.uint32)
    is_ext = np.array([False, True, True, False, True])
    got = normalize_ids(arbs, is_ext)
    assert got.dtype == np.uint32
    assert got.tolist() == [0x123, 0x80000123, 0x9FFFFFFF, 0x80000000, 0x80000001]


from core.dbc_loader import classify, classify_batch, load, message_table, \
    normalize_id


def _classify_dbc(tmp_path):
    """标准报文 BO_ 100（frame_length 8）+ 扩展报文 BO_ 2147483904（0x80000100，8B）。"""
    p = tmp_path / "c.dbc"
    p.write_text('''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 100 ABC: 8 ECU
 SG_ Speed : 0|16@1+ (0.01,0) [0|655.35] "km/h" ECU

BO_ 2147483904 M2: 8 ECU
 SG_ B : 0|8@1+ (1,0) [0|255] "" ECU
''', encoding="utf-8")
    return load(str(p))


@pytest.mark.parametrize("arb,data_len,is_known", [
    (100, 8, True),            # 已知 ID 标准帧
    (0x80000100, 8, True),     # 已知扩展帧（EFF 键空间）
    (100, 9, True),            # 超长帧（FD 变长载荷）有效——只拒绝更短
    (100, 7, False),           # 已知 ID 短帧 → None
    (0x80000100, 0, False),    # 扩展帧空载荷 → None
    (999, 8, False),           # 未知 ID → None
    (0x100, 8, False),         # 标准帧同 raw id（无 EFF 位）→ 键不存在
])
def test_classify_boundaries(tmp_path, arb, data_len, is_known):
    """分类规则 = 归一化键查找 + 帧长校验（未知 ID/短帧 → None）。"""
    dbc = _classify_dbc(tmp_path)
    md = classify(dbc, arb, data_len)
    if is_known:
        assert md is dbc.messages[arb]
    else:
        assert md is None


def test_message_table_contract(tmp_path):
    """长度表：键 uint32 排序（searchsorted 前提）、lens int64、mds 与键对齐。"""
    dbc = _classify_dbc(tmp_path)
    keys, lens, mds = message_table(dbc)
    assert keys.dtype == np.uint32 and lens.dtype == np.int64
    assert np.all(np.diff(keys) > 0), "键必须严格排序（searchsorted 前提）"
    assert keys.tolist() == sorted(dbc.messages)
    assert lens.tolist() == [dbc.messages[k].frame_length for k in keys]
    assert [m.name for m in mds] == [dbc.messages[k].name for k in keys]


def test_classify_batch_matches_scalar_random(tmp_path):
    """批量与标量逐元素等价（属性测试）：随机帧 on 随机信号 DBC。

    复用 test_decoder_vectorized 的生成器（spec A4：同 A2 测试节）。
    每行断言：found = 键在表内；valid = 键在表内且帧长足够（= classify 非 None）。"""
    from test_decoder_vectorized import _random_frames, _random_signal_dbc
    rng = np.random.default_rng(20260819)
    for trial in range(20):
        frame_len = int(rng.choice([8, 8, 16]))
        p = tmp_path / f"r{trial}.dbc"
        p.write_text(_random_signal_dbc(rng, frame_len), encoding="utf-8")
        dbc = load(str(p))
        table = message_table(dbc)
        frames = _random_frames(rng, int(rng.integers(10, 80)), known_ids=[100])
        arb = np.array([normalize_id(f.arbitration_id, f.is_extended)
                        for f in frames], dtype=np.uint32)
        data_len = np.array([len(f.data) for f in frames], dtype=np.int64)
        found, valid = classify_batch(table, arb, data_len)
        assert found.dtype == np.bool_ and valid.dtype == np.bool_
        assert len(found) == len(frames) == len(valid)
        for i, fr in enumerate(frames):
            assert bool(found[i]) == (int(arb[i]) in dbc.messages), i
            md = classify(dbc, int(arb[i]), int(data_len[i]))
            assert bool(valid[i]) == (md is not None), i


def test_classify_batch_empty_table(tmp_path):
    """空 DBC：classify None；classify_batch 全 False（调用方按 ~valid 全计未知）。"""
    p = tmp_path / "e.dbc"
    p.write_text('''VERSION ""

NS_ :

BS_:

BU_: ECU
''', encoding="utf-8")
    dbc = load(str(p))
    assert classify(dbc, 1, 8) is None
    table = message_table(dbc)
    assert table[0].size == 0 and table[1].size == 0 and table[2] == []
    found, valid = classify_batch(table, np.array([1, 2], dtype=np.uint32),
                                  np.array([8, 8], dtype=np.int64))
    assert not found.any() and not valid.any()
