"""金标准：样例 BLF 转换结果与 CANoe 生成的 _T058.mdf 对比。"""
import numpy as np
import pytest
from asammdf import MDF

from conftest import DBC_DIR, MDF_DIR, OUTPUTS_DIR, sample_blf
from core.blf_reader import list_channels
from core.converter import convert
from core.dbc_loader import load

REF = MDF_DIR / "_T058.mdf"

# 通道→DBC 绑定：按报文 ID 匹配得出（样例 BLF 各通道承载不同网络，
# 单一 DBC 无法覆盖参考信号集；CAN0/CAN14 无匹配 DBC，按原始帧导出）。
# 推导过程与验证见 task-8 报告。
BINDING = {
    1: "VDCPublic_CANFD1.dbc",
    2: "VDCCIDC_CANFD.dbc",
    3: "VDCCCU_CANFD2.dbc",
    6: "VDCCCU_CANFD3.dbc",
    8: "VDCCZF_CANFD.dbc",
    9: "VDCCZL_CANFD.dbc",
    10: "VDCCZR_CANFD.dbc",
    11: "VDCCZT_CANFD.dbc",
    12: "VDCCIDC_CANFD.dbc",
    13: "VDCCCU_CANFD1.dbc",
}


def _signal_names(mdf: MDF) -> set[str]:
    """收集解码信号通道名。

    两侧均按报文名分组（'1s' 为 CANoe 总线统计组，Raw:: 为自产原始帧组）；
    排除时间通道（修复项 6 后两侧均为 't'；保留 'time' 以兼容旧自产文件）。
    """
    names = set()
    for g in mdf.groups:
        acq = g.channel_group.acq_name
        if acq and acq != "1s" and not acq.startswith("Raw::"):
            for ch in g.channels:
                if ch.name not in ("t", "time"):
                    names.add(ch.name)
    return names


def _first_signal(mdf: MDF, name: str):
    """按名取信号（组内定位；参考 MDF 同名通道跨组出现时 mdf.get 会抛
    Multiple occurrences）。"""
    for gi, g in enumerate(mdf.groups):
        if any(ch.name == name for ch in g.channels):
            return mdf.get(name, group=gi)
    return None


@pytest.mark.golden
def test_golden_signal_coverage_and_values():
    blf = sample_blf()
    if blf is None or not REF.exists():
        pytest.skip("缺少样例 BLF 或参考 MDF")
    channels = set(list_channels(str(blf)))
    assert channels, "样例 BLF 无通道"

    ref = MDF(str(REF))
    ref_names = _signal_names(ref)
    assert ref_names, "参考 MDF 无解码信号"

    bindings = {}
    for ch, dbc in BINDING.items():
        p = DBC_DIR / dbc
        if ch in channels and p.exists():
            bindings[ch] = load(str(p))
    assert bindings, "候选 DBC 均不可用"
    out = OUTPUTS_DIR / "golden.mdf"
    convert(str(blf), bindings, str(out))
    m = MDF(str(out))
    ours = _signal_names(m)
    overlap = len(ours & ref_names) / len(ref_names)
    print(f"信号覆盖 {len(ours & ref_names)}/{len(ref_names)} = {overlap:.2%}")
    assert overlap >= 0.9, f"信号覆盖率不足: {overlap:.2%}（参考 {len(ref_names)} 个信号）"

    # 抽样对比：数值信号前 3 个 × 前 100 点 + 枚举文本信号前 2 个 × 前 100 点
    # （修复项 3+8：两侧均按 DBC 定义存储——float64 物理值/最小整型/枚举文本）
    common = sorted(ours & ref_names)
    checked = 0
    num_picked, txt_picked = 0, 0
    for name in common:
        if num_picked >= 3 and txt_picked >= 2:
            break
        ours_sig = _first_signal(m, name)
        theirs = _first_signal(ref, name)
        if ours_sig is None or theirs is None or len(theirs.samples) == 0:
            continue
        t_ref = np.asarray(theirs.timestamps)
        v_ref = np.asarray(theirs.samples)
        t_our = np.asarray(ours_sig.timestamps)
        v_our = np.asarray(ours_sig.samples)
        # 修复项 2：两侧均为相对时间（自产以全局首帧归零，与 CANoe 一致），
        # 无需偏移；首点时间差在窗口内即可直接逐点最近邻对比
        assert abs(float(t_our[0]) - float(t_ref[0])) < 1e-3, \
            f"{name} 时间轴不一致: 自产 {t_our[0]} vs 参考 {t_ref[0]}"
        if v_ref.dtype.kind in ("S", "O", "U"):
            if txt_picked >= 2:
                continue
            # 枚举信号：文本逐值比对（两侧均存 DBC value table 文本，UTF-8 bytes）
            for i in range(min(100, len(t_ref))):
                idx = int(np.argmin(np.abs(t_our - t_ref[i])))
                if abs(t_our[idx] - t_ref[i]) > 1e-3:
                    continue  # 时间无对应采样点则跳过
                assert bytes(v_our[idx]).rstrip(b"\x00") == bytes(v_ref[i]).rstrip(b"\x00"), \
                    f"{name} t={t_ref[i]:.3f}: 期望 {v_ref[i]!r} 实际 {v_our[idx]!r}"
                checked += 1
            txt_picked += 1
        elif (np.issubdtype(v_ref.dtype, np.number)
                and np.issubdtype(v_our.dtype, np.number)):
            if num_picked >= 3:
                continue
            for i in range(min(100, len(t_ref))):
                idx = int(np.argmin(np.abs(t_our - t_ref[i])))
                if abs(t_our[idx] - t_ref[i]) > 1e-3:
                    continue  # 时间无对应采样点则跳过
                assert abs(float(v_our[idx]) - float(v_ref[i])) <= max(1e-6, 1e-6 * abs(float(v_ref[i]))), \
                    f"{name} t={t_ref[i]:.3f}: 期望 {v_ref[i]} 实际 {v_our[idx]}"
                checked += 1
            num_picked += 1
    assert checked > 0, "未能找到可对比的采样点"


@pytest.mark.golden
def test_golden_duration():
    blf = sample_blf()
    if blf is None:
        pytest.skip("无样例 BLF")
    channels = list_channels(str(blf))
    p = DBC_DIR / "VDCPublic_CANFD1.dbc"
    if not p.exists():
        pytest.skip("缺 VDCPublic_CANFD1.dbc")
    # 绑定到 VDCPublic 网络实际承载通道 CAN1（ID 匹配，见 BINDING）；
    # 绑定 channels[0]（CAN0）无该网络报文，时长为 0。
    main_ch = 1
    if main_ch not in channels:
        pytest.skip(f"样例无 CAN{main_ch}")
    out = OUTPUTS_DIR / "golden_dur.mdf"
    result = convert(str(blf), {main_ch: load(str(p))}, str(out))
    assert 580 <= result.duration_seconds <= 620, \
        f"时长异常: {result.duration_seconds:.1f}s（参考文件为 10 分钟）"
