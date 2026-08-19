"""CANoe 参考质量校验（固定验收脚本）：转换 → 全量对比 → 需求线判定。

需求线硬门（退出码 0 = 两组全部满足）：
- 信号组全部信号：值（reference 容差 isclose 1e-6/1e-6, equal_nan=True）+ 时间戳逐位；
- '1s' 统计组 t 轴 ≤1ns 网格容差（ch0 StdData，网格构造同构，代表全部统计组；
  起点带毫秒残值时两侧 float64 有 ULP 级表示噪声，1ns 以下不算差异）。

报告层（不参与退出码，但完整列出供核对）：header/structure 差异（头部 comment、
组序、组内通道序、存储表示等已知格式差异）+ 统计组逐点值差异（CANoe 自身时钟域，
已知不可复现）——若出现超出已知清单的新差异行，说明转换质量漂移，需人工介入。

用法：
    python tools/verify_vs_canoe.py [--skip-convert] [--outdir outputs/verify_canoe]

--skip-convert：复用 --outdir 下最新的 cmp_*_A19G1.mdf / cmp_*_AHT.mdf，不重新转换。
转换产物与报告均写入 --outdir（默认 outputs/verify_canoe/，用户按需管理）。
"""
import argparse
import contextlib
import multiprocessing
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import blf_reader, converter, project_loader
from gui.binding import decide_bindings
from tools.mdf_compare import compare_files_reference

CCU3 = ROOT / "inputs/dbc_ccu3.0"
# CANoe 参考统计布局（22 项/通道，10 项统计名的组内偏移；probe 实测）
_STATS_IDX = "StdData:4,StdDataRate:5,ExtData:6,ExtDataRate:7," \
             "StdRemote:8,StdRemoteRate:9,ExtRemote:10,ExtRemoteRate:11," \
             "ErrorFrames:12,ErrorFrameRate:13"

SAMPLES = [
    ("A19G1",
     ROOT / "inputs/blf/A19G1_ACFCAN_00112_20260614_141114.blf",
     ROOT / "inputs/mdf_canoe/A19G1.mdf"),
    ("AHT",
     ROOT / "inputs/blf/AHT_ACFCANPUB_20260317_210430_59125089-"
            "ACFCAN_20260317_210930_59125099.blf",
     ROOT / "inputs/mdf_canoe/AHT.mdf"),
]

_HARD_STATS_PREFIX = "统计 t 轴"  # stats 维度中属硬门的行前缀


class _Tee:
    """同时写多个流（终端 + 报告文件）。"""

    def __init__(self, *files):
        self.files = files

    def write(self, s):
        for f in self.files:
            f.write(s)

    def flush(self):
        for f in self.files:
            f.flush()


def _stats_layout():
    offsets = {}
    for pair in _STATS_IDX.split(","):
        k, v = pair.split(":")
        offsets[k.strip()] = int(v.strip())
    return 22, offsets


def _latest_artifact(outdir, project):
    hits = sorted(outdir.glob(f"cmp_*_{project}.mdf"), key=lambda p: p.stat().st_mtime)
    return hits[-1] if hits else None


def convert_one(project, blf, out):
    """无头同参链路：与 GUI ConvertWorker.run 逐参一致（raw_export=False, parallel=True）。"""
    dbcs = project_loader.load_project(CCU3, project)
    mapping = project_loader.load_mapping(CCU3 / "dbc_对应关系.txt")
    auto = project_loader.auto_bindings(dbcs, mapping)
    channels = blf_reader.probe_channels(str(blf))
    rows = decide_bindings(channels, auto, None, [d.path for d in dbcs])
    by_path = {d.path: d for d in dbcs}
    bindings = {row.channel: by_path[row.binding] for row in rows if row.binding}
    print(f"    BLF通道={channels} 绑定={sorted(bindings)}")
    result = converter.convert(str(blf), bindings, str(out),
                               raw_export=False, parallel=True,
                               stats_export=True)
    print(f"    转换耗时 {result.duration_seconds:.3f}s warnings={result.warnings}")
    for s in result.summaries:
        print(f"      CAN{s.channel}: bound={s.bound} 解码帧={s.decoded_frames} "
              f"信号数={s.signal_count} 未知帧={s.unknown_frames} 未知ID={s.unknown_ids}")


def compare_one(ours, canoe):
    """三次调用（维度隔离）：报告层 header+structure / 硬门 values / stats 分类。"""
    layout = _stats_layout()
    meta = compare_files_reference(ours, canoe, stats_ref_layout=layout,
                                   dims=frozenset({"header", "structure"}))
    val = compare_files_reference(ours, canoe, stats_ref_layout=layout,
                                  dims=frozenset({"values"}))
    stats = compare_files_reference(ours, canoe, stats_ref_layout=layout,
                                    dims=frozenset({"stats"}))
    stat_t = [d for d in stats if d.startswith(_HARD_STATS_PREFIX)]
    stat_rest = [d for d in stats if not d.startswith(_HARD_STATS_PREFIX)]
    return meta, val, stat_t, stat_rest


def run_sample(project, blf, canoe, outdir, skip_convert, ts):
    if skip_convert:
        ours = _latest_artifact(outdir, project)
        if ours is None:
            print(f"  [SKIP] --skip-convert 但 {outdir} 下无 cmp_*_{project}.mdf，跳过")
            return None
        print(f"== {project} ==（复用产物 {ours.name}）")
    else:
        ours = outdir / f"cmp_{ts}_{project}.mdf"
        print(f"== {project} ==（转换中…）")
        convert_one(project, blf, ours)
        print(f"  产物: {ours}")

    meta, val, stat_t, stat_rest = compare_one(ours, canoe)

    print(f"  参考: {canoe}")
    print("\n  硬门（需求线）:")
    print(f"    values: {len(val)} 差异（信号组值 + 时间戳逐位）")
    for d in val:
        print(f"      - {d}")
    print(f"    stats t 轴: {len(stat_t)} 差异")
    for d in stat_t:
        print(f"      - {d}")
    if not val and not stat_t:
        print("    判定: PASS")
    else:
        print("    判定: FAIL")

    print("\n  报告层（不参与退出码）:")
    print(f"    header+structure: {len(meta)} 差异")
    for d in meta[:5]:
        print(f"      - {d}")
    if len(meta) > 5:
        print(f"      …（其余 {len(meta) - 5} 条见报告文件）")
    print(f"    stats 值: {len(stat_rest)} 差异")
    for d in stat_rest[:5]:
        print(f"      - {d}")
    if len(stat_rest) > 5:
        print(f"      …（其余 {len(stat_rest) - 5} 条见报告文件）")
    print()
    return (project, ours, canoe, meta, val, stat_t, stat_rest)


def main(argv=None):
    p = argparse.ArgumentParser(description="CANoe 参考质量校验（转换+全量对比+需求线判定）")
    p.add_argument("--skip-convert", action="store_true",
                   help="复用 --outdir 下最新产物，不重新转换")
    p.add_argument("--outdir", default="outputs/verify_canoe",
                   help="转换产物与报告目录（默认 outputs/verify_canoe）")
    args = p.parse_args(argv)

    outdir = ROOT / args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = outdir / f"verify_vs_canoe_{ts}.md"
    with open(report, "w", encoding="utf-8") as fp:
        fp.write(f"# CANoe 参考质量校验报告\n\n"
                 f"- 命令: {' '.join(sys.argv)}\n"
                 f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                 f"- 判定口径: 需求线硬门 = 信号组值+时间戳逐位 + 统计组 t 轴逐位；"
                 f"文件层差异（header/structure/stats 值）属报告层\n\n")
        tee = _Tee(sys.stdout, fp)
        with contextlib.redirect_stdout(tee):
            fails = []
            for project, blf, canoe in SAMPLES:
                res = run_sample(project, blf, canoe, outdir, args.skip_convert, ts)
                if res is None:
                    continue
                _, ours, canoe, meta, val, stat_t, stat_rest = res
                fp.write(f"\n--- {project} 报告层明细 ---\n")
                for d in meta:
                    fp.write(f"- {d}\n")
                for d in stat_rest:
                    fp.write(f"- {d}\n")
                if not meta and not stat_rest:
                    fp.write("（无）\n")
                if not val and not stat_t:
                    continue
                fails.append(project)
            print(f"=== 汇总: 硬门失败 {'/'.join(fails) if fails else '无'}，"
                  f"退出码 {'1' if fails else '0'} ===")
    print(f"[报告已写入] {report}")
    return 1 if fails else 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
