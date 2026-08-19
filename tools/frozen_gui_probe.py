"""冻结 GUI 转换探针（临时）：以 main.py 同款入口结构（__main__ 守卫 +
freeze_support()）跑一次真实 GUI 转换，验证冻结打包下「开始转换」不会弹出
第二个主面板（回归：spawn worker 以 `exe --multiprocessing-fork` 重跑入口
脚本时被 freeze_support() 拦截）。

判定：
- 转换期间可见的「BLF → MDF 转换」窗口数恒 = 1（无新面板）→ GUI_PROBE_OK
- 出现第 2 个同标题可见窗口（worker 未被拦截、重开面板）或转换超时
  → GUI_PROBE_FAIL

用法：与 blf2mdf.spec 同裁剪配置打成 console exe，exe 同目录需有
inputs/blf/A19G1_ACFCAN_00112_20260614_141114.blf 与
inputs/dbc/VDCCCU_CANFD1.dbc。退出码 0 = 通过，1 = 失败。
"""
import ctypes
import multiprocessing
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core.dbc_loader import load
from gui.main_window import MainWindow

WINDOW_TITLE = "BLF → MDF 转换"
BLF_REL = "inputs/blf/A19G1_ACFCAN_00112_20260614_141114.blf"
DBC_REL = "inputs/dbc/VDCCCU_CANFD1.dbc"


def _visible_panel_count() -> int:
    """统计所有进程中可见且标题含 WINDOW_TITLE 的窗口数（含本探针主窗口）。"""
    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _lp):
        nonlocal count
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if WINDOW_TITLE in buf.value:
            count += 1
        return True

    count = 0
    user32.EnumWindows(cb, None)
    return count


def _run_probe() -> int:
    root = Path(sys.executable).resolve().parent
    blf = root / BLF_REL
    dbc = root / DBC_REL
    out = root / "outputs" / "frozen_gui_probe.mdf"
    if not blf.is_file() or not dbc.is_file():
        print(f"GUI_PROBE_FAIL 缺少样例: {blf} / {dbc}", file=sys.stderr)
        return 1

    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()  # 真实场景：主窗口可见，作为窗口数基准 1
    win.blf_path = str(blf)
    win.out_edit.setText(str(out))
    d = load(str(dbc))
    win.dbc_list = [d]
    win.auto_bind = {1: d.path}
    win._rebuild_channel_table([1, 2], prev=None)

    started = time.time()
    win._start_convert()

    state = {"max_windows": 1}

    def tick():
        n = _visible_panel_count()
        state["max_windows"] = max(state["max_windows"], n)
        done = win.summary.toPlainText()  # _on_done 填充摘要 = 转换完成标志
        wt = getattr(win, "worker_thread", None)  # _finish 后为 None，勿直访
        elapsed = time.time() - started
        if int(elapsed) % 5 == 0:
            print(f"[TICK] t={elapsed:.0f}s windows={n} "
                  f"thread_alive={wt is not None and wt.isRunning()} "
                  f"summary_len={len(done)}",
                  file=sys.stderr)
        if state["max_windows"] > 1:
            print("GUI_PROBE_FAIL 出现新面板（worker 未被 freeze_support 拦截）",
                  file=sys.stderr)
            app.exit(1)
        elif done:
            print(f"GUI_PROBE_OK dt={elapsed:.1f}s "
                  f"max_windows={state['max_windows']} "
                  f"out={out.stat().st_size // 1024}KB",
                  file=sys.stderr)
            app.exit(0)
        elif elapsed > 90:
            print("GUI_PROBE_FAIL 转换超时 90s", file=sys.stderr)
            app.exit(1)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(300)
    return app.exec()


if __name__ == "__main__":
    # 与 main.py 同款入口结构：守卫 + freeze_support（冻结 spawn 拦截必需）
    multiprocessing.freeze_support()
    sys.exit(_run_probe())
