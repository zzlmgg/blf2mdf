"""Windows 轻玻璃效果，以及不支持原生效果时的圆角回退。"""

import ctypes
import sys

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainterPath, QRegion
from PySide6.QtWidgets import QWidget

CORNER_RADIUS = 10

_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_ROUND = 2
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMSBT_TRANSIENTWINDOW = 3


def _set_dwm_attribute(hwnd: int, attribute: int, value: int) -> bool:
    """设置一个整数型 DWM 属性；旧版 Windows 或 API 失败时返回 False。"""
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
        data = ctypes.c_int(value)
        result = dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd),
            ctypes.c_uint(attribute),
            ctypes.byref(data),
            ctypes.sizeof(data),
        )
    except (AttributeError, OSError):
        return False
    return result == 0


def apply_light_glass(window: QWidget) -> bool:
    """请求 Windows 11 的圆角与轻玻璃材质；完整成功才返回 True。"""
    if sys.platform != "win32":
        return False

    try:
        hwnd = int(window.winId())
    except (RuntimeError, TypeError, ValueError):
        return False

    rounded = _set_dwm_attribute(
        hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND
    )
    glass = _set_dwm_attribute(
        hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, _DWMSBT_TRANSIENTWINDOW
    )
    return rounded and glass


def _rounded_region(window: QWidget) -> QRegion:
    path = QPainterPath()
    path.addRoundedRect(
        QRectF(0, 0, window.width(), window.height()),
        CORNER_RADIUS,
        CORNER_RADIUS,
    )
    return QRegion(path.toFillPolygon().toPolygon())


def sync_rounded_window(window: QWidget, native_rounding: bool) -> None:
    """在正常窗口使用圆角；最大化或已有原生圆角时移除软件遮罩。"""
    if native_rounding or window.isMaximized():
        window.clearMask()
        return

    if window.width() > 0 and window.height() > 0:
        window.setMask(_rounded_region(window))
