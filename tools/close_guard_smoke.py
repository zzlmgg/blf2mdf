"""关闭守卫冒烟脚本（tools/ 下，开发用，不参与打包）。

验证 gui/main_window.py::MainWindow.closeEvent 守卫：
  1. 用 can.BLFWriter 写一个小 BLF；
  2. monkeypatch gui.main_window.convert 为"先 sleep 再走真实转换"，
     保证 win.close() 必然发生在转换中途；
  3. 转换进行中（1s 后）调用 win.close()（QMessageBox.question 自动点 Yes）；
  4. 断言进程干净退出：无 "QThread: Destroyed while thread is still running" abort。

失败判据：若 closeEvent 缺失/不等待，窗口关闭 → quitOnLastWindowClosed → app 退出 →
worker 线程仍在运行，解释器关闭时销毁 QThread → qFatal + SIGABRT（退出码非 0，
stderr 出现该消息）。

运行：
  QT_QPA_PLATFORM=offscreen PYTHONIOENCODING=utf-8 \
    /c/ProgramData/anaconda3/envs/blfmdf/python.exe tools/close_guard_smoke.py
"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import can  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import gui.main_window as mw  # noqa: E402


def _make_blf(path: str) -> None:
    with can.BLFWriter(path) as w:
        for i in range(5):
            w.on_message_received(can.Message(
                arbitration_id=0x100, is_extended_id=False,
                data=bytes([i, 0, 0, 0, 0, 0, 0, 0]),
                channel=1, timestamp=1000.0 + i))


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    with tempfile.TemporaryDirectory() as td:
        blf = Path(td) / "smoke.blf"
        _make_blf(str(blf))
        out = Path(td) / "smoke.mdf"

        # 慢速 convert：先睡 4 秒再走真实转换，保证 close() 落在转换中途
        real_convert = mw.convert

        def slow_convert(*args, **kwargs):
            time.sleep(4.0)
            return real_convert(*args, **kwargs)

        mw.convert = slow_convert

        # offscreen 无人点按钮：question/critical 全部自动确认
        QMessageBox.question = staticmethod(
            lambda *a, **k: QMessageBox.StandardButton.Yes)
        QMessageBox.critical = staticmethod(
            lambda *a, **k: QMessageBox.StandardButton.Ok)

        win = mw.MainWindow()
        win.blf_path = str(blf)
        win._rebuild_channel_table([1])          # 通道 1 未绑定 → 原始帧路径
        win.out_edit.setText(str(out))
        win.show()                               # 必须 show，lastWindowClosed 才会触发 app 退出
        print(f"start convert at t=0（close 将在 1s 后触发）", flush=True)
        win._start_convert()                     # worker 线程启动，convert 先睡 4s

        # 转换进行中（1s 后）关闭窗口 → closeEvent 守卫触发
        QTimer.singleShot(1000, win.close)
        # 兜底：30s 后强制退出（正常路径约 5s 内结束，超时即判定卡死）
        QTimer.singleShot(30000, app.quit)
        rc = app.exec()

        print(f"app.exec() 返回码: {rc}", flush=True)
        thread = win.worker_thread
        if thread is not None:
            print(f"close 后 worker_thread: isRunning={thread.isRunning()} "
                  f"isFinished={thread.isFinished()}", flush=True)
            if thread.isRunning():
                print("FAIL: 关闭时工作线程仍在运行（守卫缺失）", flush=True)
                return 2
        print("PASS: 转换中关闭窗口后进程干净退出", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
