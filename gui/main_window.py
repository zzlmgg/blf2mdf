"""主窗口：BLF/DBC 选择 → 通道绑定 → 转换 → 摘要。"""
from pathlib import Path

from PySide6.QtCore import QThread, QObject, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core import blf_reader
from core.converter import ConversionResult, convert
from core.dbc_loader import DbcDef, load

UNBOUND = "不绑定（导出原始帧）"


class ConvertWorker(QObject):
    progress = Signal(str, float)
    done = Signal(object)
    error = Signal(str)

    def __init__(self, blf_path, bindings, out_path):
        super().__init__()
        self.blf_path = blf_path
        self.bindings = bindings
        self.out_path = out_path

    @Slot()
    def run(self):
        try:
            result = convert(self.blf_path, self.bindings, self.out_path,
                             progress_cb=lambda s, p: self.progress.emit(s, p))
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

        # DBC 列表
        v.addWidget(QLabel("DBC 矩阵文件"))
        self.dbc_list_widget = QListWidget()
        v.addWidget(self.dbc_list_widget, 1)
        dbc_btns = QHBoxLayout()
        btn_add = QPushButton("添加 DBC…")
        btn_add.clicked.connect(self._add_dbc)
        btn_rm = QPushButton("移除选中")
        btn_rm.clicked.connect(self._remove_dbc)
        dbc_btns.addWidget(btn_add)
        dbc_btns.addWidget(btn_rm)
        dbc_btns.addStretch(1)
        v.addLayout(dbc_btns)

        # 通道表
        v.addWidget(QLabel("通道"))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["通道", "DBC 矩阵", "状态"])
        self.table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.table, 1)

        # 输出路径
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("输出文件"))
        self.out_edit = QLineEdit()
        out_row.addWidget(self.out_edit, 1)
        btn_out = QPushButton("浏览…")
        btn_out.clicked.connect(self._pick_out)
        out_row.addWidget(btn_out)
        v.addLayout(out_row)

        # 转换按钮 + 进度
        self.convert_btn = QPushButton("转 换")
        self.convert_btn.setEnabled(False)
        self.convert_btn.clicked.connect(self._start_convert)
        v.addWidget(self.convert_btn)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        v.addWidget(self.progress)
        self.stage_label = QLabel("")
        v.addWidget(self.stage_label)

        # 摘要
        v.addWidget(QLabel("结果摘要"))
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        v.addWidget(self.summary, 1)

    # ---- 文件选择 ----
    def _pick_blf(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 BLF 文件", "",
                                              "BLF 文件 (*.blf)")
        if not path:
            return
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
        default_out = Path(path).with_name(Path(path).stem + "_conv.mdf")
        self.out_edit.setText(str(default_out))
        self.convert_btn.setEnabled(True)

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
            self.table.setCellWidget(row, 1, combo)
            combo.currentIndexChanged.connect(
                lambda _idx, r=row: self._update_status(r)
            )
            self._update_status(row)

    def _update_status(self, row: int):
        combo = self.table.cellWidget(row, 1)
        status = "已绑定" if combo.currentText() != UNBOUND else "原始"
        self.table.item(row, 2).setText(status)

    def _add_dbc(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择 DBC 文件", "",
                                                "DBC 文件 (*.dbc)")
        for p in paths:
            try:
                self.dbc_list.append(load(p))
                self.dbc_list_widget.addItem(f"{Path(p).name}    {Path(p).parent}")
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, "DBC 解析失败", f"{p}\n{e}")
        # 刷新所有下拉框
        channels = [int(self.table.item(r, 0).text()[3:])
                    for r in range(self.table.rowCount())]
        self._rebuild_channel_table(channels)

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
        self.worker = ConvertWorker(self.blf_path, bindings, out)
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
                lines.append(f"CAN{s.channel} 未绑定: 原始帧 {s.raw_frames} 帧")
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
