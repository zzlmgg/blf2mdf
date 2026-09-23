"""批量编排：待转清单 → 逐文件转换（顺序 / 进度 / 失败继续 / 取消收口）。

不用 Qt（批量编排接缝，见 .scratch/batch-import/spec.md Testing Decisions）；
清单用真实合成的 BLF 经来源解析产出（与候选条目同形）。
"""
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import PROJECT_ROOT
from core import batch, source_resolver
from core.blf_reader import ConversionCancelled
from core.converter import convert
from core.dbc_loader import load

INLINE_DBC = '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 100 ABC: 8 ECU
 SG_ Speed : 0|16@1+ (0.01,0) [0|655.35] "km/h" ECU
'''


@pytest.fixture()
def dbc_path(tmp_path) -> str:
    path = tmp_path / "t.dbc"
    path.write_text(INLINE_DBC, encoding="utf-8")
    return str(path)


def _write_blf(path: Path, offsets=(0, 1, 2)) -> Path:
    """合成 BLF：通道 1 上若干帧（DBC 已知 ID 0x100，Speed 随序号递增）。"""
    import can

    path.parent.mkdir(parents=True, exist_ok=True)
    with can.BLFWriter(str(path)) as writer:
        for i, offset in enumerate(offsets):
            writer.on_message_received(can.Message(
                arbitration_id=100, is_extended_id=False,
                data=(100 + i).to_bytes(2, "little") + bytes(6),
                channel=1, timestamp=1784716800.0 + offset))
    return path


def _write_rich_blf(path: Path, seed: int = 1) -> Path:
    """对拍用合成 BLF：两通道的 DBC 已知帧 + 一个未知 ID 帧（内容随 seed 变化）。"""
    import can

    path.parent.mkdir(parents=True, exist_ok=True)
    with can.BLFWriter(str(path)) as writer:
        for i, offset in enumerate((0, 1, 2)):
            for channel in (1, 2):
                writer.on_message_received(can.Message(
                    arbitration_id=100, is_extended_id=False,
                    data=(seed * 100 + i).to_bytes(2, "little") + bytes(6),
                    channel=channel, timestamp=1784716800.0 + offset))
        writer.on_message_received(can.Message(
            arbitration_id=999, is_extended_id=False, data=bytes(8),
            channel=1, timestamp=1784716803.0))
    return path


def _corrupt_blf(path: Path) -> Path:
    """不可读的伪 BLF（内容无效）：用于制造单个文件的转换失败。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a blf at all" * 8)
    return path


@pytest.fixture()
def three_blfs(tmp_path) -> list[Path]:
    root = tmp_path / "AHT"
    return [_write_blf(root / "sub" / f"run{i:03d}.blf") for i in range(1, 4)]


def _bindings(dbc_path) -> dict:
    return {1: load(dbc_path)}


def _run(candidates, dbc_path, **overrides):
    """跑一批。单测默认关文件内并行（省进程池开销，与断言无关）；
    对拍硬门自己显式传 parallel（两种模式都要过）。"""
    overrides.setdefault("parallel", False)
    return batch.run_batch(candidates, _bindings(dbc_path), **overrides)


def test_converts_every_file_in_order(tmp_path, three_blfs, dbc_path):
    """按顺序转换 N 个文件：逐文件返回成功结果，产物落在清单给定的输出路径。"""
    candidates = source_resolver.resolve([tmp_path / "AHT"])
    result = _run(candidates, dbc_path)

    assert [o.candidate.blf for o in result.outcomes] == \
        [c.blf for c in candidates]                       # 顺序 = 清单顺序
    assert all(o.ok for o in result.outcomes)
    assert [o.result.duration_seconds for o in result.outcomes] == [2.0, 2.0, 2.0]
    for candidate in candidates:
        assert candidate.output.is_file(), candidate.output
    assert result.all_succeeded and result.failures == []   # 全部成功分支


def test_one_failure_does_not_stop_the_batch(tmp_path, three_blfs, dbc_path):
    """单个文件失败不中断批次：失败原因记在该文件上，剩余文件照常转换；
    返回结构能区分「全部成功」与「部分失败」（后者含失败清单）。"""
    broken = _corrupt_blf(tmp_path / "AHT" / "sub" / "run002.blf")
    candidates = source_resolver.resolve([tmp_path / "AHT"])
    result = _run(candidates, dbc_path)

    assert [o.candidate.blf for o in result.outcomes] == \
        [c.blf for c in candidates]
    by_name = {o.candidate.blf.name: o for o in result.outcomes}
    failed = by_name["run002.blf"]
    assert failed.candidate.blf == broken
    assert failed.error, "失败文件必须带原因"
    assert failed.result is None
    assert by_name["run001.blf"].ok and by_name["run003.blf"].ok, \
        "失败前后的文件照常转换"
    assert not result.all_succeeded
    assert result.failures == [failed]
    assert candidates[0].output.is_file() and candidates[2].output.is_file(), \
        "失败前后的文件照常产出"
    assert not candidates[1].output.exists()


def test_failed_file_keeps_the_previous_product(tmp_path, dbc_path):
    """失败文件的输出遵守无残留：该路径上一次成功的产物保留（失败 ≠ 全清），
    不留半成品；同批其他文件的产物不受影响。"""
    root = tmp_path / "AHT"
    _write_blf(root / "sub" / "run001.blf")
    _write_blf(root / "sub" / "run002.blf")
    candidates = source_resolver.resolve([root])
    assert _run(candidates, dbc_path).all_succeeded
    kept = candidates[1].output.read_bytes()

    _corrupt_blf(candidates[1].blf)             # 同一个文件，第二次读不出来
    again = _run(candidates, dbc_path)

    assert not again.all_succeeded
    assert candidates[1].output.read_bytes() == kept, "上一次成功的产物应保留"
    assert not candidates[1].output.with_suffix(".mf4").exists()
    assert candidates[0].output.is_file(), "同批其他文件照常产出"


def test_failed_write_leaves_no_empty_tree_branch(tmp_path, dbc_path,
                                                  monkeypatch):
    """写出阶段失败的产物位置不在镜像树里留空枝（空目录不该出现）；
    注入点在 os.replace——save 已写出半成品、目录链已建好，覆盖整条清理链。"""
    import os

    from core import mdf_writer

    root = tmp_path / "AHT"
    _write_blf(root / "a" / "run001.blf")
    _write_blf(root / "b" / "run002.blf")
    real_replace = os.replace

    def fail_run002(src, dst):
        if "run002" in str(dst):
            raise OSError("simulated replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(mdf_writer.os, "replace", fail_run002)
    result = _run(source_resolver.resolve([root]), dbc_path)

    assert [o.error for o in result.failures] == ["simulated replace failure"]
    assert (tmp_path / "AHT_t" / "a" / "run001_t.mdf").is_file()
    assert not (tmp_path / "AHT_t" / "b").exists(), "失败文件不留空目录"
    assert not (tmp_path / "AHT_t" / "b" / "run002_t.mf4").exists()


@pytest.mark.parametrize("parallel", [False, True])
def test_batch_output_is_identical_to_file_by_file(tmp_path, dbc_path, parallel):
    """硬门：批量转 N 份的输出，与逐个单文件转同一批的输出**逐位一致**。

    判定走既有对拍链（compare_files_identical：按组序号对齐、组序一致是硬性
    要求；维度 = 文件头 / 通道结构 / 逐点数值 / 统计组）。产物落位不同不影响
    判定——比较的是**内容**，不是路径。串行与并行解码各跑一遍（GUI 用并行）。
    """
    from tools.mdf_compare import compare_files_identical

    bindings = {1: load(dbc_path), 2: load(dbc_path)}
    root = tmp_path / "AHT"
    for i in (1, 2, 3):
        _write_rich_blf(root / "sub" / f"run{i:03d}.blf", seed=i)
    candidates = source_resolver.resolve([root])
    assert batch.run_batch(candidates, bindings, parallel=parallel).all_succeeded

    one_by_one = tmp_path / "by_hand"
    for index, candidate in enumerate(candidates):
        convert(str(candidate.blf), bindings,
                str(one_by_one / f"run{index:03d}.mdf"), parallel=parallel)

    for index, candidate in enumerate(candidates):
        diffs = compare_files_identical(
            str(one_by_one / f"run{index:03d}.mdf"), str(candidate.output))
        assert diffs == [], f"{candidate.display}: {diffs}"


def test_cancel_stops_queue_keeps_finished_products(tmp_path, dbc_path):
    """批次取消 ≠ 单文件取消：停止队列 + 当前在转文件按其自身契约清理 +
    已完成文件的产物全部保留（CONTEXT.md「取消信号」）。"""
    import threading

    root = tmp_path / "AHT"
    for i in (1, 2, 3):
        _write_blf(root / "sub" / f"run{i:03d}.blf")
    candidates = source_resolver.resolve([root])
    stop = threading.Event()
    stages = []

    def progress(stage, _percent):
        stages.append(stage)
        if stage.startswith("第 2/3 个"):      # 第二个文件一开工就取消
            stop.set()

    with pytest.raises(batch.BatchCancelled) as excinfo:
        _run(candidates, dbc_path, progress_cb=progress, cancel_cb=stop.is_set)

    assert [o.candidate.blf.name for o in excinfo.value.outcomes] == ["run001.blf"], \
        "取消信号携带已完成结局（界面据此记「已完成 X / N」）"
    assert candidates[0].output.is_file(), "已完成文件的产物保留"
    assert not candidates[1].output.exists(), "当前在转文件按其自身契约清理"
    assert not candidates[1].output.with_suffix(".mf4").exists()
    assert not candidates[2].output.exists(), "队列停止"
    assert not any(s.startswith("第 3/3 个") for s in stages), "后续文件不开工"


def test_cancel_between_files_stops_before_starting_the_next(tmp_path, dbc_path):
    """队列检查点：一个文件收尾后取消 → 下一个文件根本不开工（无进度、无产物）。"""
    import threading

    root = tmp_path / "AHT"
    for i in (1, 2):
        _write_blf(root / "sub" / f"run{i:03d}.blf")
    candidates = source_resolver.resolve([root])
    stop = threading.Event()
    stages = []

    def progress(stage, _percent):
        stages.append(stage)
        if stage.endswith("完成"):              # 第一个文件收尾即取消
            stop.set()

    with pytest.raises(ConversionCancelled) as excinfo:   # 取消信号基线契约一网打尽
        _run(candidates, dbc_path, progress_cb=progress, cancel_cb=stop.is_set)

    assert isinstance(excinfo.value, batch.BatchCancelled)
    assert len(excinfo.value.outcomes) == 1, "已完成的那个文件在取消信号里"
    assert candidates[0].output.is_file()
    assert not candidates[1].output.exists()
    assert not any(s.startswith("第 2/2 个") for s in stages)


def test_progress_is_monotonic_and_mapped_over_the_batch(tmp_path, three_blfs,
                                                         dbc_path):
    """整体进度 = (i + 文件内进度)/N：单调不降；每个文件的上报落在自己的区间；
    阶段文案带「第 i/N 个」前缀且保留文件内阶段（读取/解码/写 MDF）。"""
    candidates = source_resolver.resolve([tmp_path / "AHT"])
    calls = []
    _run(candidates, dbc_path,
         progress_cb=lambda stage, pct: calls.append((stage, pct)))

    total = len(candidates)
    percents = [pct for _, pct in calls]
    assert percents == sorted(percents), "整体进度应单调不降"
    assert percents[-1] == 100.0, "最后一个文件走完后应为 100"

    indexes, inner_stages = [], set()
    for stage, pct in calls:
        prefix, _, inner = stage.partition(" · ")
        index = int(prefix.removeprefix("第 ").removesuffix(" 个").split("/")[0])
        indexes.append(index)
        inner_stages.add(inner)
        assert (index - 1) * 100 / total <= pct <= index * 100 / total, stage
    assert indexes == sorted(indexes), "文件按清单顺序推进"
    assert set(indexes) == set(range(1, total + 1))
    assert {"读取 BLF", "写 MDF", "完成"} <= inner_stages, inner_stages


def test_module_has_no_qt_dependency():
    """无 Qt 依赖（可脱离界面直接单测）：单独导入本模块后 PySide6 不进 sys.modules。"""
    code = "import sys, core.batch; assert 'PySide6' not in sys.modules"
    proc = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
