"""验证 PySide/QSS onefile 体积，并可执行短时启动冒烟。"""

import argparse
import os
import subprocess
import time
from pathlib import Path

MAX_EXE_BYTES = 65 * 1024 * 1024


def build_clean_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """保留普通用户环境，但移除 Python/conda 注入并限定系统 DLL PATH。"""
    env = dict(os.environ if source is None else source)
    system_root = env.get("SystemRoot", r"C:\Windows")
    for name in (
        "CONDA_DEFAULT_ENV",
        "CONDA_EXE",
        "CONDA_PREFIX",
        "CONDA_PROMPT_MODIFIER",
        "PYTHONHOME",
        "PYTHONPATH",
        "_CE_CONDA",
        "_CE_M",
    ):
        env.pop(name, None)
    env["PATH"] = rf"{system_root}\System32;{system_root}"
    return env


def verify_size(exe: Path) -> None:
    if not exe.is_file():
        raise ValueError(f"EXE 不存在：{exe}")
    if exe.stat().st_size > MAX_EXE_BYTES:
        size_mib = exe.stat().st_size / 1024 / 1024
        raise ValueError(f"EXE 体积 {size_mib:.2f} MiB，超过 65 MiB")


def launch_smoke(exe: Path, timeout: float = 8.0) -> None:
    process = subprocess.Popen(
        [str(exe)], cwd=str(exe.parent), env=build_clean_environment()
    )
    try:
        time.sleep(timeout)
        if process.poll() is not None:
            raise RuntimeError(f"冻结 GUI 启动后提前退出：{process.returncode}")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("exe", type=Path)
    parser.add_argument("--launch-smoke", action="store_true")
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()

    exe = args.exe.resolve()
    verify_size(exe)
    if args.launch_smoke:
        launch_smoke(exe, args.timeout)
    print(f"PACKAGE_OK {exe.stat().st_size / 1024 / 1024:.2f} MiB {exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
