"""source_resolver：来源解析（拖入路径集合 → 候选清单）与输出路径映射。"""
import logging
import os
import subprocess
import sys
import threading
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
