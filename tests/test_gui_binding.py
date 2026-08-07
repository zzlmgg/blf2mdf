"""GUI 通道绑定回归：选项目后的自动匹配应用（离线 offscreen 运行）。

修复前 bug：auto_bind 键为 int 通道号，_rebuild_channel_table 却用字符串
"CANn" 查表，恒不命中 → 选完项目表格全为"不绑定"。
"""
import os
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.dbc_loader import DbcDef  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def window(qapp, monkeypatch):
    import gui.main_window as mw

    # 避免 __init__ 自动加载调试 BLF（慢且路径是机器相关的）
    monkeypatch.setattr(mw, "DEFAULT_BLF", r"E:\__nonexistent__.blf")
    return mw.MainWindow()


def _d(name: str) -> DbcDef:
    return DbcDef(path=str(Path("x") / name), db=None)


def test_project_select_applies_auto_bindings(window):
    """选项目（auto=int 键映射）后，表格每行按映射选中对应 DBC。"""
    window.dbc_list = [_d("PFCAN1.dbc"), _d("CFCAN1.dbc")]
    window._rebuild_channel_table([1, 13], auto={1: "PFCAN1.dbc",
                                                 13: "CFCAN1.dbc"},
                                  keep_prev=False)
    assert window.table.cellWidget(0, 1).currentText() == "PFCAN1.dbc"
    assert window.table.cellWidget(1, 1).currentText() == "CFCAN1.dbc"
    assert window.table.item(0, 2).text() == "已绑定"


def test_auto_bind_fallback_after_blf_load(window):
    """BLF 晚于项目加载：无 auto 参数时由 self.auto_bind（int 键）兜底。"""
    window.dbc_list = [_d("PFCAN1.dbc")]
    window.auto_bind = {1: "PFCAN1.dbc"}
    window._rebuild_channel_table([1])
    assert window.table.cellWidget(0, 1).currentText() == "PFCAN1.dbc"


def test_prev_selection_preserved_on_rebuild(window):
    """添加/移除 DBC 重建时不丢失用户手动选择。"""
    window.dbc_list = [_d("PFCAN1.dbc"), _d("CFCAN1.dbc")]
    window._rebuild_channel_table([1])
    combo = window.table.cellWidget(0, 1)
    combo.setCurrentText("CFCAN1.dbc")
    window._rebuild_channel_table([1])  # keep_prev=True 默认
    assert window.table.cellWidget(0, 1).currentText() == "CFCAN1.dbc"


def test_auto_bind_missing_dbc_keeps_unbound(window):
    """auto 映射的 DBC 不在列表（AH8 缺 PFCAN2 场景）→ 保持不绑定。"""
    window.dbc_list = [_d("PFCAN1.dbc")]
    window._rebuild_channel_table([15], auto={15: "PFCAN2.dbc"},
                                  keep_prev=False)
    assert window.table.cellWidget(0, 1).currentText() == "不绑定"


def test_mapping_channel_missing_from_blf_row_added(window):
    """映射通道不在 BLF（样例 BLF 无 CAN15）：仍显示该行并绑定，
    状态标记「无数据」——映射是完整规格，不能静默缺失。"""
    window.dbc_list = [_d("PFCAN2.dbc"), _d("PFCAN1.dbc")]
    window.auto_bind = {1: "PFCAN1.dbc", 15: "PFCAN2.dbc"}
    window._rebuild_channel_table([1])  # BLF 只有 CAN1
    rows = {window.table.item(r, 0).text(): r
            for r in range(window.table.rowCount())}
    assert "CAN15" in rows
    r15 = rows["CAN15"]
    assert window.table.cellWidget(r15, 1).currentText() == "PFCAN2.dbc"
    assert window.table.item(r15, 2).text() == "无数据"
    # BLF 有的通道不受影响：CAN1 正常绑定
    r1 = rows["CAN1"]
    assert window.table.cellWidget(r1, 1).currentText() == "PFCAN1.dbc"
    assert window.table.item(r1, 2).text() == "已绑定"


def test_dbc_combo_ignores_mouse_wheel(window):
    """DBC 矩阵列下拉禁用滚轮改选（悬停滚轮误触会改掉绑定）。"""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    window.dbc_list = [_d("PFCAN1.dbc"), _d("PFCAN2.dbc")]
    window._rebuild_channel_table([1])
    combo = window.table.cellWidget(0, 1)
    combo.setCurrentText("PFCAN1.dbc")
    combo.setFocus()
    ev = QWheelEvent(
        QPointF(50, 50), QPointF(50, 50),   # pos, globalPos
        QPoint(0, 0), QPoint(0, 120),       # pixelDelta, angleDelta
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(combo, ev)
    assert combo.currentText() == "PFCAN1.dbc"


def test_channel_table_shows_all_rows_without_scrollbar(window):
    """通道匹配高度足够展示全部行（13 行场景无垂直滚动条）。"""
    import gui.main_window as mw

    window.dbc_list = [_d(f"PFCAN1.dbc")]
    window._rebuild_channel_table([0, 1, 2, 3, 6, 8, 9, 10, 11, 12, 13, 14, 15])
    window.resize(760, 920)
    window.show()
    assert window.table.verticalScrollBar().maximum() == 0
    assert isinstance(window.table.cellWidget(0, 1), mw.DbcCombo)


def test_table_height_fits_rows_plus_slack(window, qapp):
    """通道匹配表格高度 = 表头 + 各行 + 少量余量（「刚好 N 行多一丢丢」）。

    13 行（样例 BLF 12 通道 + 映射 CAN15）时表格恰好容纳所有行，
    余量不超过半行——不再按窗口高度占比分配导致过高。
    """
    window.dbc_list = [_d("PFCAN1.dbc")]
    window._rebuild_channel_table(list(range(13)))
    window.show()
    qapp.processEvents()
    content = (window.table.horizontalHeader().height()
               + sum(window.table.rowHeight(r) for r in range(13))
               + 2 * window.table.frameWidth())
    assert 0 <= window.table.height() - content <= 12


def test_summary_height_reduced_to_1_6(window, qapp):
    """结果摘要 ≈ 46px（默认布局下原 277px 的 1/6），不再抢高度。"""
    window._rebuild_channel_table([1])
    window.show()
    qapp.processEvents()
    assert window.summary.height() == 46


def test_window_snaps_to_content_height(window, qapp):
    """加载后窗口高度贴合内容（不再固定 920 造成底部大段空白）。"""
    window.dbc_list = [_d("PFCAN1.dbc")]
    window._rebuild_channel_table(list(range(13)))
    window.show()
    qapp.processEvents()
    v = window.centralWidget().layout()
    assert abs(window.height() - v.minimumSize().height()) <= 2
    assert window.height() < 850


def test_project_switch_drops_stale_mapped_row(window):
    """换项目后，旧项目映射出的「无数据」行不应残留。

    镜像 _select_project 的真实调用：以 self.blf_channels（真实 BLF 通道，
    与表格当前行无关）作为行集基准。修复前 _select_project 传的是当前
    表格行，会把上一项目的残留行（如 CAN15）带进来。
    """
    window.dbc_list = [_d("PFCAN1.dbc")]
    window.blf_channels = [1]  # 真实 BLF 只有 CAN1
    window.auto_bind = {1: "PFCAN1.dbc", 15: "PFCAN1.dbc"}  # 项目 A：映射含 15
    window._rebuild_channel_table(window.blf_channels, auto=window.auto_bind,
                                  keep_prev=False)
    assert [window.table.item(r, 0).text()
            for r in range(window.table.rowCount())] == ["CAN1", "CAN15"]
    window.auto_bind = {1: "PFCAN1.dbc"}  # 项目 B：映射不含 15
    window._rebuild_channel_table(window.blf_channels, auto=window.auto_bind,
                                  keep_prev=False)
    assert [window.table.item(r, 0).text()
            for r in range(window.table.rowCount())] == ["CAN1"]
