"""BLF 向量化解析（H1）：候选扫描快路径 + 标量回退。

设计见 docs/2026-08-14-H1-vectorized-parse-plan.md（§2 语义契约、§4 快路径
算法、§5 回退）。核心思想：按容器做 numpy 候选扫描 + 移位字段提取，快路径
条件任一不满足即整容器回退标量行走 `walk_container`（= 现行实现，逐字节
复刻）——回退即 oracle，正确性由对拍测试（tests/test_blf_vector.py）锁定。

窗口语义（§2.1，实测 CPython bytes.index）：每步在 [pos, pos+8) 内找第一
个 "LOBJ" 候选，匹配须完整落在窗口内 → 命中界 c ≤ pos+4；尾部判断用
pos+8 > max_pos（+4/+8 不对称）。

字段偏移以参考实现（blf_reader.walk_container 对 python-can 结构体的解包
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
    """一个容器解析出的全部帧（快路径/回退同构表示，计划 §3）。

    载荷消费契约（Proto E 归一，2026-08-19）：glen 恒存在、glen ≤ data_len、
    data_off + glen ≤ len(data8)；第 i 帧载荷 = data8[data_off:data_off+glen]
    真实字节 + 行尾补零至 data_len（ljust 语义）。物理布局仍双——packed
    （回退）定跨距打包块 / scattered（快路径）容器字节零拷贝视图——但布局
    差异是性能性质，消费方不感知：载荷访问统一走 payload_block / payload。
    """
    channel: np.ndarray    # int64 (N,) 0-based
    ts: np.ndarray         # float64 (N,)（整数 ns 构造，与 Frame.ts_seconds 同语义）
    arb: np.ndarray        # uint32 (N,)（不含 EFF 位）
    is_ext: np.ndarray     # bool (N,)
    is_remote: np.ndarray  # bool (N,)
    is_error: np.ndarray   # bool (N,)
    is_fd: np.ndarray      # bool (N,)
    dlc: np.ndarray        # uint8 (N,)（经典=原值；FD=dlc2len，与 Frame.dlc 同语义）
    data8: np.ndarray      # uint8 (M,)（packed=打包载荷；scattered=容器字节视图）
    data_off: np.ndarray   # int64 (N,) 帧载荷在 data8 中的起点（块内/容器内视布局）
    data_len: np.ndarray   # int64 (N,)（= len(Frame.data)）
    glen: np.ndarray       # int64 (N,) 真实载荷字节数（FD64 可 < data_len；packed = data_len）

    def payload_block(self, sel) -> np.ndarray:
        """sel 帧子集 → (N, L) 定宽载荷块，补零就位（行尾 ljust 语义）。

        双布局唯一载荷语义实现（Proto E）：向量化零拷贝 gather + glen 掩码，
        与 H7b scattered 分支逐位相同；全满桶跳过掩码（同现优化）。不变量
        由生产者保证，本方法消费侧零分支。
        """
        lens = self.data_len[sel]
        n = len(lens)
        if n == 0:
            return np.zeros((0, 0), dtype=np.uint8)
        L = int(lens.max())
        idt = np.int32 if self.data8.size < 2 ** 31 else np.int64
        src2 = self.data_off[sel].astype(idt)[:, None] + np.arange(L, dtype=idt)
        block = np.take(self.data8, src2, mode="clip")
        if not bool(np.all(self.glen[sel] == L)):
            block[np.arange(L)[None, :] >= self.glen[sel][:, None]] = 0
        return block

    def payload(self, i) -> bytes:
        """单帧载荷（测试面）：真实字节 + 行尾补零（ljust 语义）。"""
        o = int(self.data_off[i]); n = int(self.data_len[i]); g = int(self.glen[i])
        return bytes(self.data8[o:o + g]) + b"\x00" * (n - g)


def _empty_container_frames() -> ContainerFrames:
    return ContainerFrames(
        channel=np.empty(0, np.int64), ts=np.empty(0, np.float64),
        arb=np.empty(0, np.uint32), is_ext=np.empty(0, bool),
        is_remote=np.empty(0, bool), is_error=np.empty(0, bool),
        is_fd=np.empty(0, bool), dlc=np.empty(0, np.uint8),
        data8=np.empty(0, np.uint8), data_off=np.empty(0, np.int64),
        data_len=np.empty(0, np.int64), glen=np.empty(0, np.int64))


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


def _u32_at_v(v32: np.ndarray, p: np.ndarray, max_pos: int) -> np.ndarray:
    """u32 视图读（候选全 4 对齐时与 _u32_at 逐位同值；p 不齐勿用）。"""
    if p.dtype != np.int64:
        p = p.astype(np.int64)
    safe = (p >= 0) & (p + 4 <= max_pos)
    q = np.where(safe, p, 0)
    return v32[q >> 2]   # p+4 ≤ max_pos ⇒ p>>2 < len(v32)


def _u64_at_v(v32: np.ndarray, p: np.ndarray, max_pos: int) -> np.ndarray:
    """u64 视图读 = 两个 u32 对拼（c+24/c+28 均 4 对齐；无 8 对齐分支）。"""
    if p.dtype != np.int64:
        p = p.astype(np.int64)
    safe = (p >= 0) & (p + 8 <= max_pos)
    q = np.where(safe, p, 0)
    lo = v32[q >> 2].astype(np.uint64)
    hi = v32[(q + 4) >> 2].astype(np.uint64)   # q+8 ≤ max_pos ⇒ (q+4)>>2 < len(v32)
    return lo | (hi << np.uint64(32))


def _u16_at_v(v32: np.ndarray, p: np.ndarray, max_pos: int) -> np.ndarray:
    """u16 视图读 = u32 低 16 位（p 4 对齐时与 _u16_at 逐位同值）。"""
    if p.dtype != np.int64:
        p = p.astype(np.int64)
    safe = (p >= 0) & (p + 2 <= max_pos)
    q = np.where(safe, p, 0)
    return v32[q >> 2] & np.uint32(0xFFFF)


def _candidates(data: bytes):
    """单对齐类扫描：起始窗口探测定类 o，只扫 ≡ o mod 4 的 LOBJ。

    起始探测复刻 walk 首窗口 [0,8)（can/io/blf.py:242，命中 p ≤ 4）。
    返回 (候选位置 int64 升序, j_first；起始窗口无 LOBJ 则 -1)。
    单类 + 免排序（AHT 实测候选 100% 单类，4 遍扫描 0.60s → 一遍）。
    """
    L = len(data)
    j_first = -1
    for j in range(5):
        if j + 4 <= L and data[j:j + 4] == b"LOBJ":
            j_first = j
            break
    if j_first < 0:
        return np.empty(0, dtype=np.int64), -1
    o = j_first % 4
    n = (L - o) // 4
    if n <= 0:
        return np.empty(0, dtype=np.int64), j_first
    v = np.frombuffer(data, dtype="<u4", count=n, offset=o)   # 零拷贝（勿切片）
    return np.nonzero(v == _LOBJ)[0].astype(np.int64) * 4 + o, j_first


def _parse_fast(data: bytes, ms_part: int):
    """快路径（计划 §4）：返回 (ContainerFrames, tail)；条件不满足 → None。

    任一条件不满足一律返回 None 交由标量回退（本函数不抛异常），唯二
    例外是契约直接要求的终结：N==0 且 max_pos≥8 → raise BLFParseError
    （与标量路径同语义，§4.2.1）。
    """
    from can.io.blf import BLFParseError

    max_pos = len(data)
    b8 = np.frombuffer(data, dtype=np.uint8)
    c, j_first = _candidates(data)
    N = len(c)
    if N == 0:
        # 无候选：max_pos < 8 → 尾部（含 0 字节空容器）；否则起始窗口有
        # LOBJ（全错位病态容器，防御分支）→ 回退（勿 raise：标量正常而
        # 快路径抛异常 = 违约）；否则 raise（§2.1）
        if max_pos < 8:
            return _empty_container_frames(), data
        if j_first >= 0:
            return None
        raise BLFParseError("Could not find next object")
    if max_pos < 16:
        # 任意候选都无法通过 base_fit（c+16 ≤ max_pos），且提取 helper 的
        # 回退位置 0 需读至 b8[7] → 直接回退
        return None

    # ── H7e 字段提取分派：候选全 4 对齐（实测 100%）→ u32 视图一次
    # gather（4 次字节散点 → 1 次视图读）；错位容器（fuzz/手工构造）
    # 走字节 gather 现路径——两读法读同一字节串，逐位相同
    aligned = bool(np.all((c & 3) == 0))
    if aligned:
        v32 = np.frombuffer(data, dtype="<u4", count=max_pos // 4)
        u32 = lambda p, mp: _u32_at_v(v32, p, mp)
        u64 = lambda p, mp: _u64_at_v(v32, p, mp)
        u16 = lambda p, mp: _u16_at_v(v32, p, mp)
    else:
        u32 = lambda p, mp: _u32_at(b8, p, mp)
        u64 = lambda p, mp: _u64_at(b8, p, mp)
        u16 = lambda p, mp: _u16_at(b8, p, mp)

    # ── 对象头字段（裁剪安全域读出，垃圾值由后续掩码屏蔽）──
    obj_size = u32(c + 8, max_pos)
    obj_type = u32(c + 12, max_pos)
    hdr_word = u32(c + 4, max_pos)
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

    # H7a 双探针：重叠定理下仅两处窗口可能有扫描不可见的 walk 命中
    # （gap > 4 的首坏轮 / first == N 的末窗口）；探针命中即回退。
    # 有效轮（gap ∈ {0,4}）由重叠定理保证 walk 首窗命中 = 扫描候选。
    # 全窗口 j∈[0,4]：obj_size ≢ 0 mod 4（fuzz FD64）可使 s ≢ o mod 4，
    # 此时 j=0/j=4 的 LOBJ 对扫描与 (1,2,3) 探针双不可见而 walk 会命中
    # （fuzz 裁决修复；探针两分支的窗口内可见 LOBJ 由前置条件排除，
    # 检查全部 5 位与只查不可见位等价）。
    if first < N and int(c[first]) - int(s[first]) > 4:
        w = int(s[first])
        for j in range(5):
            if w + j + 4 <= max_pos and data[w + j:w + j + 4] == b"LOBJ":
                return None
    if first == N and N >= 1:
        s_end = int(s[N - 1]) + int(obj_size[N - 1])   # walk 末轮后的搜索起点
        for j in range(5):
            if s_end + j + 4 <= max_pos and data[s_end + j:s_end + j + 4] == b"LOBJ":
                return None

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
    flags = u32(c + 16, max_pos)   # V1/V2 均为 u32（核对修正）
    rel = u64(c + 24, max_pos)
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
        ch_full[m] = u16(p, max_pos) - 1
        fl8 = _u8_at(b8, p + 2, max_pos)
        dlc_full[m] = _u8_at(b8, p + 3, max_pos)
        can_id = u32(p + 4, max_pos)
        arb_full[m] = can_id & np.uint32(0x1FFFFFFF)
        ext_full[m] = (can_id & np.uint32(0x80000000)) != 0
        remote_full[m] = (fl8 & np.uint8(0x80)) != 0
        dlen_full[m] = np.minimum(dlc_full[m], 8)
        doff_full[m] = p + 8

    m = use & t_err
    if m.any():
        p = pos[m]
        ch_full[m] = u16(p, max_pos) - 1
        dlc_full[m] = _u8_at(b8, p + 10, max_pos)
        can_id = u32(p + 16, max_pos)
        arb_full[m] = can_id & np.uint32(0x1FFFFFFF)
        ext_full[m] = (can_id & np.uint32(0x80000000)) != 0
        err_full[m] = True
        dlen_full[m] = np.minimum(dlc_full[m], 8)
        doff_full[m] = p + 24

    m = use & t_fd
    if m.any():
        p = pos[m]
        ch_full[m] = u16(p, max_pos) - 1
        fl8 = _u8_at(b8, p + 2, max_pos)
        dlc_code = _u8_at(b8, p + 3, max_pos)
        dlc_full[m] = np.where(dlc_code <= 15,
                               _DLC2LEN[np.minimum(dlc_code, 15)], 64)
        can_id = u32(p + 4, max_pos)
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
        can_id = u32(p + 4, max_pos)
        arb_full[m] = can_id & np.uint32(0x1FFFFFFF)
        ext_full[m] = (can_id & np.uint32(0x80000000)) != 0
        fd_flags = u32(p + 12, max_pos)
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

    # ── 载荷输出（H7b + Proto E 归一）：快路径不打包——data8 = 容器字节
    # 零拷贝视图、data_off = 帧载荷容器内绝对偏移；glen 恒存在（FD64 截断
    # 补零由 ContainerFrames.payload_block 按 glen 统一掩码）。容器级打包
    # 拷贝（362MB 结果 + 1.45GB 索引）整体删除，载荷只拷一次（桶级固有
    # 拷贝 164MB）。
    lens = dlen_full[idx]
    n_emit = len(idx)
    if n_emit:
        data8 = b8
        data_off = doff_full[idx]
        glen = glen_full[idx]
    else:
        data8 = np.zeros(0, dtype=np.uint8)
        data_off = np.zeros(0, dtype=np.int64)
        glen = np.zeros(0, dtype=np.int64)

    # ── 时间戳：守卫保证 ms_part + rel_ns ≤ 2^53，int→float64 精确 ──
    rel_ns = np.where(flags == 1, rel * np.uint64(10000), rel)
    ts = (np.uint64(ms_part) + rel_ns[idx]).astype(np.float64) * 1e-9

    cf = ContainerFrames(
        channel=ch_full[idx], ts=ts, arb=arb_full[idx], is_ext=ext_full[idx],
        is_remote=remote_full[idx], is_error=err_full[idx], is_fd=fd_full[idx],
        dlc=dlc_full[idx], data8=data8, data_off=data_off,
        data_len=lens, glen=glen)

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
        data8=data8, data_off=starts[:-1].copy(), data_len=lens, glen=lens)


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
    for start_ns, data in blf_reader.iter_containers(path, progress_cb=progress_cb):
        if cancel_cb is not None and cancel_cb():
            raise blf_reader.ConversionCancelled()
        if ms_part is None:
            ms_part = blf_reader.ms_part_ns(start_ns)
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
        frames, tail = blf_reader.walk_container(data, ms_part, cancel_cb=cancel_cb)
        if frames:
            yield _frames_to_container(frames)
