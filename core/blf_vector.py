"""BLF 向量化解析（H1）：候选扫描快路径 + 标量回退。

设计见 docs/2026-08-14-H1-vectorized-parse-plan.md（§2 语义契约、§4 快路径
算法、§5 回退）。核心思想：按容器做 numpy 候选扫描 + 移位字段提取，快路径
条件任一不满足即整容器回退标量行走 `_walk_container`（= 现行实现，逐字节
复刻）——回退即 oracle，正确性由对拍测试（tests/test_blf_vector.py）锁定。

窗口语义（§2.1，实测 CPython bytes.index）：每步在 [pos, pos+8) 内找第一
个 "LOBJ" 候选，匹配须完整落在窗口内 → 命中界 c ≤ pos+4；尾部判断用
pos+8 > max_pos（+4/+8 不对称）。

字段偏移以参考实现（blf_reader._walk_container 对 python-can 结构体的解包
索引）为准：V1/V2 flags 均为 u32@+16；FD fd_flags=u8@+12、valid_bytes=
u8@+13；FD64 ext_data_offset=u8@+35（m[13]）。计划文档 §2.3 实施时已按此
核对修正。

时间戳：ts = (ms_part + rel_ns) × 1e-9；ms_part + rel_ns ≤ 2^53 时 int→
float64 精确（Python 与 numpy 同舍入，逐位一致），超出即回退（守卫按
flags 单位分判，杜绝 rel×10000 的 uint64 回绕假守卫）。
"""
from dataclasses import dataclass
from typing import Iterator

import numpy as np

_LOBJ = 0x4A424F4C  # b"LOBJ" 小端

# dlc 码 → 字节数（python-can can/util.py dlc2len 实测：码 ≤15 查 CAN_FD_DLC，
# 码 >15 → 64）
_DLC2LEN = np.array([0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64],
                    dtype=np.uint8)


@dataclass
class ContainerFrames:
    """一个容器解析出的全部帧（快路径/回退同构表示，计划 §3）。"""
    channel: np.ndarray    # int64 (N,) 0-based
    ts: np.ndarray         # float64 (N,)（整数 ns 构造，与 Frame.ts_seconds 同语义）
    arb: np.ndarray        # uint32 (N,)（不含 EFF 位）
    is_ext: np.ndarray     # bool (N,)
    is_remote: np.ndarray  # bool (N,)
    is_error: np.ndarray   # bool (N,)
    is_fd: np.ndarray      # bool (N,)
    dlc: np.ndarray        # uint8 (N,)（经典=原值；FD=dlc2len，与 Frame.dlc 同语义）
    data8: np.ndarray      # uint8 (M,) 打包载荷（FD64 已含补零）
    data_off: np.ndarray   # int64 (N,) 帧载荷在 data8 中的起点
    data_len: np.ndarray   # int64 (N,)（= len(Frame.data)）


def _empty_container_frames() -> ContainerFrames:
    return ContainerFrames(
        channel=np.empty(0, np.int64), ts=np.empty(0, np.float64),
        arb=np.empty(0, np.uint32), is_ext=np.empty(0, bool),
        is_remote=np.empty(0, bool), is_error=np.empty(0, bool),
        is_fd=np.empty(0, bool), dlc=np.empty(0, np.uint8),
        data8=np.empty(0, np.uint8), data_off=np.empty(0, np.int64),
        data_len=np.empty(0, np.int64))


# ── 移位组装提取（显式小端，机器无关；p 越界处读出垃圾值，由调用方掩码
#    屏蔽——回退位置 0 需 max_pos ≥ 8，快路径在 max_pos < 16 时已整体回退，
#    保证安全）。numpy 移位不自动提升类型，必须先转宽类型再移位（uint8<<24
#    会回绕）。 ──

def _u32_at(b8: np.ndarray, p: np.ndarray, max_pos: int) -> np.ndarray:
    if p.dtype != np.int64:
        p = p.astype(np.int64)
    safe = (p >= 0) & (p + 4 <= max_pos)
    q = np.where(safe, p, 0)
    acc = b8[q].astype(np.uint32)
    acc |= b8[q + 1].astype(np.uint32) << 8
    acc |= b8[q + 2].astype(np.uint32) << 16
    acc |= b8[q + 3].astype(np.uint32) << 24
    return acc


def _u64_at(b8: np.ndarray, p: np.ndarray, max_pos: int) -> np.ndarray:
    if p.dtype != np.int64:
        p = p.astype(np.int64)
    safe = (p >= 0) & (p + 8 <= max_pos)
    q = np.where(safe, p, 0)
    acc = b8[q].astype(np.uint64)
    for s in range(1, 8):
        acc |= b8[q + s].astype(np.uint64) << np.uint64(8 * s)
    return acc


def _u16_at(b8: np.ndarray, p: np.ndarray, max_pos: int) -> np.ndarray:
    if p.dtype != np.int64:
        p = p.astype(np.int64)
    safe = (p >= 0) & (p + 2 <= max_pos)
    q = np.where(safe, p, 0)
    return (b8[q].astype(np.uint32) | (b8[q + 1].astype(np.uint32) << 8))


def _u8_at(b8: np.ndarray, p: np.ndarray, max_pos: int) -> np.ndarray:
    if p.dtype != np.int64:
        p = p.astype(np.int64)
    safe = (p >= 0) & (p + 1 <= max_pos)
    return b8[np.where(safe, p, 0)]


def _candidates(data: bytes) -> np.ndarray:
    """全部 "LOBJ" 候选位置（int64，升序）：4 个对齐偏移的 u32 比较。

    frombuffer(offset, count) 零拷贝视图（切片 data[o:...] 会全量拷贝，
    626MB 实测 ~3.6s → ~1s）。
    """
    parts = []
    L = len(data)
    for o in range(4):
        n = (L - o) // 4
        if n > 0:
            v = np.frombuffer(data, dtype="<u4", count=n, offset=o)
            parts.append(np.nonzero(v == _LOBJ)[0].astype(np.int64) * 4 + o)
    if not parts:
        return np.empty(0, dtype=np.int64)
    c = np.concatenate(parts)
    if len(parts) > 1:
        c = np.sort(c)
    return c


def _parse_fast(data: bytes, ms_part: int):
    """快路径（计划 §4）：返回 (ContainerFrames, tail)；条件不满足 → None。

    任一条件不满足一律返回 None 交由标量回退（本函数不抛异常），唯二
    例外是契约直接要求的终结：N==0 且 max_pos≥8 → raise BLFParseError
    （与标量路径同语义，§4.2.1）。
    """
    from can.io.blf import BLFParseError

    max_pos = len(data)
    b8 = np.frombuffer(data, dtype=np.uint8)
    c = _candidates(data)
    N = len(c)
    if N == 0:
        # 无候选：max_pos < 8 → 尾部（含 0 字节空容器）；否则 raise（§2.1）
        if max_pos < 8:
            return _empty_container_frames(), data
        raise BLFParseError("Could not find next object")
    if max_pos < 16:
        # 任意候选都无法通过 base_fit（c+16 ≤ max_pos），且提取 helper 的
        # 回退位置 0 需读至 b8[7] → 直接回退
        return None

    # ── 对象头字段（裁剪安全域读出，垃圾值由后续掩码屏蔽）──
    obj_size = _u32_at(b8, c + 8, max_pos)
    obj_type = _u32_at(b8, c + 12, max_pos)
    hdr_word = _u32_at(b8, c + 4, max_pos)
    hdr_size = (hdr_word & np.uint32(0xFFFF)).astype(np.int64)  # u16 字段（≠ 实际 hsz）
    hdr_ver = hdr_word >> np.uint32(16)
    e = c + obj_size.astype(np.int64)
    v1 = hdr_ver == np.uint32(1)
    v2 = hdr_ver == np.uint32(2)
    unk = ~(v1 | v2)
    hsz = np.where(v1, np.int64(32), np.where(v2, np.int64(40), np.int64(0)))
    t_cls = (obj_type == 1) | (obj_type == 86)
    t_err = obj_type == 73
    t_fd = obj_type == 100
    t_fd64 = obj_type == 101
    base_fit = c + 16 <= max_pos
    obj_fit = e <= max_pos
    ver_fit = unk | (v1 & (c + 32 <= max_pos)) | (v2 & (c + 40 <= max_pos))
    msg_fit = (unk | (t_cls & (c + hsz + 16 <= max_pos))
               | (t_err & (c + hsz + 32 <= max_pos))
               | (t_fd & (c + hsz + 84 <= max_pos))
               | (t_fd64 & (c + hsz + 40 <= max_pos))
               | (~(t_cls | t_err | t_fd | t_fd64)))   # 其他类型恒真（整体跳过）
    valid = base_fit & (unk | ver_fit) & (unk | msg_fit) & obj_fit

    # ── 窗口状态机（§4.2 实施核对修正）：walk 的搜索窗口下界 s 按
    # obj_size 前缀和推进（s' = s + obj_size；obj_size 不含 base 头，
    # python-can writer 约定），候选 c 与窗口下界 s 的间隙 ∈ [0,4] 才
    # 命中（fit 语义 c+4 ≤ s+8）；所有尾部均以窗口下界 s 为界
    # （data[s:]，含未消费 padding），而非对象末尾 e = c + obj_size ──
    s = np.concatenate(([0], np.cumsum(obj_size.astype(np.int64))))[:N]
    bad = ~valid | (c - s > 4)
    first = int(np.argmax(bad)) if bool(bad.any()) else N   # 第一个失败轮

    if first < N and int(c[first]) < int(s[first]):
        return None   # 防御：候选落窗口下界之前（不变量破坏）→ 回退

    # ── 发射（§4.3）：use = 版本已知 ∧ 四类消息，截断至 first 前
    # （walk 在轮 first 失败后返回已累积帧 + tail，§4.4 核对修正）──
    emit = ((v1 | v2) & (t_cls | t_err | t_fd | t_fd64)).copy()
    use = emit.copy()
    if first < N:
        use[first:] = False
    idx = np.flatnonzero(use)

    # 条件 6（§4.2.6）：时间守卫仅作用于解析轮（walk 不读跳过对象的时间
    # 头），按 flags 单位分判（10µs 单位先除再比，杜绝 rel×10000 的
    # uint64 回绕假守卫）
    flags = _u32_at(b8, c + 16, max_pos)   # V1/V2 均为 u32（核对修正）
    rel = _u64_at(b8, c + 24, max_pos)
    unit = np.where(flags == 1, np.uint64(10000), np.uint64(1))
    rel_max = (np.uint64(2 ** 53) - np.uint64(ms_part)) // unit
    if not bool(np.all(~use | (rel <= rel_max))):
        return None

    ch_full = np.zeros(N, dtype=np.int64)
    dlc_full = np.zeros(N, dtype=np.uint8)
    arb_full = np.zeros(N, dtype=np.uint32)
    ext_full = np.zeros(N, dtype=bool)
    remote_full = np.zeros(N, dtype=bool)
    err_full = np.zeros(N, dtype=bool)
    fd_full = np.zeros(N, dtype=bool)
    doff_full = np.zeros(N, dtype=np.int64)
    dlen_full = np.zeros(N, dtype=np.int64)
    glen_full = np.zeros(N, dtype=np.int64)   # 实际 gather 字节数（FD64 可 < dlen）

    pos = c + hsz  # 消息体起点（emit 处版本已知，hsz 正确）

    m = use & t_cls
    if m.any():
        p = pos[m]
        ch_full[m] = _u16_at(b8, p, max_pos) - 1
        fl8 = _u8_at(b8, p + 2, max_pos)
        dlc_full[m] = _u8_at(b8, p + 3, max_pos)
        can_id = _u32_at(b8, p + 4, max_pos)
        arb_full[m] = can_id & np.uint32(0x1FFFFFFF)
        ext_full[m] = (can_id & np.uint32(0x80000000)) != 0
        remote_full[m] = (fl8 & np.uint8(0x80)) != 0
        dlen_full[m] = np.minimum(dlc_full[m], 8)
        doff_full[m] = p + 8

    m = use & t_err
    if m.any():
        p = pos[m]
        ch_full[m] = _u16_at(b8, p, max_pos) - 1
        dlc_full[m] = _u8_at(b8, p + 10, max_pos)
        can_id = _u32_at(b8, p + 16, max_pos)
        arb_full[m] = can_id & np.uint32(0x1FFFFFFF)
        ext_full[m] = (can_id & np.uint32(0x80000000)) != 0
        err_full[m] = True
        dlen_full[m] = np.minimum(dlc_full[m], 8)
        doff_full[m] = p + 24

    m = use & t_fd
    if m.any():
        p = pos[m]
        ch_full[m] = _u16_at(b8, p, max_pos) - 1
        fl8 = _u8_at(b8, p + 2, max_pos)
        dlc_code = _u8_at(b8, p + 3, max_pos)
        dlc_full[m] = np.where(dlc_code <= 15,
                               _DLC2LEN[np.minimum(dlc_code, 15)], 64)
        can_id = _u32_at(b8, p + 4, max_pos)
        arb_full[m] = can_id & np.uint32(0x1FFFFFFF)
        ext_full[m] = (can_id & np.uint32(0x80000000)) != 0
        fd_full[m] = (_u8_at(b8, p + 13, max_pos) & np.uint8(0x1)) != 0
        remote_full[m] = (fl8 & np.uint8(0x80)) != 0
        dlen_full[m] = np.minimum(_u8_at(b8, p + 14, max_pos), 64)
        doff_full[m] = p + 20

    m = use & t_fd64
    if m.any():
        p = pos[m]
        ch_full[m] = _u8_at(b8, p, max_pos) - 1
        dlc_code = _u8_at(b8, p + 1, max_pos)
        dlc_full[m] = np.where(dlc_code <= 15,
                               _DLC2LEN[np.minimum(dlc_code, 15)], 64)
        vb = _u8_at(b8, p + 2, max_pos).astype(np.int64)
        can_id = _u32_at(b8, p + 4, max_pos)
        arb_full[m] = can_id & np.uint32(0x1FFFFFFF)
        ext_full[m] = (can_id & np.uint32(0x80000000)) != 0
        fd_flags = _u32_at(b8, p + 12, max_pos)
        fd_full[m] = (fd_flags & np.uint32(0x1000)) != 0
        remote_full[m] = (fd_flags & np.uint32(0x0010)) != 0
        ext_off = _u8_at(b8, p + 35, max_pos).astype(np.int64)  # m[13]
        span = np.where(ext_off != 0, ext_off, obj_size[m].astype(np.int64))
        dfl = np.minimum(vb, span - hdr_size[m] - 40)
        # 可用字节 = min(dfl, 容器尾截断)——逐位复刻 Python 切片 clamp；
        # dfl 可为负（虚假 header_size 字段）→ 0（全零帧，ljust 语义）
        available = np.maximum(0, np.minimum(dfl, max_pos - (p + 40)))
        dlen_full[m] = vb                       # 最终长度 = valid_bytes（ljust 语义）
        doff_full[m] = p + 40
        glen_full[m] = available
    not_fd64 = use & ~t_fd64
    glen_full[not_fd64] = dlen_full[not_fd64]

    # ── 载荷打包（定宽 (n, Lmax) 块 + 单次 take）──
    # 变长 row/col 摊平在 5.6M 帧上实测 ~10s（M 尺度随机 gather ×4），
    # 定宽块只需一次 (n, Lmax) gather；行尾垃圾字节由 data_len 界定，
    # FD64 可用字节之外补零 = walk ljust 语义。data_off = i*Lmax 定跨距
    # （契约仅要求 data_off/data_len 自洽，消费者按两者切片）。
    lens = dlen_full[idx]
    n_emit = len(idx)
    if n_emit:
        Lmax = int(lens.max())
        idt = np.int32 if max_pos < 2 ** 31 else np.int64
        src2 = doff_full[idx].astype(idt)[:, None] + np.arange(Lmax, dtype=idt)
        blk = np.take(b8, src2, mode="clip")   # 越界索引 clip → 垃圾位不读
        glens = glen_full[idx]
        if not bool(np.all(glens == lens)):
            # 仅 FD64 截断行需要补零（walk ljust 语义）；全满容器跳过
            blk[np.arange(Lmax)[None, :] >= glens[:, None]] = 0
        data8 = blk.ravel()
        data_off = np.arange(n_emit, dtype=np.int64) * Lmax
    else:
        data8 = np.zeros(0, dtype=np.uint8)
        data_off = np.zeros(0, dtype=np.int64)

    # ── 时间戳：守卫保证 ms_part + rel_ns ≤ 2^53，int→float64 精确 ──
    rel_ns = np.where(flags == 1, rel * np.uint64(10000), rel)
    ts = (np.uint64(ms_part) + rel_ns[idx]).astype(np.float64) * 1e-9

    cf = ContainerFrames(
        channel=ch_full[idx], ts=ts, arb=arb_full[idx], is_ext=ext_full[idx],
        is_remote=remote_full[idx], is_error=err_full[idx], is_fd=fd_full[idx],
        dlc=dlc_full[idx], data8=data8, data_off=data_off,
        data_len=lens)

    # ── 早退轮（§4.4 核对修正）：walk 在轮 first 失败后返回已累积帧 +
    # tail = data[s[first]:]（窗口下界为界，含未消费 padding）──
    if first < N:
        if int(c[first]) - int(s[first]) > 4:
            # 窗口内无 fit 候选：index ValueError 路径
            if int(s[first]) + 8 > max_pos:
                return cf, data[int(s[first]):]
            raise BLFParseError("Could not find next object")
        # 候选命中窗口但对象解析失败——按 walk 分型：
        if not bool(base_fit[first]) or not bool(obj_fit[first]):
            # base 头/对象体跨容器截断（常态）：struct.error 捕获 → 尾部
            return cf, data[int(s[first]):]
        if bool(v1[first] | v2[first]) and not bool(ver_fit[first]):
            return None   # 版本头截断：walk 未捕获 struct.error 传播 → 回退
        return cf, data[int(s[first]):]   # 消息体截断 → 尾部

    # ── 尾部（正常终止）：s_end = 窗口下界推进终点（Σ obj_size，含跳过
    # 对象）；s_end+8 > max_pos → 尾部 = 未消费字节；否则 walk 会 raise ──
    s_end = int(s[N - 1]) + int(obj_size[N - 1])
    if s_end + 8 > max_pos:
        tail = data[s_end:]
    else:
        raise BLFParseError("Could not find next object")
    return cf, tail


def _frames_to_container(frames: list) -> ContainerFrames:
    """标量回退产物 Frame[] → ContainerFrames（每容器一次性转换）。"""
    n = len(frames)
    ts = np.fromiter((f.ts_seconds for f in frames), dtype=np.float64, count=n)
    lens = np.fromiter((len(f.data) for f in frames), dtype=np.int64, count=n)
    starts = np.concatenate(([0], np.cumsum(lens)))
    data8 = np.frombuffer(b"".join(f.data for f in frames), dtype=np.uint8)
    return ContainerFrames(
        channel=np.fromiter((f.channel for f in frames), dtype=np.int64, count=n),
        ts=ts,
        arb=np.fromiter((f.arbitration_id for f in frames), dtype=np.uint32, count=n),
        is_ext=np.fromiter((f.is_extended for f in frames), dtype=bool, count=n),
        is_remote=np.fromiter((f.is_remote for f in frames), dtype=bool, count=n),
        is_error=np.fromiter((f.is_error for f in frames), dtype=bool, count=n),
        is_fd=np.fromiter((f.is_fd for f in frames), dtype=bool, count=n),
        dlc=np.fromiter((f.dlc for f in frames), dtype=np.uint8, count=n),
        data8=data8, data_off=starts[:-1].copy(), data_len=lens)


def iter_container_frames(path: str, progress_cb=None, cancel_cb=None,
                          _force_fallback: bool = False) -> Iterator[ContainerFrames]:
    """逐容器产帧（快路径优先，条件不满足回退标量）——convert 读入入口。

    帧全局顺序 = 容器序 × 容器内对象序（BLF 时间序）。progress_cb(percent)
    逐容器字节进度（与 iter_all_messages 同语义）；cancel_cb() 每容器检查
    一次（H1 有意松弛，计划 §7：82MB 样例 58 容器 ≈ 0.5s 延迟）。
    _force_fallback 仅供测试对拍（强制全部容器走标量路径）。
    """
    from core import blf_reader

    tail = b""
    ms_part = None
    for start_ns, data in blf_reader._iter_containers(path, progress_cb=progress_cb):
        if cancel_cb is not None and cancel_cb():
            raise blf_reader.ScanCancelled()
        if ms_part is None:
            ms_part = blf_reader._ms_part_ns(start_ns)
        if tail:
            data = tail + data
            tail = b""
        if not _force_fallback:
            fast = _parse_fast(data, ms_part)
            if fast is not None:
                cf, tail = fast
                if len(cf.channel):
                    yield cf
                continue
        frames, tail = blf_reader._walk_container(data, ms_part, cancel_cb=cancel_cb)
        if frames:
            yield _frames_to_container(frames)
