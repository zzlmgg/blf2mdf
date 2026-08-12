"""PySide/QSS 界面视觉契约测试。"""

import os
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def window(qapp):
    from gui.main_window import MainWindow

    widget = MainWindow()
    yield widget
    widget.close()


def test_theme_exposes_approved_tokens():
    from gui.theme import APP_QSS, WINDOW_HEIGHT, WINDOW_WIDTH

    assert (WINDOW_WIDTH, WINDOW_HEIGHT) == (690, 596)
    theme = APP_QSS.lower()
    for color in (
        "#ffffff",
        "#1c1d20",
        "#087cf0",
        "#207e4b",
        "#c93834",
    ):
        assert color in theme
    assert "qwidget#approot" in theme
    assert "border-radius: 10px" in theme
    assert "rgba(248, 250, 253" in theme


def test_theme_uses_explicit_chinese_capable_application_font(qapp):
    from gui.theme import apply_theme

    apply_theme(qapp)
    assert qapp.font().family() == "Microsoft YaHei UI"


def test_semantic_icon_has_16px_ring_and_28px_hit_target(qapp):
    from gui.widgets import SemanticIconButton

    button = SemanticIconButton("+", "positive")
    assert button.width() == 28
    assert button.height() == 28
    assert button.property("ringSize") == 16
    assert button.property("tone") == "positive"


def test_dbc_item_keeps_full_path_in_tooltip(qapp):
    from gui.widgets import DbcListItemWidget

    path = r"E:\projects\blf_dbc\inputs\dbc_ccu3.0\A19G1\PFCAN1.dbc"
    item = DbcListItemWidget(path, lambda: None)
    assert item.file_label.text() == "PFCAN1.dbc"
    assert item.path_label.text() == path
    assert item.toolTip() == path
    assert item.remove_button.property("tone") == "negative"


def test_title_bar_exposes_centered_title_and_windows_actions(qapp):
    from PySide6.QtWidgets import QLabel

    from gui.widgets import TitleBar

    title_bar = TitleBar()
    title_bar.resize(690, 38)
    title_bar.show()
    qapp.processEvents()
    assert title_bar.height() == 38
    assert title_bar.title_label.text() == "BLF → MDF"
    assert title_bar.title_label.alignment() & Qt.AlignmentFlag.AlignHCenter
    assert "Signal Workspace" not in {
        label.text() for label in title_bar.findChildren(QLabel)
    }
    assert title_bar.close_button.objectName() == "windowCloseButton"
    assert title_bar.minimize_button.objectName() == "windowMinimizeButton"
    assert title_bar.maximize_button.objectName() == "windowMaximizeButton"
    assert title_bar.minimize_button.property("controlKind") == "minimize"
    assert title_bar.maximize_button.property("controlKind") == "maximize"
    assert title_bar.close_button.property("controlKind") == "close"
    assert (
        title_bar.minimize_button.x()
        < title_bar.maximize_button.x()
        < title_bar.close_button.x()
    )
    for button in (
        title_bar.minimize_button,
        title_bar.maximize_button,
        title_bar.close_button,
    ):
        assert (button.width(), button.height()) == (40, 38)
    assert title_bar.maximize_button.x() - title_bar.minimize_button.x() == 40
    assert title_bar.close_button.x() - title_bar.maximize_button.x() == 40
    assert title_bar.close_button.geometry().right() == title_bar.width() - 1


def test_summary_dialog_keeps_full_text_and_pinned_done_button(qapp):
    from gui.widgets import SummaryDialog

    dialog = SummaryDialog()
    dialog.set_summary("总时长 1.0 s\nCAN 1 已绑定")
    assert dialog.summary_view.toPlainText() == "总时长 1.0 s\nCAN 1 已绑定"
    assert dialog.done_button.text() == "完成"
    assert dialog.done_button.objectName() == "primaryButton"


def test_window_uses_compact_approved_geometry(window, qapp):
    window.show()
    qapp.processEvents()
    assert (window.width(), window.height()) == (690, 596)
    assert (window.minimumWidth(), window.minimumHeight()) == (690, 596)
    assert window.windowTitle() == "BLF → MDF"


def test_window_uses_translucent_rounded_shell(window, qapp, monkeypatch):
    import gui.main_window as main_window

    monkeypatch.setattr(main_window, "apply_light_glass", lambda _: False)
    window.show()
    qapp.processEvents()

    assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert window.centralWidget().property("nativeGlass") is False
    assert not window.mask().isEmpty()
    assert not window.mask().contains(window.rect().topLeft())


def test_maximize_clears_and_restore_reapplies_fallback_mask(
    window, qapp, monkeypatch
):
    import gui.main_window as main_window

    monkeypatch.setattr(main_window, "apply_light_glass", lambda _: False)
    window.show()
    qapp.processEvents()
    assert not window.mask().isEmpty()

    window.showMaximized()
    qapp.processEvents()
    assert window.mask().isEmpty()
    assert window.centralWidget().property("shellMaximized") is True

    window.showNormal()
    qapp.processEvents()
    assert not window.mask().isEmpty()
    assert window.centralWidget().property("shellMaximized") is False


def test_workspace_is_two_compact_cards(window, qapp):
    window.show()
    qapp.processEvents()
    assert 298 <= window.dbc_panel.width() <= 302
    assert 338 <= window.channel_panel.width() <= 342
    assert window.dbc_panel.height() == 350
    assert window.channel_panel.height() == 350
    assert not window.table.horizontalHeader().isVisible()
    assert not window.table.verticalHeader().isVisible()


def test_compact_rows_use_approved_labels_and_wide_browse_buttons(window):
    assert window.blf_label.text() == "输入 BLF"
    assert window.convert_btn.text() == "开始转换"
    assert window.btn_blf.minimumWidth() >= 76
    assert window.btn_out.minimumWidth() >= 76
    assert window.summary_bar.minimumHeight() == 34


def test_only_output_path_is_manually_editable(window):
    assert window.blf_edit.isReadOnly()
    assert not window.out_edit.isReadOnly()


def test_project_combo_uses_short_placeholder(window):
    assert window.project_combo.itemText(0) == "项目"
    assert not window.project_combo.model().item(0).isEnabled()


def test_ccu_version_reserves_disabled_ccu4(window):
    assert window.ccu_combo.itemText(0) == "ccu3.0"
    assert window.ccu_combo.itemText(1) == "ccu4.0 · 暂未开发"
    assert not window.ccu_combo.model().item(1).isEnabled()


def test_compact_header_controls_use_approved_widths(window):
    # Windows/QSS 实测控件固定开销 56px；宽度刚好覆盖最长实际文本。
    assert window.ccu_combo.width() == 95
    assert window.project_combo.width() == 98
    assert window.ccu_combo.width() - 56 >= 39  # ccu3.0
    assert window.project_combo.width() - 56 >= 42  # A19G1
    assert window.dbc_panel.layout().itemAt(0).layout().spacing() == 2


def test_channel_dbc_combo_fits_long_project_qualified_name(window, qapp):
    from core.dbc_loader import DbcDef

    window.dbc_list = [
        DbcDef(path=r"E:\dbc\A19G1\PFCAN2.dbc", db=None)
    ]
    window._rebuild_channel_table([1])
    combo = window.table.cellWidget(0, 1)
    combo.setCurrentIndex(1)
    window.show()
    qapp.processEvents()

    text = "PFCAN2.dbc（A19G1）"
    assert combo.currentText() == text
    # Windows 插件实测文字宽 145px；QSS 内边距与箭头占 56px。
    assert combo.width() - 56 >= 145
    assert [window.table.columnWidth(index) for index in range(3)] == [
        66,
        209,
        58,
    ]


def test_channel_rows_use_spaced_can_and_internal_scroll(window, qapp):
    window._rebuild_channel_table(list(range(20)))
    window.show()
    qapp.processEvents()
    assert window.table.item(1, 0).text() == "CAN 1"
    assert window.channel_count_label.text() == "20 路"
    assert window.table.verticalScrollBar().maximum() > 0


def test_dbc_list_renders_full_path_widget(window, qapp):
    from core.dbc_loader import DbcDef
    from gui.widgets import DbcListItemWidget

    path = r"E:\dbc\A19G1\PFCAN1.dbc"
    window.dbc_list = [DbcDef(path=path, db=None)]
    window._refresh_dbc_items()
    window.show()
    qapp.processEvents()
    widget = window.dbc_list_widget.itemWidget(window.dbc_list_widget.item(0))
    assert isinstance(widget, DbcListItemWidget)
    assert widget.file_label.text() == "PFCAN1.dbc"
    assert widget.path_label.text() == path


def test_remove_button_removes_its_own_dbc(window):
    from pathlib import Path

    from core.dbc_loader import DbcDef

    window.dbc_list = [
        DbcDef(path=r"E:\dbc\A\PFCAN1.dbc", db=None),
        DbcDef(path=r"E:\dbc\A\CFCAN1.dbc", db=None),
    ]
    window._refresh_dbc_items()
    window._remove_dbc_at(0)
    assert [Path(d.path).name for d in window.dbc_list] == ["CFCAN1.dbc"]


def test_channel_status_uses_clear_semantic_color(window):
    from core.dbc_loader import DbcDef

    window.dbc_list = [DbcDef(path=r"E:\dbc\A\PFCAN1.dbc", db=None)]
    window._rebuild_channel_table([1])
    status = window.table.item(0, 2)
    assert status.text() == "不导出"
    assert status.foreground().color().name() == "#a56400"

    window.table.cellWidget(0, 1).setCurrentIndex(1)
    assert status.text() == "已绑定"
    assert status.foreground().color().name() == "#207e4b"


def test_busy_state_locks_mutating_controls(window):
    window.blf_path = r"E:\data\run.blf"
    window.out_edit.setText(r"E:\data\run.mdf")
    window._set_busy(True)
    for widget in (
        window.btn_blf,
        window.btn_add_dbc,
        window.ccu_combo,
        window.project_combo,
        window.dbc_list_widget,
        window.table,
        window.btn_out,
        window.out_edit,
    ):
        assert not widget.isEnabled()
    assert not window.convert_btn.isEnabled()

    window._set_busy(False)
    assert window.btn_blf.isEnabled()
    assert window.btn_add_dbc.isEnabled()
    assert window.project_combo.isEnabled()
    assert window.table.isEnabled()
    assert window.btn_out.isEnabled()
    assert window.out_edit.isEnabled()
    assert window.convert_btn.isEnabled()


def test_done_updates_summary_before_showing_completion_notice(
    window, qapp, monkeypatch
):
    from PySide6.QtWidgets import QMessageBox

    from core.converter import ChannelSummary, ConversionResult

    result = ConversionResult(
        summaries=[
            ChannelSummary(
                channel=1,
                bound=True,
                decoded_frames=120,
                signal_count=8,
                unknown_frames=2,
                unknown_ids=1,
                warning="示例警告",
            ),
            ChannelSummary(channel=2, bound=False),
        ],
        duration_seconds=12.34,
    )
    monkeypatch.setattr(window, "_finish", lambda: None)

    observed = {}

    def inspect_completion_notice(dialog):
        observed["parent"] = dialog.parent()
        observed["title"] = dialog.windowTitle()
        observed["text"] = dialog.text()
        observed["buttons"] = [button.text() for button in dialog.buttons()]
        observed["summary_status"] = window.summary_status.text()
        observed["summary_enabled"] = window.summary_button.isEnabled()
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "exec", inspect_completion_notice)
    window.show()
    qapp.processEvents()
    window._on_done(result)

    assert observed["parent"] is window
    assert observed["title"] == "提示"
    assert observed["text"] == "转换完成。"
    assert observed["buttons"] == ["确定"]
    assert observed["summary_status"] == "已完成 · 12.3 s · 1 条警告"
    assert observed["summary_enabled"]
    assert window.isVisible()
    assert "总时长: 12.3 s" in window.summary_text
    assert "CAN 1 已绑定" in window.summary_text
    assert "CAN 2 未绑定" in window.summary_text
    assert window.summary.toPlainText() == window.summary_text
    assert window.summary_status.text() == "已完成 · 12.3 s · 1 条警告"
    assert window.summary_button.isEnabled()


def test_render_script_targets_styled_main_window():
    project_root = Path(__file__).resolve().parents[1]
    source = (project_root / "tools" / "render_pyside_ui.py").read_text(
        encoding="utf-8"
    )
    assert "MainWindow" in source
    assert "apply_theme" in source
    assert ".grab()" in source


def test_compact_combo_uses_custom_chevron(qapp):
    from gui.widgets import CompactCombo

    combo = CompactCombo()
    assert combo.property("customChevron") is True
    assert combo.minimumHeight() >= 29


def test_dbc_item_elides_long_path_from_left(qapp):
    from gui.widgets import DbcListItemWidget

    path = (
        r"E:\projects\blf_dbc\exe_publish\dbc_ccu3.0\A19G1"
        r"\a_very_long_intermediate_directory_name\PFCAN1.dbc"
    )
    item = DbcListItemWidget(path, lambda: None)
    item.resize(220, 52)
    item.show()
    qapp.processEvents()

    visible_path = item.path_label.visible_text()
    assert visible_path.startswith("…")
    assert visible_path.endswith(r"\PFCAN1.dbc")
    assert visible_path != path
    assert item.path_label.toolTip() == path


def test_dbc_remove_button_stays_inside_list_viewport_for_long_path(window, qapp):
    from PySide6.QtCore import QPoint

    from core.dbc_loader import DbcDef

    path = (
        r"E:\projects\blf_dbc\exe_publish\dbc_ccu3.0\A19G1"
        "\\"
        + ("long_directory_name\\" * 12)
        + "PFCAN1.dbc"
    )
    window.dbc_list = [
        DbcDef(path=path.replace("PFCAN1", f"PFCAN{row}"), db=None)
        for row in range(12)
    ]
    window._refresh_dbc_items()
    window.show()
    qapp.processEvents()

    viewport_width = window.dbc_list_widget.viewport().width()
    assert window.dbc_list_widget.verticalScrollBar().maximum() > 0
    for row in range(window.dbc_list_widget.count()):
        item = window.dbc_list_widget.item(row)
        widget = window.dbc_list_widget.itemWidget(item)
        button_right = widget.remove_button.mapTo(
            window.dbc_list_widget.viewport(),
            QPoint(widget.remove_button.width() - 1, 0),
        ).x()
        assert widget.width() <= viewport_width
        assert button_right < viewport_width


def test_dbc_list_uses_windows_extended_selection(window):
    from PySide6.QtWidgets import QAbstractItemView

    assert (
        window.dbc_list_widget.selectionMode()
        == QAbstractItemView.SelectionMode.ExtendedSelection
    )


def test_mouse_ctrl_click_selects_multiple_dbcs(window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from core.dbc_loader import DbcDef

    window.dbc_list = [
        DbcDef(path=r"E:\dbc\A\PFCAN1.dbc", db=None),
        DbcDef(path=r"E:\dbc\A\CFCAN1.dbc", db=None),
        DbcDef(path=r"E:\dbc\A\ZFCANF.dbc", db=None),
    ]
    window._refresh_dbc_items()
    window.show()
    qapp.processEvents()

    first_widget = window.dbc_list_widget.itemWidget(window.dbc_list_widget.item(0))
    third_widget = window.dbc_list_widget.itemWidget(window.dbc_list_widget.item(2))
    QTest.mouseClick(first_widget.file_label, Qt.MouseButton.LeftButton)
    QTest.mouseClick(
        third_widget.file_label,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
    )
    qapp.processEvents()

    assert {
        window.dbc_list_widget.row(item)
        for item in window.dbc_list_widget.selectedItems()
    } == {0, 2}


def test_mouse_shift_click_selects_contiguous_dbc_range(window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from core.dbc_loader import DbcDef

    window.dbc_list = [
        DbcDef(path=rf"E:\dbc\A\CAN{index}.dbc", db=None)
        for index in range(4)
    ]
    window._refresh_dbc_items()
    window.show()
    qapp.processEvents()

    first_widget = window.dbc_list_widget.itemWidget(window.dbc_list_widget.item(0))
    third_widget = window.dbc_list_widget.itemWidget(window.dbc_list_widget.item(2))
    QTest.mouseClick(first_widget.file_label, Qt.MouseButton.LeftButton)
    QTest.mouseClick(
        third_widget.file_label,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
    )
    qapp.processEvents()

    assert {
        window.dbc_list_widget.row(item)
        for item in window.dbc_list_widget.selectedItems()
    } == {0, 1, 2}


def test_selected_dbc_has_visible_feedback(window, qapp):
    from PySide6.QtGui import QColor

    from core.dbc_loader import DbcDef
    from gui.theme import apply_theme

    apply_theme(qapp)
    window.dbc_list = [DbcDef(path=r"E:\dbc\A\PFCAN1.dbc", db=None)]
    window._refresh_dbc_items()
    window.show()
    qapp.processEvents()

    item = window.dbc_list_widget.item(0)
    widget = window.dbc_list_widget.itemWidget(item)
    item.setSelected(True)
    qapp.processEvents()

    image = widget.grab().toImage()
    assert QColor(image.pixel(1, 1)).name() == "#e8f2fd"


def test_delete_key_removes_all_selected_dbcs(window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from core.dbc_loader import DbcDef

    window.dbc_list = [
        DbcDef(path=r"E:\dbc\A\PFCAN1.dbc", db=None),
        DbcDef(path=r"E:\dbc\A\CFCAN1.dbc", db=None),
        DbcDef(path=r"E:\dbc\A\ZFCANF.dbc", db=None),
    ]
    window._refresh_dbc_items()
    window.show()
    window.dbc_list_widget.setFocus()
    window.dbc_list_widget.item(0).setSelected(True)
    window.dbc_list_widget.item(2).setSelected(True)
    qapp.processEvents()

    QTest.keyClick(window.dbc_list_widget, Qt.Key.Key_Delete)
    qapp.processEvents()

    assert [Path(dbc.path).name for dbc in window.dbc_list] == ["CFCAN1.dbc"]


def test_window_outer_border_follows_rounded_shell(window, qapp, monkeypatch):
    from PySide6.QtGui import QColor

    import gui.main_window as main_window
    from gui.theme import apply_theme

    monkeypatch.setattr(main_window, "apply_light_glass", lambda _: False)
    apply_theme(qapp)
    window.show()
    qapp.processEvents()
    image = window.grab().toImage()
    edge_points = (
        (window.width() // 2, 0),
        (0, window.height() // 2),
        (window.width() - 1, window.height() // 2),
        (window.width() // 2, window.height() - 1),
    )

    assert {
        QColor(image.pixel(x, y)).name() for x, y in edge_points
    } == {"#aeb1b7"}
    assert not window.mask().contains(window.rect().topLeft())
    assert not window.mask().contains(window.rect().topRight())
    assert not window.mask().contains(window.rect().bottomLeft())
    assert not window.mask().contains(window.rect().bottomRight())
    assert (window.width(), window.height()) == (690, 596)
