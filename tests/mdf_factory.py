"""合成 MDF 测试工厂：黄金套件（test_mdf_compare）与 CLI 契约测试（test_compare_cli）共享。

构造可行性依据见 docs/reviews/2026-08-18-h1-oracle-protection-plan.md 的
「构造可行性依据」一节（2026-08-18 实测）：
- header.abs_time 必须写 int ns（写 datetime 会在 save 时报 struct.error）；
- author/department/project/subject 落盘进 HD comment XML，会连带 header.comment 差异；
- S dtype 写入时尾随 \\x00 被 asammdf 剥除；U / object dtype 不支持 append；
- channels[0] 是自动生成的 'time' 主通道，数据通道从 channels[1] 起。
"""
from datetime import datetime, timezone

import numpy as np
from asammdf import MDF, Signal

# 固定文件头起始时间：asammdf 新建文件默认盖章写入时刻（两文件必然不同）；
# core/mdf_writer 的 start_time 来自转换源而非写入时刻，此处镜像该语义。
_FIXED_START = datetime(2026, 1, 1, tzinfo=timezone.utc)

# 参考文件统计布局（每通道 22 项，10 项统计名的组内偏移；CANoe 参考文件实测布局）
REF_LAYOUT = (22, {"StdData": 4, "StdDataRate": 5, "ExtData": 6, "ExtDataRate": 7,
                   "StdRemote": 8, "StdRemoteRate": 9, "ExtRemote": 10,
                   "ExtRemoteRate": 11, "ErrorFrames": 12, "ErrorFrameRate": 13})


def _write_mdf(path, groups, comment="t", header=None, chan_mut=None):
    """groups: list[(acq, [(name, samples, dtype), ...])]；t 通道自动加（arange 秒）。

    header: save 前逐一 setattr 到 mdf.header（abs_time 传 int ns）。
    chan_mut: save 前对 mdf 就地修改通道元数据（如 flags/conversion）。
    """
    with MDF(version="4.10") as mdf:
        for acq, chans in groups:
            n = len(chans[0][1])
            ts = np.arange(n, dtype=np.float64)
            t_idx = next((i for i, (nm, _, _) in enumerate(chans) if nm == "t"), None)
            if t_idx is not None:
                # 显式 t 通道承载组时间轴：其采样即该组 timestamps（组基准 = 首信号 timestamps）
                ts = np.asarray(chans[t_idx][1], dtype=np.float64)
            sigs = []
            for name, samples, dtype in chans:
                arr = np.asarray(samples, dtype=dtype)
                kwargs = {}
                if arr.dtype.kind in ("S", "O", "U"):
                    kwargs["encoding"] = "utf-8"  # asammdf 8.x 字符串通道需显式 encoding
                sigs.append(Signal(samples=arr, name=name, timestamps=ts, **kwargs))
            if t_idx is None:
                sigs.append(Signal(samples=ts, name="t", timestamps=ts))
            mdf.append(sigs, comment="", acq_name=acq)
        mdf.header.start_time = _FIXED_START
        mdf.header.comment = comment
        if header:
            for k, v in header.items():
                setattr(mdf.header, k, v)
        if chan_mut:
            chan_mut(mdf)
        mdf.save(path, overwrite=True)  # asammdf 8.x save 强制 .mf4 后缀 → 用例用 .mf4 路径


def _simple(comment="t"):
    groups = [
        ("G1", [("SigA", [1.0, 2.0, 3.0], np.float64),
                ("SigB", [10, 20, 30], np.int64)]),
        ("G2", [("SigC", [b"x", b"y", b"z"], "S8")]),
    ]
    return _write_mdf, groups, comment
