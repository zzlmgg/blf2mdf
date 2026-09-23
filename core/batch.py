"""批量编排：待转清单 → 逐文件转换（顺序 / 进度 / 失败继续 / 取消收口）。

「批次」这一概念的**唯一定义处**（见 CONTEXT.md「批次」）：界面层不得自己写
转换循环。输入是来源解析（core/source_resolver.py）产出的候选清单加一套
通道↔DBC 绑定，按清单顺序逐个调用既有的单文件转换（core/converter.py），
每个文件独立汇报成功结果或失败原因——单个文件失败**不中断**批次。

批内串行、文件内并行（spec.md「Implementation Decisions」）：单份 BLF 可达 GB 级、
转换本身已吃满 CPU 与内存，批内并发会让内存峰值成倍上升而没有相应收益。

无 Qt 依赖。
"""
from dataclasses import dataclass, field

from core.blf_reader import ConversionCancelled, check_cancel
from core.converter import ConversionResult, convert
from core.dbc_loader import DbcDef
from core.source_resolver import Candidate


class BatchCancelled(ConversionCancelled):
    """批次取消：携带已完成文件的结局（取消 ≠ 丢弃前面已完成的劳动成果）。

    界面按「已取消」处理（沿用「取消信号」契约，except ConversionCancelled
    一网打尽），并可从 outcomes 取「已完成 X / N」与逐文件耗时写日志——
    否则调用方只能从进度文案里反推，那是脆弱的旁路。
    """

    def __init__(self, outcomes: list["FileOutcome"]):
        super().__init__()
        self.outcomes = outcomes


@dataclass
class FileOutcome:
    """一个文件的结局：成功带转换结果，失败带原因（二者互斥）。"""
    candidate: Candidate
    result: ConversionResult | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        """成功 ⟺ 有转换结果（失败分支只有原因，没有结果）。"""
        return self.result is not None


@dataclass
class BatchResult:
    """批次结局：逐文件结局（顺序 = 清单顺序）+ 可区分的整体结论。

    全部成功（failures 为空）与部分失败（failures 非空、含失败清单）可区分；
    已转成功文件的产物不受后续失败影响（无残留契约的批次语义）。
    """
    outcomes: list[FileOutcome] = field(default_factory=list)

    @property
    def failures(self) -> list[FileOutcome]:
        """失败清单（含各自的原因），按清单顺序。"""
        return [outcome for outcome in self.outcomes if not outcome.ok]

    @property
    def all_succeeded(self) -> bool:
        return not self.failures


def run_batch(candidates: list[Candidate],
              bindings: dict[int, DbcDef | None], *,
              progress_cb=None, cancel_cb=None,
              parallel: bool = True, raw_export: bool = False,
              stats_export: bool = True) -> BatchResult:
    """按顺序转换候选清单里的每个文件 → 逐文件结局。

    单个文件失败不中断批次：失败原因记入该文件的 FileOutcome.error，剩余
    文件继续；失败文件的输出遵守无残留契约（不留半成品，该路径上一次成功的
    产物保留）——清理归单文件转换（converter / mdf_writer），本模块不复述。

    progress_cb(stage, percent)：整体进度 = (i + 文件内进度)/N，单调不降；
    stage = 「第 i/N 个 · <文件内阶段>」（i 从 1 起）。

    parallel 是**文件内**解码开关（批内恒串行，见模块 docstring）；默认 True
    同现界面（gui/main_window.py 的 ConvertWorker），慢机器或单测可关。
    其余转换开关原样转发给单文件转换（默认同 convert）。

    cancel_cb() 置位 → 停止队列（当前在转文件按其自身契约清理，已完成文件
    的产物全部保留）并抛 BatchCancelled（携带已完成结局，见该类）——批次
    取消 ≠ 单文件取消（见 CONTEXT.md「取消信号」）。
    """
    outcomes: list[FileOutcome] = []
    total = len(candidates)
    for index, candidate in enumerate(candidates):
        try:
            check_cancel(cancel_cb)     # 队列检查点：取消则不开始下一个文件
            result = convert(
                str(candidate.blf), bindings, str(candidate.output),
                progress_cb=_batch_progress_cb(progress_cb, index, total),
                raw_export=raw_export, stats_export=stats_export,
                parallel=parallel, cancel_cb=cancel_cb)
        except ConversionCancelled as exc:
            # 取消不是失败：不记入失败清单，停止队列（已完成结局随异常交出）
            raise BatchCancelled(outcomes) from exc
        except Exception as exc:        # noqa: BLE001 — 单个文件失败不中断批次
            outcomes.append(FileOutcome(
                candidate, error=str(exc) or type(exc).__name__))
        else:
            outcomes.append(FileOutcome(candidate, result=result))
    return BatchResult(outcomes=outcomes)


def _batch_progress_cb(progress_cb, index: int, total: int):
    """整体进度映射：文件内进度 → 整体百分比，stage 加「第 i/N 个 · 」前缀。

    第 index（0 起，i = index + 1）个文件的文件内进度 p ∈ [0, 100] → 整体
    percent = (index + p/100) × 100 / total。文件内进度本身单调不降（convert
    契约），index 逐文件递增，故整体单调不降。
    """
    if progress_cb is None:
        return None

    def report(stage: str, percent: float) -> None:
        progress_cb(f"第 {index + 1}/{total} 个 · {stage}",
                    (index + percent / 100) / total * 100)

    return report
