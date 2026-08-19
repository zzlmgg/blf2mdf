"""绑定决策纯函数：decide_bindings / derive_state（无 Qt、无 qapp 依赖）。

迁移自 test_gui_binding.py 的绑定决策用例（B 重构：决策算法抽为
gui/binding.py 纯函数，绑定键从显示名改为 DBC 文件路径）。事故史保留：
修复前 bug：auto_bind 键为 int 通道号，_rebuild_channel_table 却用字符串
"CANn" 查表，恒不命中 → 选完项目表格全为"不绑定"。双键型契约
（auto int / prev "CANn"）在本次重构中归一为 int 通道号。
"""
from pathlib import Path

from gui.binding import (
    STATE_BOUND,
    STATE_NOT_EXPORTED,
    STATE_NO_DATA,
    BindingRow,
    decide_bindings,
    derive_state,
)


def _p(project: str, name: str) -> str:
    """DBC 路径（相对路径表示，与 GUI 测试构造一致）。"""
    return str(Path(project) / name)


PFCAN1 = _p("A19G1", "PFCAN1.dbc")
CFCAN1 = _p("A19G1", "CFCAN1.dbc")
PFCAN2 = _p("A19G1", "PFCAN2.dbc")
PFCAN2_AH8 = _p("AH8", "PFCAN2.dbc")


def test_decide_bindings_applies_auto():
    """选项目（prev=None）：auto 建议（int 通道键 + 路径值）逐行选中。"""
    rows = decide_bindings([1, 13], {1: PFCAN1, 13: CFCAN1}, None,
                           [PFCAN1, CFCAN1])
    assert rows == [
        BindingRow(1, PFCAN1, STATE_BOUND),
        BindingRow(13, CFCAN1, STATE_BOUND),
    ]


def test_decide_bindings_auto_fallback_after_blf_load():
    """BLF 晚于项目加载：表格为空（prev 无该行）时由 auto 建议兜底。"""
    rows = decide_bindings([1], {1: PFCAN1}, {}, [PFCAN1])
    assert rows == [BindingRow(1, PFCAN1, STATE_BOUND)]


def test_decide_bindings_prev_preserved():
    """添加/移除 DBC 重建：用户手动选择不丢失（prev 命中）。"""
    rows = decide_bindings([1], None, {1: CFCAN1}, [PFCAN1, CFCAN1])
    assert rows == [BindingRow(1, CFCAN1, STATE_BOUND)]


def test_decide_bindings_auto_missing_dbc_keeps_unbound():
    """auto 建议的 DBC 不在列表（AH8 缺 PFCAN2 场景）→ 保持不绑定。"""
    rows = decide_bindings([15], {15: PFCAN2}, None, [PFCAN1])
    assert rows == [BindingRow(15, None, STATE_NOT_EXPORTED)]


def test_decide_bindings_mapping_channel_missing_from_blf():
    """映射通道不在 BLF（样例 BLF 无 CAN15）：仍显示该行并绑定，
    状态「无数据」——映射是完整规格，不能静默缺失。"""
    rows = decide_bindings([1], {1: PFCAN1, 15: PFCAN2}, None,
                           [PFCAN2, PFCAN1])
    assert rows == [
        BindingRow(1, PFCAN1, STATE_BOUND),
        BindingRow(15, PFCAN2, STATE_NO_DATA),
    ]


def test_decide_bindings_same_name_dbc_paths_distinguishable():
    """同名 DBC 来自不同项目文件夹（A19G1/AH8 均有 PFCAN2.dbc）：
    按路径匹配各自文件，不互相串绑。"""
    rows = decide_bindings([15], {15: PFCAN2}, None, [PFCAN2, PFCAN2_AH8])
    assert rows == [BindingRow(15, PFCAN2, STATE_BOUND)]
    rows = decide_bindings([15], {15: PFCAN2_AH8}, None, [PFCAN2, PFCAN2_AH8])
    assert rows == [BindingRow(15, PFCAN2_AH8, STATE_BOUND)]


def test_decide_bindings_project_switch_drops_stale_row():
    """换项目后，旧项目映射出的「无数据」行不残留（行集合随 auto 收缩）。"""
    rows = decide_bindings([1], {1: PFCAN1, 15: PFCAN1}, None, [PFCAN1])
    assert [r.channel for r in rows] == [1, 15]
    rows = decide_bindings([1], {1: PFCAN1}, None, [PFCAN1])
    assert [r.channel for r in rows] == [1]


def test_decide_bindings_prev_wins_over_auto():
    """项目已选 + 用户微调 + 添加 DBC：prev（用户微调）优先于 auto 建议
    （现状 keep_prev=True 语义：auto 参数仅在选项目时传且此时 prev 为空）。"""
    rows = decide_bindings([1], {1: PFCAN1}, {1: CFCAN1}, [PFCAN1, CFCAN1])
    assert rows == [BindingRow(1, CFCAN1, STATE_BOUND)]


def test_decide_bindings_prev_explicit_unbound_keeps_unbound():
    """用户把行改回「不绑定」后重建（如添加 DBC）：保持不绑定，
    不被 auto 建议拉回（prev=None 是显式选择，压制 auto 兜底）。"""
    rows = decide_bindings([1], {1: PFCAN1}, {1: None}, [PFCAN1])
    assert rows == [BindingRow(1, None, STATE_NOT_EXPORTED)]


def test_decide_bindings_prev_invalid_filtered():
    """prev 指向不在列表的 DBC 且无 auto 兜底 → 按不绑定处理。"""
    rows = decide_bindings([1], None, {1: PFCAN2}, [PFCAN1])
    assert rows == [BindingRow(1, None, STATE_NOT_EXPORTED)]


def test_decide_bindings_prev_invalid_falls_back_to_auto():
    """prev 指向的 DBC 被移除（路径失效）→ 回退 auto 建议重绑，
    而非保持不绑定——现状 self.auto_bind 第 3 级兜底语义（Q9）。"""
    rows = decide_bindings([1], {1: PFCAN1}, {1: PFCAN2}, [PFCAN1])
    assert rows == [BindingRow(1, PFCAN1, STATE_BOUND)]


def test_decide_bindings_rows_union_sorted():
    """行集合 = BLF 通道 ∪ auto 映射通道，按通道号升序；prev 不产生行。"""
    rows = decide_bindings([13, 1], {15: PFCAN2}, None, [PFCAN2])
    assert [r.channel for r in rows] == [1, 13, 15]
    rows = decide_bindings([13, 1], None, None, [PFCAN2])
    assert [r.channel for r in rows] == [1, 13]
    # prev 中的通道不在行集合（旧表格残留）→ 不产生行
    rows = decide_bindings([1], None, {15: PFCAN2}, [PFCAN2])
    assert [r.channel for r in rows] == [1]


def test_decide_bindings_empty_inputs():
    assert decide_bindings([], None, None, []) == []


def test_derive_state_tristate():
    assert derive_state(True, PFCAN1) == STATE_BOUND
    assert derive_state(True, None) == STATE_NOT_EXPORTED
    assert derive_state(False, PFCAN1) == STATE_NO_DATA
