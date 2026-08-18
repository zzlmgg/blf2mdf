"""自产 vs 自产逐组全量对拍（薄壳：判定委托 tools/mdf_compare）。

用法：python tools/compare_two_mdf.py <a.mdf> <b.mdf> [--skip-{header,structure,values,stats}]
退出码 0 = 一致；非 0 = 存在差异（打印明细）。
"""
import argparse
import sys

from asammdf import MDF

from mdf_compare import compare_files_identical  # 同目录脚本运行（sys.path[0]=tools/）

_DIMS = ("header", "structure", "values", "stats")


def main(argv=None):
    p = argparse.ArgumentParser(description="自产 vs 自产逐位对拍")
    p.add_argument("path_a", help="自产 MDF 路径")
    p.add_argument("path_b", help="自产 MDF 路径")
    for d in _DIMS:
        p.add_argument(f"--skip-{d}", action="store_true", help=f"关闭 {d} 维度")
    args = p.parse_args(argv)
    dims = frozenset(d for d in _DIMS if not getattr(args, f"skip_{d}"))
    diffs = compare_files_identical(args.path_a, args.path_b, dims=dims)
    for line in diffs:
        print(line)
    # 汇总行带组数（与原文案一致；元数据轻量加载，~0.03s）
    with MDF(args.path_a) as a:
        n_groups = len(a.groups)
    if not diffs:
        print(f"=== {n_groups} 组对拍: 全部一致 ===")
        return 0
    print(f"=== {n_groups} 组对拍: {len(diffs)} 处差异 ===")
    return 1


if __name__ == "__main__":
    sys.exit(main())
