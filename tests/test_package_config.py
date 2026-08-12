"""独立 PySide/QSS 发布包配置回归。"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_qss_spec_builds_named_exe_with_app_icon():
    text = (PROJECT_ROOT / "blf2mdf.spec").read_text(encoding="utf-8")
    assert "['main.py']" in text
    assert "name='BLF2MDF'" in text
    assert "('assets/blf2mdf_icon.png', 'assets')" in text
    assert "icon='assets/blf2mdf_icon.ico'" in text
    for dll in (
        "ffi-8.dll",
        "libcrypto-3-x64.dll",
        "libssl-3-x64.dll",
        "liblzma.dll",
        "libbz2.dll",
        "libexpat.dll",
        "sqlite3.dll",
    ):
        assert dll in text


def test_app_icon_is_the_approved_two_colour_arrow():
    from PySide6.QtGui import QImage

    image = QImage(str(PROJECT_ROOT / "assets" / "blf2mdf_icon.png"))
    assert not image.isNull()
    assert (image.width(), image.height()) == (1024, 1024)

    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(32, 512).name().lower() == "#087cf0"
    assert image.pixelColor(991, 512).name().lower() == "#087cf0"

    assert image.pixelColor(512, 120).name().lower() == "#087cf0"
    for point in ((300, 512), (512, 512), (760, 512), (560, 304), (560, 720)):
        assert image.pixelColor(*point).name().lower() == "#ffffff"


def test_package_verifier_enforces_65_mib_limit():
    from tools.verify_pyside_package import MAX_EXE_BYTES

    assert MAX_EXE_BYTES == 65 * 1024 * 1024


def test_package_smoke_uses_clean_windows_path():
    from tools.verify_pyside_package import build_clean_environment

    env = build_clean_environment(
        {
            "SystemRoot": r"C:\Windows",
            "PATH": r"C:\ProgramData\Anaconda3\envs\blfmdf\Library\bin",
            "PYTHONPATH": r"E:\projects\blf_dbc",
            "CONDA_PREFIX": r"C:\ProgramData\Anaconda3\envs\blfmdf",
        }
    )
    assert env["PATH"] == r"C:\Windows\System32;C:\Windows"
    assert "PYTHONPATH" not in env
    assert "CONDA_PREFIX" not in env
