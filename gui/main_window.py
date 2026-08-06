"""主窗口：BLF/DBC 选择 → 通道绑定 → 转换 → 摘要。"""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QObject, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core import blf_reader
from core.converter import ConversionResult, convert
from core.dbc_loader import DbcDef, load

UNBOUND = "不绑定"

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 调试用默认 BLF：启动时若文件存在则自动加载，免去每次手动选择
DEFAULT_BLF = (r"E:\projects\blf_dbc\inputs\blf"
               r"\ACFCANPUB_20260722_104000_59489600"
               r"-ACFCANPUB_20260722_104930_59489619.blf")

# 调试用默认 DBC：启动时自动加入「DBC 矩阵文件」列表（inputs/dbc 下，存在才加载）
DEFAULT_DBCS = [
    "VDCPublic_CANFD1.dbc",  # public_CANFD1
    "VDCCCU_CANFD2.dbc",     # CCU_CANFD2
    "VDCCCU_CANFD3.dbc",     # CCU_CANFD3
    "VDCCZF_CANFD.dbc",      # CZF_CANFD
    "VDCCZL_CANFD.dbc",      # CZL_CANFD
    "VDCCZR_CANFD.dbc",      # CZR_CANFD
    "VDCCZT_CANFD.dbc",      # CZT_CANFD
    "VDCCIDC_CANFD.dbc",     # CCU_IDC_CANFD
    "VDCCCU_CANFD1.dbc",     # CCU_CANFD1
    "VDCPublic_CANFD2.dbc",  # public_CANFD2
]

# 调试用默认通道绑定：CANn → DBC 文件名（DBC 未加载或通道不存在时自动跳过）
DEFAULT_BINDINGS = {
    1: "VDCPublic_CANFD1.dbc",  # CAN1—public_CANFD1
    3: "VDCCCU_CANFD2.dbc",     # CAN3—CCU_CANFD2
    6: "VDCCCU_CANFD3.dbc",     # CAN6—CCU_CANFD3
    8: "VDCCZF_CANFD.dbc",      # CAN8—CZF_CANFD
    9: "VDCCZL_CANFD.dbc",      # CAN9—CZL_CANFD
    10: "VDCCZR_CANFD.dbc",     # CAN10—CZR_CANFD
    11: "VDCCZT_CANFD.dbc",     # CAN11—CZT_CANFD
    12: "VDCCIDC_CANFD.dbc",    # CAN12—CCU_IDC_CANFD
    13: "VDCCCU_CANFD1.dbc",    # CAN13—CCU_CANFD1
    15: "VDCPublic_CANFD2.dbc", # CAN15—public_CANFD2
}


class ConvertWorker(QObject):
    progress = Signal(str, float)
    done = Signal(object)
    error = Signal(str)

    def __init__(self, blf_path, bindings, out_path, raw_export):
        super().__init__()
        self.blf_path = blf_path
        self.bindings = bindings
        self.out_path = out_path
        self.raw_export = raw_export

    @Slot()
    def run(self):
        try:
            # 方案 G：GUI 默认开并行解码（per-bucket 多进程 finish，实测净省
            # ~5.4s）；内部自动回退串行（池失败/内存阈值），正确性零损失。
            result = convert(self.blf_path, self.bindings, self.out_path,
                             progress_cb=lambda s, p: self.progress.emit(s, p),
                             raw_export=self.raw_export,
                             parallel=True)
            self.done.emit(result)
        except Exception as e:  # noqa: BLE001 — 界面层兜底
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BLF → MDF 转换")
        self.resize(760, 640)
        self.blf_path = None
        self.dbc_list: list[DbcDef] = []
        self.worker_thread: QThread | None = None

        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)

        # BLF 文件
        row = QHBoxLayout()
        row.addWidget(QLabel("BLF 文件"))
        self.blf_edit = QLineEdit()
        self.blf_edit.setReadOnly(True)
        row.addWidget(self.blf_edit, 1)
        btn_blf = QPushButton("浏览…")
        btn_blf.clicked.connect(self._pick_blf)
        row.addWidget(btn_blf)
        v.addLayout(row)

        # 第二排：左 = DBC 矩阵文件，右 = 通道匹配
        middle = QHBoxLayout()
        left_col = QVBoxLayout()
        left_col.addWidget(QLabel("DBC 矩阵文件"))
        self.dbc_list_widget = QListWidget()
        left_col.addWidget(self.dbc_list_widget, 1)
        dbc_btns = QHBoxLayout()
        btn_add = QPushButton("添加 DBC…")
        btn_add.clicked.connect(self._add_dbc)
        btn_rm = QPushButton("移除选中")
        btn_rm.clicked.connect(self._remove_dbc)
        dbc_btns.addWidget(btn_add)
        dbc_btns.addWidget(btn_rm)
        dbc_btns.addStretch(1)
        left_col.addLayout(dbc_btns)
        middle.addLayout(left_col, 1)  # 左栏：DBC 列表

        right_col = QVBoxLayout()
        right_col.addWidget(QLabel("通道匹配"))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["通道", "DBC 矩阵", "状态"])
        # 列宽（含手动拖动）或滚动条出现/消失后，表格总宽跟随三列之和，不留白
        self.table.horizontalHeader().sectionResized.connect(self._fit_table_width)
        self.table.verticalScrollBar().rangeChanged.connect(
            lambda *_: self._fit_table_width())
        right_col.addWidget(self.table, 1)
        # 修复项 5：原始帧导出选项化，默认关闭（与 CANoe 导出一致）；
        # 勾选后未绑定 DBC 的通道以 Raw::CANn 组导出（converter.raw_export）
        self.raw_check = QCheckBox("导出未绑定通道的原始帧")
        self.raw_check.setChecked(False)
        right_col.addWidget(self.raw_check)
        middle.addLayout(right_col, 0)  # 右栏：通道表（总宽=三列列宽之和，余宽全给左侧 DBC 列表）
        v.addLayout(middle, 11)  # 第二排合计垂直额外空间 11/12

        # 输出路径
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("输出文件"))
        self.out_edit = QLineEdit()
        out_row.addWidget(self.out_edit, 1)
        btn_out = QPushButton("浏览…")
        btn_out.clicked.connect(self._pick_out)
        out_row.addWidget(btn_out)
        v.addLayout(out_row)

        # 转换按钮 + 进度（同一行，省一行高度；底部整体下移后归上方通道匹配）
        ctrl_row = QHBoxLayout()
        self.convert_btn = QPushButton("转 换")
        self.convert_btn.setEnabled(False)
        self.convert_btn.clicked.connect(self._start_convert)
        ctrl_row.addWidget(self.convert_btn)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        ctrl_row.addWidget(self.progress, 1)
        v.addLayout(ctrl_row)
        self.stage_label = QLabel("")
        v.addWidget(self.stage_label)

        # 摘要（只读，压缩到约 1/12 额外空间 = 原来的 1/4）
        v.addWidget(QLabel("结果摘要"))
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        v.addWidget(self.summary, 1)  # 额外空间分配 1/12

        # 便于调试：默认 DBC/BLF 存在则自动加载（不存在时静默跳过，不打断启动）；
        # DBC 先加载，BLF 加载重建通道表时下拉框已含这些 DBC
        self._load_default_dbcs()
        if Path(DEFAULT_BLF).exists():
            self._load_blf(DEFAULT_BLF)

    # ---- 文件选择 ----
    def _pick_blf(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 BLF 文件", "",
                                              "BLF 文件 (*.blf)")
        if not path:
            return
        self._load_blf(path)

    def _load_blf(self, path: str):
        """加载 BLF：枚举通道 → 刷新通道表与默认输出路径。"""
        try:
            channels = blf_reader.list_channels(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "BLF 读取失败", f"{path}\n{e}")
            return
        if not channels:
            QMessageBox.warning(self, "提示", "文件中未找到有效报文数据")
            return
        self.blf_path = path
        self.blf_edit.setText(path)
        self._rebuild_channel_table(channels)
        self._set_default_output()
        self.convert_btn.setEnabled(True)

    def _set_default_output(self):
        """默认输出路径：outputs/{时间戳}.mdf（重复转换不互相覆盖）。"""
        out_dir = PROJECT_ROOT / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        self.out_edit.setText(str(out_dir / f"{datetime.now():%Y%m%d_%H%M%S}.mdf"))

    def _rebuild_channel_table(self, channels: list[int]):
        # 记录现有绑定选择，重建后恢复（添加/移除 DBC 时不丢失用户选择）
        prev = {}
        for r in range(self.table.rowCount()):
            combo = self.table.cellWidget(r, 1)
            if combo is not None and self.table.item(r, 0) is not None:
                prev[self.table.item(r, 0).text()] = combo.currentText()
        self.table.setRowCount(0)
        for ch in channels:
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = f"CAN{ch}"
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 2, QTableWidgetItem("原始"))
            combo = QComboBox()
            combo.addItem(UNBOUND)
            for dbc in self.dbc_list:
                combo.addItem(Path(dbc.path).name)
            if name in prev:
                valid = [combo.itemText(i) for i in range(combo.count())]
                if prev[name] in valid:
                    combo.setCurrentText(prev[name])
            else:
                # 首次加载：套用调试默认绑定（DBC 已加载且该通道存在时）
                default = DEFAULT_BINDINGS.get(ch)
                if default:
                    valid = [combo.itemText(i) for i in range(combo.count())]
                    if default in valid:
                        combo.setCurrentText(default)
            self.table.setCellWidget(row, 1, combo)
            combo.currentIndexChanged.connect(
                lambda _idx, r=row: self._update_status(r)
            )
            self._update_status(row)
        self._apply_column_widths()

    def _apply_column_widths(self):
        """通道匹配列宽：原默认列宽（760px 面板下 100/100/261）按 0.7/2.0/0.3 缩放。

        通道 70px（CAN 名够用）、DBC 矩阵 200px（最长文件名 ~190px 可完整显示）、
        状态 78px；仍可手动拖动列宽（表格总宽自动跟随，见 _fit_table_width）。
        """
        hdr = self.table.horizontalHeader()
        hdr.setStretchLastSection(False)
        for i, (base, ratio) in enumerate(zip((100, 100, 261), (0.7, 2.0, 0.3))):
            hdr.resizeSection(i, max(1, int(base * ratio)))
        self._fit_table_width()

    def _fit_table_width(self):
        """通道表总宽 = 三列列宽之和 + 行号表头 + 边框（垂直滚动条可见时再补其宽度）。

        表格固定宽度，视口内零留白；右侧余宽全部让给「DBC 矩阵文件」列表。
        列宽拖动（sectionResized）或滚动条出现/消失（rangeChanged）时自动重算。
        """
        hdr = self.table.horizontalHeader()
        sb = self.table.verticalScrollBar()
        # sizeHint 而非 width()：行号表头在布局前 width() 为 0，sizeHint 始终反映内容宽
        total = self.table.verticalHeader().sizeHint().width() + 2 * self.table.frameWidth()
        for i in range(hdr.count()):
            total += hdr.sectionSize(i)
        # 用范围而非 isVisible 判断：rangeChanged 触发时滚动条可能尚未隐藏（时序滞后）
        policy = self.table.verticalScrollBarPolicy()
        need_sb = (policy == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
                   or (policy != Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                       and sb.maximum() > 0))
        if need_sb:
            total += sb.sizeHint().width()
        self.table.setFixedWidth(total)

    def _update_status(self, row: int):
        combo = self.table.cellWidget(row, 1)
        status = "已绑定" if combo.currentText() != UNBOUND else "原始"
        self.table.item(row, 2).setText(status)

    def _add_dbc(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择 DBC 文件", "",
                                                "DBC 文件 (*.dbc)")
        if not paths:
            return
        self._load_dbcs(paths)

    def _load_dbcs(self, paths: list[str]):
        """加载一组 DBC：逐个解析加入列表（失败弹窗），最后刷新通道表下拉框。"""
        for p in paths:
            try:
                self.dbc_list.append(load(p))
                self.dbc_list_widget.addItem(f"{Path(p).name}    {Path(p).parent}")
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, "DBC 解析失败", f"{p}\n{e}")
        channels = [int(self.table.item(r, 0).text()[3:])
                    for r in range(self.table.rowCount())]
        self._rebuild_channel_table(channels)

    def _load_default_dbcs(self):
        """调试用：把 inputs/dbc 下的默认 DBC 自动加入列表（不存在的跳过）。"""
        dbc_dir = PROJECT_ROOT / "inputs" / "dbc"
        paths = [str(dbc_dir / name) for name in DEFAULT_DBCS
                 if (dbc_dir / name).exists()]
        if paths:
            self._load_dbcs(paths)

    def _remove_dbc(self):
        row = self.dbc_list_widget.currentRow()
        if row < 0:
            return
        self.dbc_list.pop(row)
        self.dbc_list_widget.takeItem(row)
        channels = [int(self.table.item(r, 0).text()[3:])
                    for r in range(self.table.rowCount())]
        self._rebuild_channel_table(channels)

    def _pick_out(self):
        path, _ = QFileDialog.getSaveFileName(self, "选择输出文件", "",
                                              "MDF 文件 (*.mdf)")
        if path:
            self.out_edit.setText(path)

    # ---- 转换 ----
    def _start_convert(self):
        if not self.blf_path:
            return
        out = self.out_edit.text().strip()
        if not out:
            QMessageBox.warning(self, "提示", "请指定输出文件路径")
            return
        if Path(out).exists():
            ans = QMessageBox.question(
                self, "覆盖确认", f"输出文件已存在：\n{out}\n\n是否覆盖？")
            if ans != QMessageBox.StandardButton.Yes:
                return
        bindings = {}
        for r in range(self.table.rowCount()):
            ch = int(self.table.item(r, 0).text()[3:])
            text = self.table.cellWidget(r, 1).currentText()
            if text == UNBOUND:
                bindings[ch] = None
            else:
                bindings[ch] = next(d for d in self.dbc_list
                                    if Path(d.path).name == text)
        self._set_busy(True)
        self.progress.setValue(0)
        self.summary.clear()
        self.worker_thread = QThread()
        self.worker = ConvertWorker(self.blf_path, bindings, out,
                                    self.raw_check.isChecked())
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.worker.error.connect(self._on_error)
        self.worker_thread.start()

    def _set_busy(self, busy: bool):
        self.convert_btn.setEnabled(not busy)

    @Slot(str, float)
    def _on_progress(self, stage: str, percent: float):
        self.stage_label.setText(stage)
        self.progress.setValue(int(percent))

    @Slot(object)
    def _on_done(self, result: ConversionResult):
        self._finish()
        lines = [f"总时长: {result.duration_seconds:.1f} s"]
        for s in result.summaries:
            if s.bound:
                lines.append(
                    f"CAN{s.channel} 已绑定: 解码 {s.decoded_frames} 帧 · "
                    f"{s.signal_count} 个信号 · 未知 {s.unknown_frames} 帧 "
                    f"({s.unknown_ids} 个 ID)"
                )
                if s.warning:
                    lines.append(f"  警告: {s.warning}")
            else:
                if self.worker.raw_export:
                    line = f"CAN{s.channel} 未绑定: 原始帧 {s.raw_frames} 帧"
                    if s.unknown_frames:
                        line += f" · DBC 未匹配丢弃 {s.unknown_frames} 帧 ({s.unknown_ids} 个 ID)"
                else:
                    line = f"CAN{s.channel} 未绑定: 原始帧导出关闭"
                lines.append(line)
                if s.warning:
                    lines.append(f"  警告: {s.warning}")
        self.summary.setPlainText("\n".join(lines))

    @Slot(str)
    def _on_error(self, msg: str):
        self._finish()
        QMessageBox.critical(self, "转换失败", msg)

    def _finish(self):
        self.worker_thread.quit()
        self.worker_thread.wait()
        self.worker_thread = None
        self._set_busy(False)
        self.progress.setValue(0)
        self.stage_label.setText("")

    def closeEvent(self, event):
        thread = self.worker_thread
        if thread is not None and thread.isRunning():
            ans = QMessageBox.question(
                self, "确认关闭",
                "转换仍在进行中，关闭窗口将等待其完成后丢弃结果。\n确定要关闭吗？")
            if ans != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            # convert() 无法中途取消：先退出事件循环再等待线程结束，
            # 避免销毁仍在运行的 QThread（Qt 会 abort）。
            # wait() 期间主线程阻塞，_on_done/_on_error 的 _finish() 不会并发执行。
            thread.quit()
            thread.wait()
        event.accept()
