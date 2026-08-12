"""入口脚本冻结兼容性：main.py 的 __main__ 块必须调用 freeze_support。

冻结打包后 spawn worker 以 `exe --multiprocessing-fork` 启动并重跑入口脚本，
没有 freeze_support() 拦截会再开一个主窗口阻塞在事件循环（用户实测现象：
点击转换弹新面板，关掉才开始转换）。该调用是冻结行为正确性的关键，
用 AST 断言防止未来重构删除（回归测试）。
"""
import ast
from pathlib import Path

import pytest

from conftest import PROJECT_ROOT

MAIN_SRC = PROJECT_ROOT / "main.py"


def _main_block_nodes() -> list[ast.If]:
    """解析 main.py 顶层 `if __name__ == '__main__':` 块。"""
    tree = ast.parse(MAIN_SRC.read_text(encoding="utf-8"))
    blocks = []
    for node in tree.body:
        if (isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and len(node.test.comparators) == 1
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "__name__"
                and isinstance(node.test.comparators[0], ast.Constant)
                and node.test.comparators[0].value == "__main__"):
            blocks.append(node)
    return blocks


def test_main_has_main_guard():
    """入口脚本必须有 __main__ 守卫（spawn 子进程重跑时不执行 main()）。"""
    assert _main_block_nodes(), "main.py 缺少 if __name__ == '__main__': 块"


def test_main_guard_calls_freeze_support():
    """守卫块内必须调用 multiprocessing.freeze_support()（冻结 spawn 拦截）。"""
    for block in _main_block_nodes():
        calls = []
        for node in ast.walk(block):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "freeze_support":
                    calls.append(node)
        assert calls, "main.py 的 __main__ 块未调用 multiprocessing.freeze_support()"


def test_main_applies_application_theme():
    """应用级 QSS 必须在 QApplication 创建后统一安装。"""
    tree = ast.parse(MAIN_SRC.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "apply_theme"
    ]
    assert calls, "main.py 未调用 apply_theme(app)"
