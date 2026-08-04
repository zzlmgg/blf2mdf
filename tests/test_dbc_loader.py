import pytest

from core.dbc_loader import load
from conftest import all_dbc_files

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


@pytest.mark.parametrize("dbc_path", all_dbc_files(), ids=lambda p: p.name)
def test_all_real_dbc_parse(dbc_path):
    dbc = load(str(dbc_path))
    assert dbc.messages, f"{dbc_path.name} 解析后为空"


def test_load_malformed_dbc_raises_with_path(tmp_path):
    p = tmp_path / "bad.dbc"
    p.write_text("garbage line 1\nstill not dbc\n", encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        load(str(p))
    assert str(p) in str(excinfo.value)
