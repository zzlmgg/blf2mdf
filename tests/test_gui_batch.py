"""批量导入 GUI 流程：拖入 → 候选勾选 → 扫描并集 → 批量转换（无头 offscreen）。

界面侧接缝（见 .scratch/batch-import/spec.md Testing Decisions）：勾选对话框
自身（不经 exec 的行为与视觉契约）、拖拽事件注入 → 候选清单 → 按勾选结果进入
批量、候选只有 1 个时不弹列表、批量进度与结束弹窗、产物落位（含真实端到端
一次）。core 侧的顺序/失败继续/逐位一致由 tests/test_batch.py 覆盖——这里只
验证界面把什么交给 core、以及界面拿结果做了什么。
"""
import threading
import time
from pathlib import Path

import pytest

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QMessageBox

import gui.main_window as mw

from core.batch import BatchResult, FileOutcome
from core.dbc_loader import DbcDef, load
from core.converter import ChannelSummary, ConversionResult
from core.source_resolver import Candidate

INLINE_DBC = '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 100 ABC: 8 ECU
 SG_ Speed : 0|16@1+ (0.01,0) [0|655.35] "km/h" ECU
'''


# ---- 夹具与工具 ----


@pytest.fixture(autouse=True)
def _no_real_modal(monkeypatch):
    """兜底：本文件里任何**未被用例显式替换**的模态弹窗都返回 Yes。

    offscreen 平台没人点按钮，真实模态会永久阻塞——用例一旦断言失败提前
    退出（worker 线程还活着，closeEvent 就会弹「确认关闭」），整个测试进程
    就挂死，掩盖真正的失败。用例要断言弹窗时各自再 patch（后 patch 生效）。
    """
    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes))


def _write_blf(path: Path, channels=(1,), frames=3) -> Path:
    """合成 BLF：给定通道各写若干帧（够 probe_channels 探出通道）。"""
    import can

    path.parent.mkdir(parents=True, exist_ok=True)
    with can.BLFWriter(str(path)) as writer:
        for channel in channels:
            for index in range(frames):
                writer.on_message_received(can.Message(
                    arbitration_id=100, is_extended_id=False,
                    data=(100 + index).to_bytes(2, "little") + bytes(6),
                    channel=channel, timestamp=1784716800.0 + index))
    return path


def _candidate(root: Path, display: str, size: int = 2048) -> Candidate:
    blf = root / Path(display).name
    return Candidate(blf=blf, output=blf.with_name(f"{blf.stem}_t.mdf"),
                     display=display, size=size)


def _result(seconds: float = 1.5) -> ConversionResult:
    return ConversionResult(
        summaries=[ChannelSummary(channel=1, bound=True)],
        duration_seconds=seconds,
        timings=[("读入 BLF", 0.5), ("总耗时", seconds)])


def _wait_until(qapp, cond, timeout=10.0):
    """泵动事件循环直到 cond() 为真或超时（GUI 异步测试的确定性等待）。"""
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    return cond()


_MIMES = []  # QMimeData 包装器须存活到事件处理完（局部变量被 GC 后
# Shiboken 会删掉 C++ 对象，事件内 mimeData() 返回悬垂指针）


def _drop(window, *paths, cls=QDropEvent):
    """构造拖拽事件发给「输入 BLF」行并返回事件（验收本次拖入是否被接受）。

    放下前先发同源的 DragEnter：Qt 只把 Drop 交给当前放下目标，而目标身份
    来自那次被接受的悬停（真实拖拽 = 进入 → 悬停 → 放下）。
    """
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    _MIMES.append(mime)
    if cls is not QDragEnterEvent:
        QApplication.sendEvent(window.blf_edit, QDragEnterEvent(
            QPoint(10, 10), Qt.DropAction.CopyAction, mime,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    event = cls(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(window.blf_edit, event)
    return event


def _drop_and_settle(qapp, window, *paths):
    """拖入并等到「解析 → 勾选 → 扫描」整条链接力（批次已提交、输入侧空闲）。"""
    event = _drop(window, *paths)
    settled = _wait_until(
        qapp, lambda: not window._input_busy() and bool(window.batch))
    return event, settled


# ---- 勾选对话框（自身契约，不弹 exec） ----


def test_candidate_dialog_defaults_to_all_selected(qapp, tmp_path):
    """默认全选（默认全要）、显示「已选 N / 共 M」，顺序 = 清单顺序。"""
    from gui.candidate_dialog import CandidateDialog

    first = _candidate(tmp_path, r"sub\run001.blf", size=2048)
    second = _candidate(tmp_path, r"sub\run002.blf", size=3 * 1024 ** 2)
    dialog = CandidateDialog([first, second])

    assert dialog.checked_candidates() == [first, second]
    assert dialog.count_label.text() == "已选 2 / 共 2"
    assert dialog.ok_button.isEnabled()


def test_candidate_dialog_select_all_and_none_buttons(qapp, tmp_path):
    """全选 / 全不选按钮与计数联动；一个都没勾时「确定」禁用（空批次无意义）。"""
    from gui.candidate_dialog import CandidateDialog

    dialog = CandidateDialog([_candidate(tmp_path, "a.blf"),
                              _candidate(tmp_path, "b.blf")])
    dialog.select_none_button.click()
    assert dialog.checked_candidates() == []
    assert dialog.count_label.text() == "已选 0 / 共 2"
    assert not dialog.ok_button.isEnabled()

    dialog.select_all_button.click()
    assert dialog.count_label.text() == "已选 2 / 共 2"
    assert dialog.ok_button.isEnabled()


def test_candidate_dialog_counts_individual_checks(qapp, tmp_path):
    """逐个勾选：取消一个即计数与结果同步（不必先全不选）。"""
    from PySide6.QtCore import Qt as QtCore

    from gui.candidate_dialog import CandidateDialog

    first = _candidate(tmp_path, "a.blf")
    second = _candidate(tmp_path, "b.blf")
    dialog = CandidateDialog([first, second])
    dialog.list_widget.item(0).setCheckState(QtCore.CheckState.Unchecked)

    assert dialog.checked_candidates() == [second]
    assert dialog.count_label.text() == "已选 1 / 共 2"


def test_candidate_dialog_shows_relative_name_size_and_full_path(qapp, tmp_path):
    """每项显示相对路径 + 大小（区分同名不同目录、判断大文件），tooltip = 完整路径。"""
    from gui.candidate_dialog import CandidateDialog

    candidate = _candidate(tmp_path, r"20260917\run001.blf", size=5 * 1024 ** 2)
    dialog = CandidateDialog([candidate])
    item = dialog.list_widget.item(0)

    assert item.text().startswith(r"20260917\run001.blf")
    assert "5.0 MB" in item.text()
    assert item.toolTip() == str(candidate.blf)

    small = CandidateDialog([_candidate(tmp_path, "tiny.blf", size=512)])
    assert "512 B" in small.list_widget.item(0).text()


def test_candidate_dialog_uses_theme_visuals(qapp, tmp_path):
    """视觉契约：与既有对话框同一体系（标题/主按钮/次按钮 + 主题内的列表样式）。"""
    from gui.candidate_dialog import CandidateDialog
    from gui.theme import APP_QSS

    dialog = CandidateDialog([_candidate(tmp_path, "a.blf")])
    assert dialog.findChild(type(dialog.heading), "dialogHeading") is not None
    assert dialog.ok_button.objectName() == "primaryButton"
    assert dialog.cancel_button.objectName() == "secondaryButton"
    assert dialog.select_all_button.objectName() == "secondaryButton"
    assert dialog.list_widget.objectName() == "candidateList"
    assert "QListWidget#candidateList" in APP_QSS
    assert "QLabel#dialogHint" in APP_QSS


# ---- 拖入 → 候选清单 → 扫描 ----


def test_folder_drop_lists_candidates_and_scans_selection(window, qapp, tmp_path,
                                                          monkeypatch):
    """拖入文件夹 → 弹候选清单（全量候选）→ 只扫描勾选项 → 批次就位。"""
    root = tmp_path / "AHT"
    for index in (1, 2, 3):
        _write_blf(root / "sub" / f"run{index:03d}.blf", channels=(index,))

    offered = []

    def choose(candidates):
        offered.append(list(candidates))
        return candidates[:2]           # 只勾前两个

    monkeypatch.setattr(window, "_choose_candidates", choose)
    _, settled = _drop_and_settle(qapp, window, root)
    assert settled

    assert [c.display for c in offered[0]] == [
        r"sub\run001.blf", r"sub\run002.blf", r"sub\run003.blf"]
    assert [c.blf.name for c in window.batch] == ["run001.blf", "run002.blf"]
    assert window.blf_channels == [1, 2], "通道表 = 勾选文件通道的并集"
    assert window.blf_path is None, "批量没有「单个 BLF」这回事"


def test_multiple_loose_files_drop_lists_candidates(window, qapp, tmp_path,
                                                    monkeypatch):
    """一次拖入多个散 .blf → 同样进候选清单（与拖入文件夹同一条入口）。"""
    first = _write_blf(tmp_path / "run001.blf", channels=(1,))
    second = _write_blf(tmp_path / "run002.blf", channels=(2,))
    monkeypatch.setattr(window, "_choose_candidates", lambda candidates: candidates)

    _, settled = _drop_and_settle(qapp, window, first, second)
    assert settled
    assert [c.blf for c in window.batch] == [first, second]
    assert [c.output for c in window.batch] == [       # 散件就地落位
        tmp_path / "run001_t.mdf", tmp_path / "run002_t.mdf"]
    assert window.blf_channels == [1, 2]


def test_single_candidate_skips_the_checklist(window, qapp, tmp_path,
                                              monkeypatch):
    """候选只有 1 个 → 不弹勾选列表，直接进入；输出框保持可手改、默认落镜像树。"""
    root = tmp_path / "AHT"
    blf = _write_blf(root / "sub" / "run001.blf", channels=(1,))
    called = []
    monkeypatch.setattr(
        window, "_choose_candidates",
        lambda candidates: called.append(candidates) or candidates)

    _, settled = _drop_and_settle(qapp, window, root)
    assert settled

    assert called == [], "候选只有 1 个时不应弹列表"
    assert [c.blf for c in window.batch] == [blf]
    assert window.blf_path == str(blf), "单文件兼容面照常"
    assert not window.out_edit.isReadOnly()
    assert window.btn_out.isEnabled()
    assert Path(window.out_edit.text()) == \
        tmp_path / "AHT_t" / "sub" / "run001_t.mdf"


def test_single_loose_file_drop_keeps_today_flow(window, qapp, tmp_path,
                                                 monkeypatch):
    """单个散 .blf 拖入走今天的单文件入口（解析/列表都不介入）。"""
    blf = _write_blf(tmp_path / "run001.blf", channels=(1,))
    loaded = []
    monkeypatch.setattr(window, "_load_blf", lambda path: loaded.append(path))

    event = _drop(window, blf)
    assert event.isAccepted()
    # 路径来自拖入的 mime URL（分隔符风格随 Qt，不锁定）
    assert len(loaded) == 1 and Path(loaded[0]) == blf


def test_batch_makes_output_readonly_and_shows_source(window, qapp, tmp_path,
                                                      monkeypatch):
    """多文件批量：输出框只读、「浏览…」禁用；「输入 BLF」行显示来源与文件数。"""
    root = tmp_path / "AHT"
    for index in (1, 2):
        _write_blf(root / f"run{index:03d}.blf", channels=(1,))
    monkeypatch.setattr(window, "_choose_candidates", lambda candidates: candidates)

    _, settled = _drop_and_settle(qapp, window, root)
    assert settled

    assert window.blf_edit.text() == "2 个文件 · 来自 AHT"
    assert str(root / "run001.blf") in window.blf_edit.toolTip(), "完整清单进 tooltip"
    assert window.out_edit.isReadOnly()
    assert not window.btn_out.isEnabled()
    assert window.convert_btn.isEnabled()


def test_batch_scan_union_keeps_bindings_and_marks_absent_channel_no_data(
        window, qapp, tmp_path, monkeypatch):
    """映射通道在所有选中文件里都不存在 → 「无数据」；只在部分文件里存在的通道
    属正常三态（行照常出现、可配），不因缺它的文件而变「无数据」。"""
    root = tmp_path / "AHT"
    _write_blf(root / "run001.blf", channels=(1,))
    _write_blf(root / "run002.blf", channels=(1, 2))
    dbc = DbcDef(path=r"E:\dbc\A\PFCAN1.dbc", db=None)
    window.dbc_list = [dbc]
    window.auto_bind = {1: dbc.path, 9: dbc.path}
    monkeypatch.setattr(window, "_choose_candidates", lambda candidates: candidates)

    _, settled = _drop_and_settle(qapp, window, root)
    assert settled

    rows = {window.binding_row(r)[0]: window.binding_row(r)
            for r in range(window.table.rowCount())}
    assert rows["CAN 1"][3] == "已绑定"
    assert rows["CAN 2"][3] == "不导出"
    assert rows["CAN 9"][3] == "无数据"


def test_drop_without_importable_items_warns_and_keeps_state(
        window, qapp, tmp_path, monkeypatch):
    """一个 .blf 都没有 → 明确提示（不是静默无反应），且不改变现有状态。"""
    import gui.main_window as mw

    root = tmp_path / "AHT"
    root.mkdir()
    (root / "notes.txt").write_text("x", encoding="utf-8")
    warned = []
    monkeypatch.setattr(mw.QMessageBox, "warning",
                        lambda *args, **kwargs: warned.append(args))

    event = _drop(window, root)
    assert _wait_until(qapp, lambda: bool(warned) and not window._input_busy())
    assert event.isAccepted(), "文件夹条目在悬停期接受（解析后才知有无 .blf）"
    assert "未找到可导入的 .blf 文件" in warned[0][2]
    assert window.batch == []
    assert window.blf_edit.text() == ""


def test_non_importable_drop_is_rejected_on_the_row(window, qapp, tmp_path):
    """整行拒绝（沿用今天的拒绝语义）：非 .blf 且非文件夹的拖入不被接受。"""
    note = tmp_path / "note.txt"
    note.write_text("x", encoding="utf-8")
    event = _drop(window, note, cls=QDragEnterEvent)
    assert not event.isAccepted()


def test_drop_rejected_while_resolving_or_converting(window, qapp, tmp_path,
                                                     monkeypatch):
    """解析 / 扫描 / 转换进行中拒绝拖入（与「浏览…」禁用一致），不启动新的加载。"""
    blf = _write_blf(tmp_path / "run001.blf", channels=(1,))
    loaded = []
    monkeypatch.setattr(window, "_load_blf", lambda path: loaded.append(path))

    class FakeThread:
        def isRunning(self):
            return True

    for attribute in ("resolve_thread", "scan_thread", "worker_thread"):
        setattr(window, attribute, FakeThread())
        try:
            event = _drop(window, blf, cls=QDragEnterEvent)
            assert not event.isAccepted(), f"{attribute} 运行中应拒绝拖入"
        finally:
            setattr(window, attribute, None)
    assert loaded == []


def test_checklist_cancel_keeps_the_previous_state(window, qapp, tmp_path,
                                                   monkeypatch):
    """勾选列表上点取消 → 回到原状（不应用半途结果），记「已取消」。"""
    root = tmp_path / "AHT"
    for index in (1, 2):
        _write_blf(root / f"run{index:03d}.blf", channels=(1,))
    monkeypatch.setattr(window, "_choose_candidates", lambda candidates: None)

    _drop(window, root)
    assert _wait_until(qapp, lambda: not window._input_busy())
    assert window.batch == []
    assert window.summary_status.text() == "已取消"
    assert "输入BLF 已取消" in window.summary_text


def test_resolve_shows_discovered_count_while_walking(window, qapp, tmp_path,
                                                      monkeypatch):
    """遍历大目录树时看得到「已发现 N 个」（刻度是计数、无分母）且可取消。

    core 的解析刻度是「已发现候选数」（遍历前不知总数），故进 stage 文案；
    进度条同时是不确定态——两种刻度各归各位，界面不假装知道分母。
    """
    root = tmp_path / "AHT"
    for index in (1, 2, 3):
        _write_blf(root / f"run{index:03d}.blf", channels=(1,))

    seen = []
    worker = mw.ResolveWorker([str(root)], threading.Event())
    worker.progress.connect(seen.append)
    worker.run()                     # 同步跑：钉「计数如实上报」
    assert seen == [1, 2, 3]

    gate = threading.Event()         # 拦住解析，断言期间不被 _finish_resolve 复位
    monkeypatch.setattr(mw.source_resolver, "resolve",
                        lambda paths, **kwargs: gate.wait(5.0) or [])
    window._start_resolve([str(root)])
    assert not window.load_progress.isHidden(), "解析期进度条可见（不确定态）"
    window.resolve_worker.progress.emit(7)
    assert window.stage_label.text() == "正在解析来源…已发现 7 个 .blf"
    gate.set()
    assert _wait_until(qapp, lambda: not window._input_busy())
    assert window.stage_label.text() == "就绪", "解析结束归还状态文案"


# ---- 批量转换：进度、结束弹窗、产物落位 ----


def _start_batch_with_fake_run_batch(window, qapp, tmp_path, monkeypatch,
                                     fake):
    """造一个 2 文件的批次（拖入文件夹）并把 run_batch 换成桩，返回 run 记录。"""
    import gui.main_window as mw

    root = tmp_path / "AHT"
    for index in (1, 2):
        _write_blf(root / f"run{index:03d}.blf", channels=(1,))
    monkeypatch.setattr(window, "_choose_candidates", lambda candidates: candidates)
    monkeypatch.setattr(mw, "run_batch", fake)
    _, settled = _drop_and_settle(qapp, window, root)
    assert settled
    return root


def test_batch_progress_shows_file_index_and_completion_notice(
        window, qapp, tmp_path, monkeypatch):
    """批量转换：进度显示「第 i/N 个 · 文件内阶段」；结束弹「N 个 blf 文件全部转换完成」。"""
    import gui.main_window as mw

    seen = {}
    release = threading.Event()

    def fake_run_batch(candidates, bindings, *, progress_cb=None, **kwargs):
        seen["candidates"] = list(candidates)
        seen["bindings"] = bindings
        progress_cb("第 1/2 个 · 读取 BLF", 10.0)
        release.wait(5.0)
        progress_cb("第 2/2 个 · 写 MDF", 90.0)
        return BatchResult(outcomes=[FileOutcome(c, result=_result())
                                     for c in candidates])

    _start_batch_with_fake_run_batch(window, qapp, tmp_path, monkeypatch,
                                     fake_run_batch)
    stages = []
    original = window._on_progress
    monkeypatch.setattr(window, "_on_progress",
                        lambda stage, percent: (stages.append(stage),
                                                original(stage, percent)))
    notices = []
    monkeypatch.setattr(mw.QMessageBox, "exec",
                        lambda self: notices.append(self.text())
                        or QMessageBox.StandardButton.Ok)

    window.show()  # isVisible() 需要整条祖先链可见
    window._start_convert()
    assert _wait_until(qapp, lambda: window.stage_label.text().startswith("第 1/2 个"))
    # 转换进行中：会改变批次的控件全部锁定（与今天的忙碌态同一批控件）
    for widget in (window.btn_blf, window.dbc_list_widget, window.table,
                   window.project_combo, window.out_edit, window.btn_out):
        assert not widget.isEnabled()
    assert window.btn_convert_cancel.isVisible()
    assert not window.convert_btn.isEnabled()

    release.set()
    assert _wait_until(qapp, lambda: window.worker_thread is None)
    assert "第 2/2 个 · 写 MDF" in stages
    assert notices == ["2 个 blf 文件全部转换完成"]
    assert seen["candidates"] == window.batch, "顺序 = 清单顺序，逐项交给 core"
    assert "批量转换完成" in window.summary_text
    assert window.summary_status.text().startswith("批量转换完成")
    assert window.stage_label.text() == "就绪"
    assert window.convert_btn.isEnabled()


def test_batch_failure_notice_counts_and_lists_failed_files(
        window, qapp, tmp_path, monkeypatch):
    """有失败 → 「N 个 blf 文件转换成功 X 个，失败 Y 个」+ 失败文件名与原因。"""
    import gui.main_window as mw

    def fake_run_batch(candidates, bindings, *, progress_cb=None, **kwargs):
        return BatchResult(outcomes=[
            FileOutcome(candidates[0], result=_result()),
            FileOutcome(candidates[1], error="boom"),
        ])

    _start_batch_with_fake_run_batch(window, qapp, tmp_path, monkeypatch,
                                     fake_run_batch)
    notices = []
    monkeypatch.setattr(mw.QMessageBox, "exec",
                        lambda self: notices.append(self.text())
                        or QMessageBox.StandardButton.Ok)

    window._start_convert()
    assert _wait_until(qapp, lambda: window.worker_thread is None)

    assert len(notices) == 1
    body = notices[0]
    assert body.startswith("2 个 blf 文件转换成功 1 个，失败 1 个")
    assert "run002.blf" in body and "boom" in body
    assert "run001.blf" not in body, "成功的文件不进失败清单"
    assert "失败" in window.summary_status.text()


def test_batch_convert_asks_once_before_overwriting_outputs(
        window, qapp, tmp_path, monkeypatch):
    """批量开始前一次性检查输出：已存在的合成一次询问；选择不覆盖则整批不启动。"""
    import gui.main_window as mw

    calls = []

    def fake_run_batch(candidates, bindings, *, progress_cb=None, **kwargs):
        calls.append(candidates)
        return BatchResult(outcomes=[FileOutcome(c, result=_result())
                                     for c in candidates])

    root = _start_batch_with_fake_run_batch(window, qapp, tmp_path, monkeypatch,
                                            fake_run_batch)
    for candidate in window.batch:                      # 先把产物造出来
        candidate.output.parent.mkdir(parents=True, exist_ok=True)
        candidate.output.write_bytes(b"old")

    asked = []
    monkeypatch.setattr(
        mw.QMessageBox, "question",
        lambda *args, **kwargs: asked.append(args[2])
        or QMessageBox.StandardButton.No)
    window._start_convert()
    assert len(asked) == 1, "一次询问，不是逐文件弹窗"
    assert "2 个输出文件已存在" in asked[0]
    assert calls == [], "选择不覆盖 → 整批不启动"

    monkeypatch.setattr(mw.QMessageBox, "question",
                        lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(mw.QMessageBox, "exec",
                        lambda self: QMessageBox.StandardButton.Ok)
    window._start_convert()
    assert _wait_until(qapp, lambda: window.worker_thread is None)
    assert len(calls) == 1, "确认覆盖后照常转换"


def test_batch_end_to_end_lands_products_in_the_mirror_tree(
        window, qapp, tmp_path, monkeypatch):
    """端到端闭环：拖入文件夹 → 勾选 → 真实批量转换 → 产物落在同级镜像树。

    run_batch 走真身（换掉 GUI 的文件内并行开关——进程池对三帧小文件是纯开销，
    与判定无关）；钉的是「解析期算定的输出路径真的被写到那里」。
    """
    import gui.main_window as mw
    from core import batch as core_batch

    real_run_batch = core_batch.run_batch
    monkeypatch.setattr(
        mw, "run_batch",
        lambda *args, **kwargs: real_run_batch(
            *args, **{**kwargs, "parallel": False}))

    dbc_path = tmp_path / "t.dbc"
    dbc_path.write_text(INLINE_DBC, encoding="utf-8")
    dbc = load(str(dbc_path))
    window.dbc_list = [dbc]
    window.auto_bind = {1: dbc.path}

    root = tmp_path / "AHT"
    for index in (1, 2):
        _write_blf(root / "sub" / f"run{index:03d}.blf", channels=(1,))
    monkeypatch.setattr(window, "_choose_candidates", lambda candidates: candidates)

    _, settled = _drop_and_settle(qapp, window, root)
    assert settled
    notices = []
    monkeypatch.setattr(mw.QMessageBox, "exec",
                        lambda self: notices.append(self.text())
                        or QMessageBox.StandardButton.Ok)

    window._start_convert()
    assert _wait_until(qapp, lambda: window.worker_thread is None, timeout=60)

    produced = sorted((tmp_path / "AHT_t" / "sub").glob("*.mdf"))
    assert [p.name for p in produced] == ["run001_t.mdf", "run002_t.mdf"]
    assert not (root / "sub" / "run001_t.mdf").exists(), "产物不进源树"
    assert notices == ["2 个 blf 文件全部转换完成"]
