from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BLF_DIR = PROJECT_ROOT / "inputs" / "blf"
DBC_DIR = PROJECT_ROOT / "inputs" / "dbc"
MDF_DIR = PROJECT_ROOT / "inputs" / "mdf"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)


def sample_blf() -> Path | None:
    """返回样例 BLF 路径；无样例时返回 None。"""
    files = sorted(BLF_DIR.glob("*.blf"))
    return files[0] if files else None


def all_dbc_files() -> list[Path]:
    """返回 dbc/ 目录下全部 .dbc 文件（排序）。"""
    return sorted(DBC_DIR.glob("*.dbc"))
