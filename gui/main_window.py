"""主窗口：BLF/DBC 选择 → 通道绑定 → 转换 → 摘要。"""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QObject, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from core import blf_reader, project_loader
from core.converter import ConversionResult, convert
from core.dbc_loader import DbcDef, load

UNBOUND = "不绑定"

# 通道表固定高度基准：总高沿用 13 路 CAN 的旧几何（样例文件最大行数：
# AHT 13 通道、A19G1 12 通道 + 映射 CAN15），行高收缩到默认的 13/16，
# 使 16 路 CAN 全显而面板总高度不变。几何与行数无关、读取全程不跳动；
# 行数超 16（理论场景）时出现垂直滚动条。
_TABLE_ROWS = 16
_OLD_ROWS_BASIS = 13  # 高度基准：13 行 × 默认行高（改动前几何，总高不变）

# 进度条样式：填充块浅绿色（Qt 原生 Windows 样式为蓝色渐变，视觉不符合
# 预期）；轨道浅灰 + 圆角边框，进度文字居中
_PROGRESS_STYLE = (
    "QProgressBar { border: 1px solid #999; border-radius: 3px;"
    " background-color: #F0F0F0; text-align: center; }"
    "QProgressBar::chunk { background-color: #90EE90; }"
)


class DbcCombo(QComboBox):
    """「DBC 矩阵」列下拉：忽略鼠标滚轮，防悬停误触改绑（滚轮事件冒泡给表格）。

    下拉选择只应通过点击/键盘完成；滚轮悬停改选是误改绑定的常见来源。
    """

    def wheelEvent(self, event):
        event.ignore()

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ccu3.0 DBC 数据源：inputs/dbc_ccu3.0/<项目>/*.dbc + 同目录映射文件
CCU3_ROOT = PROJECT_ROOT / "inputs" / "dbc_ccu3.0"
CCU3_MAPPING_FILE = CCU3_ROOT / "dbc_对应关系.txt"

PROJECT_PLACEHOLDER = "选择项目…"  # 项目下拉首项（禁用占位，仅提示）


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


class BlfScanWorker(QObject):
    """BLF 通道枚举（worker 线程）：list_channels 全文件解析可能耗时数十秒
    （86MB/565 万帧实测 41s），放后台线程避免主界面冻结成「未响应」。

    progress 按文件字节位置报真实进度（0-100），done/error 结果回主线程。
    """
    progress = Signal(float)
    done = Signal(object)
    error = Signal(str)

    def __init__(self, path):
        super().__init__()
        self.path = path

    @Slot()
    def run(self):
        try:
            channels = blf_reader.list_channels(
                self.path, progress_cb=self.progress.emit)
            self.done.emit(channels)
        except Exception as e:  # noqa: BLE001 — 界面层兜底
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BLF → MDF 转换")
        # 启动默认 760×920；加载 BLF 后窗口高度自动贴合内容（_fit_window_height）：
        # 通道匹配表格固定 = 表头 + 各行高 + 余量（「刚好 N 行多一丢丢」），
        # 结果摘要固定 ≈46px（≈ 默认布局下 277px 的 1/6），不再抢高度
        # 启动默认 760×920；几何在 __init__ 末尾一次性贴合（_fit_window_height）：
        # 通道匹配表格固定 = 表头 + 13 路 CAN 基准总高（行高收缩后 16 路
        # 全显）+ 余量——任何行数下高度恒定，读取 BLF 过程与完成后排布一致
        # 结果摘要固定 ≈46px，不再抢高度
        self.resize(760, 920)
        self.blf_path = None
        # BLF 实际包含的通道（行集基准：通道表 = BLF 通道 ∪ 当前映射通道）
        self.blf_channels: list[int] = []
        self.dbc_list: list[DbcDef] = []
        # 当前项目的自动绑定建议 {通道: DBC 文件名}；未选项目为 None。
        # BLF 晚于项目加载时，_rebuild_channel_table 用它补齐默认绑定。
        self.auto_bind: dict[int, str] | None = None
        self.mapping = project_loader.load_mapping(CCU3_MAPPING_FILE)
        # 输出路径是否仍是自动生成名（用户手改/浏览选择后置 False，切换项目时不被覆盖）
        self._auto_out = True
        self.worker_thread: QThread | None = None
        self.scan_thread: QThread | None = None
        self.scan_worker: BlfScanWorker | None = None

        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)

        # BLF 文件
        row = QHBoxLayout()
        row.addWidget(QLabel("BLF 文件"))
        self.blf_edit = QLineEdit()
        self.blf_edit.setReadOnly(True)
        row.addWidget(self.blf_edit, 1)
        # 加载进度条：固定宽度，仅在扫描时显示（扫描结束即隐藏，不占布局空间、
        # 不影响排版）；「转换」行的大进度条只在转换时使用
        self.load_progress = QProgressBar()
        self.load_progress.setStyleSheet(_PROGRESS_STYLE)
        self.load_progress.setFixedWidth(140)
        self.load_progress.setRange(0, 100)
        self.load_progress.setValue(0)
        self.load_progress.setVisible(False)
        row.addWidget(self.load_progress)
        btn_blf = QPushButton("浏览…")
        btn_blf.clicked.connect(self._pick_blf)
        self.btn_blf = btn_blf
        row.addWidget(btn_blf)
        v.addLayout(row)

        # 第二排：左 = ccu3.0 项目 + DBC 矩阵文件，右 = 通道匹配
        middle = QHBoxLayout()
        left_col = QVBoxLayout()
        # ccu3.0 项目：选项目 = 读入该项目全部 DBC 并按映射自动匹配通道
        left_col.addWidget(QLabel("ccu3.0 项目"))
        self.project_combo = QComboBox()
        self.project_combo.currentTextChanged.connect(self._select_project)
        left_col.addWidget(self.project_combo)
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
        # 中排高度由固定高的通道表决定（_fit_table_height 恒为 16 行上限），
        # 不参与额外空间分配；窗口高度在 __init__ 末尾贴合一次后不再变动
        v.addLayout(middle)

        # 输出路径
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("输出文件"))
        self.out_edit = QLineEdit()
        # 手动输入视为自定义路径：切换项目时不再自动改名（见 _auto_out）
        self.out_edit.textEdited.connect(lambda _: setattr(self, "_auto_out", False))
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
        self.progress.setStyleSheet(_PROGRESS_STYLE)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        ctrl_row.addWidget(self.progress, 1)
        v.addLayout(ctrl_row)
        self.stage_label = QLabel("")
        v.addWidget(self.stage_label)

        # 摘要（只读）：固定 ≈46px（默认布局下原 277px 的 1/6），不抢高度；
        # 内容超出时内部滚动
        v.addWidget(QLabel("结果摘要"))
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setFixedHeight(46)
        v.addWidget(self.summary)

        # 项目下拉：占位首项禁用，列表 = dbc_ccu3.0 下含 DBC 的项目文件夹
        self.project_combo.addItem(PROJECT_PLACEHOLDER)
        self.project_combo.model().item(0).setEnabled(False)
        for name in project_loader.list_projects(CCU3_ROOT):
            self.project_combo.addItem(name)
        # 固定几何：通道表按 13 路 CAN 基准预留高度（行高收缩，16 路全显）、
        # 三列固定宽度，窗口贴合一次。
        # 此后任何 BLF 读取/表格重建都不改动几何——读取过程与完成后排布一致。
        # 行号表头也按两位数字（最大行号 16）预留固定宽度：sizeHint 随行数
        # 变化（实测 0/16/28px），会导致表格总宽随行数跳变
        self._row_header_w = (self.table.verticalHeader().fontMetrics()
                              .horizontalAdvance("16")
                              + 2 * self.table.verticalHeader().fontMetrics()
                              .horizontalAdvance("0"))
        self.table.verticalHeader().setFixedWidth(self._row_header_w)
        self._apply_column_widths()
        self._fit_table_height()
        self._fit_window_height()

    # ---- 文件选择 ----
    def _pick_blf(self):
        # 对话框默认打开项目根目录的 inputs\blf（BLF 数据统一存放处）；
        # 目录不存在时先创建，避免 Qt 回退到其他目录
        start = PROJECT_ROOT / "inputs" / "blf"
        start.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(self, "选择 BLF 文件",
                                              str(start), "BLF 文件 (*.blf)")
        if not path:
            return
        self._load_blf(path)

    def _load_blf(self, path: str):
        """加载 BLF（异步）：后台线程枚举通道 → 刷新通道表与默认输出路径。

        大文件（如 86MB/565 万帧）list_channels 全文件解析实测耗时 41s，
        若在主线程同步执行窗口会冻结成「未响应」；改为 worker 线程 +
        字节级真实进度，扫描期间界面保持响应，进度条可见推进。
        """
        if self.scan_thread is not None and self.scan_thread.isRunning():
            return  # 扫描进行中不接受新文件
        self._set_scan_busy(True)
        self.load_progress.setVisible(True)
        self.load_progress.setValue(0)
        self.scan_thread = QThread()
        self.scan_worker = BlfScanWorker(path)
        self.scan_worker.moveToThread(self.scan_thread)
        self.scan_thread.started.connect(self.scan_worker.run)
        self.scan_worker.progress.connect(self._on_scan_progress)
        self.scan_worker.done.connect(self._on_scan_done)
        self.scan_worker.error.connect(self._on_scan_error)
        self.scan_thread.start()

    def _set_scan_busy(self, busy: bool):
        """扫描期间禁用文件选择与转换；转换按钮另需已加载 BLF。"""
        self.btn_blf.setEnabled(not busy)
        self.convert_btn.setEnabled(not busy and self.blf_path is not None)
        self.load_progress.setVisible(busy)

    @Slot(float)
    def _on_scan_progress(self, percent: float):
        self.load_progress.setValue(int(percent))

    @Slot(object)
    def _on_scan_done(self, channels: list):
        self._finish_scan()
        if not channels:
            QMessageBox.warning(self, "提示", "文件中未找到有效报文数据")
            return
        path = self.scan_worker.path
        self.blf_path = path
        self.blf_edit.setText(path)
        self.blf_channels = channels
        self._rebuild_channel_table(channels)
        self._set_default_output()
        self.convert_btn.setEnabled(True)

    @Slot(str)
    def _on_scan_error(self, msg: str):
        self._finish_scan()
        QMessageBox.critical(self, "BLF 读取失败",
                             f"{self.scan_worker.path}\n{msg}")

    def _finish_scan(self):
        thread = self.scan_thread
        if thread is not None:
            thread.quit()
            thread.wait()
            self.scan_thread = None
        self.load_progress.setValue(0)
        self._set_scan_busy(False)  # 隐藏 load_progress、恢复按钮

    def _set_default_output(self):
        """默认输出路径：outputs/{项目名_}{时间戳}.mdf（重复转换不互相覆盖）。

        已选 ccu3.0 项目时文件名加项目名前缀（如 A19G1_20260807_100000.mdf），
        未选项目则保持纯时间戳。标记 _auto_out：用户手动改过后不再覆盖。
        """
        out_dir = PROJECT_ROOT / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = self.project_combo.currentText()
        prefix = f"{name}_" if name and name != PROJECT_PLACEHOLDER else ""
        self.out_edit.setText(
            str(out_dir / f"{prefix}{datetime.now():%Y%m%d_%H%M%S}.mdf"))
        self._auto_out = True

    def _rebuild_channel_table(self, channels: list[int],
                               auto: dict[int, str] | None = None,
                               keep_prev: bool = True):
        """重建通道表。绑定优先级：

        1. auto（选项目时传入）：标准映射为准，覆盖旧选择——
           选项目 = 重新套用该项目的自动匹配，用户事后微调；
        2. prev（keep_prev=True 时记录）：添加/移除 DBC 不丢失用户选择；
        3. self.auto_bind：已选项目但 BLF 后加载时的兜底（此时无 prev）。

        行集合 = BLF 通道 ∪ 映射通道：映射是完整规格（如 PFCAN2—CAN15），
        日志中无数据的映射通道也显示（状态「无数据」），不让匹配对静默缺失。
        """
        blf_channels = set(channels)
        mapped = set(self.auto_bind or {})
        all_channels = sorted(blf_channels | mapped)
        prev = {}
        if keep_prev:
            for r in range(self.table.rowCount()):
                combo = self.table.cellWidget(r, 1)
                if combo is not None and self.table.item(r, 0) is not None:
                    prev[self.table.item(r, 0).text()] = combo.currentText()
        self.table.setRowCount(0)
        for ch in all_channels:
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = f"CAN{ch}"
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 2, QTableWidgetItem(
                "无数据" if ch not in blf_channels else "原始"))
            combo = DbcCombo()
            combo.addItem(UNBOUND)
            for dbc in self.dbc_list:
                combo.addItem(Path(dbc.path).name)
            valid = [combo.itemText(i) for i in range(combo.count())]
            # auto/self.auto_bind 键为 int 通道号（与 auto_bindings 契约一致）；
            # prev 键为 "CANn" 字符串（来自表格显示名）。键型不可混用。
            if auto is not None and ch in auto and auto[ch] in valid:
                combo.setCurrentText(auto[ch])
            elif name in prev and prev[name] in valid:
                combo.setCurrentText(prev[name])
            elif self.auto_bind is not None and ch in self.auto_bind \
                    and self.auto_bind[ch] in valid:
                combo.setCurrentText(self.auto_bind[ch])
            self.table.setCellWidget(row, 1, combo)
            combo.currentIndexChanged.connect(
                lambda _idx, r=row: self._update_status(r)
            )
            if ch not in blf_channels:
                # 无数据行保持「无数据」；用户手动改下拉后由信号接管为绑定态
                self.table.item(row, 2).setText("无数据")
            else:
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

    def _fit_table_height(self):
        """通道表固定高度 = 表头 + 13 路 CAN 基准总高 + 余量（总高不变）。

        行高收缩为默认行高的 13/16（16 行 × 行高 ≤ 13 行 × 默认行高），
        16 路 CAN 全显而面板总高度与旧版 13 行基准完全一致——「保持总体
        高度」的落点。高度与当前行数无关（实际行集 ≤ 16），读取 BLF 过程
        与完成后几何一致、不跳动；行数超 16（理论场景）时出现垂直滚动条。
        余量 8px ≈ 三分之一收缩行高。
        """
        vh = self.table.verticalHeader()
        d = vh.defaultSectionSize()  # 修改前读取：总高沿用旧基准，几何不变
        row_h = (_OLD_ROWS_BASIS * d) // _TABLE_ROWS
        vh.setDefaultSectionSize(row_h)
        # 固定行高：行数重建不改变行高，16 行全显的几何恒定
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        content = (self.table.horizontalHeader().sizeHint().height()
                   + _OLD_ROWS_BASIS * d + 2 * self.table.frameWidth())
        self.table.setFixedHeight(content + 8)

    def _fit_window_height(self):
        """窗口高度贴合布局最小高度（仅在 __init__ 末尾调用一次）。

        表格/摘要均为固定几何后，布局最小高度是常量——此后读取 BLF、
        重建表格都不再改动窗口尺寸，读取过程与完成后排布一致。
        用户仍可手动拉高窗口（多余空间留白）。
        """
        v = self.centralWidget().layout()
        self.resize(self.width(), v.minimumSize().height())

    def _fit_table_width(self):
        """通道表总宽 = 三列列宽之和 + 行号表头 + 边框（垂直滚动条可见时再补其宽度）。

        表格固定宽度，视口内零留白；右侧余宽全部让给「DBC 矩阵文件」列表。
        列宽拖动（sectionResized）或滚动条出现/消失（rangeChanged）时自动重算。
        """
        hdr = self.table.horizontalHeader()
        sb = self.table.verticalScrollBar()
        # 行号表头用启动时预留的固定宽度（_row_header_w），不用 sizeHint：
        # sizeHint 随行数变化（实测 0/16/28px），会导致表格总宽随行数跳变
        total = self._row_header_w + 2 * self.table.frameWidth()
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
        self._rebuild_channel_table(self.blf_channels)

    def _select_project(self, name: str):
        """选择 ccu3.0 项目：读入该项目全部 DBC（替换现有列表）→ 按映射自动匹配。

        任一个 DBC 解析失败则整体放弃（列表保持不变），并回退下拉选择。
        """
        if not name or name == PROJECT_PLACEHOLDER:
            return
        try:
            dbcs = project_loader.load_project(CCU3_ROOT, name)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "DBC 加载失败", f"{name}\n{e}")
            self.project_combo.setCurrentIndex(0)
            return
        self.dbc_list = dbcs
        self.dbc_list_widget.clear()
        for d in dbcs:
            self.dbc_list_widget.addItem(f"{Path(d.path).name}    {Path(d.path).parent}")
        self.auto_bind = project_loader.auto_bindings(dbcs, self.mapping)
        self._rebuild_channel_table(self.blf_channels, auto=self.auto_bind,
                                    keep_prev=False)
        # 项目名进入默认输出文件名；仅当输出还是自动名时更新，手改过的路径不动
        if self.blf_path and self._auto_out:
            self._set_default_output()

    def _remove_dbc(self):
        row = self.dbc_list_widget.currentRow()
        if row < 0:
            return
        self.dbc_list.pop(row)
        self.dbc_list_widget.takeItem(row)
        self._rebuild_channel_table(self.blf_channels)

    def _pick_out(self):
        path, _ = QFileDialog.getSaveFileName(self, "选择输出文件", "",
                                              "MDF 文件 (*.mdf)")
        if path:
            self.out_edit.setText(path)
            self._auto_out = False  # 用户自选输出路径，切换项目不再覆盖

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
        thread = self.scan_thread
        if thread is not None and thread.isRunning():
            # 扫描无法中途取消：等待其完成，避免销毁仍在运行的 QThread
            thread.quit()
            thread.wait()
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
