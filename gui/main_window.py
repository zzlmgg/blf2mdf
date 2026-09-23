"""主窗口：BLF/DBC 选择 → 通道绑定 → 转换 → 日志。"""
import datetime
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QThread, QObject, Signal, Slot
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QListWidgetItem,
)

from core import blf_reader, project_loader, source_resolver
from core.batch import BatchCancelled, BatchResult, overall_percent, run_batch
from core.blf_reader import ConversionCancelled
from core.converter import ConversionResult, convert
from core.dbc_loader import DbcDef, load
from core.source_resolver import Candidate
from gui.binding import (
    STATE_BOUND,
    STATE_NOT_EXPORTED,
    STATE_NO_DATA,
    decide_bindings,
    derive_state,
)
from gui.candidate_dialog import CandidateDialog
from gui.resources import application_icon
from gui.theme import POSITIVE_COLOR, WINDOW_HEIGHT, WINDOW_WIDTH
from gui.widgets import (
    AppShell,
    CompactCombo,
    DbcListItemWidget,
    DbcListWidget,
    SemanticIconButton,
    SummaryDialog,
    TitleBar,
)
from gui.windows_effects import apply_light_glass, sync_rounded_window

UNBOUND = "不绑定"

# 批量开始前的输出预检：三选一弹窗的取值。与 GUI 的 STATE_* 同一写法——
# 显示文字即取值，按钮文案与判定同源。统一前缀把这一组与 CONTEXT.md 的
# 「取消」区分开：那是中止一次进行中的转换，这里是「干脆不开始」。
PRECHECK_OVERWRITE_ALL = "覆盖全部"
PRECHECK_SKIP_EXISTING = "跳过已存在"
PRECHECK_CANCEL = "取消"


def _droppable_paths(event) -> list[str]:
    """拖拽事件里的可导入条目：本地文件夹或 .blf 文件（其余忽略）。

    扩展名大小写不敏感（Windows 资源管理器拖出的扩展名可能为大写 .BLF）；
    文件夹只是「可能含 .blf」——里面到底有没有，展开（来源解析）后才知道。
    """
    paths = []
    for url in event.mimeData().urls():
        if not url.isLocalFile():
            continue
        local = url.toLocalFile()
        # 先看扩展名（拖入多个 .blf 时零 stat），非 .blf 条目再问文件系统
        if local.lower().endswith(source_resolver.BLF_SUFFIX) \
                or Path(local).is_dir():
            paths.append(local)
    return paths


def _source_label(paths: list[str]) -> str:
    """拖入来源的显示名：单个来源用其目录/文件名，多个来源用「N 个来源」。"""
    names = [Path(path).name for path in paths]
    return names[0] if len(names) == 1 else f"{len(names)} 个来源"


def _total_seconds(result: ConversionResult) -> float:
    """单文件墙钟总耗时：timings 里的「总耗时」为主，无 timings
    （测试构造/旧调用方）兜底数据时长。"""
    return next((t for label, t in result.timings if label == "总耗时"),
                result.duration_seconds)


def _scan_progress_cb(progress_cb, index: int, total: int):
    """多文件扫描的进度映射：文件内进度 → 整体百分比（刻度归 core，见
    core.batch.overall_percent——与批量转换同一套「i/N + 文件内进度」）。

    单文件原样透传（与今天的字节级进度逐位一致）；单文件的新旧两条入口
    都走这里，故 total == 1 必须零改动。
    """
    if progress_cb is None or total <= 1:
        return progress_cb

    def report(percent: float) -> None:
        progress_cb(overall_percent(index, total, percent))

    return report


class DbcCombo(CompactCombo):
    """「DBC 矩阵」列下拉：忽略鼠标滚轮，防悬停误触改绑（滚轮事件冒泡给表格）。

    下拉选择只应通过点击/键盘完成；滚轮悬停改选是误改绑定的常见来源。
    """

    def wheelEvent(self, event):
        event.ignore()

def _app_root() -> Path:
    """运行根目录：源码运行时为项目根（gui/ 的上一级）；PyInstaller 冻结后
    __file__ 指向临时解压目录，改为 exe 所在目录（inputs/outputs 随 exe 分发）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _app_root()


CCU3_ROOT = project_loader.find_ccu3_root(PROJECT_ROOT)
CCU3_MAPPING_FILE = CCU3_ROOT / "dbc_对应关系.txt" if CCU3_ROOT else None

PROJECT_PLACEHOLDER = "项目"  # 项目下拉首项（禁用占位，仅提示）


class ConvertWorker(QObject):
    progress = Signal(str, float)
    done = Signal(object)
    cancelled = Signal()
    error = Signal(str)

    def __init__(self, blf_path, bindings, out_path, cancel_event):
        super().__init__()
        self.blf_path = blf_path
        self.bindings = bindings
        self.out_path = out_path
        self.cancel_event = cancel_event

    @Slot()
    def run(self):
        try:
            # 方案 G：GUI 默认开并行解码（per-bucket 多进程 finish，实测净省
            # ~5.4s）；内部自动回退串行（池失败/内存阈值），正确性零损失。
            # 原始帧导出已从面板移除，GUI 固定关闭（与 CANoe 导出一致）；
            # converter 的 raw_export 参数保留供库层/测试使用。
            result = convert(self.blf_path, self.bindings, self.out_path,
                             progress_cb=lambda s, p: self.progress.emit(s, p),
                             raw_export=False,
                             parallel=True,
                             cancel_cb=self.cancel_event.is_set)
            self.done.emit(result)
        except ConversionCancelled:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001 — 界面层兜底
            self.error.emit(str(e))


class ResolveWorker(QObject):
    """来源解析（worker 线程）：拖入的路径集合 → 候选清单。

    目录树遍历可能很慢（大目录、网络盘），放后台线程避免主界面冻结；
    cancel_event 置位后解析在两个检查点内抛 ConversionCancelled。
    progress(已发现候选数)：遍历前不知总数，刻度是计数而非百分比（见
    core/source_resolver.resolve）——界面把它当状态文案，不当进度条刻度。
    """
    progress = Signal(int)
    done = Signal(object)
    cancelled = Signal()
    error = Signal(str)

    def __init__(self, paths, cancel_event):
        super().__init__()
        self.paths = [str(path) for path in paths]
        self.source_label = _source_label(self.paths)
        self.cancel_event = cancel_event

    @Slot()
    def run(self):
        try:
            candidates = source_resolver.resolve(
                self.paths, progress_cb=self.progress.emit,
                cancel_cb=self.cancel_event.is_set)
            self.done.emit(candidates)
        except ConversionCancelled:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001 — 界面层兜底
            self.error.emit(str(e))


class BlfScanWorker(QObject):
    """BLF 通道探测（worker 线程）：probe_channels 对象头级轻量行走
    （list_channels 全文件解析 86MB/565 万帧实测 41s，探测 5-20 倍更快），
    放后台线程避免主界面冻结成「未响应」。

    多个文件时逐个探测并取**通道并集**（批次的行集基准）；progress 按
    (i + 文件内进度)/N 报整体进度，单文件即文件字节位置（0-100）；
    cancel_event 置位后 probe 在 1024 对象内抛 ConversionCancelled →
    cancelled 信号回主线程。error 带出错文件的路径（多文件时得知道是哪个）。
    """
    progress = Signal(float)
    done = Signal(object)
    cancelled = Signal()
    error = Signal(str, str)

    def __init__(self, paths, cancel_event):
        super().__init__()
        self.paths = [str(path) for path in paths]
        # 扫描目标路径（单文件入口的兼容面：_on_scan_done 直接读它）
        self.path = self.paths[0]
        self.cancel_event = cancel_event

    @Slot()
    def run(self):
        total = len(self.paths)
        channels: set[int] = set()
        for index, path in enumerate(self.paths):
            try:
                channels.update(blf_reader.probe_channels(
                    path,
                    progress_cb=_scan_progress_cb(self.progress.emit, index,
                                                 total),
                    cancel_cb=self.cancel_event.is_set))
            except ConversionCancelled:
                self.cancelled.emit()
                return
            except Exception as e:  # noqa: BLE001 — 界面层兜底
                self.error.emit(path, str(e))
                return
        # 并集排序后交出：行集基准要确定性（探测顺序 = 清单顺序，不保证升序）
        self.done.emit(sorted(channels))


class BatchConvertWorker(QObject):
    """批量转换（worker 线程）：整批交给 core.batch.run_batch。

    「批次」的定义与转换循环都在 core/batch.py——界面只转发配置与回调，
    不自建循环（stage/percent 由 run_batch 映射好，stage 带「第 i/N 个 · 」）。
    cancelled 携带已完成结局（BatchCancelled）：界面据此说明产物保留情况。
    """
    progress = Signal(str, float)
    done = Signal(object)
    cancelled = Signal(object)
    error = Signal(str)

    def __init__(self, candidates, bindings, cancel_event):
        super().__init__()
        self.candidates = list(candidates)
        self.bindings = bindings
        self.cancel_event = cancel_event

    @Slot()
    def run(self):
        try:
            # 与单文件 ConvertWorker 同一套开关：文件内并行开、原始帧导出关
            result = run_batch(
                self.candidates, self.bindings,
                progress_cb=lambda s, p: self.progress.emit(s, p),
                cancel_cb=self.cancel_event.is_set,
                raw_export=False,
                parallel=True)
            self.done.emit(result)
        except BatchCancelled as exc:
            self.cancelled.emit(exc.outcomes)
        except ConversionCancelled:
            self.cancelled.emit([])
        except Exception as e:  # noqa: BLE001 — 界面层兜底
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BLF → MDF")
        self.setWindowIcon(application_icon())
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self._visual_effects_applied = False
        self._native_rounding = False
        # 单个 BLF 的路径；批次（> 1）时为空——批量的身份是 self.batch
        self.blf_path = None
        # 已就位可转换的批次（清单顺序 = 转换顺序）；空 = 只有 blf_path 那条路
        self.batch: list[Candidate] = []
        # 本批中因「输出已存在」被预检跳过的文件（结束弹窗与日志的报数依据；
        # 每次批量转换开头重新算定，见 _start_batch_convert）
        self.skipped: list[Candidate] = []
        # 批次来源显示名（「输入 BLF」行用）
        self.batch_source = ""
        # 正在扫描的候选与来源（解析产出的本次扫描输入；扫描收尾即清空，
        # 否则扫描失败时会与已就位的批次张冠李戴）
        self.pending: list[Candidate] = []
        self.pending_source = ""
        # BLF 实际包含的通道（行集基准：通道表 = BLF 通道 ∪ 当前映射通道）
        self.blf_channels: list[int] = []
        self.dbc_list: list[DbcDef] = []
        # 当前项目的自动绑定建议 {通道: DBC 文件路径}；未选项目为 None。
        # BLF 晚于项目加载时，_rebuild_channel_table 用它补齐默认绑定。
        self.auto_bind: dict[int, str] | None = None
        self.mapping = project_loader.load_mapping(CCU3_MAPPING_FILE)
        self.worker_thread: QThread | None = None
        self.scan_thread: QThread | None = None
        self.scan_worker: BlfScanWorker | None = None
        # 拖入来源的解析线程（文件夹/多条目才用得上；单文件走 _load_blf 捷径）
        self.resolve_thread: QThread | None = None
        self.resolve_worker: ResolveWorker | None = None
        # 解析/扫描/转换取消事件：每次加载/转换重建；取消按钮与 closeEvent
        # 置位，worker 循环检查后抛 ConversionCancelled（threading.Event 跨线程安全）
        self.scan_cancel = threading.Event()
        self.convert_cancel = threading.Event()

        central = AppShell()
        central.setObjectName("appRoot")
        central.setProperty("nativeGlass", False)
        central.setProperty("shellMaximized", False)
        self.setCentralWidget(central)
        shell = QVBoxLayout(central)
        shell.setContentsMargins(1, 1, 1, 1)
        shell.setSpacing(0)

        self.title_bar = TitleBar()
        shell.addWidget(self.title_bar)

        body = QWidget()
        body.setObjectName("appBody")
        v = QVBoxLayout(body)
        v.setContentsMargins(17, 8, 17, 8)
        v.setSpacing(4)
        shell.addWidget(body, 1)

        # 输入 BLF：浏览与拖拽两种入口保持等价。
        self.input_panel = QFrame()
        self.input_panel.setProperty("card", True)
        self.input_panel.setObjectName("inputPanel")
        self.input_panel.setFixedHeight(48)
        row = QHBoxLayout(self.input_panel)
        row.setContentsMargins(12, 6, 8, 6)
        row.setSpacing(8)
        self.blf_label = QLabel("输入 BLF")
        self.blf_label.setObjectName("sectionLabel")
        self.blf_label.setFixedWidth(64)
        row.addWidget(self.blf_label)
        self.blf_edit = QLineEdit()
        self.blf_edit.setObjectName("pathField")
        self.blf_edit.setReadOnly(True)
        self.blf_edit.setPlaceholderText("选择或拖入 .blf 文件")
        row.addWidget(self.blf_edit, 1)
        # 拖拽导入：标签 + 路径框整行接受 .blf 拖入（与「浏览…」并列的入口）。
        # 事件过滤不新增控件、不改几何——排版与其他行完全一致
        for w in (self.blf_label, self.blf_edit):
            w.setAcceptDrops(True)
            w.installEventFilter(self)
        # 加载进度条：固定宽度，仅在扫描时显示（扫描结束即隐藏，不占布局空间、
        # 不影响排版）；「转换」行的大进度条只在转换时使用
        self.load_progress = QProgressBar()
        self.load_progress.setObjectName("inlineProgress")
        self.load_progress.setFixedWidth(110)
        self.load_progress.setRange(0, 100)
        self.load_progress.setValue(0)
        self.load_progress.setTextVisible(False)
        self.load_progress.setVisible(False)
        row.addWidget(self.load_progress)
        # 扫描取消：仅扫描期间显示（_set_scan_busy 控制）；点击置位取消
        # 事件，worker 循环在 1024 对象内抛 ConversionCancelled 退出
        self.btn_scan_cancel = QPushButton("取消")
        self.btn_scan_cancel.setObjectName("secondaryButton")
        self.btn_scan_cancel.setMinimumWidth(64)
        self.btn_scan_cancel.setFixedHeight(32)
        self.btn_scan_cancel.setVisible(False)
        self.btn_scan_cancel.clicked.connect(self._cancel_scan)
        row.addWidget(self.btn_scan_cancel)
        btn_blf = QPushButton("浏览…")
        btn_blf.setObjectName("secondaryButton")
        btn_blf.setMinimumWidth(76)
        btn_blf.setFixedHeight(32)
        btn_blf.clicked.connect(self._pick_blf)
        self.btn_blf = btn_blf
        row.addWidget(btn_blf)
        v.addWidget(self.input_panel)

        # 中部工作区：左右卡片固定同高，内容在卡片内部滚动。
        workspace = QHBoxLayout()
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(10)

        self.dbc_panel = QFrame()
        self.dbc_panel.setProperty("card", True)
        self.dbc_panel.setObjectName("dbcPanel")
        self.dbc_panel.setFixedHeight(350)
        self.dbc_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        left_col = QVBoxLayout(self.dbc_panel)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(0)

        dbc_header = QHBoxLayout()
        dbc_header.setContentsMargins(12, 7, 7, 7)
        dbc_header.setSpacing(2)
        dbc_title = QLabel("DBC")
        dbc_title.setObjectName("panelTitle")
        dbc_header.addWidget(dbc_title)
        self.btn_add_dbc = SemanticIconButton("+", "positive")
        self.btn_add_dbc.clicked.connect(self._add_dbc)
        dbc_header.addWidget(self.btn_add_dbc)
        dbc_header.addStretch(1)

        self.ccu_combo = CompactCombo()
        self.ccu_combo.setObjectName("compactCombo")
        self.ccu_combo.setFixedWidth(95)
        self.ccu_combo.addItem("ccu3.0")
        self.ccu_combo.addItem("ccu4.0 · 暂未开发")
        self.ccu_combo.model().item(1).setEnabled(False)
        dbc_header.addWidget(self.ccu_combo)

        self.project_combo = CompactCombo()
        self.project_combo.setObjectName("compactCombo")
        self.project_combo.setFixedWidth(98)
        self.project_combo.currentTextChanged.connect(self._select_project)
        dbc_header.addWidget(self.project_combo)
        left_col.addLayout(dbc_header)

        self.dbc_list_widget = DbcListWidget()
        self.dbc_list_widget.setObjectName("dbcList")
        self.dbc_list_widget.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.dbc_list_widget.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.dbc_list_widget.delete_requested.connect(self._remove_selected_dbcs)
        left_col.addWidget(self.dbc_list_widget, 1)

        self.channel_panel = QFrame()
        self.channel_panel.setProperty("card", True)
        self.channel_panel.setObjectName("channelPanel")
        self.channel_panel.setFixedHeight(350)
        self.channel_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        right_col = QVBoxLayout(self.channel_panel)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(0)
        channel_header = QHBoxLayout()
        channel_header.setContentsMargins(12, 7, 11, 7)
        channel_title = QLabel("Channel  ⟷  DBC")
        channel_title.setObjectName("panelTitle")
        channel_header.addWidget(channel_title)
        channel_header.addStretch(1)
        self.channel_count_label = QLabel("0 路")
        self.channel_count_label.setObjectName("channelCount")
        self.channel_count_label.setFixedWidth(48)
        self.channel_count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        channel_header.addWidget(self.channel_count_label)
        right_col.addLayout(channel_header)

        self.table = QTableWidget(0, 3)
        self.table.setObjectName("channelTable")
        self.table.horizontalHeader().hide()
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.table.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        right_col.addWidget(self.table, 1)

        workspace.addWidget(self.dbc_panel, 300)
        workspace.addWidget(self.channel_panel, 340)
        v.addLayout(workspace)

        # 输出路径
        self.output_panel = QFrame()
        self.output_panel.setProperty("card", True)
        self.output_panel.setObjectName("outputPanel")
        self.output_panel.setFixedHeight(42)
        out_row = QHBoxLayout(self.output_panel)
        out_row.setContentsMargins(12, 4, 8, 4)
        out_row.setSpacing(8)
        out_label = QLabel("输出 MDF")
        out_label.setObjectName("sectionLabel")
        out_label.setFixedWidth(64)
        out_row.addWidget(out_label)
        self.out_edit = QLineEdit()
        self.out_edit.setObjectName("pathField")
        self.out_edit.setPlaceholderText("选择 BLF 后自动生成输出路径")
        out_row.addWidget(self.out_edit, 1)
        btn_out = QPushButton("浏览…")
        btn_out.setObjectName("secondaryButton")
        btn_out.setMinimumWidth(76)
        btn_out.setFixedHeight(30)
        btn_out.clicked.connect(self._pick_out)
        self.btn_out = btn_out
        out_row.addWidget(btn_out)
        v.addWidget(self.output_panel)

        # 转换控制区
        self.conversion_panel = QFrame()
        self.conversion_panel.setObjectName("conversionPanel")
        self.conversion_panel.setFixedHeight(44)
        ctrl_row = QHBoxLayout(self.conversion_panel)
        ctrl_row.setContentsMargins(0, 3, 0, 3)
        ctrl_row.setSpacing(10)
        progress_col = QVBoxLayout()
        progress_col.setContentsMargins(2, 0, 0, 0)
        progress_col.setSpacing(2)
        self.stage_label = QLabel("就绪")
        self.stage_label.setObjectName("stageLabel")
        progress_col.addWidget(self.stage_label)
        self.progress = QProgressBar()
        self.progress.setObjectName("conversionProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        progress_col.addWidget(self.progress)
        ctrl_row.addLayout(progress_col, 1)
        # 转换取消：仅转换期间显示（_set_busy 控制）；大文件转换读取是
        # 唯一长耗时步骤，读取/解码检查点都尊重该取消事件
        self.btn_convert_cancel = QPushButton("取消")
        self.btn_convert_cancel.setObjectName("secondaryButton")
        self.btn_convert_cancel.setFixedSize(64, 36)
        self.btn_convert_cancel.setVisible(False)
        self.btn_convert_cancel.clicked.connect(self._cancel_convert)
        ctrl_row.addWidget(self.btn_convert_cancel)
        self.convert_btn = QPushButton("开始转换")
        self.convert_btn.setObjectName("primaryButton")
        self.convert_btn.setFixedSize(126, 36)
        self.convert_btn.setEnabled(False)
        self.convert_btn.clicked.connect(self._start_convert)
        ctrl_row.addWidget(self.convert_btn)
        v.addWidget(self.conversion_panel)

        # 主窗口只保留紧凑日志条；完整日志（耗时 + 摘要）在对话框内展示。
        self.summary_bar = QFrame()
        self.summary_bar.setObjectName("summaryBar")
        self.summary_bar.setMinimumHeight(34)
        self.summary_bar.setMaximumHeight(34)
        summary_row = QHBoxLayout(self.summary_bar)
        summary_row.setContentsMargins(10, 0, 7, 0)
        summary_row.setSpacing(8)
        summary_title = QLabel("日志")
        summary_title.setObjectName("summaryTitle")
        summary_row.addWidget(summary_title)
        self.summary_status = QLabel("尚未转换")
        self.summary_status.setObjectName("summaryStatus")
        summary_row.addWidget(self.summary_status)
        summary_row.addStretch(1)
        self.summary_button = QPushButton("查看日志")
        self.summary_button.setObjectName("linkButton")
        self.summary_button.setEnabled(False)
        self.summary_button.clicked.connect(self._show_log)
        summary_row.addWidget(self.summary_button)
        v.addWidget(self.summary_bar)

        # 兼容既有外部引用；不进入主布局，因此不会再抬高窗口。
        self.summary = QPlainTextEdit(central)
        self.summary.setReadOnly(True)
        self.summary.hide()
        self.summary_text = ""
        self.summary_dialog = SummaryDialog(self)

        # 项目下拉：占位首项禁用，列表 = dbc_ccu3.0 下含 DBC 的项目文件夹
        self.project_combo.addItem(PROJECT_PLACEHOLDER)
        self.project_combo.model().item(0).setEnabled(False)
        for name in project_loader.list_projects(CCU3_ROOT):
            self.project_combo.addItem(name)
        self._apply_column_widths()
        self._fit_table_height()
        self._fit_window_height()

    # ---- 文件选择 ----
    def eventFilter(self, obj, event):
        """BLF 文件行的拖拽导入：文件夹或多条目拖入标签/路径框 → 解析 → 勾选 → 扫描。

        「浏览…」手动选择功能不变；结构性不可导入的拖入（非 .blf 且非文件夹）
        整行拒绝，且事件被消费、不落到 QLineEdit 默认的文本拖放。解析/扫描/
        转换进行中拒绝拖入（与浏览按钮禁用一致，避免「接受却无动作」的困惑）。
        """
        if obj in (self.blf_label, self.blf_edit) and event.type() in (
                QEvent.Type.DragEnter, QEvent.Type.DragMove,
                QEvent.Type.Drop):
            paths = [] if self._input_busy() else _droppable_paths(event)
            if paths and event.type() == QEvent.Type.Drop:
                self._start_import(paths)
            if paths:
                event.acceptProposedAction()
            else:
                event.ignore()
            return True  # 消费拖拽事件，控件默认处理不参与
        return super().eventFilter(obj, event)

    def _input_busy(self) -> bool:
        """输入侧忙碌：解析/扫描/转换任一在跑（拖入与「浏览…」同步拒绝）。"""
        return any(thread is not None and thread.isRunning() for thread in (
            self.resolve_thread, self.scan_thread, self.worker_thread))

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

    def _start_import(self, paths: list[str]):
        """一次拖入的统一入口：单个散 .blf 走今天的单文件路径（逐字不变），
        文件夹/多条目先解析出候选，再按候选数决定是否弹勾选列表。"""
        if len(paths) == 1 and Path(paths[0]).is_file():
            self._load_blf(paths[0])
            return
        self._start_resolve(paths)

    def _load_blf(self, path: str):
        """加载单个 BLF（异步）：后台线程探测通道 → 刷新通道表与默认输出路径。

        大文件（如 86MB/565 万帧）全文件解析实测耗时 41s，若在主线程
        同步执行窗口会冻结成「未响应」；改为 worker 线程（probe_channels
        对象头级轻量行走，5-20 倍更快）+ 字节级真实进度 + 取消按钮，
        扫描期间界面保持响应，进度条可见推进。
        """
        if self._input_busy():
            return  # 解析/扫描/转换进行中不接受新文件
        self.scan_cancel = threading.Event()  # 每次加载重建（取消即作废本次尝试）
        self._set_scan_busy(True)
        # 单个 .blf 不经解析（没有目录要展开）：就地取候选，路径规则仍归 core
        self._begin_scan([source_resolver.blf_candidate(Path(path))])

    def _start_resolve(self, paths: list[str]):
        """解析拖入的来源（后台线程）→ _on_resolve_done 拿到候选清单。

        进度条转不确定态：解析的进度刻度是「已发现候选数」，没有分母
        （见 core/source_resolver.py）；输入行先显示来源名，解析完再定论。
        """
        self.scan_cancel = threading.Event()
        self._set_scan_busy(True)
        self.load_progress.setRange(0, 0)
        self.blf_edit.setText(_source_label(paths))
        self.blf_edit.setToolTip("\n".join(paths))
        self.resolve_thread = QThread()
        self.resolve_worker = ResolveWorker(paths, self.scan_cancel)
        self.resolve_worker.moveToThread(self.resolve_thread)
        self.resolve_thread.started.connect(self.resolve_worker.run)
        self.resolve_worker.progress.connect(self._on_resolve_progress)
        self.resolve_worker.done.connect(self._on_resolve_done)
        self.resolve_worker.cancelled.connect(self._on_resolve_cancelled)
        self.resolve_worker.error.connect(self._on_resolve_error)
        self.resolve_thread.start()

    def _begin_scan(self, candidates: list[Candidate], source: str = ""):
        """扫描这批候选的通道（多文件取并集）→ _on_scan_done 提交批次。

        candidates 即本次扫描的输入（pending）：来源解析的产出，或单文件
        入口就地构造的单个候选。source 是「输入 BLF」行要显示的来源名
        （批量才有意义；单文件行显示完整路径，故为空）。
        """
        self.pending = list(candidates)
        self.pending_source = source
        self._load_start = time.perf_counter()  # 耗时计时（_on_scan_done 记日志）
        self.load_progress.setVisible(True)
        self.load_progress.setRange(0, 100)
        self.load_progress.setValue(0)
        self.scan_thread = QThread()
        self.scan_worker = BlfScanWorker([c.blf for c in candidates],
                                        self.scan_cancel)
        self.scan_worker.moveToThread(self.scan_thread)
        self.scan_thread.started.connect(self.scan_worker.run)
        self.scan_worker.progress.connect(self._on_scan_progress)
        self.scan_worker.done.connect(self._on_scan_done)
        self.scan_worker.cancelled.connect(self._on_scan_cancelled)
        self.scan_worker.error.connect(self._on_scan_error)
        self.scan_thread.start()

    def _cancel_scan(self):
        """「取消」按钮：置位取消事件（worker 在 1024 对象内抛 ConversionCancelled）。

        解析与扫描共用本次导入的取消事件，故这一个按钮同时覆盖两个阶段。
        """
        self.scan_cancel.set()
        self.btn_scan_cancel.setEnabled(False)

    def _set_scan_busy(self, busy: bool):
        """解析/扫描期间禁用文件选择与转换；转换按钮另需已就位的输入。"""
        self.btn_blf.setEnabled(not busy)
        self.convert_btn.setEnabled(not busy and self._can_convert())
        self.load_progress.setVisible(busy)
        self.btn_scan_cancel.setVisible(busy)
        self.btn_scan_cancel.setEnabled(busy)

    @property
    def _is_batch(self) -> bool:
        """本批是否涉及多个文件（已提交批次的分岔判据，阈值只此一处）。

        批量下产物落位由解析期算定、输出框只读；单文件保持今天「唯一可
        手改输入框」的地位（手改值优先）——凡按这条分岔的（输出框形态、
        可用性判定、转换入口）都读这个属性。导入阶段另有两处「1 个」判断
        （_choose_import_selection 里的候选数与勾选数），那问的是「还要不要
        弹列表/提示」，与已提交批次是两件事，故不复用本属性。
        """
        return len(self.batch) > 1

    def _can_convert(self) -> bool:
        """可转换：输入已就位（单个 BLF 或已扫描的批次）且输出非空。

        批量的输出由解析期算定、不参与判定（输出框只读）；单文件沿用
        今天「路径 + 输出框非空」的判定。
        """
        if self._is_batch:
            return True
        return bool(self.blf_path and self.out_edit.text().strip())

    @Slot(float)
    def _on_scan_progress(self, percent: float):
        self.load_progress.setValue(int(percent))

    @Slot(int)
    def _on_resolve_progress(self, count: int):
        """解析期的状态文案：遍历前不知总数，故报「已发现 N 个」（不是百分比）。

        进度条此时是不确定态（setRange(0, 0)）——两种刻度各归各位，界面
        不假装知道分母。
        """
        self.stage_label.setText(f"正在解析来源…已发现 {count} 个 .blf")

    @Slot(object)
    def _on_resolve_done(self, candidates: list[Candidate]):
        """候选清单 → 勾选与共用配置提示（> 1 个时）→ 扫描选中集。

        候选只有 1 个时不弹列表、直接进入；一个都没有是「拖入的文件夹里
        没有 .blf」——明确告知并保持原状（不是静默无反应）。
        """
        source = self.resolve_worker.source_label
        self._finish_resolve()
        if not candidates:
            self._end_import()
            QMessageBox.warning(self, "提示", "未找到可导入的 .blf 文件")
            return
        chosen = self._choose_import_selection(candidates)
        if chosen is None:      # 勾选列表上取消 = 放弃本次导入
            self._end_import()
            self._log("输入BLF 已取消", status="已取消")
            return
        self._begin_scan(chosen, source)

    def _choose_import_selection(
            self, candidates: list[Candidate]) -> list[Candidate] | None:
        """勾选 + 共用配置提示的接力：真正参与批量的清单，取消返回 None。

        候选只有 1 个时不弹列表；实际参与批量的文件 > 1 个时才弹共用配置
        提示。「返回勾选列表」带上次的勾选重开列表（反悔代价为零：此时仍在
        导入阶段，平台/项目/绑定一个都没动过——它们在扫描之后才配）。
        """
        if len(candidates) == 1:
            return list(candidates)
        checked = None
        while True:
            chosen = self._choose_candidates(candidates, checked=checked)
            if chosen is None or len(chosen) <= 1:
                return chosen
            if self._confirm_shared_config(len(chosen)):
                return chosen
            checked = chosen

    @Slot()
    def _on_resolve_cancelled(self):
        self._end_import()
        self._log("输入BLF 已取消", status="输入BLF 已取消")

    @Slot(str)
    def _on_resolve_error(self, msg: str):
        self._end_import()
        self._log(f"输入BLF 读取失败: {msg}", status="输入BLF 读取失败")
        QMessageBox.critical(self, "BLF 读取失败", msg)

    def _choose_candidates(self, candidates: list[Candidate],
                           checked: list[Candidate] | None = None,
                           ) -> list[Candidate] | None:
        """弹候选勾选列表（可直接调用的接缝）；取消返回 None。

        checked = 上一轮的勾选（「返回勾选列表」重开时恢复，用户不必重勾），
        None = 默认全选。
        """
        dialog = CandidateDialog(candidates, self, checked=checked)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.checked_candidates()

    def _confirm_shared_config(self, count: int) -> bool:
        """共用配置提示（可直接调用的接缝）：继续 → True，返回勾选列表 → False。

        实际参与批量的文件 > 1 个时才弹：这一批只配一次平台（CCU 版本）/项目/
        CAN-DBC 匹配、切换会同时作用于全部文件；某个文件中不存在的通道不导出，
        该文件的产物相应少几组信号（正常情况，不是错误）。没点「继续」一律
        按返回处理——提示期未触碰任何配置，回去的代价为零。
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("共用一套配置")
        box.setText(f"本批 {count} 个文件将共用同一套平台 / 项目 / CAN-DBC 匹配。")
        box.setInformativeText(
            "这一套配置只配一次，切换会同时作用于全部文件。\n"
            "某个文件中不存在的通道不会被导出，该文件的输出会相应少几组信号。")
        proceed = box.addButton("继续", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("返回勾选列表", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(proceed)
        box.exec()
        return box.clickedButton() is proceed

    @Slot(object)
    def _on_scan_done(self, channels: list):
        pending = self.pending
        source = self.pending_source  # _finish_scan 会清掉本次扫描的输入标识
        self._finish_scan()
        if not channels:
            # 没有可用通道 = 本次导入无果：界面回到已就位状态（不提交批次）
            self._sync_blf_row()
            QMessageBox.warning(self, "提示", "文件中未找到有效报文数据")
            return
        self.batch = pending
        self.blf_channels = channels
        self._rebuild_channel_table(channels, prev=self._collect_prev())
        if self._is_batch:
            # 批量：产物落位由解析期算定，输出行只陈述「几个产物、按来源落位」
            self.blf_path = None
            self.batch_source = source
            self.out_edit.setText(f"{len(pending)} 个输出文件 · 按来源落位")
            self.out_edit.setToolTip(
                "\n".join(str(candidate.output) for candidate in pending))
        else:
            # 单文件：扫描目标即输入路径（无 pending 时为测试/工具直调）
            self.blf_path = self.scan_worker.path
            self.out_edit.setToolTip("")
            if pending:
                self.out_edit.setText(str(pending[0].output))
            else:
                self._set_default_output()
        self._sync_blf_row()
        self._sync_output_mode()
        self.convert_btn.setEnabled(True)
        # 加载耗时记入日志（测试直调 _on_scan_done 时无 _load_start → 不记耗时）
        path = f"{len(pending)} 个文件" if self._is_batch \
            else self.scan_worker.path
        t0 = getattr(self, "_load_start", None)
        if t0 is not None:
            elapsed = time.perf_counter() - t0
            self._log(f"输入BLF 完成: {path}，耗时 {elapsed:.2f} s",
                      status=f"输入BLF 完成: {elapsed:.2f} s")
        else:
            self._log(f"输入BLF 完成: {path}", status="输入BLF 完成")

    @Slot(str, str)
    def _on_scan_error(self, path: str, msg: str):
        self._end_import()
        self._log(f"输入BLF 读取失败: {msg}", status="输入BLF 读取失败")
        QMessageBox.critical(self, "BLF 读取失败", f"{path}\n{msg}")

    @Slot()
    def _on_scan_cancelled(self):
        """取消：复位界面但**不应用**结果（blf_path 保持原值——
        无文件保持 None，替换文件保持旧文件）。"""
        self._end_import()
        self._log("输入BLF 已取消", status="输入BLF 已取消")

    def _end_import(self):
        """本次导入的失败/取消收口：停解析与扫描线程、输入行回到当前已就位
        的输入（本批次或空），再由调用方记日志或弹窗。

        解析与扫描是接力的一段，任一环节的收尾都是这三步——只写一处，
        免得漏掉一环（漏了就把界面留在忙碌态）。成功路径不走这里：它由
        _on_scan_done 提交批次。"""
        self._finish_resolve()
        self._finish_scan()
        self._sync_blf_row()

    def _finish_resolve(self):
        thread = self.resolve_thread
        if thread is not None:
            thread.quit()
            thread.wait()
            self.resolve_thread = None
            self.stage_label.setText("就绪")  # 解析期占用的状态文案归还

    def _finish_scan(self):
        thread = self.scan_thread
        if thread is not None:
            thread.quit()
            thread.wait()
            self.scan_thread = None
        self.pending = []       # 本次扫描的输入已定论，不再留用
        self.pending_source = ""
        self.load_progress.setRange(0, 100)     # 解析期的不确定态在此复原
        self.load_progress.setValue(0)
        self._set_scan_busy(False)  # 隐藏 load_progress、恢复按钮

    def _sync_blf_row(self):
        """「输入 BLF」行 = 当前已就位的输入：单文件路径 / 批量来源与文件数 / 空。"""
        if self.blf_path:
            self.blf_edit.setText(self.blf_path)
            self.blf_edit.setToolTip("")
        elif self.batch:
            self.blf_edit.setText(
                f"{len(self.batch)} 个文件 · 来自 {self.batch_source}")
            self.blf_edit.setToolTip(
                "\n".join(str(candidate.blf) for candidate in self.batch))
        else:
            self.blf_edit.setText("")
            self.blf_edit.setToolTip("")

    def _sync_output_mode(self):
        """批量的产物落位由解析期算定：输出框只读、「浏览…」禁用。

        忙碌态由 _set_busy 全量禁用；本方法只管静止态——否则转换结束时的
        解锁会把批量的只读输出又放开。
        """
        self.out_edit.setReadOnly(self._is_batch)
        self.btn_out.setEnabled(not self._is_batch)

    def _set_default_output(self):
        """默认输出路径：与 BLF 同目录，文件名追加 _t（run001.blf
        → run001_t.mdf）。

        每次加载/重选 BLF 后输出自动跟随（_on_scan_done 调用）；用户手动
        改过的输出路径也会在下次选择 BLF 时被新 BLF 的路径覆盖。这里是
        「散 .blf 就地落位」这一条规则——文件夹/压缩包来源按来源类型改落
        同级镜像输出树（见 CONTEXT.md「镜像输出树」）。
        """
        source = Path(self.blf_path)
        self.out_edit.setText(str(source.with_name(f"{source.stem}_t.mdf")))

    def _rebuild_channel_table(self, channels: list[int],
                               prev: dict[int, str | None] | None = None):
        """重建通道表：绑定决策委托 decide_bindings（gui/binding.py 纯函数）。

        选择优先级：prev（上次选择，含显式「不绑定」）→ auto 建议 → 不绑定；
        行集合 = BLF 通道 ∪ 映射通道（映射是完整规格，日志中无数据的映射
        通道也显示，状态「无数据」）。选项目时传 prev=None（重新套用自动
        匹配）；添加/移除 DBC 与 BLF 加载时传 _collect_prev()（保留用户选择）。
        """
        rows = decide_bindings(channels, self.auto_bind, prev,
                               [d.path for d in self.dbc_list])
        self.table.setRowCount(0)
        for row in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            name = f"CAN {row.channel}"
            channel_item = QTableWidgetItem(name)
            channel_item.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(r, 0, channel_item)
            self.table.setItem(r, 2, QTableWidgetItem(row.state))
            combo = DbcCombo()
            combo.addItem(UNBOUND)
            for dbc in self.dbc_list:
                combo.addItem(dbc.display_name, userData=dbc)
            if row.binding is not None:
                for i in range(combo.count()):
                    data = combo.itemData(i)
                    if data is not None and data.path == row.binding:
                        combo.setCurrentIndex(i)
                        break
            self.table.setCellWidget(r, 1, combo)
            combo.currentIndexChanged.connect(
                lambda _idx, r=r: self._update_status(r)
            )
            if row.state == STATE_NO_DATA:
                # 无数据行保持「无数据」；用户手动改下拉后由信号接管为绑定态
                self._set_status_item(r, STATE_NO_DATA)
            else:
                self._update_status(r)
        self.channel_count_label.setText(f"{len(rows)} 路")
        self._apply_column_widths()

    def _collect_prev(self) -> dict[int, str | None]:
        """收集当前表格的用户选择 {int 通道号: DBC 路径 | None}。

        None = 用户明确选择「不绑定」（重建时压制 auto 建议，与现状
        keep_prev 收集 "不绑定" 语义一致）；通道号从行显示名解析
        （"CAN 1" → 1）——prev 键与 auto_bindings 同为 int，双键型
        契约（auto int / prev "CANn"）在此归一。
        """
        prev = {}
        for r in range(self.table.rowCount()):
            name, _, path, _ = self.binding_row(r)
            prev[int(name.split()[-1])] = path
        return prev

    def _apply_column_widths(self):
        """紧凑通道表列宽：状态列仅保留结果文字所需空间。"""
        hdr = self.table.horizontalHeader()
        hdr.setStretchLastSection(False)
        for index in range(3):
            hdr.setSectionResizeMode(index, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(0, 66)
        hdr.resizeSection(1, 209)
        hdr.resizeSection(2, 58)

    def _fit_table_height(self):
        """卡片负责固定高度，表格行高保持易读并允许内部滚动。"""
        vh = self.table.verticalHeader()
        vh.setDefaultSectionSize(38)
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)

    def _fit_window_height(self):
        """保留旧调用点；新窗口使用批准的固定默认尺寸。"""
        self.resize(max(self.width(), WINDOW_WIDTH),
                    max(self.height(), WINDOW_HEIGHT))

    def _fit_table_width(self):
        """兼容旧调用；新表格宽度由右侧卡片布局管理。"""

    def _update_status(self, row: int):
        """用户改下拉后的状态接管：以当前选择为准（derive_state(True, _)）。

        无数据行的「无数据」只在初始渲染由 decide_bindings 设定；用户
        动过下拉后状态由这里接管为绑定态/不导出，不回到「无数据」（Q9）。
        """
        combo = self.table.cellWidget(row, 1)
        data = combo.currentData()
        self._set_status_item(
            row, derive_state(True, data.path if data is not None else None))

    def binding_row(self, row: int) -> tuple[str, str, str | None, str]:
        """绑定表第 row 行稳定读取面（M7 收口）：(通道名, 下拉显示名,
        下拉 DBC 路径 | None, 状态文字)，None 即「不绑定」。测试与内部
        调用经此读表格状态，不穿透 table/cellWidget 对象图。"""
        combo = self.table.cellWidget(row, 1)
        data = combo.currentData()
        return (self.table.item(row, 0).text(),
                combo.currentText(),
                data.path if data is not None else None,
                self.table.item(row, 2).text())

    def set_binding_selection(self, row: int, display_name: str) -> None:
        """绑定表第 row 行下拉驱动面（M7 收口）：等价于用户改下拉，
        含状态接管（derive_state 以当前选择为准）。"""
        combo = self.table.cellWidget(row, 1)
        combo.setCurrentText(display_name)
        self._update_status(row)

    def _set_status_item(self, row: int, text: str):
        """统一状态文字、颜色和列内对齐。"""
        item = self.table.item(row, 2)
        item.setText(text)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        colors = {
            STATE_BOUND: POSITIVE_COLOR,
            STATE_NOT_EXPORTED: "#a56400",
            STATE_NO_DATA: "#8d9096",
        }
        item.setForeground(QBrush(QColor(colors[text])))

    def _refresh_dbc_items(self):
        """用双行路径组件重建 DBC 列表，并让每个删除按钮绑定自身行。"""
        self.dbc_list_widget.clear()
        for row, dbc in enumerate(self.dbc_list):
            item = QListWidgetItem(self.dbc_list_widget)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            widget = DbcListItemWidget(
                dbc.path, lambda _checked=False, r=row: self._remove_dbc_at(r)
            )
            item.setSizeHint(QSize(0, widget.sizeHint().height()))
            self.dbc_list_widget.setItemWidget(item, widget)

    def _remove_dbc_at(self, row: int):
        """移除指定 DBC，并保留其余仍有效的用户绑定。"""
        if not 0 <= row < len(self.dbc_list):
            return
        self.dbc_list.pop(row)
        self._refresh_dbc_items()
        self._rebuild_channel_table(self.blf_channels,
                                    prev=self._collect_prev())

    def _remove_selected_dbcs(self):
        """从面板移除所有选中 DBC；不影响磁盘上的原始文件。"""
        rows = sorted(
            {
                self.dbc_list_widget.row(item)
                for item in self.dbc_list_widget.selectedItems()
            },
            reverse=True,
        )
        if not rows:
            return
        for row in rows:
            if 0 <= row < len(self.dbc_list):
                self.dbc_list.pop(row)
        self._refresh_dbc_items()
        self._rebuild_channel_table(self.blf_channels,
                                    prev=self._collect_prev())

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
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, "DBC 解析失败", f"{p}\n{e}")
        self._refresh_dbc_items()
        self._rebuild_channel_table(self.blf_channels,
                                    prev=self._collect_prev())

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
        self._refresh_dbc_items()
        self.auto_bind = project_loader.auto_bindings(dbcs, self.mapping)
        self._rebuild_channel_table(self.blf_channels, prev=None)

    def _remove_dbc(self):
        row = self.dbc_list_widget.currentRow()
        self._remove_dbc_at(row)

    def _pick_out(self):
        # 对话框默认打开当前 BLF 所在目录（输出与 BLF 同目录，需求一致）
        start = str(Path(self.blf_path).parent) if self.blf_path else ""
        path, _ = QFileDialog.getSaveFileName(self, "选择输出文件", start,
                                              "MDF 文件 (*.mdf)")
        if path:
            self.out_edit.setText(path)

    # ---- 转换 ----
    def _start_convert(self):
        if self._is_batch:
            self._start_batch_convert()
            return
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
        bindings = self._collect_bindings()
        self.convert_cancel = threading.Event()  # 每次转换重建（取消即作废本次）
        self._set_busy(True)
        self.progress.setValue(0)
        self.summary_status.setText("转换中…")
        self.worker_thread = QThread()
        self.worker = ConvertWorker(self.blf_path, bindings, out,
                                    self.convert_cancel)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.worker.cancelled.connect(self._on_convert_cancelled)
        self.worker.error.connect(self._on_error)
        self.worker_thread.start()

    def _start_batch_convert(self):
        """批量转换：输出预检一次问完 → 整批交给 core.batch.run_batch。

        预检把「已存在」的输出合成**一次**三选一（覆盖全部 / 跳过已存在 /
        取消），不逐文件弹窗；「跳过已存在」只把不冲突的子集交给 core，跳过的
        记在 self.skipped 上供收尾如实报数（它们没被转换，报数不得算作成功）。
        产物落位用解析期算定的输出路径，界面的输出框不参与。
        """
        existing = [c for c in self.batch if c.output.exists()]
        self.skipped = []               # 本次批量重新算定（取消也归零）
        if existing:
            choice = self._choose_overwrite(existing)
            if choice == PRECHECK_CANCEL:
                return
            if choice == PRECHECK_SKIP_EXISTING:
                self.skipped = existing
        todo = [c for c in self.batch if c not in self.skipped]
        bindings = self._collect_bindings()
        self.convert_cancel = threading.Event()  # 每次转换重建（取消即作废本次）
        self._set_busy(True)
        self.progress.setValue(0)
        self.summary_status.setText("批量转换中…")
        self.worker_thread = QThread()
        self.worker = BatchConvertWorker(todo, bindings, self.convert_cancel)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_batch_done)
        self.worker.cancelled.connect(self._on_batch_cancelled)
        self.worker.error.connect(self._on_error)
        self.worker_thread.start()

    def _choose_overwrite(self, existing: list[Candidate]) -> str:
        """批量开始前的输出预检（可直接调用的接缝）：三选一，见模块常量。

        与单文件那次「输出文件已存在」询问对齐——整批只问一次而不是 N 个弹窗，
        底线是不静默覆盖。没点前两个按钮（含直接关掉）一律按取消处理。
        """
        listed = "\n".join(str(c.output) for c in existing[:5])
        if len(existing) > 5:
            listed += f"\n… 共 {len(existing)} 个"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("覆盖确认")
        box.setText(f"{len(existing)} 个输出文件已存在：\n{listed}")
        box.setInformativeText(
            "覆盖全部会替换这些文件；跳过已存在只转换其余文件，"
            "已有产物原样保留。")
        overwrite = box.addButton(PRECHECK_OVERWRITE_ALL,
                                  QMessageBox.ButtonRole.AcceptRole)
        skip = box.addButton(PRECHECK_SKIP_EXISTING,
                             QMessageBox.ButtonRole.ActionRole)
        box.addButton(PRECHECK_CANCEL, QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(overwrite)
        box.exec()
        clicked = box.clickedButton()
        if clicked is overwrite:
            return PRECHECK_OVERWRITE_ALL
        if clicked is skip:
            return PRECHECK_SKIP_EXISTING
        return PRECHECK_CANCEL

    def _collect_bindings(self) -> dict[int, DbcDef | None]:
        """绑定表 → {通道: DbcDef | None}（零查找、零反查表）。

        userData = DbcDef；UNBOUND 项无 userData → None。
        """
        bindings = {}
        for r in range(self.table.rowCount()):
            ch = int(self.table.item(r, 0).text().split()[-1])
            bindings[ch] = self.table.cellWidget(r, 1).currentData()
        return bindings

    def _cancel_convert(self):
        """「取消」按钮：置位取消事件（worker 在 1024 帧/每桶内抛
        ConversionCancelled；写 MDF 期间点取消 → 清理已写输出后抛出）。"""
        self.convert_cancel.set()
        self.btn_convert_cancel.setEnabled(False)

    @Slot()
    def _on_convert_cancelled(self):
        """取消：复位界面（进度归零、按钮恢复），日志记「已取消」。"""
        self._finish()
        self._log("转换已取消", status="已取消")

    def _set_busy(self, busy: bool):
        for widget in (
            self.btn_blf,
            self.btn_add_dbc,
            self.ccu_combo,
            self.project_combo,
            self.dbc_list_widget,
            self.table,
            self.btn_out,
            self.out_edit,
        ):
            widget.setEnabled(not busy)
        self.convert_btn.setEnabled(not busy and self._can_convert())
        # 解锁后回到本批次的静止态：批量下输出框仍只读、「浏览…」仍禁用
        if not busy:
            self._sync_output_mode()
        self.btn_convert_cancel.setVisible(busy)
        self.btn_convert_cancel.setEnabled(busy)

    @Slot(str, float)
    def _on_progress(self, stage: str, percent: float):
        self.stage_label.setText(stage)
        self.progress.setValue(int(percent))

    @Slot(object)
    def _on_batch_done(self, result: BatchResult):
        """批次结局：全成功弹「N 个 blf 文件全部转换完成」，部分失败报数并点名。

        逐文件结局（成功记耗时、失败记原因、预检跳过的记跳过）按**批次顺序**进
        日志——批量下这是唯一能还原「哪个文件怎么了」的地方，顺序即用户看到
        的清单顺序。跳过的文件属于本批，故报数的分母含它们（不把「跳过」说成
        转换成功）。失败后**不**回滚已完成的产物（core 契约：单个文件失败不
        中断批次）。
        """
        self._finish()
        skipped_blfs = {candidate.blf for candidate in self.skipped}
        outcome_of = {outcome.candidate.blf: outcome
                      for outcome in result.outcomes}
        lines = []
        for candidate in self.batch:        # 清单顺序；跳过的也留在原位
            if candidate.blf in skipped_blfs:
                lines.append(f"{candidate.display}: 跳过（输出已存在）")
                continue
            outcome = outcome_of[candidate.blf]
            if outcome.ok:
                lines.append(f"{candidate.display}: 完成 (总耗时 "
                             f"{_total_seconds(outcome.result):.1f} s)")
            else:
                lines.append(f"{candidate.display}: 失败 — {outcome.error}")
        converted = len(result.outcomes)
        skipped = len(self.skipped)
        total = converted + skipped         # 本批文件数 = 转换的 + 预检跳过的
        skip_clause = f"跳过 {skipped} 个已存在" if skipped else ""
        skip_suffix = f" · {skip_clause}" if skip_clause else ""
        elapsed = sum(_total_seconds(o.result) for o in result.outcomes if o.ok)
        if result.all_succeeded:
            status = (f"批量转换完成 · {converted} 个文件{skip_suffix}"
                      f" · 总耗时 {elapsed:.1f} s")
            notice = (f"{total} 个 blf 文件转换成功 {converted} 个，{skip_clause}"
                      if skipped else f"{total} 个 blf 文件全部转换完成")
        else:
            failed = result.failures
            succeeded = converted - len(failed)
            status = (f"批量转换完成 · {succeeded} 成功 / {len(failed)} 失败"
                      f"{skip_suffix} · 总耗时 {elapsed:.1f} s")
            notice = (f"{total} 个 blf 文件转换成功 {succeeded} 个，失败 "
                      f"{len(failed)} 个"
                      + (f"，{skip_clause}" if skipped else "")
                      + "\n失败文件：\n"
                      + "\n".join(f"{outcome.candidate.display} — "
                                  f"{outcome.error}" for outcome in failed))
        self._log("批量转换完成\n" + "\n".join(lines), status=status)
        box = QMessageBox(QMessageBox.Icon.Information, "提示", notice,
                          QMessageBox.StandardButton.Ok, self)
        box.button(QMessageBox.StandardButton.Ok).setText("确定")
        box.exec()

    @Slot()
    def _on_batch_cancelled(self, outcomes: list):
        """批量取消：已完成文件的产物保留（core 契约），界面据实说明。

        不写「已完成的 X / N 个产物保留」以外的话——取消不是失败，界面
        不复述 core 的清理细节（见 CONTEXT.md「取消信号」）。
        """
        self._finish()
        kept = [outcome for outcome in outcomes if outcome.ok]
        text = "转换已取消"
        if kept:
            text += f"（已完成的 {len(kept)} 个文件产物保留）"
        self._log(text, status="已取消")

    @Slot(object)
    def _on_done(self, result: ConversionResult):
        self._finish()
        lines = [f"{label}: {t:.2f} s" for label, t in result.timings]
        body = "转换完成"
        if result.warnings:
            body += "\n" + "\n".join(result.warnings)
        if lines:
            body += "\n" + "\n".join(lines)
        detail = self._format_summary(result)
        if detail:
            body += "\n转换摘要:\n" + detail
        total = _total_seconds(result)
        warning_count = len(result.warnings) + \
            sum(bool(s.warning) for s in result.summaries)
        status = f"转换完成 · 总耗时 {total:.1f} s"
        if warning_count:
            status += f" · {warning_count} 条警告"
        self._log(body, status=status)
        notice = QMessageBox(
            QMessageBox.Icon.Information,
            "提示",
            "转换完成。",
            QMessageBox.StandardButton.Ok,
            self,
        )
        notice.button(QMessageBox.StandardButton.Ok).setText("确定")
        notice.exec()

    @staticmethod
    def _format_summary(result: ConversionResult) -> str:
        """把转换结果格式化为摘要对话框使用的完整中文文本。"""
        lines = [f"总时长: {result.duration_seconds:.1f} s"]
        for s in result.summaries:
            if s.bound:
                lines.append(
                    f"CAN {s.channel} 已绑定: 解码 {s.decoded_frames} 帧 · "
                    f"{s.signal_count} 个信号 · 未知 {s.unknown_frames} 帧 "
                    f"({s.unknown_ids} 个 ID)"
                )
                if s.warning:
                    lines.append(f"  警告: {s.warning}")
            else:
                # 原始帧导出已从面板移除（固定关闭，与 CANoe 一致）
                lines.append(f"CAN {s.channel} 未绑定: 原始帧导出关闭")
                if s.warning:
                    lines.append(f"  警告: {s.warning}")
        return "\n".join(lines)

    def _show_log(self):
        if not self.summary_text:
            return
        self.summary_dialog.set_summary(self.summary_text)
        self.summary_dialog.exec()

    @Slot(str)
    def _on_error(self, msg: str):
        self._finish()
        self._log(f"转换失败: {msg}", status="转换失败")
        QMessageBox.critical(self, "转换失败", msg)

    def _log(self, text: str, status: str | None = None) -> None:
        """追加一条日志（带墙钟时间戳），同步底条状态与隐藏缓冲。

        只在终态槽调用（扫描/转换的 done/cancelled/error）——隐藏的
        summary 缓冲被 frozen_gui_probe 当作「转换完成」标志，转换完成
        前不得写入（见 tools/frozen_gui_probe.py）。
        """
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        entry = f"[{ts}] {text}"
        self.summary_text = (self.summary_text + "\n" + entry
                             if self.summary_text else entry)
        self.summary.setPlainText(self.summary_text)
        self.summary_status.setText(status if status is not None else text)
        self.summary_button.setEnabled(True)

    def _finish(self):
        self.worker_thread.quit()
        self.worker_thread.wait()
        self.worker_thread = None
        self._set_busy(False)
        self.progress.setValue(0)
        self.stage_label.setText("就绪")

    def _sync_window_shape(self):
        root = self.centralWidget()
        maximized = self.isMaximized()
        if root is not None and root.property("shellMaximized") != maximized:
            root.setProperty("shellMaximized", maximized)
            root.style().unpolish(root)
            root.style().polish(root)
        sync_rounded_window(self, self._native_rounding)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._visual_effects_applied:
            self._visual_effects_applied = True
            self._native_rounding = apply_light_glass(self)
            root = self.centralWidget()
            root.setProperty("nativeGlass", self._native_rounding)
            root.style().unpolish(root)
            root.style().polish(root)
        self._sync_window_shape()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_native_rounding"):
            self._sync_window_shape()

    def changeEvent(self, event):
        super().changeEvent(event)
        if (
            event.type() == QEvent.Type.WindowStateChange
            and hasattr(self, "_native_rounding")
        ):
            self._sync_window_shape()

    def closeEvent(self, event):
        thread = self.resolve_thread
        if thread is not None and thread.isRunning():
            # 同上：先置位取消事件，解析在目录/候选检查点快速返回
            self.scan_cancel.set()
            thread.quit()
            thread.wait()
            self.resolve_thread = None
        thread = self.scan_thread
        if thread is not None and thread.isRunning():
            # 先置位取消事件：probe 在 1024 对象内抛 ConversionCancelled 快速返回，
            # wait() 不再阻塞到全文件扫完
            self.scan_cancel.set()
            thread.quit()
            thread.wait()
            self.scan_thread = None
        thread = self.worker_thread
        if thread is not None and thread.isRunning():
            ans = QMessageBox.question(
                self, "确认关闭",
                "转换仍在进行中，关闭窗口将等待其完成后丢弃结果。\n确定要关闭吗？")
            if ans != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            # 先置位取消事件：convert 在 1024 帧/每桶检查点快速返回，
            # wait() 不再阻塞到转换完成；写 MDF 期间取消会清理已写输出。
            self.convert_cancel.set()
            thread.quit()
            thread.wait()
            self.worker_thread = None
        event.accept()
