"""GUI 通道绑定回归：选项目后的自动匹配应用（离线 offscreen 运行）。

修复前 bug：auto_bind 键为 int 通道号，_rebuild_channel_table 却用字符串
"CANn" 查表，恒不命中 → 选完项目表格全为"不绑定"。
"""
from pathlib import Path

from core.dbc_loader import DbcDef


def _d(name: str) -> DbcDef:
    # 显示名 = 文件名 + 所在文件夹（如 PFCAN1.dbc（A19G1））
    return DbcDef(path=str(Path("A19G1") / name), db=None)


def test_rebuild_renders_decision_rows_to_table(window):
    """接线：decide_bindings 的行（路径绑定 + 状态）渲染为表格显示名与状态列。

    决策层用路径，渲染层负责路径 → display_name 的展示转换；「无数据」
    行保持「无数据」，用户未动下拉时状态与决策一致。
    """
    pfcan1 = _d("PFCAN1.dbc")
    pfcan2 = _d("PFCAN2.dbc")
    window.dbc_list = [pfcan1, pfcan2]
    window.auto_bind = {1: pfcan1.path, 15: pfcan2.path}
    window._rebuild_channel_table([1], prev=None)
    rows = {window.binding_row(r)[0]: r
            for r in range(window.table.rowCount())}
    r1 = rows["CAN 1"]
    assert window.binding_row(r1) == ("CAN 1", "PFCAN1.dbc（A19G1）",
                                      pfcan1.path, "已绑定")
    r15 = rows["CAN 15"]
    assert window.binding_row(r15) == ("CAN 15", "PFCAN2.dbc（A19G1）",
                                       pfcan2.path, "无数据")


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


def test_channel_table_uses_internal_scrollbar(window, qapp):
    """紧凑面板不增高；通道较多时只在表格内部滚动。"""
    import gui.main_window as mw

    window.dbc_list = [_d("PFCAN1.dbc")]
    window._rebuild_channel_table(list(range(16)))
    window.show()
    qapp.processEvents()
    assert window.table.verticalScrollBar().maximum() > 0
    assert isinstance(window.table.cellWidget(0, 1), mw.DbcCombo)


def test_table_height_fixed_16_rows(window, qapp):
    """表格高度不随行数变化，超出内容由内部滚动处理。"""
    window.dbc_list = [_d("PFCAN1.dbc")]
    window._rebuild_channel_table(list(range(2)))
    window.show()
    qapp.processEvents()
    h2 = window.table.height()
    window._rebuild_channel_table(list(range(16)))
    qapp.processEvents()
    h16 = window.table.height()
    assert h2 == h16
    assert window.table.verticalScrollBar().maximum() > 0


def test_summary_uses_compact_bar(window, qapp):
    """完整摘要移出主布局，主窗口只保留 34px 摘要条。"""
    window._rebuild_channel_table([1])
    window.show()
    qapp.processEvents()
    assert window.summary.isHidden()
    assert window.summary_bar.height() == 34


def test_window_geometry_stable_through_load(window, qapp):
    """窗口固定为定稿默认尺寸，重建表格不改变几何。"""
    window.dbc_list = [_d("PFCAN1.dbc")]
    window.show()
    qapp.processEvents()
    before = window.size()
    assert (before.width(), before.height()) == (690, 596)
    window._rebuild_channel_table(list(range(13)))
    qapp.processEvents()
    assert window.size() == before


# ---- BLF 浏览默认目录 + 输出路径跟随 BLF ----

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
    stage_before = window.stage_label.text()
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
    assert window.stage_label.text() == stage_before
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
    assert window.stage_label.text() == stage_before
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


def test_blf_load_sets_output_follows_blf_path(window):
    """加载 BLF 完成后，输出路径 = BLF 同目录、文件名追加 _t、扩展名 .mdf。

    首次加载（启动后选第一个文件）即生效；不再落到 outputs/ 时间戳名。
    """
    class FakeWorker:
        path = r"E:\data\run001.blf"
    window.scan_worker = FakeWorker()
    window._on_scan_done([1, 2])
    assert window.blf_path == r"E:\data\run001.blf"
    assert window.out_edit.text() == r"E:\data\run001_t.mdf"


def test_new_blf_updates_output_even_after_custom_edit(window):
    """需求 2：用户手改过输出路径后，重选 BLF 输出仍无条件跟随新 BLF。"""
    window.blf_path = r"E:\data\old.blf"
    window.out_edit.setText(r"F:\custom\my.mdf")   # 用户自选输出

    class FakeWorker:
        path = r"E:\data2\new.blf"
    window.scan_worker = FakeWorker()
    window._on_scan_done([1, 2])
    assert window.out_edit.text() == r"E:\data2\new_t.mdf"


def test_default_output_always_appends_t(window):
    """自动命名固定追加 _t，不把已有的 _t 当作特殊情况。"""
    window.blf_path = r"E:\data\run001_t.blf"
    window._set_default_output()

    assert window.out_edit.text() == r"E:\data\run001_t_t.mdf"


def test_project_selection_does_not_rename_output(window):
    """选项目不再把项目名写进输出文件名——输出名只跟随 BLF（需求 3）。"""
    window.blf_path = r"E:\data\run001.blf"
    window._set_default_output()
    window._select_project = lambda name: None  # 只切选择，不触发真实加载
    window.project_combo.setCurrentText("A19G1")
    assert window.out_edit.text() == r"E:\data\run001_t.mdf"


def test_project_select_keeps_custom_output_path(window, monkeypatch):
    """用户手动改过的输出路径，切换项目时不被覆盖。"""
    import gui.main_window as mw

    monkeypatch.setattr(mw.project_loader, "load_project",
                        lambda root, name: [])
    window.blf_path = r"E:\x.blf"
    custom = r"E:\mine\custom.mdf"
    window.out_edit.setText(custom)
    window.project_combo.setCurrentText("A19G1")
    assert window.out_edit.text() == custom


# ---- BLF 拖拽导入 ----

def test_blf_drop_imports_file(window, tmp_path, monkeypatch):
    """拖拽 .blf 到「BLF 文件」行（标签/路径框）→ _load_blf；非 .blf 拒绝。

    「浏览…」手动选择功能不变；拖拽是并列的另一种文件入口。悬停接受
    仅表示可放下，不触发加载；放下才走 _load_blf（异步扫描本身由
    test_load_blf_async_does_not_block 覆盖，这里只验证接线）。
    """
    from PySide6.QtCore import QMimeData, QPoint, QUrl, Qt
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from PySide6.QtWidgets import QApplication

    blf = tmp_path / "run001.blf"
    blf.write_bytes(b"\x00")

    live_mimes = []  # QMimeData 包装器须存活到事件处理完：局部变量被 GC 后
    # Shiboken 会删掉 C++ 对象，事件内 mimeData() 返回悬垂指针（裸 QObject）

    def drag(urls, cls=QDragEnterEvent):
        mime = QMimeData()
        mime.setUrls(urls)
        live_mimes.append(mime)
        return cls(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                   Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

    calls = []
    monkeypatch.setattr(window, "_load_blf", lambda p: calls.append(p))

    # .blf：标签与路径框均接受拖入（整行区域可拖）
    for w in (window.blf_label, window.blf_edit):
        ev = drag([QUrl.fromLocalFile(str(blf))])
        QApplication.sendEvent(w, ev)
        assert ev.isAccepted(), f"{type(w).__name__} 应接受 .blf 拖入"
    assert calls == []  # 悬停只是接受，不触发加载

    # 非 .blf（如 .txt）：整行拒绝
    txt = tmp_path / "note.txt"
    txt.write_text("x")
    for w in (window.blf_label, window.blf_edit):
        ev = drag([QUrl.fromLocalFile(str(txt))])
        QApplication.sendEvent(w, ev)
        assert not ev.isAccepted()

    # 放下 .blf → _load_blf(路径)
    drop = drag([QUrl.fromLocalFile(str(blf))], QDropEvent)
    QApplication.sendEvent(window.blf_edit, drop)
    assert drop.isAccepted()
    assert len(calls) == 1 and Path(calls[0]) == blf


def test_blf_drop_rejected_during_scan(window, tmp_path, monkeypatch):
    """扫描进行中拒绝拖入（与「浏览…」按钮禁用一致），不启动新加载。

    拖入被视觉接受却无动作比直接拒绝更困惑，故扫描期间光标显示禁止。
    """
    from PySide6.QtCore import QMimeData, QPoint, QUrl, Qt
    from PySide6.QtGui import QDropEvent
    from PySide6.QtWidgets import QApplication

    class FakeScanThread:
        def isRunning(self):
            return True

    calls = []
    monkeypatch.setattr(window, "_load_blf", lambda p: calls.append(p))
    window.scan_thread = FakeScanThread()
    try:
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(tmp_path / "x.blf"))])
        ev = QDropEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                        Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(window.blf_edit, ev)
        assert not ev.isAccepted()
        assert calls == []
    finally:
        window.scan_thread = None


def test_start_convert_bindings_from_user_data(window, qapp, monkeypatch):
    """接线：转换绑定从 combo userData 组装——选中行 → 对应 DbcDef 对象
    （零查找、零反查表，_dbc_by_display 已删）。"""
    import gui.main_window as mw

    from core.converter import ConversionResult

    captured = {}

    def fake_convert(blf_path, bindings, out_path, **kwargs):
        captured["bindings"] = bindings
        return ConversionResult(summaries=[], duration_seconds=0.0,
                                timings=[])

    monkeypatch.setattr(mw, "convert", fake_convert)
    monkeypatch.setattr(mw.QMessageBox, "exec",
                        lambda self: mw.QMessageBox.StandardButton.Ok)

    window.blf_path = r"E:\x.blf"
    window.out_edit.setText(r"E:\x_t.mdf")
    pfcan1 = _d("PFCAN1.dbc")
    pfcan2 = _d("PFCAN2.dbc")
    window.dbc_list = [pfcan1, pfcan2]
    window.auto_bind = {1: pfcan1.path}
    window._rebuild_channel_table([1, 15], prev=None)
    # 行 15（无数据）用户手动改选 PFCAN2 → 绑定该文件对象
    window.set_binding_selection(1, "PFCAN2.dbc（A19G1）")
    window._start_convert()
    assert _wait_until(qapp, lambda: captured.get("bindings") is not None)
    assert captured["bindings"][1] is pfcan1
    assert captured["bindings"][15] is pfcan2
    # 收尾：等 _on_done 执行完（_finish 收掉 worker 线程）再关窗——否则
    # teardown 时 closeEvent 会因 worker 仍在运行而弹阻塞的确认框
    assert _wait_until(qapp, lambda: window.worker_thread is None)
    window.close()


def test_start_convert_unbound_row_yields_none(window, qapp, monkeypatch):
    """接线：UNBOUND 行（无 userData）→ bindings 值为 None，与现状
    「text == UNBOUND → None」语义一致。"""
    import gui.main_window as mw

    from core.converter import ConversionResult

    captured = {}

    def fake_convert(blf_path, bindings, out_path, **kwargs):
        captured["bindings"] = bindings
        return ConversionResult(summaries=[], duration_seconds=0.0,
                                timings=[])

    monkeypatch.setattr(mw, "convert", fake_convert)
    monkeypatch.setattr(mw.QMessageBox, "exec",
                        lambda self: mw.QMessageBox.StandardButton.Ok)

    window.blf_path = r"E:\x.blf"
    window.out_edit.setText(r"E:\x_t.mdf")
    window.dbc_list = [_d("PFCAN1.dbc")]
    window.auto_bind = {1: window.dbc_list[0].path}
    window._rebuild_channel_table([1], prev=None)
    window.set_binding_selection(0, mw.UNBOUND)  # 改回「不绑定」
    window._start_convert()
    assert _wait_until(qapp, lambda: captured.get("bindings") is not None)
    assert captured["bindings"][1] is None
    # 收尾：等 _on_done 执行完（_finish 收掉 worker 线程）再关窗——否则
    # teardown 时 closeEvent 会因 worker 仍在运行而弹阻塞的确认框
    assert _wait_until(qapp, lambda: window.worker_thread is None)
    window.close()


# ---- 扫描/转换取消 ----

def _wait_until(qapp, cond, timeout=10.0):
    """泵动事件循环直到 cond() 为真或超时（GUI 异步测试的确定性等待）。"""
    import time

    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    return cond()


def _looping_probe_until_cancel():
    """确定性探测桩：循环检查 cancel_cb，置位即抛 ConversionCancelled——
    避免真实 probe 在毫秒级完成导致的时序抖动。"""
    import time

    from core.blf_reader import ConversionCancelled

    def fake(path, progress_cb=None, cancel_cb=None):
        while True:
            if cancel_cb and cancel_cb():
                raise ConversionCancelled()
            time.sleep(0.001)

    return fake


def _looping_convert_until_cancel():
    """确定性转换桩：循环检查 cancel_cb，置位即抛 ConversionCancelled。"""
    import time

    from core.blf_reader import ConversionCancelled

    def fake(*args, cancel_cb=None, **kwargs):
        while True:
            if cancel_cb and cancel_cb():
                raise ConversionCancelled()
            time.sleep(0.001)

    return fake


def test_scan_cancel_aborts_and_resets(window, qapp, tmp_path, monkeypatch):
    """扫描中取消：worker 抛 ConversionCancelled → 状态复位（blf_path 不变、
    进度条隐藏、文件选择恢复），不弹错误框。"""
    import gui.main_window as mw

    blf = tmp_path / "run001.blf"
    blf.write_bytes(b"\x00")
    errors = []
    monkeypatch.setattr(mw.QMessageBox, "critical",
                        lambda *a, **k: errors.append(a))
    monkeypatch.setattr(mw.blf_reader, "probe_channels",
                        _looping_probe_until_cancel())

    window.show()  # isVisible() 需要整条祖先链可见
    window._load_blf(str(blf))
    assert window.scan_thread is not None and window.scan_thread.isRunning()
    assert window.btn_scan_cancel.isVisible(), "扫描期间应显示取消按钮"
    window.btn_scan_cancel.click()  # 设置取消事件
    assert _wait_until(qapp, lambda: window.scan_thread is None), \
        "取消后扫描线程应收尾"
    assert window.blf_path is None, "取消不应用结果（未加载过文件）"
    assert window.blf_channels == []
    assert window.load_progress.isHidden(), "进度条应隐藏"
    assert window.btn_blf.isEnabled(), "文件选择应恢复"
    assert not window.convert_btn.isEnabled(), "无已加载 BLF 不能转换"
    assert errors == [], "取消不是错误，不应弹错误框"
    window.close()


def test_convert_cancel_resets_state(window, qapp, tmp_path, monkeypatch):
    """转换中取消：worker 抛 ConversionCancelled → 状态复位（进度归零、
    stage_label 就绪、按钮恢复、摘要「已取消」），不弹任何框。"""
    import gui.main_window as mw

    blf = tmp_path / "run001.blf"
    blf.write_bytes(b"\x00")
    out = tmp_path / "run001_t.mdf"
    boxes = []
    monkeypatch.setattr(mw.QMessageBox, "critical",
                        lambda *a, **k: boxes.append(("critical", a)))
    monkeypatch.setattr(mw.QMessageBox, "information",
                        lambda *a, **k: boxes.append(("info", a)))
    monkeypatch.setattr(mw, "convert", _looping_convert_until_cancel())

    window.blf_path = str(blf)
    window.out_edit.setText(str(out))
    window._rebuild_channel_table([1])  # 一行「不绑定」→ bindings {1: None}
    window.show()  # isVisible() 需要整条祖先链可见
    window._start_convert()
    assert window.worker_thread is not None and window.worker_thread.isRunning()
    assert window.btn_convert_cancel.isVisible(), "转换期间应显示取消按钮"
    window.btn_convert_cancel.click()
    assert _wait_until(qapp, lambda: window.worker_thread is None), \
        "取消后转换线程应收尾"
    assert window.stage_label.text() == "就绪"
    assert window.progress.value() == 0
    assert window.convert_btn.isEnabled(), "转换按钮应恢复可用"
    assert window.summary_status.text() == "已取消"
    assert "转换已取消" in window.summary_text, "取消应记入日志"
    assert boxes == [], "取消不是错误/成功，不应弹任何框"
    window.close()


def test_close_during_scan_returns_promptly(window, qapp, tmp_path, monkeypatch):
    """扫描中关窗：closeEvent 先置取消事件再等待 → 快速返回（不再等全文件扫完）。"""
    import gui.main_window as mw

    blf = tmp_path / "run001.blf"
    blf.write_bytes(b"\x00")
    monkeypatch.setattr(mw.blf_reader, "probe_channels",
                        _looping_probe_until_cancel())
    window._load_blf(str(blf))
    assert window.scan_thread is not None and window.scan_thread.isRunning()
    window.close()  # 若未置取消事件，此调用会卡住（桩循环永不退出）
    assert window.scan_thread is None, "关窗后扫描线程应收尾"


def test_close_during_convert_returns_promptly(window, qapp, tmp_path,
                                               monkeypatch):
    """转换中关窗：确认后先置取消事件再等待 → 快速返回。"""
    import gui.main_window as mw

    blf = tmp_path / "run001.blf"
    blf.write_bytes(b"\x00")
    out = tmp_path / "run001_t.mdf"
    monkeypatch.setattr(mw, "convert", _looping_convert_until_cancel())
    monkeypatch.setattr(
        mw.QMessageBox, "question",
        lambda *a, **k: mw.QMessageBox.StandardButton.Yes)

    window.blf_path = str(blf)
    window.out_edit.setText(str(out))
    window._rebuild_channel_table([1])
    window._start_convert()
    assert window.worker_thread is not None and window.worker_thread.isRunning()
    window.close()  # 未置取消事件会卡住（桩循环永不退出）
    assert window.worker_thread is None, "关窗后转换线程应收尾"


# ---- 日志（加载/转换计时记录） ----

def test_blf_load_logs_duration_into_log(window):
    """BLF 加载完成：耗时记入日志（时间戳前缀），底条显示短状态、
    按钮启用、隐藏缓冲同步。"""
    import re
    import time

    class FakeWorker:
        path = r"E:\data\run001.blf"
    window.scan_worker = FakeWorker()
    window._load_start = time.perf_counter() - 2.0
    window._on_scan_done([1, 2])
    assert re.match(r"^\[\d{2}:\d{2}:\d{2}\.\d{3}\]", window.summary_text)
    assert "输入BLF 完成" in window.summary_text
    assert window.summary_status.text().startswith("输入BLF 完成: 2.")
    assert window.summary_button.isEnabled()
    assert window.summary.toPlainText() == window.summary_text


def test_scan_done_without_load_start_logs_no_crash(window):
    """测试直调 _on_scan_done（无 _load_start）不崩溃：记无耗时的完成行。"""
    class FakeWorker:
        path = r"E:\data\run001.blf"
    window.scan_worker = FakeWorker()
    window._on_scan_done([1, 2])  # getattr 兜底，不记录耗时
    assert "输入BLF 完成" in window.summary_text
    assert window.summary_status.text() == "输入BLF 完成"


def test_start_convert_does_not_prefill_log_buffer(window, qapp, tmp_path,
                                                   monkeypatch):
    """探针契约：_start_convert 不预写日志缓冲——frozen_gui_probe 以
    summary.toPlainText() 非空判断「转换完成」，转换中不得提前写入。"""
    import gui.main_window as mw

    blf = tmp_path / "run001.blf"
    blf.write_bytes(b"\x00")
    out = tmp_path / "run001_t.mdf"
    monkeypatch.setattr(mw, "convert", _looping_convert_until_cancel())
    window.blf_path = str(blf)
    window.out_edit.setText(str(out))
    window._rebuild_channel_table([1])
    window.show()
    qapp.processEvents()
    window._start_convert()
    assert window.summary_text == ""
    assert window.summary.toPlainText() == ""
    window.btn_convert_cancel.click()  # 收尾：取消并等待线程结束
    assert _wait_until(qapp, lambda: window.worker_thread is None)
    window.close()


def test_log_accumulates_across_events(window, monkeypatch):
    """日志会话内累积：BLF 完成行与转换完成块并存，底条为最后事件状态。"""
    from PySide6.QtWidgets import QMessageBox

    from core.converter import ChannelSummary, ConversionResult

    class FakeWorker:
        path = r"E:\data\run001.blf"
    window.scan_worker = FakeWorker()
    window._load_start = 0.0
    window._on_scan_done([1])
    result = ConversionResult(
        summaries=[ChannelSummary(channel=1, bound=True)],
        duration_seconds=3.0,
        timings=[("读入 BLF", 1.0), ("解码 CAN1", 0.5),
                 ("统计聚合", 0.1), ("写 MDF", 0.2), ("总耗时", 1.9)],
    )
    monkeypatch.setattr(window, "_finish", lambda: None)
    monkeypatch.setattr(QMessageBox, "exec",
                        lambda self: QMessageBox.StandardButton.Ok)
    window._on_done(result)
    assert "输入BLF 完成" in window.summary_text
    assert "转换完成" in window.summary_text
    assert "读入 BLF: 1.00 s" in window.summary_text
    assert window.summary_status.text() == "转换完成 · 总耗时 1.9 s"


def test_convert_error_logs_failure_line(window, monkeypatch):
    """转换失败：错误信息记入日志、底条「转换失败」，仍弹 critical。"""
    import gui.main_window as mw

    errors = []
    monkeypatch.setattr(mw.QMessageBox, "critical",
                        lambda *a, **k: errors.append(a))
    monkeypatch.setattr(window, "_finish", lambda: None)
    window._on_error("boom")
    assert "转换失败: boom" in window.summary_text
    assert window.summary_status.text() == "转换失败"
    assert errors, "critical 应被调用"
