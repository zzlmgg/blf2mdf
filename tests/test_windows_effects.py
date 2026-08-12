"""Windows 轻玻璃与圆角回退的行为测试。"""

import os

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QWidget

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    return QApplication.instance() or QApplication([])


def test_light_glass_is_safe_on_unsupported_platform(monkeypatch):
    import gui.windows_effects as effects

    _app()
    window = QWidget()
    monkeypatch.setattr(effects.sys, "platform", "linux")

    assert effects.apply_light_glass(window) is False


def test_light_glass_uses_documented_dwm_attributes(monkeypatch):
    import gui.windows_effects as effects

    _app()
    window = QWidget()
    calls = []
    monkeypatch.setattr(effects.sys, "platform", "win32")
    monkeypatch.setattr(
        effects,
        "_set_dwm_attribute",
        lambda hwnd, attribute, value: calls.append((hwnd, attribute, value)) or True,
    )

    assert effects.apply_light_glass(window) is True
    assert [(attribute, value) for _, attribute, value in calls] == [
        (33, 2),
        (38, 3),
    ]


def test_native_failure_falls_back_without_raising(monkeypatch):
    import gui.windows_effects as effects

    _app()
    window = QWidget()
    monkeypatch.setattr(effects.sys, "platform", "win32")
    monkeypatch.setattr(effects, "_set_dwm_attribute", lambda *_: False)

    assert effects.apply_light_glass(window) is False


def test_normal_fallback_mask_has_rounded_corners():
    from gui.windows_effects import sync_rounded_window

    _app()
    window = QWidget()
    window.resize(100, 100)
    sync_rounded_window(window, native_rounding=False)

    mask = window.mask()
    assert not mask.isEmpty()
    assert not mask.contains(QPoint(0, 0))
    assert mask.contains(QPoint(50, 50))


def test_native_or_maximized_window_has_no_fallback_mask():
    from gui.windows_effects import sync_rounded_window

    app = _app()
    window = QWidget()
    window.resize(100, 100)
    sync_rounded_window(window, native_rounding=False)
    assert not window.mask().isEmpty()

    sync_rounded_window(window, native_rounding=True)
    assert window.mask().isEmpty()

    sync_rounded_window(window, native_rounding=False)
    window.showMaximized()
    app.processEvents()
    sync_rounded_window(window, native_rounding=False)
    assert window.mask().isEmpty()
    window.close()
