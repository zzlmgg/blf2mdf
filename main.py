import multiprocessing
import sys

from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow
from gui.resources import install_application_icon
from gui.theme import apply_theme


def main():
    app = QApplication(sys.argv)
    apply_theme(app)
    install_application_icon(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    # 冻结打包必需：spawn worker 以 `exe --multiprocessing-fork` 启动，会以
    # __main__ 重新执行本脚本；freeze_support() 在此拦截并转 spawn_main，
    # 否则 worker 会再开一个主窗口并阻塞在事件循环（转换永远等不到 worker）。
    # 源码运行下 is_forking 为 False，此处是安全 no-op。
    multiprocessing.freeze_support()
    main()
