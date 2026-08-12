"""Runtime paths and Qt resources shared by source and PyInstaller builds."""

import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication, QIcon


def resource_path(relative: str) -> Path:
    """Resolve a bundled resource under source checkout or ``sys._MEIPASS``."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    root = Path(bundle_root) if bundle_root else Path(__file__).resolve().parents[1]
    return root / relative


def application_icon() -> QIcon:
    """Return the shared BLF2MDF application icon."""
    return QIcon(str(resource_path("assets/blf2mdf_icon.png")))


def install_application_icon(app: QGuiApplication) -> QIcon:
    """Install and return the shared icon used by Windows and Qt windows."""
    icon = application_icon()
    app.setWindowIcon(icon)
    return icon

