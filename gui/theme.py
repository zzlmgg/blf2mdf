"""应用级视觉令牌与 QSS。"""

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

WINDOW_WIDTH = 690
WINDOW_HEIGHT = 596

APP_QSS = """
QWidget {
    color: #1c1d20;
    font-family: "Microsoft YaHei UI";
    font-size: 13px;
}
QMainWindow {
    background: transparent;
}
QWidget#appRoot {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 rgba(252, 253, 255, 244),
        stop: 1 rgba(248, 250, 253, 238)
    );
    border: 1px solid #aeb1b7;
    border-radius: 10px;
}
QWidget#appRoot[nativeGlass="true"] {
    background: rgba(248, 250, 253, 220);
}
QWidget#appRoot[shellMaximized="true"] {
    border-radius: 0px;
}
QWidget#appBody {
    background: transparent;
}
QFrame[card="true"] {
    background: rgba(255, 255, 255, 238);
    border: 1px solid rgba(32, 35, 40, 28);
    border-radius: 12px;
}
QPushButton#primaryButton {
    background: #087cf0;
    color: #ffffff;
    border: 0;
    border-radius: 9px;
}
QPushButton[tone="positive"] {
    color: #207e4b;
}
QPushButton[tone="negative"] {
    color: #c93834;
}
QWidget#titleBar {
    background: transparent;
    border-bottom: 1px solid rgba(32, 35, 40, 28);
}
QLabel#windowTitle {
    font-size: 16px;
    font-weight: 600;
    background: transparent;
}
QPushButton#windowCloseButton,
QPushButton#windowMinimizeButton,
QPushButton#windowMaximizeButton {
    background: transparent;
    border: 0;
    border-radius: 0;
    padding: 0;
}
QPushButton#semanticIconButton {
    border: 0;
    padding: 0;
    background: transparent;
}
QFrame#dbcListItem {
    background: #ffffff;
    border: 0;
    border-bottom: 1px solid rgba(32, 35, 40, 20);
}
QFrame#dbcListItem[selected="true"] {
    background: #e8f2fd;
}
QLabel#dbcFileName {
    font-size: 13px;
    font-weight: 600;
}
QLabel#dbcFullPath {
    color: #64676d;
    font-family: "Cascadia Mono";
    font-size: 10px;
}
QLabel#dialogHeading {
    font-size: 17px;
    font-weight: 600;
}
QPlainTextEdit#summaryView {
    background: #f4f5f6;
    border: 1px solid rgba(32, 35, 40, 28);
    border-radius: 9px;
    padding: 8px;
}
QLabel#sectionLabel {
    color: #64676d;
    font-size: 12px;
    font-weight: 600;
    background: transparent;
}
QLabel#panelTitle {
    color: #1c1d20;
    font-size: 15px;
    font-weight: 650;
    background: transparent;
}
QLabel#channelCount {
    color: #64676d;
    font-size: 11px;
    background: transparent;
}
QLineEdit#pathField {
    min-height: 28px;
    background: #f4f5f6;
    border: 1px solid rgba(32, 35, 40, 24);
    border-radius: 8px;
    padding: 0 9px;
    selection-background-color: #087cf0;
}
QLineEdit#pathField:focus {
    border-color: rgba(8, 124, 240, 110);
}
QPushButton#secondaryButton {
    background: #ffffff;
    border: 1px solid rgba(32, 35, 40, 42);
    border-radius: 8px;
    padding: 0 12px;
    font-weight: 600;
}
QPushButton#secondaryButton:hover {
    background: #f4f5f6;
    border-color: rgba(32, 35, 40, 62);
}
QPushButton#secondaryButton:pressed { background: #eceef0; }
QPushButton#primaryButton {
    min-height: 32px;
    background: #087cf0;
    color: #ffffff;
    border: 0;
    border-radius: 9px;
    padding: 0 18px;
    font-size: 13px;
    font-weight: 650;
}
QPushButton#primaryButton:hover { background: #0874df; }
QPushButton#primaryButton:pressed { background: #0869c8; }
QPushButton#primaryButton:disabled {
    background: #c7c9cd;
    color: #f8f8f8;
}
QComboBox {
    min-height: 27px;
    background: #f4f5f6;
    border: 1px solid rgba(32, 35, 40, 28);
    border-radius: 7px;
    padding: 0 24px 0 8px;
}
QComboBox:hover { border-color: rgba(32, 35, 40, 52); }
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border: 0;
}
QComboBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
}
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid rgba(32, 35, 40, 36);
    selection-background-color: #e8f2fd;
    selection-color: #1c1d20;
    outline: 0;
}
QListWidget#dbcList,
QTableWidget#channelTable {
    background: #ffffff;
    alternate-background-color: #ffffff;
    border: 0;
    outline: 0;
}
QListWidget#dbcList::item {
    background: #ffffff;
    border: 0;
    padding: 0;
}
QListWidget#dbcList::item:selected,
QListWidget#dbcList::item:hover {
    background: #ffffff;
}
QTableWidget#channelTable::item {
    background: #ffffff;
    border-bottom: 1px solid rgba(32, 35, 40, 18);
    padding-left: 8px;
}
QScrollBar:vertical {
    width: 7px;
    margin: 2px 1px 2px 0;
    background: transparent;
}
QScrollBar::handle:vertical {
    min-height: 28px;
    background: rgba(100, 103, 109, 70);
    border-radius: 3px;
}
QScrollBar::handle:vertical:hover { background: rgba(100, 103, 109, 105); }
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
    height: 0;
    background: transparent;
}
QProgressBar#inlineProgress,
QProgressBar#conversionProgress {
    border: 0;
    border-radius: 2px;
    background: #e7e8ea;
    max-height: 4px;
}
QProgressBar#inlineProgress::chunk {
    border-radius: 2px;
    background: #207e4b;
}
QProgressBar#conversionProgress::chunk {
    border-radius: 2px;
    background: #087cf0;
}
QLabel#stageLabel {
    color: #64676d;
    font-size: 11px;
    background: transparent;
}
QFrame#summaryBar {
    background: #f4f5f6;
    border: 1px solid rgba(32, 35, 40, 24);
    border-radius: 9px;
}
QLabel#summaryTitle {
    font-size: 12px;
    font-weight: 650;
}
QLabel#summaryStatus {
    color: #64676d;
    font-size: 11px;
}
QPushButton#linkButton {
    color: #087cf0;
    background: transparent;
    border: 0;
    padding: 3px 6px;
    font-weight: 600;
}
QPushButton#linkButton:disabled { color: #8d9096; }
"""


def apply_theme(app: QApplication) -> None:
    """为整个应用安装统一的 Fusion 基础和项目 QSS。"""
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(APP_QSS)
