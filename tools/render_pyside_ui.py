"""渲染带示例数据的 PySide/QSS 主窗口，供视觉验收。"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.dbc_loader import DbcDef  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402
from gui.theme import apply_theme  # noqa: E402


def render(output: Path) -> None:
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    window = MainWindow()

    blf_path = r"E:\vehicle_data\2026-08-12\road_test_001.blf"
    window.blf_path = blf_path
    window.blf_channels = [0, 1, 2, 3, 8, 13]
    window.blf_edit.setText(blf_path)
    window.blf_edit.setToolTip(blf_path)
    source = Path(blf_path)
    out_path = str(source.with_name(f"{source.stem}_t.mdf"))
    window.out_edit.setText(out_path)
    window.out_edit.setToolTip(out_path)

    window.dbc_list = [
        DbcDef(path=r"E:\projects\blf_dbc\exe_publish\dbc_ccu3.0\A19G1\PFCAN2.dbc", db=None),
        DbcDef(path=r"E:\projects\blf_dbc\exe_publish\dbc_ccu3.0\A19G1\CFCAN1.dbc", db=None),
        DbcDef(path=r"E:\projects\blf_dbc\exe_publish\dbc_ccu3.0\A19G1\BFCAN.dbc", db=None),
        DbcDef(path=r"E:\projects\blf_dbc\exe_publish\dbc_ccu3.0\A19G1\CHCAN.dbc", db=None),
        DbcDef(path=r"E:\projects\blf_dbc\exe_publish\dbc_ccu3.0\A19G1\IDCAN.dbc", db=None),
    ]
    window._refresh_dbc_items()
    window.auto_bind = {
        0: window.dbc_list[0].path,
        1: window.dbc_list[1].path,
        2: window.dbc_list[2].path,
        3: window.dbc_list[3].path,
        8: window.dbc_list[4].path,
    }
    window._rebuild_channel_table(window.blf_channels, prev=None)
    window.convert_btn.setEnabled(True)
    window.stage_label.setText("就绪 · 已读取 6 路通道")

    if os.environ.get("BLF_UI_RENDER_HIDDEN") == "1":
        window.move(-10_000, -10_000)
    window.show()
    app.processEvents()
    window.dbc_list_widget.item(1).setSelected(True)
    window.dbc_list_widget.item(3).setSelected(True)
    app.processEvents()
    output.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(output), "PNG"):
        raise RuntimeError(f"无法保存界面截图：{output}")
    window.close()


def main() -> int:
    output = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else PROJECT_ROOT / "build_ui_review" / "pyside-qss.png"
    )
    render(output.resolve())
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
