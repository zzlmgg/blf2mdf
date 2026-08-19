import os
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BLF_DIR = PROJECT_ROOT / "inputs" / "blf"

SAMPLE_BLF_NAME = "A19G1_ACFCAN_00112_20260614_141114.blf"


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def window(qapp):
    from gui.main_window import MainWindow

    widget = MainWindow()
    yield widget
    widget.close()


def sample_blf() -> Path | None:
    """返回固定样例 BLF 路径（A19G1，与冻结探针同文件）；缺失时返回 None。

    显式具名固定样例（2026-08-19），替代「排序取首」——新增/改名样例
    文件不再静默改变测试输入。
    """
    path = BLF_DIR / SAMPLE_BLF_NAME
    return path if path.is_file() else None
