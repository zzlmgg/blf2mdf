"""来源解析：拖入的路径集合 → 候选清单（BLF 路径 / 输出路径 / 相对显示名 + 大小）。

一次拖入可以是散 .blf、文件夹（下一阶段扩展压缩包）的任意混合。每个候选的三项
信息在解析期一次算定，下游（界面层、批量编排）不再重算：

- **散 .blf**：输出就在该文件旁边（`<主名>_t.mdf`），相对显示名 = 文件名；
- **文件夹**：输出落在与源**同级**的镜像树 `<源名>_t/` 里（见 CONTEXT.md
  「镜像输出树」），相对显示名 = 该文件在源内的相对路径。

本模块只认路径与文件系统，不感知平台/项目/DBC，也不感知转换；无 Qt 依赖。
"""
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from core.blf_reader import check_cancel

LOGGER = logging.getLogger(__name__)

# 可导入的扩展名（大小写不敏感：资源管理器拖出的可能是 .BLF/.Blf）
BLF_SUFFIX = ".blf"


@dataclass(frozen=True)
class Candidate:
    """待转候选：源 .blf、输出路径、相对显示名（界面列用）、字节大小。"""
    blf: Path
    output: Path
    display: str
    size: int


def resolve(paths, *, progress_cb=None, cancel_cb=None) -> list[Candidate]:
    """路径集合 → 候选清单（按相对显示名排序；无 .blf 时为空清单）。

    结果只取决于路径集合本身，与拖入顺序无关（条目先排序再解析）。
    同一文件被两条规则命中（拖入文件夹、又单独拖入其内的某个 .blf）时**文件夹规则赢**：
    更外层的来源是其中文件的路径前缀，排序天然在前；改排序键会静默改变赢家。
    progress_cb(已发现候选数: int)：每发现一个候选上报一次（单调不减）——
    遍历前不知总数，本模块的进度刻度是计数而非百分比；
    cancel_cb() 置位 → raise ConversionCancelled（沿用「取消信号」契约）。
    """
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for path in sorted((Path(raw) for raw in paths), key=_source_key):
        check_cancel(cancel_cb)
        for candidate in _entry_candidates(path, cancel_cb):
            mark = _identity(candidate.blf)
            if mark in seen:          # 重复拖入 / 软链接别名 → 只留先到的那个
                continue
            seen.add(mark)
            candidates.append(candidate)
            if progress_cb is not None:
                progress_cb(len(candidates))
    candidates.sort(key=_sort_key)
    return candidates


def _is_blf(path: Path) -> bool:
    """可导入条目：扩展名大小写不敏感（资源管理器拖出的可能是 .BLF/.Blf）。"""
    return path.suffix.lower() == BLF_SUFFIX


def _identity(path: Path) -> str:
    """条目标识：真实路径（解析软链接/联接点）+ 大小写归一——同一文件的两个名字同标识。"""
    return os.path.normcase(os.path.realpath(path))


def _source_key(path: Path) -> tuple[str, str]:
    """条目排序：标识优先，别名并列时以路径字面量为次序（排序全序 → 结果与拖入顺序无关）。"""
    return (_identity(path), os.path.normcase(str(path)))


def _entry_candidates(path: Path, cancel_cb) -> Iterator[Candidate]:
    """一个拖入条目 → 它的候选（0..N 个）：条目类型在这里识别。

    文件夹递归进镜像树；散 .blf 就地落位（输出就在它自己旁边，相对显示名 =
    文件名）；非 .blf 的条目与不存在的路径不是候选（是筛选，不是异常）；
    读不到的条目跳过并告警——与目录跳过一个政策，都不中断整次解析。
    """
    try:
        if path.is_dir():
            yield from _folder_candidates(path, cancel_cb)
        elif path.is_file() and _is_blf(path):
            yield Candidate(
                blf=path,
                output=path.with_name(f"{path.stem}_t.mdf"),
                display=path.name,
                size=path.stat().st_size,
            )
    except OSError as exc:
        LOGGER.warning("条目跳过（不可读）: %s（%s）", path, exc)


def _folder_candidates(root: Path, cancel_cb) -> Iterator[Candidate]:
    """文件夹来源：递归找出全部 .blf → 同级镜像树 `<源名>_t/` 下的镜像路径。

    镜像树只复刻通向至少一个 .blf 的目录——空目录与无关文件不产生候选，
    路径上的中间目录由输出路径的父目录携带（建目录是写出侧的事）。
    目录按名排序、深度优先，同一棵树两次解析顺序一致。
    """
    output_root = root.parent / f"{root.name}_t"
    stack = [root]
    while stack:
        check_cancel(cancel_cb)
        directory = stack.pop()
        files, subdirs = _scan_dir(directory)
        for blf, size in files:
            check_cancel(cancel_cb)
            if not _is_blf(blf):
                continue
            rel = blf.relative_to(root)
            yield Candidate(
                blf=blf,
                output=output_root / rel.parent / f"{rel.stem}_t.mdf",
                display=str(rel),
                size=size,
            )
        stack.extend(reversed(subdirs))


def _scan_dir(directory: Path) -> tuple[list[tuple[Path, int]], list[Path]]:
    """目录 → (文件 [(路径, 大小)], 子目录 [路径])，各自按条目名排序。"""
    files: list[tuple[Path, int]] = []
    subdirs: list[Path] = []
    try:
        with os.scandir(directory) as scan:
            for entry in scan:
                if entry.is_dir():
                    subdirs.append(Path(entry.path))
                elif entry.is_file():
                    files.append((Path(entry.path), entry.stat().st_size))
    except OSError as exc:
        LOGGER.warning("目录跳过（不可读）: %s（%s）", directory, exc)
        return [], []
    files.sort(key=lambda item: item[0].name.casefold())
    subdirs.sort(key=lambda path: path.name.casefold())
    return files, subdirs


def _sort_key(candidate: Candidate) -> tuple[str, str]:
    """确定性排序：相对显示名（大小写不敏感）→ 输出路径兜底（同名不同目录）。"""
    return (candidate.display.casefold(), os.path.normcase(str(candidate.output)))
