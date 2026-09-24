"""CANoe 参考质量校验（固定验收脚本）：转换 → 全量对比 → 需求线判定。

需求线硬门（退出码 0 = 全部样例满足）：
- 信号组全部信号：值（reference 容差 isclose 1e-6/1e-6, equal_nan=True）+ 时间戳逐位；
- '1s' 统计组 t 轴 ≤1ns 网格容差（ch0 StdData，网格构造同构，代表全部统计组；
  起点带毫秒残值时两侧 float64 有 ULP 级表示噪声，1ns 以下不算差异）。记录空洞
  样例（参考侧在空洞处重启 1s 网格，长度必然不等）改按自产轴契约验收：前段一致 +
  末点一致 + 网格自洽，见 tools/mdf_compare._stats_t_axis_contract。

报告层（不参与退出码，但完整列出供核对）：header/structure 差异（头部 comment、
组序、组内通道序、存储表示等已知格式差异）+ 统计组逐点值差异（CANoe 自身时钟域，
已知不可复现）+ 统计 t 轴已知差异说明行——若出现超出已知清单的新差异行，
说明转换质量漂移，需人工介入。

用法：
    python tools/verify_vs_canoe.py [--skip-convert] [--outdir outputs/verify_canoe]

样本从仓库根 canoe_golden/ 发现，不写死 BLF/MDF 路径：
    canoe_golden/<平台>/<项目>/<BLF 主文件名>/source.blf
    canoe_golden/<平台>/<项目>/<BLF 主文件名>/canoe.mdf
平台名对应 DBC 根 inputs/dbc_<平台>（及其中 dbc_对应关系.txt）；项目名即
load_project 的项目文件夹。空平台不产生样本；样本目录缺 source.blf 或
canoe.mdf 则本次运行失败。

--skip-convert：复用 --outdir 下各样例最新的
cmp_<时间戳>_<平台>_<项目>_<样本>.mdf，不重新转换。
转换产物与报告均写入 --outdir（默认 outputs/verify_canoe/，用户按需管理）。
"""
import argparse
import contextlib
import multiprocessing
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import blf_reader, converter, project_loader
from gui.binding import decide_bindings
from tools.mdf_compare import KNOWN_DIFF_PREFIX, compare_files_reference

GOLDEN_ROOT = ROOT / "canoe_golden"
_SOURCE_NAME = "source.blf"
_CANOE_NAME = "canoe.mdf"
# CANoe 参考统计布局（22 项/通道，10 项统计名的组内偏移；probe 实测）
_STATS_IDX = "StdData:4,StdDataRate:5,ExtData:6,ExtDataRate:7," \
             "StdRemote:8,StdRemoteRate:9,ExtRemote:10,ExtRemoteRate:11," \
             "ErrorFrames:12,ErrorFrameRate:13"
_TS_RE = re.compile(r"\d{8}_\d{6}")


def dbc_root(platform):
    """平台名 → DBC 根目录（inputs/dbc_<平台>）。"""
    return ROOT / "inputs" / f"dbc_{platform}"


def discover_samples(golden_root=GOLDEN_ROOT):
    """枚举 canoe_golden/<平台>/<项目>/<样本>/ 下成对的 source.blf 与 canoe.mdf。

    返回 [(platform, project, sample, blf, canoe), ...]，按三层目录名排序。
    空平台（无项目或项目下无样本目录）不产生条目。第三层目录缺任一固定文件
    则抛出 FileNotFoundError，不跳过、不返回部分结果。
    """
    golden_root = Path(golden_root)
    if not golden_root.is_dir():
        raise FileNotFoundError(f"CANoe 金样本根不存在: {golden_root}")
    samples = []
    missing = []
    for platform_dir in sorted(p for p in golden_root.iterdir() if p.is_dir()):
        for project_dir in sorted(p for p in platform_dir.iterdir() if p.is_dir()):
            for sample_dir in sorted(p for p in project_dir.iterdir() if p.is_dir()):
                blf = sample_dir / _SOURCE_NAME
                canoe = sample_dir / _CANOE_NAME
                absent = [name for name, path in
                          ((_SOURCE_NAME, blf), (_CANOE_NAME, canoe))
                          if not path.is_file()]
                if absent:
                    rel = sample_dir.relative_to(golden_root)
                    missing.append(f"{rel} 缺 {', '.join(absent)}")
                    continue
                samples.append((
                    platform_dir.name, project_dir.name, sample_dir.name, blf, canoe,
                ))
    if missing:
        raise FileNotFoundError(
            "CANoe 金样本目录不完整，拒绝跳过:\n" + "\n".join(missing))
    return samples


SAMPLES = discover_samples()


def artifact_filename(ts, platform, project, sample):
    """产物名：时间戳 + 平台 + 项目 + 样本目录名，避免同项目多 BLF 撞名。"""
    return f"cmp_{ts}_{platform}_{project}_{sample}.mdf"


def latest_artifact(outdir, platform, project, sample):
    """按产物名精确匹配该样本最新的 cmp_<YYYYMMDD_HHMMSS>_….mdf。"""
    pattern = re.compile(
        rf"^cmp_{_TS_RE.pattern}_{re.escape(platform)}_{re.escape(project)}_"
        rf"{re.escape(sample)}\.mdf$")
    hits = [p for p in Path(outdir).glob("cmp_*.mdf") if pattern.fullmatch(p.name)]
    hits.sort(key=lambda p: p.stat().st_mtime)
    return hits[-1] if hits else None

_HARD_STATS_PREFIX = "统计 t 轴:"  # stats 维度中属硬门的行前缀


def _is_hard_stats(d):
    """stats 行是否属硬门：统计 t 轴差异，且不是 KNOWN_DIFF_PREFIX 说明行。

    记录空洞样例的说明行（"已知差异: 统计 t 轴 …"，见 mdf_compare）不匹配
    _HARD_STATS_PREFIX → 落报告层；此处再按语义前缀兜一道，避免说明行格式
    变动后被静默升级为硬门差异。
    """
    return d.startswith(_HARD_STATS_PREFIX) and not d.startswith(KNOWN_DIFF_PREFIX)


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


def convert_one(platform, project, blf, out):
    """无头同参链路：与 GUI ConvertWorker.run 逐参一致（raw_export=False, parallel=True）。"""
    root = dbc_root(platform)
    dbcs = project_loader.load_project(root, project)
    mapping = project_loader.load_mapping(root / "dbc_对应关系.txt")
    auto = project_loader.auto_bindings(dbcs, mapping)
    channels = blf_reader.probe_channels(str(blf))
    rows = decide_bindings(channels, auto, None, [d.path for d in dbcs])
    by_path = {d.path: d for d in dbcs}
    bindings = {row.channel: by_path[row.binding] for row in rows if row.binding}
    print(f"    BLF通道={channels} 绑定={sorted(bindings)}")
    result = converter.convert(str(blf), bindings, str(out),
                               raw_export=False, parallel=True,
                               stats_export=True)
    # duration_seconds = max_ts - min_ts（测量时长，非耗时）；墙钟取阶段计时
    wall = dict(result.timings).get("总耗时", 0.0)
    print(f"    测量时长 {result.duration_seconds:.3f}s 转换墙钟 {wall:.3f}s "
          f"warnings={result.warnings}")
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
    stat_t = [d for d in stats if _is_hard_stats(d)]
    stat_rest = [d for d in stats if not _is_hard_stats(d)]
    return meta, val, stat_t, stat_rest


def run_sample(platform, project, sample, blf, canoe, outdir, skip_convert, ts):
    label = f"{platform}/{project}/{sample}"
    if skip_convert:
        ours = latest_artifact(outdir, platform, project, sample)
        if ours is None:
            expect = artifact_filename("<时间戳>", platform, project, sample)
            print(f"  [SKIP] --skip-convert 但 {outdir} 下无 {expect}，跳过")
            return None
        print(f"== {label} ==（复用产物 {ours.name}）")
    else:
        ours = outdir / artifact_filename(ts, platform, project, sample)
        print(f"== {label} ==（转换中…）")
        convert_one(platform, project, blf, ours)
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
    return (label, ours, canoe, meta, val, stat_t, stat_rest)


def main(argv=None):
    p = argparse.ArgumentParser(description="CANoe 参考质量校验（转换+全量对比+需求线判定）")
    p.add_argument("--skip-convert", action="store_true",
                   help="复用 --outdir 下各样本最新产物，不重新转换")
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
            for platform, project, sample, blf, canoe in SAMPLES:
                res = run_sample(platform, project, sample, blf, canoe,
                                 outdir, args.skip_convert, ts)
                if res is None:
                    continue
                label, ours, canoe, meta, val, stat_t, stat_rest = res
                fp.write(f"\n--- {label} 报告层明细 ---\n")
                for d in meta:
                    fp.write(f"- {d}\n")
                for d in stat_rest:
                    fp.write(f"- {d}\n")
                if not meta and not stat_rest:
                    fp.write("（无）\n")
                if not val and not stat_t:
                    continue
                fails.append(label)
            print(f"=== 汇总: 硬门失败 {'/'.join(fails) if fails else '无'}，"
                  f"退出码 {'1' if fails else '0'} ===")
    print(f"[报告已写入] {report}")
    return 1 if fails else 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
