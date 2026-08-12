"""与业务无关的 PySide 展示组件。"""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QDialog,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.resources import application_icon


class AppShell(QWidget):
    """Antialiased main-window shell whose outline stays inside its bounds."""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        maximized = bool(self.property("shellMaximized"))
        inset = 0.625 if maximized else 1.0
        rect = QRectF(
            inset,
            inset,
            self.width() - 2 * inset,
            self.height() - 2 * inset,
        )
        path = QPainterPath()
        radius = 0.0 if maximized else 10.0
        path.addRoundedRect(rect, radius, radius)

        if bool(self.property("nativeGlass")):
            painter.setBrush(QColor(248, 250, 253, 220))
        else:
            background = QLinearGradient(0, rect.top(), 0, rect.bottom())
            background.setColorAt(0, QColor(252, 253, 255, 244))
            background.setColorAt(1, QColor(248, 250, 253, 238))
            painter.setBrush(background)

        border = QPen(QColor("#aeb1b7"), 1.25)
        border.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(border)
        painter.drawPath(path)
        painter.end()
        super().paintEvent(event)


class CompactCombo(QComboBox):
    """使用 Qt 自绘细箭头的紧凑下拉框。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("customChevron", True)
        self.setMinimumHeight(29)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#8d9096" if not self.isEnabled() else "#64676d")
        pen = QPen(color, 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        x = self.width() - 13
        y = self.height() // 2
        painter.drawLine(x - 3, y - 1, x, y + 2)
        painter.drawLine(x, y + 2, x + 3, y - 1)


class ElidedPathLabel(QLabel):
    """Keep the end of a long path visible and retain its full text."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

    def visible_text(self) -> str:
        return self.fontMetrics().elidedText(
            self.text(),
            Qt.TextElideMode.ElideLeft,
            max(0, self.contentsRect().width()),
        )

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setFont(self.font())
        color_role = (
            QPalette.ColorRole.WindowText
            if self.isEnabled()
            else QPalette.ColorRole.PlaceholderText
        )
        painter.setPen(self.palette().color(color_role))
        painter.drawText(self.contentsRect(), self.alignment(), self.visible_text())


class DbcListWidget(QListWidget):
    """DBC list with Windows multi-selection and a Delete action signal."""

    delete_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setSelectionMode(self.SelectionMode.ExtendedSelection)
        self.itemSelectionChanged.connect(self._sync_selection_visuals)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete:
            self.delete_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _sync_selection_visuals(self):
        for row in range(self.count()):
            item = self.item(row)
            widget = self.itemWidget(item)
            if widget is None:
                continue
            widget.setProperty("selected", item.isSelected())
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()


class SemanticIconButton(QPushButton):
    """28px 点击热区内绘制严格居中的 16px 语义圆圈。"""

    def __init__(self, symbol: str, tone: str, parent: QWidget | None = None):
        super().__init__(symbol, parent)
        if symbol not in {"+", "−", "-"}:
            raise ValueError(f"unsupported semantic icon: {symbol}")
        if tone not in {"positive", "negative"}:
            raise ValueError(f"unsupported semantic tone: {tone}")
        self._symbol = "−" if symbol == "-" else symbol
        self.setObjectName("semanticIconButton")
        self.setProperty("tone", tone)
        self.setProperty("ringSize", 16)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("添加 DBC" if tone == "positive" else "移除此 DBC")

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        tone = self.property("tone")
        color = QColor("#207e4b" if tone == "positive" else "#c93834")
        if not self.isEnabled():
            color.setAlpha(90)
        elif self.underMouse():
            fill = QColor(color)
            fill.setAlpha(20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawEllipse(QRectF(5.5, 5.5, 17, 17))

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, 1.2))
        painter.drawEllipse(QRectF(6.5, 6.5, 15, 15))
        symbol_pen = QPen(color, 1.4)
        symbol_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(symbol_pen)
        painter.drawLine(11, 14, 17, 14)
        if self._symbol == "+":
            painter.drawLine(14, 11, 14, 17)


class DbcListItemWidget(QFrame):
    """显示 DBC 文件名、完整路径和逐项移除入口。"""

    remove_requested = Signal()

    def __init__(
        self,
        path: str,
        on_remove: Callable[[], None],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("dbcListItem")
        self.setToolTip(path)
        self.setMinimumHeight(52)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 5, 5)
        layout.setSpacing(6)

        labels = QVBoxLayout()
        labels.setContentsMargins(0, 0, 0, 0)
        labels.setSpacing(1)
        self.file_label = QLabel(Path(path).name)
        self.file_label.setObjectName("dbcFileName")
        self.path_label = ElidedPathLabel(path)
        self.path_label.setObjectName("dbcFullPath")
        for label in (self.file_label, self.path_label):
            label.setToolTip(path)
            label.setMinimumWidth(0)
            label.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
        labels.addWidget(self.file_label)
        labels.addWidget(self.path_label)
        layout.addLayout(labels, 1)

        self.remove_button = SemanticIconButton("−", "negative")
        self.remove_button.clicked.connect(on_remove)
        self.remove_button.clicked.connect(self.remove_requested.emit)
        layout.addWidget(self.remove_button, 0, Qt.AlignmentFlag.AlignVCenter)


class WindowControlButton(QPushButton):
    """Windows 风格的最小化、最大化/还原与关闭按钮。"""

    def __init__(self, kind: str, parent: QWidget | None = None):
        super().__init__(parent)
        if kind not in {"minimize", "maximize", "close"}:
            raise ValueError(f"unsupported window control: {kind}")
        self.kind = kind
        object_names = {
            "minimize": "windowMinimizeButton",
            "maximize": "windowMaximizeButton",
            "close": "windowCloseButton",
        }
        tooltips = {
            "minimize": "最小化",
            "maximize": "最大化 / 还原",
            "close": "关闭",
        }
        self.setObjectName(object_names[kind])
        self.setProperty("controlKind", kind)
        self.setToolTip(tooltips[kind])
        self.setFixedSize(40, 38)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = self.underMouse()
        if hovered or self.isDown():
            if self.kind == "close":
                background = QColor("#c42b1c" if self.isDown() else "#e81123")
            else:
                background = QColor(0, 0, 0, 22 if self.isDown() else 13)
            painter.fillRect(self.rect(), background)

        color = QColor("#ffffff" if self.kind == "close" and hovered else "#1c1d20")
        pen = QPen(color, 1.15)
        pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        cx = self.width() // 2
        cy = self.height() // 2

        if self.kind == "minimize":
            painter.drawLine(cx - 5, cy + 3, cx + 5, cy + 3)
        elif self.kind == "maximize":
            if self.window().isMaximized():
                painter.drawRect(cx - 4, cy - 3, 8, 8)
                painter.drawLine(cx - 2, cy - 5, cx + 6, cy - 5)
                painter.drawLine(cx + 6, cy - 5, cx + 6, cy + 3)
            else:
                painter.drawRect(cx - 5, cy - 5, 10, 10)
        else:
            painter.drawLine(cx - 5, cy - 5, cx + 5, cy + 5)
            painter.drawLine(cx + 5, cy - 5, cx - 5, cy + 5)


class TitleBar(QWidget):
    """Windows 风窗口标题栏；标题按整个标题栏几何中心定位。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(38)
        self._drag_offset: QPoint | None = None

        self.title_label = QLabel("BLF → MDF", self)
        self.title_label.setObjectName("windowTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )

        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("windowTitleIcon")
        self.icon_label.setFixedSize(24, 24)
        self.icon_label.setPixmap(application_icon().pixmap(QSize(24, 24)))
        self.icon_label.setScaledContents(False)
        self.icon_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 0, 0, 0)
        layout.setSpacing(0)
        layout.addStretch(1)
        self.minimize_button = WindowControlButton("minimize")
        self.maximize_button = WindowControlButton("maximize")
        self.close_button = WindowControlButton("close")
        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)

        self.close_button.clicked.connect(lambda: self.window().close())
        self.minimize_button.clicked.connect(lambda: self.window().showMinimized())
        self.maximize_button.clicked.connect(self._toggle_maximized)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.title_label.setGeometry(self.rect())
        self.icon_label.move(14, (self.height() - self.icon_label.height()) // 2)

    def _toggle_maximized(self):
        window = self.window()
        if window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()
        self.maximize_button.update()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
            self._drag_offset = (
                event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class SummaryDialog(QDialog):
    """主窗口之外显示完整逐通道转换摘要。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("summaryDialog")
        self.setWindowTitle("转换摘要")
        self.setModal(True)
        self.resize(520, 410)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)
        heading = QLabel("转换摘要")
        heading.setObjectName("dialogHeading")
        layout.addWidget(heading)

        self.summary_view = QPlainTextEdit()
        self.summary_view.setObjectName("summaryView")
        self.summary_view.setReadOnly(True)
        layout.addWidget(self.summary_view, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.done_button = QPushButton("完成")
        self.done_button.setObjectName("primaryButton")
        self.done_button.setFixedSize(88, 34)
        self.done_button.clicked.connect(self.accept)
        footer.addWidget(self.done_button)
        layout.addLayout(footer)

    def set_summary(self, text: str) -> None:
        self.summary_view.setPlainText(text)
        cursor = self.summary_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        self.summary_view.setTextCursor(cursor)
