from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BLF_DIR = PROJECT_ROOT / "inputs" / "blf"
MDF_DIR = PROJECT_ROOT / "inputs" / "mdf"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)


def sample_blf() -> Path | None:
    """返回样例 BLF 路径；无样例时返回 None。

    排除历史大文件（1.2GB，全量转换过慢，2026-08-18 起不作为样例）。
    """
    _EXCLUDED = {"20260324_AHT_PVL105_坡道上带车速RND切换偶发明显撞击声.blf"}
    files = sorted(p for p in BLF_DIR.glob("*.blf") if p.name not in _EXCLUDED)
    return files[0] if files else None
