"""对比两个 MDF：结构（组/通道/信号）+ 数据内容。"""
import sys
from asammdf import MDF


def dump_structure(path):
    with MDF(path) as mdf:
        print(f"### {path}  groups={len(mdf.groups)}")
        for gi, g in enumerate(mdf.groups):
            ch = g.channels
            nch = len(ch)
            common = getattr(g, "common_channel", None)  # may be None
            try:
                acq = g.channel_group.acq_name
            except Exception:
                acq = None
            cyc = None
            try:
                cyc = g.cycles_nr
            except Exception:
                pass
            print(f"  group[{gi}] '{acq}' cycles={cyc} channels={nch}")
            for ci, c in enumerate(ch):
                n = c.name
                try:
                    unit = c.unit
                except Exception:
                    unit = ""
                try:
                    dt = str(c.data_type)
                except Exception:
                    dt = "?"
                try:
                    num = c.number_of_bytes
                except Exception:
                    num = "?"
                print(f"    ch[{ci}] '{n}' unit='{unit}' type={dt} bytes={num}")
        # 信号名全集，便于全局对比
        return mdf


def main(path_a, path_b):
    for p in (path_a, path_b):
        dump_structure(p)
        print()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
