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
