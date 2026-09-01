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


@pytest.fixture()
def unaligned_blf(tmp_path) -> Path:
    """含连续未对齐 APP_TRIGGER 的最小真实 BLF（对象间 padding 会累积）。"""
    from can.io.blf import (
        CAN_MESSAGE,
        CAN_MSG_STRUCT,
        FILE_HEADER_SIZE,
        FILE_HEADER_STRUCT,
        LOG_CONTAINER,
        LOG_CONTAINER_STRUCT,
        NO_COMPRESSION,
        OBJ_HEADER_BASE_STRUCT,
        OBJ_HEADER_V1_STRUCT,
    )

    def obj(body: bytes, obj_type: int, padding: int) -> bytes:
        header_size = OBJ_HEADER_BASE_STRUCT.size + OBJ_HEADER_V1_STRUCT.size
        obj_size = header_size + len(body)
        return (
            OBJ_HEADER_BASE_STRUCT.pack(
                b"LOBJ", header_size, 1, obj_size, obj_type
            )
            + OBJ_HEADER_V1_STRUCT.pack(0, 0, 0, 0)
            + body
            + b"\x00" * padding
        )

    message_1 = obj(
        CAN_MSG_STRUCT.pack(1, 0, 1, 0x100, b"\x01" + b"\x00" * 7),
        CAN_MESSAGE,
        0,
    )
    trigger_1 = obj(b"A" * 17, 65, 3)  # 65 = APP_TRIGGER; obj_size = 49
    trigger_2 = obj(b"B" * 17, 65, 3)
    message_2 = obj(
        CAN_MSG_STRUCT.pack(2, 0, 1, 0x101, b"\x02" + b"\x00" * 7),
        CAN_MESSAGE,
        0,
    )
    payload = message_1 + trigger_1 + trigger_2 + message_2
    container = (
        OBJ_HEADER_BASE_STRUCT.pack(
            b"LOBJ", 16, 1, 32 + len(payload), LOG_CONTAINER
        )
        + LOG_CONTAINER_STRUCT.pack(NO_COMPRESSION, len(payload))
        + payload
    )

    total_size = FILE_HEADER_SIZE + len(container)
    header = [
        b"LOGG", FILE_HEADER_SIZE, 5, 0, 0, 0, 2, 6, 8, 1,
        total_size, FILE_HEADER_SIZE + len(payload), 1, 0,
        2026, 8, 1, 17, 12, 34, 56, 789,
        2026, 8, 1, 17, 12, 34, 56, 800,
    ]
    path = tmp_path / "unaligned.blf"
    with path.open("wb") as stream:
        stream.write(FILE_HEADER_STRUCT.pack(*header))
        stream.write(b"\x00" * (FILE_HEADER_SIZE - FILE_HEADER_STRUCT.size))
        stream.write(container)
    return path
