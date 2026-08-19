"""ccu3.0 项目 DBC 加载：项目枚举 + 新名—通道映射解析 + 自动绑定建议。

数据源约定：inputs/dbc_ccu3.0/<项目>/*.dbc（项目文件夹内即该项目的全部 DBC
矩阵），映射关系来自同目录 dbc_对应关系.txt（行格式：旧名——新名——CAN通道）。
"""
import logging
from pathlib import Path

from core.dbc_loader import DbcDef, _detect_encoding, load

LOGGER = logging.getLogger(__name__)

# 映射文件缺失时的内置回退表（与 dbc_对应关系.txt 前 10 行同步；键 = DBC 文件
# 主名，值 = CAN 通道号）
DEFAULT_MAPPING = {
    "CFCAN1": 13,
    "CFCAN2": 3,
    "CFCAN3": 6,
    "IFCAN": 12,
    "PFCAN1": 1,
    "PFCAN2": 15,
    "ZFCANF": 8,
    "ZFCANL": 9,
    "ZFCANR": 10,
    "ZFCANT": 11,
}


def list_projects(root: str | Path | None) -> list[str]:
    """枚举含 .dbc 文件的项目文件夹（按名称排序）。

    只认目录中确实存在 DBC 的项目，避免把空文件夹/说明文件目录当项目。
    root 为 None 或不存在时返回空列表（未定位到数据源/发布布局缺失时
    GUI 降级为手动添加 DBC，而不是启动崩溃）。
    """
    if root is None:
        return []
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and any(d.glob("*.dbc"))
    )


def _parse_mapping_text(text: str) -> dict[str, int]:
    """解析映射文本 → {DBC 主名: 通道号}。

    行格式 `旧名——新名——CAN13`（全角破折号分隔）：旧名列忽略；
    无通道（如 `VDCTBOX_CANFD——`）、空行、# 注释行跳过；通道号解析失败
    的行跳过并告警。
    """
    mapping = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("——")]
        if len(parts) < 3 or not parts[2]:
            continue  # 无通道的行（如 VDCTBOX_CANFD——）不作为绑定依据
        try:
            ch = int(parts[2][3:])  # "CAN13" → 13
        except ValueError:
            LOGGER.warning("映射行通道号无法解析，已跳过: %s", line)
            continue
        mapping[parts[1]] = ch
    return mapping


def load_mapping(txt_path: str | Path | None = None) -> dict[str, int]:
    """读取映射文件 → {DBC 主名: 通道号}。

    文件不存在/解析失败时回退内置 DEFAULT_MAPPING（告警），保证 GUI 总能用。
    映射文件为 GBK 编码（文件名含中文），沿用 dbc_loader 的编码探测。
    """
    if txt_path is not None:
        path = Path(txt_path)
        if path.exists():
            try:
                raw = path.read_bytes()
                raw = raw.rstrip(b"\x00")  # 同 dbc_loader：容固定缓冲尾部 NUL 填充
                text = raw.decode(_detect_encoding(raw))
                mapping = _parse_mapping_text(text)
                if mapping:
                    return mapping
                LOGGER.warning("映射文件无有效行，回退内置映射: %s", path)
            except Exception:  # noqa: BLE001 — 读取/解码异常一律回退
                LOGGER.warning("映射文件读取失败，回退内置映射: %s", path)
    return dict(DEFAULT_MAPPING)


def load_project(root: str | Path, project: str) -> list[DbcDef]:
    """加载项目文件夹下的全部 DBC（按文件名排序）。

    任一个 DBC 解析失败即整体失败（抛出带文件名的异常）：项目矩阵是
    完整交付物，部分加载会让对应通道静默不绑定，不如失败告知。
    """
    folder = Path(root) / project
    paths = sorted(folder.glob("*.dbc"))
    if not paths:
        raise ValueError(f"项目 {project} 下未找到 DBC 文件")
    dbcs = []
    for p in paths:
        try:
            dbcs.append(load(str(p)))
        except Exception as e:
            raise ValueError(f"{p}: {e}") from e
    return dbcs


def auto_bindings(dbcs: list[DbcDef], mapping: dict[str, int]) -> dict[int, str]:
    """按映射计算自动绑定建议 {通道号: DBC 文件路径}。

    仅含「映射表内且已加载」的 DBC；项目缺文件（如 AH8 无 PFCAN2.dbc）
    或 DBC 不在映射表内时对应通道不出现，保持不绑定。值为 DBC 文件路径
    （结构化身份，与 GUI 下拉 userData 一致）；显示名只作展示、
    不参与匹配（见 CONTEXT.md「绑定」词条）。
    """
    result = {}
    for d in dbcs:
        ch = mapping.get(Path(d.path).stem)
        if ch is not None:
            result[ch] = d.path
    return result
