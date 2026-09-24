"""source_resolver：来源解析（拖入路径集合 → 候选清单）与输出路径映射。"""
import logging
import os
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

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
