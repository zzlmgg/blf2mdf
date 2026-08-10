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
def window(qapp):
    import gui.main_window as mw

    return mw.MainWindow()


def _d(name: str) -> DbcDef:
    # 显示名 = 文件名 + 所在文件夹（如 PFCAN1.dbc（A19G1））
    return DbcDef(path=str(Path("A19G1") / name), db=None)


def test_project_select_applies_auto_bindings(window):
    """选项目（auto=int 键映射）后，表格每行按映射选中对应 DBC。"""
    window.dbc_list = [_d("PFCAN1.dbc"), _d("CFCAN1.dbc")]
    window._rebuild_channel_table([1, 13], auto={1: "PFCAN1.dbc（A19G1）",
                                                 13: "CFCAN1.dbc（A19G1）"},
                                  keep_prev=False)
    assert window.table.cellWidget(0, 1).currentText() == "PFCAN1.dbc（A19G1）"
    assert window.table.cellWidget(1, 1).currentText() == "CFCAN1.dbc（A19G1）"
    assert window.table.item(0, 2).text() == "已绑定"


def test_auto_bind_fallback_after_blf_load(window):
    """BLF 晚于项目加载：无 auto 参数时由 self.auto_bind（int 键）兜底。"""
    window.dbc_list = [_d("PFCAN1.dbc")]
    window.auto_bind = {1: "PFCAN1.dbc（A19G1）"}
    window._rebuild_channel_table([1])
    assert window.table.cellWidget(0, 1).currentText() == "PFCAN1.dbc（A19G1）"


def test_prev_selection_preserved_on_rebuild(window):
    """添加/移除 DBC 重建时不丢失用户手动选择。"""
    window.dbc_list = [_d("PFCAN1.dbc"), _d("CFCAN1.dbc")]
    window._rebuild_channel_table([1])
    combo = window.table.cellWidget(0, 1)
    combo.setCurrentText("CFCAN1.dbc（A19G1）")
    window._rebuild_channel_table([1])  # keep_prev=True 默认
    assert window.table.cellWidget(0, 1).currentText() == "CFCAN1.dbc（A19G1）"


def test_auto_bind_missing_dbc_keeps_unbound(window):
    """auto 映射的 DBC 不在列表（AH8 缺 PFCAN2 场景）→ 保持不绑定。"""
    window.dbc_list = [_d("PFCAN1.dbc")]
    window._rebuild_channel_table([15], auto={15: "PFCAN2.dbc（A19G1）"},
                                  keep_prev=False)
    assert window.table.cellWidget(0, 1).currentText() == "不绑定"


def test_mapping_channel_missing_from_blf_row_added(window):
    """映射通道不在 BLF（样例 BLF 无 CAN15）：仍显示该行并绑定，
    状态标记「无数据」——映射是完整规格，不能静默缺失。"""
    window.dbc_list = [_d("PFCAN2.dbc"), _d("PFCAN1.dbc")]
    window.auto_bind = {1: "PFCAN1.dbc（A19G1）", 15: "PFCAN2.dbc（A19G1）"}
    window._rebuild_channel_table([1])  # BLF 只有 CAN1
    rows = {window.table.item(r, 0).text(): r
            for r in range(window.table.rowCount())}
    assert "CAN15" in rows
    r15 = rows["CAN15"]
    assert window.table.cellWidget(r15, 1).currentText() == "PFCAN2.dbc（A19G1）"
    assert window.table.item(r15, 2).text() == "无数据"
    # BLF 有的通道不受影响：CAN1 正常绑定
    r1 = rows["CAN1"]
    assert window.table.cellWidget(r1, 1).currentText() == "PFCAN1.dbc（A19G1）"
    assert window.table.item(r1, 2).text() == "已绑定"


def test_same_name_dbc_from_two_projects_distinguishable(window):
    """同名 DBC 来自不同项目文件夹（A19G1/AH8 均有 PFCAN2.dbc）：
    下拉两项并存、自动绑定与手动改选均命中各自文件，不互相串绑。

    场景入口是「添加 DBC…」：选项目会整体替换列表，同名单项不会共存；
    手动添加后两个 PFCAN2.dbc 同时入列，靠文件夹后缀区分。
    """
    a19 = DbcDef(path=str(Path("A19G1") / "PFCAN2.dbc"), db=None)
    ah8 = DbcDef(path=str(Path("AH8") / "PFCAN2.dbc"), db=None)
    window.dbc_list = [a19, ah8]
    window._rebuild_channel_table([15], auto={15: "PFCAN2.dbc（A19G1）"},
                                  keep_prev=False)
    combo = window.table.cellWidget(0, 1)
    items = [combo.itemText(i) for i in range(combo.count())]
    assert "PFCAN2.dbc（A19G1）" in items
    assert "PFCAN2.dbc（AH8）" in items
    assert len(items) == len(set(items))  # 同名不同文件夹 = 两项，不合并
    # 自动绑定选中 A19G1 那份
    assert combo.currentText() == "PFCAN2.dbc（A19G1）"
    # 手动改选 AH8 那份 → 转换回查精确命中 AH8 的文件对象（非同名误绑）
    combo.setCurrentText("PFCAN2.dbc（AH8）")
    assert window._dbc_by_display(combo.currentText()) is ah8
    assert window._dbc_by_display("PFCAN2.dbc（A19G1）") is a19
    # 防御：未知显示名（如界面状态异常）→ None，不抛 StopIteration
    assert window._dbc_by_display("PFCAN2.dbc（X）") is None


def test_dbc_combo_ignores_mouse_wheel(window):
    """DBC 矩阵列下拉禁用滚轮改选（悬停滚轮误触会改掉绑定）。"""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    window.dbc_list = [_d("PFCAN1.dbc"), _d("PFCAN2.dbc")]
    window._rebuild_channel_table([1])
    combo = window.table.cellWidget(0, 1)
    combo.setCurrentText("PFCAN1.dbc（A19G1）")
    combo.setFocus()
    ev = QWheelEvent(
        QPointF(50, 50), QPointF(50, 50),   # pos, globalPos
        QPoint(0, 0), QPoint(0, 120),       # pixelDelta, angleDelta
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(combo, ev)
    assert combo.currentText() == "PFCAN1.dbc（A19G1）"


def test_channel_table_shows_all_rows_without_scrollbar(window):
    """通道匹配高度足够展示全部行（16 行场景无垂直滚动条）。"""
    import gui.main_window as mw

    window.dbc_list = [_d(f"PFCAN1.dbc")]
    window._rebuild_channel_table(list(range(16)))
    window.resize(760, 920)
    window.show()
    assert window.table.verticalScrollBar().maximum() == 0
    assert isinstance(window.table.cellWidget(0, 1), mw.DbcCombo)


def test_table_height_fixed_16_rows(window, qapp):
    """通道匹配表格固定高度 = 表头 + 13 路 CAN 基准总高，16 行全显。

    固定几何保证读取 BLF 过程与完成后排布一致（表格不随行数跳变）；
    行高收缩为默认行高的 13/16，2 行与 16 行场景高度相同，16 个通道
    全部可见且总高度与旧版 13 行基准一致（面板不增高）。
    """
    window.dbc_list = [_d("PFCAN1.dbc")]
    window._rebuild_channel_table(list(range(2)))
    window.show()
    qapp.processEvents()
    h2 = window.table.height()
    window._rebuild_channel_table(list(range(16)))
    qapp.processEvents()
    h16 = window.table.height()
    assert h2 == h16
    # 总高 = 表头 + 13×旧默认行高 + 边框 + 余量；13×旧默认行高 =
    # 16×收缩行高 + r（r ∈ [0,15]，整除取整丢失），故落在如下区间
    row_h = window.table.verticalHeader().defaultSectionSize()
    expected = (window.table.horizontalHeader().height()
                + 16 * row_h + 2 * window.table.frameWidth() + 8)
    assert expected <= h16 <= expected + 15
    # 16 行全显，无垂直滚动条（行高收缩的验收点）
    assert window.table.verticalScrollBar().maximum() == 0


def test_summary_height_reduced_to_1_6(window, qapp):
    """结果摘要 ≈ 46px（默认布局下原 277px 的 1/6），不再抢高度。"""
    window._rebuild_channel_table([1])
    window.show()
    qapp.processEvents()
    assert window.summary.height() == 46


def test_window_geometry_stable_through_load(window, qapp):
    """窗口几何在启动时贴合一次，重建表格不再改动——读取中与读取后一致。"""
    window.dbc_list = [_d("PFCAN1.dbc")]
    window.show()
    qapp.processEvents()
    v = window.centralWidget().layout()
    h0 = window.height()
    assert abs(h0 - v.minimumSize().height()) <= 2  # 启动时贴合
    window._rebuild_channel_table(list(range(13)))
    qapp.processEvents()
    assert window.height() == h0                    # 重建后几何不变


# ---- BLF 浏览默认目录 + 输出文件名项目前缀 ----

def test_pick_blf_opens_at_inputs_blf_dir(window, monkeypatch):
    """「浏览…」选择 BLF 时，对话框默认打开项目根目录的 inputs\\blf。"""
    import gui.main_window as mw

    captured = {}

    def fake(parent, title, start_dir, filt):
        captured["start"] = start_dir
        return ("", "")  # 取消选择，不触发加载

    monkeypatch.setattr(mw.QFileDialog, "getOpenFileName",
                        staticmethod(fake))
    window._pick_blf()
    assert captured["start"] == str(mw.PROJECT_ROOT / "inputs" / "blf")


def test_startup_does_not_read_blf(qapp):
    """启动不自动读取 BLF：未选择文件前不触发任何扫描、不显示路径。

    修复项：启动自动加载 DEFAULT_BLF 已移除——用户没选 BLF 之前，
    程序不得自行读文件（大文件读取会肉眼可见地"卡住/读进度"）。
    """
    import gui.main_window as mw

    win = mw.MainWindow()
    try:
        assert win.blf_path is None
        assert win.scan_thread is None
        assert win.blf_edit.text() == ""
        assert win.load_progress.isHidden()
    finally:
        win.close()


def test_load_blf_async_does_not_block(window, qapp, tmp_path):
    """_load_blf 异步加载：立即返回（后台 worker 扫描），完成后刷新界面。

    修复前 list_channels 在主线程全文件同步解析（86MB/565 万帧实测 41s），
    期间窗口冻结成「未响应」；修复后扫描在后台线程，主线程保持响应，
    返回时路径尚未提交，事件循环泵动后结果到达。加载进度显示在
    BLF 文件行的内嵌进度条（load_progress），「转换」行进度条与
    stage_label 只属于转换流程，加载期间保持原样。
    """
    import can
    import time

    p = tmp_path / "tiny.blf"
    with can.BLFWriter(str(p)) as w:
        w.on_message_received(can.Message(arbitration_id=0x123, data=b"\x01",
                                          channel=1, timestamp=1784716800.0))
        w.on_message_received(can.Message(arbitration_id=0x456, data=b"\x03",
                                          channel=2, timestamp=1784716801.0))
    window._load_blf(str(p))
    # 立即返回且未提交路径：扫描在后台进行（同步实现此处会阻塞数十秒）
    assert window.blf_path is None
    assert window.scan_thread is not None
    assert not window.btn_blf.isEnabled()  # 扫描期间禁用文件选择
    window.show()
    qapp.processEvents()
    # 加载反馈在 BLF 行内嵌进度条；「转换」进度条与状态标签不被动用
    assert window.load_progress.isVisible()
    assert window.progress.value() == 0
    assert window.stage_label.text() == ""
    deadline = time.monotonic() + 10
    while window.blf_path is None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert window.blf_path == str(p)
    assert window.blf_channels == [1, 2]
    assert window.scan_thread is None          # 扫描线程已收尾
    assert window.convert_btn.isEnabled()      # 加载完成后可转换
    assert window.load_progress.isHidden()     # 内嵌进度条已隐藏（不占排版）
    assert window.progress.value() == 0        # 转换进度条仍归零
    assert window.stage_label.text() == ""
    assert window.btn_blf.isEnabled()          # 文件选择恢复
    window.close()


def test_layout_identical_before_during_after_load(window, qapp, tmp_path):
    """读取 BLF 全程（读取前/读取中/完成后）面板几何完全一致。

    修复项：通道表固定 16 行高 + 三列固定宽 + 窗口启动时贴合一次后，
    窗口尺寸、通道表尺寸、左栏（ccu3.0 项目选择）尺寸在加载全程不变，
    不随表格行数跳变。
    """
    import can
    import time

    p = tmp_path / "tiny.blf"
    with can.BLFWriter(str(p)) as w:
        w.on_message_received(can.Message(arbitration_id=0x123, data=b"\x01",
                                          channel=1, timestamp=1784716800.0))
    window.show()
    qapp.processEvents()

    def snap():
        return (window.size(), window.table.size(),
                window.project_combo.size())

    before = snap()
    window._load_blf(str(p))
    qapp.processEvents()
    assert snap() == before, "读取中：面板几何不得变化"
    deadline = time.monotonic() + 10
    while window.blf_path is None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert window.blf_channels == [1]
    assert snap() == before, "读取完成：面板几何不得变化"
    window.close()


def test_default_output_plain_timestamp_without_project(window):
    """未选项目：输出名保持 {时间戳}.mdf，不带前缀。"""
    import re

    window._set_default_output()
    assert re.search(r"\d{8}_\d{6}\.mdf$", window.out_edit.text())
    assert not re.search(r"\w+_\d{8}_\d{6}\.mdf$", window.out_edit.text())


def test_default_output_prefixed_with_selected_project(window):
    """已选项目：输出名 = {项目名}_{时间戳}.mdf。"""
    import re

    window._select_project = lambda name: None  # 只切选择，不触发真实加载
    window.project_combo.setCurrentText("A19G1")
    window._set_default_output()
    assert re.search(r"A19G1_\d{8}_\d{6}\.mdf$", window.out_edit.text())


def test_project_select_refreshes_default_output(window, monkeypatch):
    """BLF 已加载、输出还是自动名时，切换项目会把项目名前缀补进输出名。"""
    import gui.main_window as mw
    import re

    monkeypatch.setattr(mw.project_loader, "load_project",
                        lambda root, name: [])  # 保持测试快速、不依赖真实数据
    window.blf_path = r"E:\x.blf"
    window._set_default_output()               # 先产生无前缀的自动名
    assert not re.search(r"\w+_\d{8}_\d{6}\.mdf$", window.out_edit.text())
    window.project_combo.setCurrentText("A19G1")  # 真实 _select_project
    assert re.search(r"A19G1_\d{8}_\d{6}\.mdf$", window.out_edit.text())


def test_project_select_keeps_custom_output_path(window, monkeypatch):
    """用户手动改过的输出路径，切换项目时不被自动名覆盖。"""
    import gui.main_window as mw

    monkeypatch.setattr(mw.project_loader, "load_project",
                        lambda root, name: [])
    window.blf_path = r"E:\x.blf"
    custom = r"E:\mine\custom.mdf"
    window.out_edit.setText(custom)
    window.out_edit.textEdited.emit(custom)  # 模拟用户手输
    window.project_combo.setCurrentText("A19G1")
    assert window.out_edit.text() == custom


def test_project_switch_drops_stale_mapped_row(window):
    """换项目后，旧项目映射出的「无数据」行不应残留。

    镜像 _select_project 的真实调用：以 self.blf_channels（真实 BLF 通道，
    与表格当前行无关）作为行集基准。修复前 _select_project 传的是当前
    表格行，会把上一项目的残留行（如 CAN15）带进来。
    """
    window.dbc_list = [_d("PFCAN1.dbc")]
    window.blf_channels = [1]  # 真实 BLF 只有 CAN1
    # 项目 A：映射含 15
    window.auto_bind = {1: "PFCAN1.dbc（A19G1）", 15: "PFCAN1.dbc（A19G1）"}
    window._rebuild_channel_table(window.blf_channels, auto=window.auto_bind,
                                  keep_prev=False)
    assert [window.table.item(r, 0).text()
            for r in range(window.table.rowCount())] == ["CAN1", "CAN15"]
    window.auto_bind = {1: "PFCAN1.dbc（A19G1）"}  # 项目 B：映射不含 15
    window._rebuild_channel_table(window.blf_channels, auto=window.auto_bind,
                                  keep_prev=False)
    assert [window.table.item(r, 0).text()
            for r in range(window.table.rowCount())] == ["CAN1"]
