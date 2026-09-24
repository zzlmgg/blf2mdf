"""来源解析：拖入的路径集合 → 候选清单（BLF 路径 / 输出路径 / 相对显示名 + 大小）。

一次拖入可以是散 .blf、文件夹、压缩包（zip / rar / 7z）的任意混合。每个候选的
三项信息在解析期一次算定，下游（界面层、批量编排）不再重算：

- **散 .blf**：输出就在该文件旁边（`<主名>_t.mdf`），相对显示名 = 文件名；
- **文件夹**：输出落在与源**同级**的镜像树 `<源名>_t/` 里（见 CONTEXT.md
  「镜像输出树」），相对显示名 = 该文件在源内的相对路径；
- **压缩包**：解压到同级 `<压缩包主名>/`。该路径已存在则不删除，改解到
  `<主名>_<本地时间戳>/`（仍占用则 `_2`、`_3`…），再按文件夹来源处理。
  产物在实际解压目录同级的 `<解压目录名>_t/`。zip/rar 走系统 tar；7z 走 py7zr。

本模块只认路径与文件系统，不感知平台/项目/DBC，也不感知转换；无 Qt 依赖。
"""
import logging
import os
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

import py7zr
from py7zr.exceptions import PasswordRequired

from core.blf_reader import check_cancel

LOGGER = logging.getLogger(__name__)

# 可导入的扩展名（大小写不敏感：资源管理器拖出的可能是 .BLF/.Blf / .ZIP）
BLF_SUFFIX = ".blf"
ZIP_SUFFIX = ".zip"
RAR_SUFFIX = ".rar"
SEVEN_Z_SUFFIX = ".7z"
ARCHIVE_SUFFIXES = frozenset({ZIP_SUFFIX, RAR_SUFFIX, SEVEN_Z_SUFFIX})
# zip/rar 共用系统 tar；7z 不能走 tar（libarchive 未编 LZMA）
TAR_ARCHIVE_SUFFIXES = frozenset({ZIP_SUFFIX, RAR_SUFFIX})
ENCRYPTED_ARCHIVE_MSG = "当前版本不支持加密压缩包"


@dataclass(frozen=True)
class Candidate:
    """待转候选：源 .blf、输出路径、相对显示名（界面列用）、字节大小。"""
    blf: Path
    output: Path
    display: str
    size: int


def resolve(paths, *, progress_cb=None, cancel_cb=None,
            extracting_cb=None) -> list[Candidate]:
    """路径集合 → 候选清单（按相对显示名排序；无 .blf 时为空清单）。

    结果只取决于路径集合本身，与拖入顺序无关（条目先排序再解析）。
    同一文件被两条规则命中（拖入文件夹、又单独拖入其内的某个 .blf）时**文件夹规则赢**：
    更外层的来源是其中文件的路径前缀，排序天然在前；改排序键会静默改变赢家。
    同一次拖入里，已存在因而被避让开的同名文件夹仍按文件夹来源处理。
    本次将创建的解压目录若也在路径集合里，则跳过，避免扫一个尚未写完的目录。
    progress_cb(已发现候选数: int)：每发现一个候选上报一次（单调不减）——
    遍历前不知总数，本模块的进度刻度是计数而非百分比；
    extracting_cb(压缩包文件名: str)：开始解压某个压缩包时上报一次（解压段文案）；
    cancel_cb() 置位 → raise ConversionCancelled（沿用「取消信号」契约）。
    """
    ordered = sorted((Path(raw) for raw in paths), key=_source_key)
    plans = _plan_extract_roots(
        [path for path in ordered if _is_archive(path)])
    planned_roots = [root for root, _avoided in plans.values()]
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for path in ordered:
        check_cancel(cancel_cb)
        if planned_roots and _covered_by_extract_root(path, planned_roots):
            continue
        for candidate in _entry_candidates(
                path, cancel_cb, extracting_cb=extracting_cb,
                plan=plans.get(_identity(path))):
            mark = _identity(candidate.blf)
            if mark in seen:          # 重复拖入 / 软链接别名 → 只留先到的那个
                continue
            seen.add(mark)
            candidates.append(candidate)
            if progress_cb is not None:
                progress_cb(len(candidates))
    candidates.sort(key=_sort_key)
    return candidates


def blf_candidate(path: Path) -> Candidate:
    """单个散 .blf 的候选：输出就在它旁边（`<主名>_t.mdf`），显示名 = 文件名。

    「输出路径在解析期一次算定」对单文件入口同样成立：拖入/浏览单个 .blf
    不经解析（没有目录要展开），界面就地取这条规则——规则本身只有这一处
    定义，界面不得自己拼 `_t.mdf`（读不到的条目大小记 0，同条目跳过政策）。
    """
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return Candidate(blf=path, output=path.with_name(f"{path.stem}_t.mdf"),
                     display=path.name, size=size)


def is_archive_path(path: Path | str) -> bool:
    """拖入条目是否按压缩包识别（zip / rar / 7z；扩展名大小写不敏感；不 stat）。"""
    return Path(path).suffix.lower() in ARCHIVE_SUFFIXES


def _is_blf(path: Path) -> bool:
    """可导入条目：扩展名大小写不敏感（资源管理器拖出的可能是 .BLF/.Blf）。"""
    return path.suffix.lower() == BLF_SUFFIX


def _is_archive(path: Path) -> bool:
    """压缩包文件条目：扩展名大小写不敏感且确实是文件。"""
    return is_archive_path(path) and path.is_file()


def _extract_stamp() -> str:
    """避让用的本地时间戳：YYYYMMDD_HHMMSS。"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _archive_extract_root(archive: Path) -> Path:
    """首选解压根 = 压缩包同级 / `<主名>`（只去掉最后一个扩展名）。"""
    return archive.parent / archive.stem


def _path_is_free(path: Path, reserved: set[str]) -> bool:
    """路径尚未占用：磁盘上不存在，且本次规划还没把它分给别的压缩包。"""
    return not path.exists() and _identity(path) not in reserved


def _allocate_extract_root(archive: Path, reserved: set[str]) -> tuple[Path, bool]:
    """选定解压根。返回 (目录, 是否因同名占用而避让)。

    首选名空闲则用它。已存在（文件夹或同名文件）则不删，改用
    `<主名>_<时间戳>`；该名也占用则 `_2`、`_3`…
    """
    preferred = _archive_extract_root(archive)
    if _path_is_free(preferred, reserved):
        return preferred, False
    base = f"{archive.stem}_{_extract_stamp()}"
    candidate = archive.parent / base
    if _path_is_free(candidate, reserved):
        return candidate, True
    number = 2
    while True:
        candidate = archive.parent / f"{base}_{number}"
        if _path_is_free(candidate, reserved):
            return candidate, True
        number += 1


def _plan_extract_roots(archives: list[Path]) -> dict[str, tuple[Path, bool]]:
    """一次解析里为每个压缩包预定解压根（同主名的多个包各得一个空闲名）。"""
    reserved: set[str] = set()
    plans: dict[str, tuple[Path, bool]] = {}
    for archive in archives:
        root, avoided = _allocate_extract_root(archive, reserved)
        reserved.add(_identity(root))
        plans[_identity(archive)] = (root, avoided)
    return plans


def _covered_by_extract_root(path: Path, roots: list[Path]) -> bool:
    """路径是否就是本次将创建的解压根、或位于其内部（跳过，避免扫未写完的目录）。"""
    for root in roots:
        try:
            Path(_identity(path)).relative_to(Path(_identity(root)))
            return True
        except ValueError:
            continue
    return False


def _identity(path: Path) -> str:
    """条目标识：真实路径（解析软链接/联接点）+ 大小写归一——同一文件的两个名字同标识。"""
    return os.path.normcase(os.path.realpath(path))


def _source_key(path: Path) -> tuple[str, str]:
    """条目排序：标识优先，别名并列时以路径字面量为次序（排序全序 → 结果与拖入顺序无关）。"""
    return (_identity(path), os.path.normcase(str(path)))


def _entry_candidates(path: Path, cancel_cb,
                      extracting_cb=None, plan=None) -> Iterator[Candidate]:
    """一个拖入条目 → 它的候选（0..N 个）：条目类型在这里识别。

    压缩包解到预定的同级目录（同名占用则避让到时间戳目录），再按文件夹来源
    产出候选；文件夹递归进镜像树；
    散 .blf 就地落位（输出就在它自己旁边，相对显示名 = 文件名）；非上述类型
    与不存在的路径不是候选（是筛选，不是异常）；读不到的条目跳过并告警——
    与目录跳过一个政策，都不中断整次解析。
    """
    try:
        if _is_archive(path):
            if plan is None:
                root, avoided = _allocate_extract_root(path, set())
            else:
                root, avoided = plan
            yield from _archive_candidates(
                path, root, avoided, cancel_cb, extracting_cb)
        elif path.is_dir():
            yield from _folder_candidates(path, cancel_cb)
        elif path.is_file() and _is_blf(path):
            yield blf_candidate(path)
    except OSError as exc:
        LOGGER.warning("条目跳过（不可读）: %s（%s）", path, exc)


def _archive_candidates(archive: Path, extract_root: Path, avoided: bool,
                        cancel_cb,
                        extracting_cb: Callable[[str], None] | None,
                        ) -> Iterator[Candidate]:
    """压缩包来源：解压到预定目录，再交给文件夹候选逻辑。

    解压失败或解压/遍历途中取消：只清掉这次新建的解压根。避让前已存在的
    同名路径和镜像输出树都不动。
    """
    if extracting_cb is not None:
        extracting_cb(archive.name)
    touched = [False]  # 单元素：本次已创建解压根（异常路径也能读到）
    try:
        _extract_archive(archive, extract_root, cancel_cb, touched)
        if avoided:
            LOGGER.info("已解压到 %s", extract_root)
        yield from _folder_candidates(extract_root, cancel_cb)
    except Exception:
        if touched[0]:
            _remove_extract_root(extract_root)
        raise


def _remove_extract_root(extract_root: Path) -> None:
    """删掉未完成的解压根（取消 / 解压失败收口）；不存在则忽略。"""
    if extract_root.exists():
        shutil.rmtree(extract_root, ignore_errors=True)


def _extract_archive(archive: Path, extract_root: Path, cancel_cb,
                     touched: list[bool]) -> None:
    """解压到已经选定的空闲目录。不删除任何已存在路径。

    touched[0]：本次已创建解压根时置位，供失败/取消时只清理这个新目录。
    """
    check_cancel(cancel_cb)
    try:
        extract_root.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise RuntimeError(
            f"解压目录不可写（{extract_root.parent}）: {exc}") from exc
    touched[0] = True
    check_cancel(cancel_cb)
    try:
        suffix = archive.suffix.lower()
        if suffix in TAR_ARCHIVE_SUFFIXES:
            _extract_with_tar(archive, extract_root)
        elif suffix == SEVEN_Z_SUFFIX:
            _extract_with_py7zr(archive, extract_root)
        else:
            raise RuntimeError(f"不支持的压缩包格式（{archive.name}）")
    except OSError as exc:
        raise RuntimeError(f"解压失败（{archive.name}）: {exc}") from exc


def _system_tar() -> Path:
    """Windows 自带 bsdtar；不存在时由调用方把失败亮给用户。"""
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"


def _extract_with_tar(archive: Path, extract_root: Path) -> None:
    """zip / rar → System32\\tar.exe（bsdtar）；无额外解压二进制。"""
    if archive.suffix.lower() == ZIP_SUFFIX:
        _reject_encrypted_zip(archive)
    tar = _system_tar()
    if not tar.is_file():
        raise RuntimeError("缺少系统解压组件（System32\\tar.exe）")
    completed = subprocess.run(
        [str(tar), "-xf", str(archive), "-C", str(extract_root)],
        capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip() or "未知错误"
        if _looks_like_password_error(detail):
            raise RuntimeError(ENCRYPTED_ARCHIVE_MSG)
        raise RuntimeError(f"解压失败（{archive.name}）: {detail}")


def _reject_encrypted_zip(archive: Path) -> None:
    """zip 条目带加密标志则直接拒绝（不弹密码、不调 tar 半截写出）。"""
    try:
        with zipfile.ZipFile(archive) as zf:
            if any(info.flag_bits & 0x1 for info in zf.infolist()):
                raise RuntimeError(ENCRYPTED_ARCHIVE_MSG)
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"解压失败（{archive.name}）: 无法识别的 zip") from exc


def _looks_like_password_error(detail: str) -> bool:
    """后端文案是否在要密码（tar / rar 加密包）。"""
    lower = detail.casefold()
    return any(token in lower for token in (
        "passphrase", "password", "encrypted", "密码"))


def _extract_with_py7zr(archive: Path, extract_root: Path) -> None:
    """7z → py7zr（系统 tar 的 libarchive 未编 LZMA，不能解 7z）。"""
    try:
        with py7zr.SevenZipFile(archive, mode="r") as sz:
            if sz.needs_password():
                raise RuntimeError(ENCRYPTED_ARCHIVE_MSG)
            _reject_unsafe_7z_members(sz.getnames(), extract_root)
            sz.extractall(path=extract_root)
            _assert_extract_stays_inside(extract_root)
    except RuntimeError:
        raise
    except PasswordRequired as exc:
        raise RuntimeError(ENCRYPTED_ARCHIVE_MSG) from exc
    except Exception as exc:
        # 损坏、越界条目名等：映射成用户可读的一句
        raise RuntimeError(f"解压失败（{archive.name}）: {exc}") from exc


def _reject_unsafe_7z_members(names: list[str], extract_root: Path) -> None:
    """7z 解压前拒绝 ..、盘符、绝对路径，并确认解析后仍落在解压根内。"""
    root = extract_root.resolve()
    for name in names:
        if _is_unsafe_archive_member(name):
            raise RuntimeError(
                f"解压失败：压缩包含越界路径（{name}）")
        target = (extract_root / name).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise RuntimeError(
                f"解压失败：压缩包含越界路径（{name}）") from exc


def _assert_extract_stays_inside(extract_root: Path) -> None:
    """解压后抽查：解压根内每条路径 resolve 后仍在根下（防写出后逃逸）。"""
    root = extract_root.resolve()
    for path in extract_root.rglob("*"):
        try:
            path.resolve().relative_to(root)
        except ValueError as exc:
            raise RuntimeError(
                f"解压失败：写出路径跳出解压根（{path}）") from exc


def _is_unsafe_archive_member(name: str) -> bool:
    """条目名是否含路径穿越或绝对路径（盘符 / 根开头）。"""
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("\\"):
        return True
    if len(normalized) >= 2 and normalized[1] == ":":
        return True
    parts = normalized.split("/")
    return any(part == ".." for part in parts)


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
