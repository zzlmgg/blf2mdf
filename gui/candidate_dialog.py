"""候选勾选对话框：一次拖入（文件夹 / 多个 .blf）后选择本次要转换哪些文件。

一次拖入的候选可能有几十上百个，全转与只转一部分都是常态：默认全选，
逐个可取消，实时显示「已选 N / 共 M」；一个都没勾时不给确定（空批次无意义）。
每项显示相对路径 + 大小（区分同名不同目录、一眼看出大文件），完整路径进 tooltip。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.source_resolver import Candidate


def format_size(size: int) -> str:
    """人类可读的文件大小（候选列表的副信息）。"""
    if size < 1024:
        return f"{size} B"
    for unit in ("KB", "MB"):
        size /= 1024
        if size < 1024:
            return f"{size:.1f} {unit}"
    return f"{size / 1024:.1f} GB"


class CandidateDialog(QDialog):
    """勾选要转换的候选文件（顺序 = 清单顺序，即转换顺序）。

    checked 是上一轮的勾选（共用配置提示上「返回勾选列表」时传入）：返回即
    恢复原样，用户不必重勾；不传则默认全选。
    """

    def __init__(self, candidates: list[Candidate],
                 parent: QWidget | None = None,
                 checked: list[Candidate] | None = None):
        super().__init__(parent)
        self.setObjectName("candidateDialog")
        self.setWindowTitle("选择要转换的文件")
        self.setModal(True)
        self.resize(560, 440)
        self.candidates = list(candidates)
        preselected = None if checked is None else {c.blf for c in checked}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(10)
        self.heading = QLabel("选择要转换的文件")
        self.heading.setObjectName("dialogHeading")
        layout.addWidget(self.heading)
        hint = QLabel("勾选本次要转换的 .blf 文件；通道与 DBC 绑定按勾选结果共同决定。")
        hint.setObjectName("dialogHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("candidateList")
        self.list_widget.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        for candidate in self.candidates:
            item = QListWidgetItem(
                f"{candidate.display}    {format_size(candidate.size)}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(                      # 上次的勾选优先，默认全要
                Qt.CheckState.Checked
                if preselected is None or candidate.blf in preselected
                else Qt.CheckState.Unchecked)
            item.setToolTip(str(candidate.blf))
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.count_label = QLabel()
        self.count_label.setObjectName("dialogHint")
        footer.addWidget(self.count_label)
        footer.addStretch(1)
        self.select_all_button = QPushButton("全选")
        self.select_none_button = QPushButton("全不选")
        self.cancel_button = QPushButton("取消")
        for button in (self.select_all_button, self.select_none_button,
                       self.cancel_button):
            button.setObjectName("secondaryButton")
            button.setFixedHeight(34)
            footer.addWidget(button)
        self.ok_button = QPushButton("确定")
        self.ok_button.setObjectName("primaryButton")
        self.ok_button.setFixedSize(88, 34)
        footer.addWidget(self.ok_button)
        layout.addLayout(footer)

        self.select_all_button.clicked.connect(
            lambda: self._check_all(Qt.CheckState.Checked))
        self.select_none_button.clicked.connect(
            lambda: self._check_all(Qt.CheckState.Unchecked))
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self.accept)
        # itemChanged 在填充之后再接：填充期不触发回调（_sync_count 依赖的
        # 控件此时也才齐备）
        self.list_widget.itemChanged.connect(self._sync_count)
        self._sync_count()

    def checked_candidates(self) -> list[Candidate]:
        """已勾选的候选，顺序 = 清单顺序（批次顺序由清单给定）。"""
        return [candidate for index, candidate in enumerate(self.candidates)
                if self._is_checked(index)]

    def _is_checked(self, index: int) -> bool:
        return (self.list_widget.item(index).checkState()
                == Qt.CheckState.Checked)

    def _check_all(self, state: Qt.CheckState):
        for index in range(self.list_widget.count()):
            self.list_widget.item(index).setCheckState(state)

    def _sync_count(self):
        """计数与确定按钮联动：空批次（0 个）不给确定。"""
        checked = len(self.checked_candidates())
        self.count_label.setText(f"已选 {checked} / 共 {len(self.candidates)}")
        self.ok_button.setEnabled(checked > 0)
