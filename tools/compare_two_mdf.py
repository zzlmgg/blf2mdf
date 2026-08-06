"""自产 vs 自产逐组全量对拍：组序/组名/通道序/采样/dtype/unit 逐位一致。

用法：python tools/compare_two_mdf.py <a.mdf> <b.mdf>
退出码 0 = 一致；非 0 = 存在差异（打印明细）。
"""
import sys
import numpy as np
from asammdf import MDF


def norm(v):
    """bytes 采样归一化（尾随 \\x00 剥离），便于比较文本通道。"""
    v = np.asarray(v)
    if v.dtype.kind == "S":
        return np.char.rstrip(v.astype("|S256"), b"\x00")
    return v


def main():
    a, b = MDF(sys.argv[1]), MDF(sys.argv[2])
    if len(a.groups) != len(b.groups):
        print(f"组数不同: {len(a.groups)} vs {len(b.groups)}")
        return 1
    bad = 0
    for gi, (ga, gb) in enumerate(zip(a.groups, b.groups)):
        na = ga.channel_group.acq_name
        nb = gb.channel_group.acq_name
        if na != nb:
            print(f"组 {gi}: 组名不同 {na!r} vs {nb!r}")
            bad += 1
        ca = [c.name for c in ga.channels]
        cb = [c.name for c in gb.channels]
        if ca != cb:
            print(f"组 {gi} {na}: 通道序不同 {ca[:8]}... vs {cb[:8]}...")
            bad += 1
            continue
        for chn in ca:
            sa = np.asarray(a.get(chn, group=gi).samples)
            sb = np.asarray(b.get(chn, group=gi).samples)
            if sa.dtype != sb.dtype:
                print(f"组 {gi} {na}.{chn}: dtype {sa.dtype} vs {sb.dtype}")
                bad += 1
                continue
            if not np.array_equal(norm(sa), norm(sb)):
                # 浮点/文本逐位不一致 → 尝试 allclose 定位是「微小差」还是「结构性差」
                if np.issubdtype(sa.dtype, np.floating):
                    diff = np.nanmax(np.abs(sa - sb)) if sa.size else 0.0
                    if np.isnan(diff) or diff > 1e-12:
                        print(f"组 {gi} {na}.{chn}: 采样不一致 maxdiff={diff}")
                        bad += 1
                else:
                    print(f"组 {gi} {na}.{chn}: 采样不一致 ({sa.size} vs {sb.size})")
                    bad += 1
        ta = np.asarray(a.get("t", group=gi).samples)
        tb = np.asarray(b.get("t", group=gi).samples)
        if not np.array_equal(ta, tb):
            print(f"组 {gi} {na}: t 轴不一致")
            bad += 1
    print(f"=== {len(a.groups)} 组对拍: {'全部一致' if bad == 0 else f'{bad} 处差异'} ===")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
