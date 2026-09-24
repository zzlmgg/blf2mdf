"""source_resolver：来源解析（拖入路径集合 → 候选清单）与输出路径映射。"""
import logging
import os
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest
import py7zr

from conftest import PROJECT_ROOT
from core import source_resolver
from core.blf_reader import ConversionCancelled


def _touch(path: Path, data: bytes = b"x") -> Path:
    """落一个带内容的文件（大小可断言），返回其路径。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _make_zip(zip_path: Path, members: dict[str, bytes]) -> Path:
    """合成 zip（标准库），成员名用包内相对路径。"""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return zip_path


def test_zip_source_extracts_beside_archive_and_mirrors_output(tmp_path):
    """zip → 同级解压根 `<主名>/`，候选输出在同级 `<主名>_t/` 镜像树。"""
    archive = _make_zip(tmp_path / "A02.zip", {
        "20260917/run001.blf": b"abc",
        "run000.blf": b"xy",
        "notes.txt": b"skip",
    })
    result = source_resolver.resolve([archive])
    extract_root = tmp_path / "A02"
    assert extract_root.is_dir()
    assert (extract_root / "20260917" / "run001.blf").is_file()
    assert [(c.display, c.output, c.size) for c in result] == [
        (r"20260917\run001.blf",
         tmp_path / "A02_t" / "20260917" / "run001_t.mdf", 3),
        ("run000.blf", tmp_path / "A02_t" / "run000_t.mdf", 2),
    ]
    assert archive.is_file()  # 压缩包本身保留


def test_loose_blf_outputs_beside_itself(tmp_path):
    blf = _touch(tmp_path / "run001.blf", b"abc")
    [candidate] = source_resolver.resolve([blf])
    assert candidate.blf == blf
    assert candidate.output == tmp_path / "run001_t.mdf"
    assert candidate.display == "run001.blf"
    assert candidate.size == 3


def test_loose_files_each_beside_itself(tmp_path):
    later = _touch(tmp_path / "b_run.blf")
    earlier = _touch(tmp_path / "a_run.blf")
    result = source_resolver.resolve([later, earlier])
    assert [c.blf for c in result] == [earlier, later]  # 顺序与拖入顺序无关
    assert [c.output for c in result] == [
        tmp_path / "a_run_t.mdf", tmp_path / "b_run_t.mdf"]


def test_folder_source_mirrors_tree_beside_the_source(tmp_path):
    root = tmp_path / "AHT"
    _touch(root / "20260917" / "run001.blf")
    _touch(root / "run000.blf")          # 源根下直接躺着的 .blf，规则无例外
    result = source_resolver.resolve([root])
    assert [(c.display, c.output) for c in result] == [
        (r"20260917\run001.blf",
         tmp_path / "AHT_t" / "20260917" / "run001_t.mdf"),
        ("run000.blf", tmp_path / "AHT_t" / "run000_t.mdf"),
    ]


def test_mirror_tree_copies_only_dirs_leading_to_a_blf(tmp_path):
    root = tmp_path / "AHT"
    _touch(root / "readme.txt")                    # 无关文件
    _touch(root / "logs" / "session.log")          # 无关目录
    (root / "empty" / "deeper").mkdir(parents=True)  # 空目录
    deep = _touch(root / "a" / "b" / "c" / "deep.blf")  # 路径上的中间目录要建
    [candidate] = source_resolver.resolve([root])
    assert candidate.blf == deep
    assert candidate.output == tmp_path / "AHT_t" / "a" / "b" / "c" / "deep_t.mdf"
    assert not candidate.output.is_relative_to(root)   # 镜像树在源同级，不污染源


def test_extension_match_is_case_insensitive(tmp_path):
    root = tmp_path / "data"
    _touch(root / "UPPER.BLF")
    _touch(root / "Mixed.Blf")
    _touch(root / "lower.blf")
    _touch(root / "notes.txt")            # 非 .blf 不是候选
    _touch(tmp_path / "loose.BLF")
    result = source_resolver.resolve([root, tmp_path / "loose.BLF"])
    assert sorted(c.blf.name for c in result) == [
        "Mixed.Blf", "UPPER.BLF", "loose.BLF", "lower.blf"]
    assert sorted(c.output.name for c in result) == [
        "Mixed_t.mdf", "UPPER_t.mdf", "loose_t.mdf", "lower_t.mdf"]


def test_mixed_sources_each_follow_their_own_rule(tmp_path):
    root = tmp_path / "AHT"
    in_folder = _touch(root / "sub" / "in_folder.blf")
    loose = _touch(tmp_path / "loose.blf")
    result = source_resolver.resolve([root, loose])
    assert {c.blf: c.output for c in result} == {
        in_folder: tmp_path / "AHT_t" / "sub" / "in_folder_t.mdf",
        loose: tmp_path / "loose_t.mdf",
    }


def test_repeated_entries_produce_one_candidate(tmp_path):
    root = tmp_path / "AHT"
    blf = _touch(root / "sub" / "run001.blf")
    assert [c.blf for c in source_resolver.resolve([root, root])] == [blf]


def test_folder_wins_when_a_loose_drop_overlaps_it(tmp_path):
    """同一文件既在拖入的文件夹里、又被单独拖入：文件夹规则赢（2026-09-23 定调）。

    赢家只取决于路径集合——拖入顺序不影响结果。
    """
    root = tmp_path / "AHT"
    blf = _touch(root / "sub" / "run001.blf")
    for paths in ([root, blf], [blf, root]):   # 两种拖入顺序，同一份结果
        assert [c.output for c in source_resolver.resolve(paths)] == [
            tmp_path / "AHT_t" / "sub" / "run001_t.mdf"]


def test_unreadable_dir_is_skipped_with_a_log(tmp_path, monkeypatch, caplog):
    root = tmp_path / "AHT"
    good = _touch(root / "ok.blf")
    locked = root / "locked"
    _touch(locked / "hidden.blf")
    real_scandir = os.scandir

    def deny_locked(path, *args, **kwargs):
        if os.path.normcase(str(path)) == os.path.normcase(str(locked)):
            raise PermissionError(13, "拒绝访问")
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", deny_locked)
    with caplog.at_level(logging.WARNING):
        result = source_resolver.resolve([root])
    assert [c.blf for c in result] == [good]     # 坏目录不毁掉整次解析
    assert str(locked) in caplog.text            # 跳过留痕


def test_unreadable_loose_entry_is_skipped_with_a_log(tmp_path, monkeypatch,
                                                      caplog):
    blf = _touch(tmp_path / "locked.blf")
    monkeypatch.setattr(Path, "is_file", lambda self: True)

    def deny(self, *args, **kwargs):
        raise PermissionError(13, "被占用")

    monkeypatch.setattr(Path, "stat", deny)
    with caplog.at_level(logging.WARNING):
        assert source_resolver.resolve([blf]) == []
    assert str(blf) in caplog.text            # 与目录跳过一个政策：留痕不中断


def test_traversal_order_is_deterministic(tmp_path):
    root = tmp_path / "AHT"
    for name in ("c.blf", "a.blf", "B.blf"):   # 落盘顺序 ≠ 排序顺序
        _touch(root / name)
    assert [c.blf.name for c in source_resolver.resolve([root])] == [
        "a.blf", "B.blf", "c.blf"]


def test_module_has_no_qt_dependency():
    """无 Qt 依赖（可脱离界面直接单测）：单独导入本模块后 PySide6 不进 sys.modules。"""
    code = "import sys, core.source_resolver; assert 'PySide6' not in sys.modules"
    proc = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_without_blf_the_list_is_empty(tmp_path):
    root = tmp_path / "AHT"
    _touch(root / "notes.txt")
    (root / "empty").mkdir()
    assert source_resolver.resolve([root]) == []
    assert source_resolver.resolve([tmp_path / "gone.blf"]) == []
    assert source_resolver.resolve([]) == []


def test_progress_reports_running_candidate_count(tmp_path):
    root = tmp_path / "AHT"
    _touch(root / "a.blf")
    _touch(root / "sub" / "b.blf")
    _touch(tmp_path / "c.blf")
    counts = []
    source_resolver.resolve([root, tmp_path / "c.blf"], progress_cb=counts.append)
    assert counts == [1, 2, 3]           # 每发现一个候选上报一次，单调不减


def test_cancel_stops_resolution(tmp_path):
    root = tmp_path / "AHT"
    _touch(root / "a.blf")
    with pytest.raises(ConversionCancelled):
        source_resolver.resolve([root], cancel_cb=lambda: True)


def test_cancel_stops_traversal_while_it_runs(tmp_path):
    root = tmp_path / "AHT"
    for i in range(5):
        _touch(root / f"run{i}.blf")
    stop = threading.Event()
    found = []

    def progress(count):
        found.append(count)
        stop.set()                        # 第一个候选到手即取消

    with pytest.raises(ConversionCancelled):
        source_resolver.resolve([root], progress_cb=progress,
                                cancel_cb=stop.is_set)
    assert found == [1]                   # 立刻停下，不再继续搜集


def test_symlink_alias_produces_one_candidate(tmp_path):
    real = _touch(tmp_path / "real" / "run001.blf")
    alias = tmp_path / "alias.blf"
    try:
        alias.symlink_to(real)
    except OSError as exc:                       # 无符号链接权限的机器
        pytest.skip(f"本机不支持符号链接: {exc}")
    assert source_resolver.resolve([real, alias]) \
        == source_resolver.resolve([alias, real])
    assert len(source_resolver.resolve([real, alias])) == 1


def test_zip_reextract_replaces_root_but_keeps_archive_and_output_tree(tmp_path):
    """解压根已存在则整目录重建；压缩包与已有 `<主名>_t/` 保留。"""
    archive = _make_zip(tmp_path / "A02.zip", {"keep.blf": b"new"})
    extract_root = tmp_path / "A02"
    stale = _touch(extract_root / "stale.blf", b"old")
    output_tree = tmp_path / "A02_t"
    prior = _touch(output_tree / "prior_t.mdf", b"keep-me")

    result = source_resolver.resolve([archive])

    assert not stale.exists()
    assert (extract_root / "keep.blf").read_bytes() == b"new"
    assert prior.read_bytes() == b"keep-me"
    assert archive.is_file()
    assert [c.display for c in result] == ["keep.blf"]


def test_zip_and_its_extract_root_in_same_drop_uses_only_zip(tmp_path):
    """同一次拖入同时有 zip 与其解压目录时，只按 zip 处理。"""
    extract_root = tmp_path / "A02"
    _touch(extract_root / "old.blf", b"from-folder")
    archive = _make_zip(tmp_path / "A02.zip", {"from_zip.blf": b"from-zip"})

    result = source_resolver.resolve([extract_root, archive])

    assert [c.display for c in result] == ["from_zip.blf"]
    assert all(c.blf.is_relative_to(extract_root) for c in result)
    assert (extract_root / "from_zip.blf").is_file()
    assert not (extract_root / "old.blf").exists()


def test_mixed_zip_folder_and_loose_each_follow_own_rule(tmp_path):
    """混合 zip、文件夹、散 .blf：三种来源各按各的规则落位。"""
    folder = tmp_path / "AHT"
    in_folder = _touch(folder / "sub" / "in_folder.blf", b"f")
    loose = _touch(tmp_path / "loose.blf", b"l")
    archive = _make_zip(tmp_path / "pack.zip", {"nested/in_zip.blf": b"z"})

    result = source_resolver.resolve([folder, loose, archive])
    by_blf = {c.blf: c.output for c in result}

    assert by_blf[in_folder] == tmp_path / "AHT_t" / "sub" / "in_folder_t.mdf"
    assert by_blf[loose] == tmp_path / "loose_t.mdf"
    assert by_blf[tmp_path / "pack" / "nested" / "in_zip.blf"] == (
        tmp_path / "pack_t" / "nested" / "in_zip_t.mdf")


def test_zip_without_blf_yields_empty_list(tmp_path):
    archive = _make_zip(tmp_path / "empty.zip", {"notes.txt": b"x"})
    assert source_resolver.resolve([archive]) == []
    assert (tmp_path / "empty").is_dir()


def test_zip_extension_and_inner_blf_are_case_insensitive(tmp_path):
    archive = _make_zip(tmp_path / "Data.ZIP", {
        "UPPER.BLF": b"a",
        "Mixed.Blf": b"bb",
    })
    result = source_resolver.resolve([archive])
    assert sorted(c.blf.name for c in result) == ["Mixed.Blf", "UPPER.BLF"]
    assert sorted(c.output.name for c in result) == ["Mixed_t.mdf", "UPPER_t.mdf"]
    assert (tmp_path / "Data").is_dir()  # 主名只去掉最后一个扩展名


def test_zip_stem_keeps_dots_before_final_extension(tmp_path):
    archive = _make_zip(tmp_path / "run.2026.zip", {"a.blf": b"x"})
    [candidate] = source_resolver.resolve([archive])
    assert (tmp_path / "run.2026" / "a.blf").is_file()
    assert candidate.output == tmp_path / "run.2026_t" / "a_t.mdf"


def test_zip_extracting_cb_reports_archive_name_before_candidates(tmp_path):
    archive = _make_zip(tmp_path / "A02.zip", {"a.blf": b"x", "b.blf": b"y"})
    extracting = []
    counts = []
    source_resolver.resolve(
        [archive],
        progress_cb=counts.append,
        extracting_cb=extracting.append,
    )
    assert extracting == ["A02.zip"]
    assert counts == [1, 2]


def _make_7z(archive_path: Path, members: dict[str, bytes]) -> Path:
    """合成 7z（py7zr），成员名用包内相对路径。"""
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with py7zr.SevenZipFile(archive_path, "w") as archive:
        for name, data in members.items():
            archive.writestr(data, name)
    return archive_path


def test_7z_source_extracts_beside_archive_and_mirrors_output(tmp_path):
    """7z → 同级解压根 `<主名>/`，候选输出在同级 `<主名>_t/` 镜像树。"""
    archive = _make_7z(tmp_path / "A02.7z", {
        "20260917/run001.blf": b"abc",
        "run000.blf": b"xy",
        "notes.txt": b"skip",
    })
    result = source_resolver.resolve([archive])
    extract_root = tmp_path / "A02"
    assert extract_root.is_dir()
    assert (extract_root / "20260917" / "run001.blf").is_file()
    assert [(c.display, c.output, c.size) for c in result] == [
        (r"20260917\run001.blf",
         tmp_path / "A02_t" / "20260917" / "run001_t.mdf", 3),
        ("run000.blf", tmp_path / "A02_t" / "run000_t.mdf", 2),
    ]
    assert archive.is_file()


def _make_rar(archive_path: Path, members: dict[str, bytes]) -> Path:
    """合成最小 RAR4（仅 store），供无 rar 写入器的环境造夹具；tar 可解。"""
    import binascii
    import struct

    def header_crc(data: bytes) -> int:
        return binascii.crc32(data) & 0xFFFF

    out = bytearray(b"Rar!\x1a\x07\x00")
    ah_after = (bytes([0x73]) + struct.pack("<H", 0) + struct.pack("<H", 13)
                + struct.pack("<H", 0) + struct.pack("<I", 0))
    out += struct.pack("<H", header_crc(ah_after)) + ah_after
    for name, data in members.items():
        name_b = name.replace("\\", "/").encode("utf-8")
        head_size = 32 + len(name_b)
        after = bytearray()
        after.append(0x74)
        after += struct.pack("<H", 0)  # flags
        after += struct.pack("<H", head_size)
        after += struct.pack("<I", len(data))
        after += struct.pack("<I", len(data))
        after.append(2)  # Windows
        after += struct.pack("<I", binascii.crc32(data) & 0xFFFFFFFF)
        after += struct.pack("<I", 0)  # ftime
        after.append(20)  # unp ver
        after.append(0x30)  # store
        after += struct.pack("<H", len(name_b))
        after += struct.pack("<I", 0x20)
        after += name_b
        out += struct.pack("<H", header_crc(after)) + after
        out += data
    eh_after = bytes([0x7b]) + struct.pack("<HH", 0, 7)
    out += struct.pack("<H", header_crc(eh_after)) + eh_after
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(out)
    return archive_path


def test_rar_source_extracts_beside_archive_and_mirrors_output(tmp_path):
    """rar → 同级解压根 `<主名>/`，候选与镜像输出树规则与 zip 相同。"""
    archive = _make_rar(tmp_path / "A02.rar", {
        "20260917/run001.blf": b"abc",
        "run000.blf": b"xy",
        "notes.txt": b"skip",
    })
    result = source_resolver.resolve([archive])
    extract_root = tmp_path / "A02"
    assert extract_root.is_dir()
    assert (extract_root / "20260917" / "run001.blf").is_file()
    assert [(c.display, c.output, c.size) for c in result] == [
        (r"20260917\run001.blf",
         tmp_path / "A02_t" / "20260917" / "run001_t.mdf", 3),
        ("run000.blf", tmp_path / "A02_t" / "run000_t.mdf", 2),
    ]
    assert archive.is_file()


@pytest.mark.parametrize("make_archive,name", [
    (_make_rar, "A02.rar"),
    (_make_7z, "A02.7z"),
])
def test_rar_or_7z_reextract_keeps_archive_and_output_tree(
        tmp_path, make_archive, name):
    """再次拖入同一 rar/7z：删解压根重建；压缩包与已有 `<主名>_t/` 保留。"""
    archive = make_archive(tmp_path / name, {"keep.blf": b"new"})
    extract_root = tmp_path / "A02"
    stale = _touch(extract_root / "stale.blf", b"old")
    prior = _touch(tmp_path / "A02_t" / "prior_t.mdf", b"keep-me")

    result = source_resolver.resolve([archive])

    assert not stale.exists()
    assert (extract_root / "keep.blf").read_bytes() == b"new"
    assert prior.read_bytes() == b"keep-me"
    assert archive.is_file()
    assert [c.display for c in result] == ["keep.blf"]


@pytest.mark.parametrize("make_archive,name", [
    (_make_rar, "Data.RAR"),
    (_make_7z, "Data.7Z"),
])
def test_rar_or_7z_extension_case_insensitive(tmp_path, make_archive, name):
    archive = make_archive(tmp_path / name, {
        "UPPER.BLF": b"a",
        "Mixed.Blf": b"bb",
    })
    result = source_resolver.resolve([archive])
    assert sorted(c.blf.name for c in result) == ["Mixed.Blf", "UPPER.BLF"]
    assert (tmp_path / "Data").is_dir()


def test_rar_fails_clearly_when_system_tar_missing(tmp_path, monkeypatch):
    """缺少 tar 时 rar 条目失败并说明缺少解压组件，不静默丢弃。"""
    archive = _make_rar(tmp_path / "A02.rar", {"a.blf": b"x"})
    monkeypatch.setattr(
        source_resolver, "_system_tar",
        lambda: tmp_path / "no-such-tar.exe")
    with pytest.raises(RuntimeError, match=r"缺少系统解压组件"):
        source_resolver.resolve([archive])


def test_mixed_rar_7z_folder_and_loose_each_follow_own_rule(tmp_path):
    """混合 rar、7z、文件夹、散 .blf：各按各的规则落位。"""
    folder = tmp_path / "AHT"
    in_folder = _touch(folder / "sub" / "in_folder.blf", b"f")
    loose = _touch(tmp_path / "loose.blf", b"l")
    rar = _make_rar(tmp_path / "pack.rar", {"nested/in_rar.blf": b"r"})
    seven = _make_7z(tmp_path / "bag.7z", {"nested/in_7z.blf": b"z"})

    result = source_resolver.resolve([folder, loose, rar, seven])
    by_blf = {c.blf: c.output for c in result}

    assert by_blf[in_folder] == tmp_path / "AHT_t" / "sub" / "in_folder_t.mdf"
    assert by_blf[loose] == tmp_path / "loose_t.mdf"
    assert by_blf[tmp_path / "pack" / "nested" / "in_rar.blf"] == (
        tmp_path / "pack_t" / "nested" / "in_rar_t.mdf")
    assert by_blf[tmp_path / "bag" / "nested" / "in_7z.blf"] == (
        tmp_path / "bag_t" / "nested" / "in_7z_t.mdf")


def test_corrupt_zip_fails_with_readable_reason_and_no_half_root(tmp_path):
    """损坏 zip：失败并带可读原因；不留下半截解压根。"""
    archive = tmp_path / "bad.zip"
    archive.write_bytes(b"not-a-zip-at-all")
    with pytest.raises(RuntimeError, match=r"解压失败|无法|损坏|识别") as excinfo:
        source_resolver.resolve([archive])
    assert str(excinfo.value)  # 用户能读的一句
    assert not (tmp_path / "bad").exists()


def test_corrupt_7z_fails_with_readable_reason_and_no_half_root(tmp_path):
    """损坏 7z：失败并带可读原因；不留下半截解压根。"""
    archive = tmp_path / "bad.7z"
    archive.write_bytes(b"not-a-7z-at-all")
    with pytest.raises(RuntimeError, match=r"解压失败"):
        source_resolver.resolve([archive])
    assert not (tmp_path / "bad").exists()


def _make_encrypted_zip(zip_path: Path) -> Path:
    """合成带加密标志的 zip（flag 置位即可被识别；不依赖真实 ZipCrypto）。"""
    import struct

    buf_path = zip_path.with_suffix(".plain.zip")
    _make_zip(buf_path, {"a.blf": b"secret-payload"})
    data = bytearray(buf_path.read_bytes())
    buf_path.unlink()
    for sig, flag_at in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        i = 0
        while True:
            i = data.find(sig, i)
            if i < 0:
                break
            flags = struct.unpack_from("<H", data, i + flag_at)[0]
            struct.pack_into("<H", data, i + flag_at, flags | 0x1)
            i += 4
    zip_path.write_bytes(data)
    return zip_path


def test_encrypted_zip_fails_without_half_root(tmp_path):
    """加密 zip：说明不支持加密；不留下半截解压根。"""
    archive = _make_encrypted_zip(tmp_path / "enc.zip")
    with pytest.raises(RuntimeError, match=r"不支持加密压缩包"):
        source_resolver.resolve([archive])
    assert not (tmp_path / "enc").exists()


def test_encrypted_7z_fails_without_half_root(tmp_path):
    """加密 7z：说明不支持加密；不留下半截解压根。"""
    archive = tmp_path / "enc.7z"
    with py7zr.SevenZipFile(
            archive, "w", password="secret", header_encryption=True) as sz:
        sz.writestr(b"payload", "a.blf")
    with pytest.raises(RuntimeError, match=r"不支持加密压缩包"):
        source_resolver.resolve([archive])
    assert not (tmp_path / "enc").exists()


def test_zip_path_traversal_fails_and_writes_nothing_outside(tmp_path):
    """条目名含 ../：包失败；解压根之外不出现被穿越写出的文件。"""
    archive = _make_zip(tmp_path / "trav.zip", {
        "../outside.blf": b"evil",
        "ok.blf": b"good",
    })
    outside = tmp_path / "outside.blf"
    with pytest.raises(RuntimeError):
        source_resolver.resolve([archive])
    assert not outside.exists()
    assert not (tmp_path / "trav").exists()


def test_7z_path_traversal_fails_and_writes_nothing_outside(tmp_path, monkeypatch):
    """7z 条目含 ../：拒绝；解压根之外无文件。

    py7zr 写入侧本身拒写越界名，故用同级合法包 + getnames 注入越界条目，
    断言解析接缝仍拒绝且不写出解压根之外。
    """
    archive = _make_7z(tmp_path / "trav.7z", {"ok.blf": b"good"})
    real_cls = py7zr.SevenZipFile

    class _NamesInjected(real_cls):
        def getnames(self):
            return ["../outside.blf", "ok.blf"]

    monkeypatch.setattr(source_resolver.py7zr, "SevenZipFile", _NamesInjected)
    with pytest.raises(RuntimeError, match=r"越界|解压失败"):
        source_resolver.resolve([archive])
    assert not (tmp_path / "outside.blf").exists()
    assert not (tmp_path / "trav").exists()


def test_nested_archives_are_not_extracted_or_treated_as_blf(tmp_path):
    """包内嵌套 zip/rar/7z：只解一层；内层包不当成 .blf，也不再解开。"""
    inner = _make_zip(tmp_path / "_inner.zip", {"hidden.blf": b"nope"})
    archive = _make_zip(tmp_path / "outer.zip", {
        "keep.blf": b"yes",
        "nested.zip": inner.read_bytes(),
        "nested.7z": b"not-really-7z",
        "nested.rar": b"not-really-rar",
    })
    inner.unlink()
    result = source_resolver.resolve([archive])
    extract_root = tmp_path / "outer"
    assert [c.display for c in result] == ["keep.blf"]
    assert (extract_root / "nested.zip").is_file()
    assert (extract_root / "nested.7z").is_file()
    assert (extract_root / "nested.rar").is_file()
    assert not (extract_root / "hidden.blf").exists()


def test_cancel_during_archive_traversal_cleans_root_keeps_output_tree(tmp_path):
    """解压后遍历途中取消：抛取消信号；未完成解压根删除；已有 `_t/` 保留。"""
    archive = _make_zip(tmp_path / "A02.zip", {
        "a.blf": b"1",
        "b.blf": b"2",
        "c.blf": b"3",
    })
    prior = _touch(tmp_path / "A02_t" / "prior_t.mdf", b"keep")
    stop = threading.Event()

    def progress(_count):
        stop.set()

    with pytest.raises(ConversionCancelled):
        source_resolver.resolve(
            [archive], progress_cb=progress, cancel_cb=stop.is_set)
    assert not (tmp_path / "A02").exists()
    assert prior.read_bytes() == b"keep"


def test_unwritable_parent_fails_without_extract_elsewhere(tmp_path, monkeypatch):
    """父目录不可写：失败并说明原因；其他位置不出现解压根。"""
    archive = _make_zip(tmp_path / "A02.zip", {"a.blf": b"x"})
    real_mkdir = Path.mkdir

    def deny_extract_root(self, *args, **kwargs):
        if self.name == "A02":
            raise PermissionError(13, "拒绝访问", str(self))
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny_extract_root)
    with pytest.raises(RuntimeError, match=r"不可写"):
        source_resolver.resolve([archive])
    assert not (tmp_path / "A02").exists()
    # 不改解到别处：临时目录旁没有冒出同名解压根
    assert not list(tmp_path.glob("**/A02/a.blf"))


@pytest.mark.parametrize("make_archive,name", [
    (_make_zip, "A02.zip"),
    (_make_rar, "A02.rar"),
    (_make_7z, "A02.7z"),
])
def test_reextract_logs_reextracted_for_all_formats(
        tmp_path, make_archive, name, caplog):
    """强制重解压在日志中留「已重新解压」，三种格式都成立。"""
    archive = make_archive(tmp_path / name, {"a.blf": b"x"})
    _touch(tmp_path / "A02" / "stale.blf", b"old")
    with caplog.at_level(logging.INFO):
        source_resolver.resolve([archive])
    assert "已重新解压" in caplog.text


def test_archive_unreadable_subdir_skipped_with_log(tmp_path, monkeypatch, caplog):
    """解开后的目录里无权限子目录：跳过并记日志，不中断其余候选。"""
    archive = _make_zip(tmp_path / "pack.zip", {
        "ok.blf": b"1",
        "locked/hidden.blf": b"2",
    })
    locked = tmp_path / "pack" / "locked"
    real_scandir = os.scandir

    def deny_locked(path, *args, **kwargs):
        if os.path.normcase(str(path)) == os.path.normcase(str(locked)):
            raise PermissionError(13, "拒绝访问")
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", deny_locked)
    with caplog.at_level(logging.WARNING):
        result = source_resolver.resolve([archive])
    assert [c.display for c in result] == ["ok.blf"]
    assert str(locked) in caplog.text
