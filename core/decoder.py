"""帧流 → 按报文聚合的信号物理值。"""
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

from core.blf_reader import Frame
from core.dbc_loader import DbcDef, MessageDef, SignalDef, classify, normalize_id


@dataclass(frozen=True)
class EnumTable:
    """值表信号的转换元数据（writer 写成 TABX 文本表 + default 换算）。

    choices: DBC VAL_ 原始值 → 文本；scale/offset/unit: 表外原始值的换算
    （CANoe 语义：表内原始值 → 文本，表外 → 线性换算后的物理值）。
    """
    choices: dict
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""


@dataclass
class SignalSeries:
    channel: int
    message_name: str
    node: str
    signal_names: list[str]
    timestamps: np.ndarray          # float64 (N,)
    # 每信号 dtype 由 DBC 定义决定（修复项 3+8，与 CANoe 一致）：
    #   float64 物理值 / 最小整型原值（uint8..int64）；值表信号存原始整型，
    #   文本表由 writer 侧写成 TABX 转换（enums），见 _signal_kind
    values: dict[str, np.ndarray] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    enums: dict[str, EnumTable] = field(default_factory=dict)


@dataclass
class Bucket:
    """解码桶（跨模块契约，词条见 CONTEXT.md「解码桶」）：
    arb=归一化键（= buckets dict 键，finish 入口断言）；raw_id=首帧原始 id；
    桶内帧均已分类（构造即预检，finish 不再防御）；插入序=系列序（dict 插入序）。
    相位互斥（由工厂方法保证）：
      feed 相位（feed_ts: list[float], feed_data: list[bytes]）→ 数组相位（to_array）
      blocks 相位（blocks/ts_blocks/lens_blocks: list[ndarray]）→ 数组相位（to_array）
    数组相位 = ts (N,) float64 / lens (N,) int64 / data (N, L) uint8（finish 消费）。"""
    arb: int
    raw_id: int
    md: MessageDef
    feed_ts: list[float] | None = None
    feed_data: list[bytes] | None = None
    blocks: list[np.ndarray] | None = None
    ts_blocks: list[np.ndarray] | None = None
    lens_blocks: list[np.ndarray] | None = None
    ts: np.ndarray | None = None
    lens: np.ndarray | None = None
    data: np.ndarray | None = None

    @classmethod
    def from_feed(cls, arb: int, raw_id: int, md: MessageDef) -> "Bucket":
        """feed 路由建桶（decoder.feed setdefault 语义）：raw_id = 首帧原始 id。"""
        return cls(arb=arb, raw_id=raw_id, md=md, feed_ts=[], feed_data=[])

    @classmethod
    def from_blocks(cls, arb: int, raw_id: int, md: MessageDef,
                    block: np.ndarray, ts: np.ndarray, lens: np.ndarray) -> "Bucket":
        """向量化路由建桶（converter._read_vectorized 语义）：raw_id = 首帧原始 id。"""
        return cls(arb=arb, raw_id=raw_id, md=md, blocks=[block],
                   ts_blocks=[ts], lens_blocks=[lens])

    def add_frame(self, ts_seconds: float, data: bytes) -> None:
        """feed 追加一帧（仅 feed 相位）。"""
        self.feed_ts.append(ts_seconds)
        self.feed_data.append(data)

    def add_block(self, block: np.ndarray, ts: np.ndarray, lens: np.ndarray) -> None:
        """向量化追加一块（仅 blocks 相位；参数序与 from_blocks 一致：block 在前）。"""
        self.ts_blocks.append(ts)
        self.lens_blocks.append(lens)
        self.blocks.append(block)

    def to_array(self) -> None:
        """三相位 → 数组相位（幂等：数组相位 no-op）。

        feed 相位 → 数组：lens 从 data 字节长推导（原 _normalize_bucket +
        _bucket_data_array 语义内化）；blocks 相位 → 数组：一次末态连接（原
        _assemble_bucket 语义内化）。转换后源相位字段置 None（互斥不变式）。
        """
        if self.ts is not None:
            return
        if self.feed_ts is not None:
            lens = np.fromiter((len(d) for d in self.feed_data), dtype=np.intp,
                               count=len(self.feed_ts))
            n = len(self.feed_data)
            max_len = int(lens.max()) if n else 0
            data = np.zeros((n, max_len), dtype=np.uint8)
            for i, d in enumerate(self.feed_data):
                if d:
                    data[i, : len(d)] = np.frombuffer(d, dtype=np.uint8)
            self.lens = lens
            self.data = data
            self.ts = np.asarray(self.feed_ts, dtype=np.float64)
            self.feed_ts = None
            self.feed_data = None
            return
        # blocks 相位 → 数组（H2a：一次末态连接替代逐容器 np.concatenate
        # 重分配——实测 3774 次追加拷贝 4.83GB、其中 4.67GB 可避免、函数内
        # 1.65s → 块切片批量拷贝，每字节只写一次；不用 row/col 散点——桶规模
        # 1.6 亿位置 × int64 索引数组的 fancy indexing 实测 7.6s，比块拷贝
        # 慢一个数量级）。单块桶直接复用入桶数组（零拷贝、对象同一性保持——
        # 数组相位 no-op 测试的同一性前提），多块走一次末态连接。
        if len(self.ts_blocks) == 1:
            ts = self.ts_blocks[0]
            lens = self.lens_blocks[0]
        else:
            ts = np.concatenate(self.ts_blocks)
            lens = np.concatenate(self.lens_blocks)
        n = len(ts)
        L = max(int(blk.shape[1]) for blk in self.blocks) if self.blocks else 0
        if len(self.blocks) == 1 and self.blocks[0].shape[1] == L:
            data = self.blocks[0]          # 单块且无需补零：直接复用，不拷贝
        else:
            data = np.zeros((n, L), dtype=np.uint8)
            start = 0
            for blk, bl in zip(self.blocks, self.lens_blocks):
                m = len(bl)
                if m:
                    if blk.shape[1] < L:
                        blk = np.pad(blk, ((0, 0), (0, L - blk.shape[1])))
                    data[start:start + m] = blk
                start += m
        self.ts = ts
        self.lens = lens
        self.data = data
        self.blocks = None
        self.ts_blocks = None
        self.lens_blocks = None

    @property
    def n_frames(self) -> int:
        """桶内帧数（三相位）：mp_finish 任务/进度用（现 len(b["ts"])）。"""
        if self.ts is not None:
            return len(self.ts)
        if self.feed_ts is not None:
            return len(self.feed_ts)
        if self.ts_blocks is not None:
            return sum(len(t) for t in self.ts_blocks)
        return 0

    def memory_estimate(self) -> int:
        """桶内存估算（三相位）：feed 按帧 ts ~32B + data 字节；blocks/数组按
        nbytes 求和。bucket_bytes 用（isinstance 分派收进类内）。"""
        if self.ts is not None:
            return int(self.ts.nbytes) + int(self.lens.nbytes) + int(self.data.nbytes)
        if self.feed_ts is not None:
            return len(self.feed_ts) * 32 + sum(len(d) for d in self.feed_data)
        if self.ts_blocks is not None:
            return (sum(t.nbytes for t in self.ts_blocks)
                    + sum(l.nbytes for l in self.lens_blocks)
                    + sum(blk.nbytes for blk in self.blocks))
        return 0


# ── 方案C：向量化位提取（与 bitstruct/cantools 逐位等价，测试对拍）──

def _pad_to_64(data: np.ndarray) -> np.ndarray:
    """(N, L) uint8 → (N, Lp) uint8，Lp 为 8 的倍数（尾部补零，供 uint64 视图）。"""
    n, l = data.shape
    if l % 8 == 0:
        return data
    pad = np.zeros((n, 8 - l % 8), dtype=np.uint8)
    return np.concatenate([data, pad], axis=1)


def _extract_bits(data64: np.ndarray, pos: int, length: int,
                  byte_order: str) -> np.ndarray:
    """从 (N, W) uint64 小端字视图的位流 pos 起提取 length（≤64）位 → uint64。

    大端: pos 为 MSB-first 位流位置（位 p = 字节 p//8 的 MSB 起第 p%8 位）；
    小端: pos = DBC start bit（位 i = 字节 i//8 的 LSB 起第 i%8 位）。
    跨界（跨 64 位字）两字拼接；末字越界补零。
    """
    w, o = divmod(pos, 64)
    if byte_order == "big_endian":
        data64 = data64.view(">u8")   # 字节组内位序反转：位 p = 字 (p//64) 的位 (63 - p%64)
    hi = data64[:, w]
    mask = np.uint64((1 << length) - 1)
    if byte_order == "big_endian":
        if o + length <= 64:
            return (hi >> (64 - o - length)) & mask
        k = o + length - 64
        lo = (data64[:, w + 1] if w + 1 < data64.shape[1]
              else np.zeros(data64.shape[0], dtype=np.uint64))
        return (((hi << k) | (lo >> (64 - k))) & mask)
    else:
        if o + length <= 64:
            return (hi >> o) & mask
        k = 64 - o
        lo = (data64[:, w + 1] if w + 1 < data64.shape[1]
              else np.zeros(data64.shape[0], dtype=np.uint64))
        return ((hi >> o) | (lo << k)) & mask


def _sign_extend(v: np.ndarray, length: int) -> np.ndarray:
    """length 位无符号 → 补码符号扩展 int64（length=64 直接 view）。"""
    if length == 64:
        return v.view(np.int64)
    mask = (np.uint64(1) << length) - 1
    sign = np.uint64(1) << (length - 1)
    return np.where(v & sign, (v | ~mask).view(np.int64), v.astype(np.int64))


def _extract_signal(data64: np.ndarray, sd: SignalDef) -> np.ndarray:
    """单信号向量化提取 → uint64（无符号）/ int64（有符号）/ float32|64（浮点）。

    >64 位信号取低 64 位（与 Task 5 存储截断语义一致）：大端低 64 位 =
    位流末 64 位（pos 后移 length-64）；小端 LSB 在 pos，低 64 位 = 从
    pos 起 64 位（pos 不动，裁决修正 2）。
    """
    length = sd.length
    if sd.byte_order == "big_endian":
        pos = 8 * (sd.start_bit // 8) + (7 - sd.start_bit % 8)
    else:
        pos = sd.start_bit
    if length > 64:
        if sd.byte_order == "big_endian":
            pos += length - 64
        length = 64
    v = _extract_bits(data64, pos, length, sd.byte_order)
    if sd.is_float:
        bits = v.astype(np.uint32 if length == 32 else np.uint64)
        return bits.view(np.float32 if length == 32 else np.float64)
    if sd.is_signed:
        return _sign_extend(v, length)
    return v


def _has_enum_storage(sd: SignalDef) -> bool:
    """值表信号（DBC VAL_）是否走「原始整型 + TABX 值表」存储。

    浮点信号带值表（SIG_VALTYPE_ + VAL_）项目内不存在、无 CANoe 参考，
    维持 float 分支（见 _signal_kind）。
    """
    return bool(sd.choices) and not sd.is_float


def _signal_kind(sd: SignalDef) -> str:
    """存储类型（纯 SignalDef 函数，2026-09-20 重做）：
    - 有值表（choices）→ 原始整型值（含物理变换的信号：表内/表外的换算交给
      writer 的 TABX 转换，见 EnumTable）；
    - 有物理变换或浮点（无值表）→ float64 物理值；
    - 其余 → 原始整型（最小 dtype）。

    旧实现按观察值定类型（观察值全在表内 → |Sn 文本，混表外 → float64 且表内
    值存 nan）。字符串通道 CANape 画不成数值曲线 —— 枚举信号渲染成一条粗直线，
    看不出变化（2026-09-20 CANape 显示 bug），故与 CANoe 一致改存数值 + 转换表。
    """
    if _has_enum_storage(sd):
        return "int"
    if sd.is_float or sd.scale != 1.0 or sd.offset != 0.0:
        return "float"
    return "int"


def _enum_tables(md: MessageDef) -> dict[str, EnumTable]:
    """值表信号 → EnumTable（两条解码路径共用，writer 侧写 TABX 转换的唯一来源）。"""
    return {s.name: EnumTable(choices=s.choices, scale=s.scale,
                              offset=s.offset, unit=s.unit)
            for s in md.signals if _has_enum_storage(s)}


def _clamped_int_array(vals: list, dtype: np.dtype) -> np.ndarray:
    """Python 值列表 → 最小整型 dtype；非整数（mux 非活跃 nan）→ 0；
    超出 dtype 范围的值按模截断（0x7DF 512 位信号修复，语义见 Task 5）。"""
    if not vals:
        return np.asarray(vals, dtype=dtype)
    info = np.iinfo(dtype)
    if all(isinstance(v, (int, np.integer)) and info.min <= int(v) <= info.max
           for v in vals):
        return np.asarray(vals, dtype=dtype)
    bits = dtype.itemsize * 8
    mod = 1 << bits
    clean = [0 if not isinstance(v, (int, np.integer)) else int(v) % mod
             for v in vals]
    u = np.asarray(clean, dtype=np.uint64 if bits >= 32
                   else np.uint32 if bits >= 16 else np.uint8)
    if dtype.kind == "u":
        return u.astype(dtype)
    half = 1 << (bits - 1)
    return np.where(u.astype(object) >= half,
                    u.astype(object) - (1 << bits), u.astype(object)).astype(dtype)


def _physical(sd: SignalDef, raw: np.ndarray) -> np.ndarray:
    """scale/offset 物理变换 → float64（与参考实现逐位一致）。

    参考路径对整型 scale/offset 做 Python 精确整数运算后再一次转 float64；
    仅当 |raw*scale+offset| 可能 ≥2^53（float64 无法精确表示）时走逐帧精确路径，
    否则 float64 快路径（raw<2^53 且乘积<2^53 时浮点运算精确，二者逐位相同）。
    守卫取 max(|min|, |max|)（裁决修正 3）：全负大值 raw 时 |min| 才是上界，
    旧守卫只查 max 对 |min|>|max| 低估 → 恒走快路径差 4 ulp。
    """
    scale, offset = float(sd.scale), float(sd.offset)
    if not (scale.is_integer() and offset.is_integer() and not sd.is_float):
        return raw.astype(np.float64) * scale + offset   # 与参考同序 float64 运算
    s, o = int(scale), int(offset)
    if raw.size == 0:        # 空 raw 的 min/max 抛异常：短路守卫放取值前
        return raw.astype(np.float64) * float(s) + float(o)
    bound = max(abs(float(raw.min())), abs(float(raw.max())))
    if (bound * abs(s) + abs(o)) < 2**53:
        return raw.astype(np.float64) * float(s) + float(o)
    return np.fromiter((int(r) * s + o for r in raw), dtype=np.float64, count=raw.size)


def _store_signal(sd: SignalDef, raw: np.ndarray, active: np.ndarray) -> np.ndarray:
    """按存储类型落盘：int 非活跃→0；float 非活跃→nan。

    值表信号存原始整型（表内外一律原值，含物理变换的信号——换算由 writer 的
    TABX 转换承担，读回物理值与 CANoe 一致）。
    """
    if _signal_kind(sd) == "float":
        out = np.full(len(raw), np.nan, dtype=np.float64)
        out[active] = _physical(sd, raw)[active]
        return out
    out = np.zeros(len(raw), dtype=_int_dtype(sd.length, sd.is_signed))
    out[active] = raw[active].astype(out.dtype)
    return out


def _msg_vectorizable(md: MessageDef) -> bool:
    """向量化支持面：无 mux，或选择信号为 (1,0) 无 choices 的单级 mux。
    选择信号带变换/choices 的 mux（cantools 按缩放值路由）回退逐帧参考。"""
    selector = next((s for s in md.signals if s.is_multiplexer), None)
    if selector is None:
        return True
    return selector.scale == 1.0 and selector.offset == 0.0 and not selector.choices


def _mux_plan(md: MessageDef) -> tuple[SignalDef, dict[int, list[SignalDef]]] | None:
    """单级 mux 分解 → (选择信号, {mux 值: 子信号列表})；无 mux → None。

    前提（_msg_vectorizable 已保证）：选择信号为 (1,0) 无 choices；DBC 格式
    无多级 mux（cantools 的多级支持仅 ARXML 有）。
    """
    selector = next((s for s in md.signals if s.is_multiplexer), None)
    if selector is None:
        return None
    children: dict[int, list[SignalDef]] = defaultdict(list)
    for s in md.signals:
        if s.multiplexer_ids is not None:
            for mv in s.multiplexer_ids:
                children[mv].append(s)
    return selector, dict(children)


def _finish_bucket_vectorized(dbc, channel, b, stats) -> list[SignalSeries]:
    """单桶向量化解码（无 mux 或单级 mux），与逐帧参考实现逐点等价。

    A：桶为数组相位 Bucket（to_array 收敛：ts float64 (N,)、lens int64
    (N,)、data (N, L) uint8——feed/向量化路由统一经 to_array 到数组相位）。
    """
    md = b.md
    n = len(b.ts)
    # 桶内帧均已分类（构造即预检，见 Bucket docstring）——不再防御短帧
    decodable = np.ones(n, dtype=bool)
    plan = _mux_plan(md)
    if plan is not None:
        data64 = _pad_to_64(b.data).view("<u8")
        selector, children = plan
        sel_raw = _extract_signal(data64, selector)
        known_arr = np.array(sorted(children), dtype=sel_raw.dtype)
        bad = ~np.isin(sel_raw, known_arr)      # 无子组的 mux 值 → DecodeError → 未知
        decodable &= ~bad
    n_unknown = n - int(decodable.sum())
    if n_unknown:
        stats.unknown_frames += n_unknown
        stats.unknown_ids.add(b.raw_id)
    if not decodable.any():
        return []
    if plan is None:
        data64 = _pad_to_64(b.data).view("<u8")
    d64 = data64[decodable]
    timestamps = np.asarray(b.ts, dtype=np.float64)[decodable]
    n_ok = int(decodable.sum())
    values = {}
    for s in md.signals:
        if plan is not None and s.multiplexer_ids is not None:
            active = np.isin(sel_raw[decodable],
                             np.array(sorted(s.multiplexer_ids), dtype=sel_raw.dtype))
        else:
            active = np.ones(n_ok, dtype=bool)
        raw = _extract_signal(d64, s)
        values[s.name] = _store_signal(s, raw, active)
    return [SignalSeries(
        channel=channel, message_name=md.name, node=md.sender_node,
        signal_names=[s.name for s in md.signals],
        timestamps=timestamps, values=values,
        units={s.name: s.unit for s in md.signals},
        enums=_enum_tables(md))]


def _decode_bucket_reference(dbc, channel, b, stats) -> list[SignalSeries]:
    """非向量化报文（mux 报文）回退：逐帧 cantools 解码。

    逐帧语义与方案C前完全一致（decode_message/未知帧分类/值累积/收尾类型判定）。
    数组相位（to_array 收敛）——data (N, L) uint8 按 lens 行切片还原帧字节。
    """
    from cantools.database.errors import DecodeError

    md = b.md
    enum_names = {s.name for s in md.signals if _has_enum_storage(s)}
    vals_by_sig = {s.name: [] for s in md.signals}
    ts_ok = []
    for ts, ln, row in zip(b.ts, b.lens, b.data):
        try:
            payload = row[:ln].tobytes()
            # 归一化键（含 EFF 位）与 feed/参考实现对拍一致（rulings 修正 2）
            decoded = dbc.db.decode_message(b.arb, payload)
            # 值表信号存原始值：默认解码把表内值替换成文本，另取一次原始值
            # （不缩放不替换）；无值表信号的缩放仍由 cantools 承担（对拍保留）
            raw_decoded = (dbc.db.decode_message(b.arb, payload,
                                                 decode_choices=False, scaling=False)
                           if enum_names else None)
        except (KeyError, DecodeError):
            stats.unknown_frames += 1
            stats.unknown_ids.add(b.raw_id)
            continue
        ts_ok.append(ts)
        for s in md.signals:
            src = raw_decoded if s.name in enum_names else decoded
            vals_by_sig[s.name].append(src.get(s.name, float("nan")))
    if not ts_ok:
        return []
    values = {}
    for s in md.signals:
        vals = vals_by_sig[s.name]
        if _signal_kind(s) == "float":
            values[s.name] = np.asarray(vals, dtype=np.float64)
        else:
            values[s.name] = _clamped_int_array(vals, _int_dtype(s.length, s.is_signed))
    return [SignalSeries(
        channel=channel, message_name=md.name, node=md.sender_node,
        signal_names=[s.name for s in md.signals],
        timestamps=np.asarray(ts_ok, dtype=np.float64),
        values=values, units={s.name: s.unit for s in md.signals},
        enums=_enum_tables(md))]


def _int_dtype(length: int, is_signed: bool) -> np.dtype:
    """按位宽取最小整型 dtype（修复项 8）：8→8 位，9..16→16 位，17..32→32 位，>32→64 位。"""
    if is_signed:
        return np.dtype(np.int8 if length <= 8 else np.int16 if length <= 16
                        else np.int32 if length <= 32 else np.int64)
    return np.dtype(np.uint8 if length <= 8 else np.uint16 if length <= 16
                    else np.uint32 if length <= 32 else np.uint64)


@dataclass
class DecodeStats:
    total_frames: int = 0
    unknown_frames: int = 0
    unknown_ids: set[int] = field(default_factory=set)


class ChannelDecoder:
    """增量式通道解码器：单遍扫描管线按帧 feed，收齐后 finish 产出系列。

    方案C：feed 只按 (通道, 报文 ID) 分桶收集原始字节（未知 ID 键查即预检，
    不再逐帧 cantools 解码），finish 用 numpy 批量位提取（与逐帧参考实现
    逐点等价，属性测试锁定）；无法向量化的报文回退逐帧解码。
    """

    def __init__(self, dbc: DbcDef, channel: int):
        self.dbc = dbc
        self.channel = channel
        self.stats = DecodeStats()
        self.buckets = {}  # arb -> Bucket（三相位互斥，见 Bucket docstring）

    def feed(self, fr: Frame) -> None:
        stats = self.stats
        stats.total_frames += 1
        arb = normalize_id(fr.arbitration_id, fr.is_extended)
        md = classify(self.dbc, arb, len(fr.data))
        if md is None:
            # 未知 ID，或已知 ID 短帧（cantools DecodeError）→ 未知帧。
            # 桶只收已通过预检的帧：短帧入桶会使系列位置前移，与参考系列
            # 顺序不符（_assert_series_equal 按顺序 zip）。分类规则唯一实现
            # 在 dbc_loader.classify（A2：feed 与向量化路由共用）。
            stats.unknown_frames += 1
            stats.unknown_ids.add(fr.arbitration_id)
            return
        bucket = self.buckets.setdefault(
            arb, Bucket.from_feed(arb, fr.arbitration_id, md))
        bucket.add_frame(fr.ts_seconds, fr.data)

    def finish(self) -> tuple[list[SignalSeries], DecodeStats]:
        series = []
        for arb, b in self.buckets.items():
            assert arb == b.arb, f"桶键与字段漂移: {arb} != {b.arb}"
            b.to_array()
            md = b.md
            if not _msg_vectorizable(md):
                series.extend(_decode_bucket_reference(self.dbc, self.channel, b, self.stats))
                continue
            s = _finish_bucket_vectorized(self.dbc, self.channel, b, self.stats)
            series.extend(s)
        return series, self.stats


def decode_channel(frames: Iterator[Frame], dbc: DbcDef, channel: int):
    """帧流 → 按报文聚合的信号物理值（ChannelDecoder 的流式包装）。"""
    dec = ChannelDecoder(dbc, channel)
    for fr in frames:
        dec.feed(fr)
    return dec.finish()
