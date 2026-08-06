"""帧流 → 按报文聚合的信号物理值。"""
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

from cantools.database.namedsignalvalue import NamedSignalValue

from core.blf_reader import Frame
from core.dbc_loader import DbcDef, MessageDef, SignalDef


@dataclass
class SignalSeries:
    channel: int
    message_name: str
    node: str
    signal_names: list[str]
    timestamps: np.ndarray          # float64 (N,)
    # 每信号 dtype 由 DBC 定义决定（修复项 3+8，与 CANoe 一致）：
    #   float64 物理值 / 最小整型原值（uint8..int64）/ |Sn 枚举文本 bytes
    values: dict[str, np.ndarray] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)


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


def _signal_kind(sd: SignalDef, vals: list) -> str:
    """CANoe 最小存储类型规则（参考 _T058.mdf 全量核对：3724 信号 0 反例）：
    - 物理变换（factor≠1 / offset≠0 / 浮点）：默认 float64 物理值；但有 choices 且
      **观察到的值全部在表内**（全为 NamedSignalValue）→ 文本（实测
      HVAC_RearTempSelect 全 31→|S7；HVAC_DriverTempSelect 混表外 0 → float64，
      表内值存 nan）；
    - 无物理变换且有 choices → 文本（表外原始值存空字节，实测 FanPWMSt）；
    - 其余 → 原始整型（最小 dtype）。"""
    if sd.is_float or sd.scale != 1.0 or sd.offset != 0.0:
        if sd.choices and all(isinstance(v, NamedSignalValue) for v in vals):
            return "text"
        return "float"
    if sd.choices:
        return "text"
    return "int"


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


def _bucket_data_array(data_bytes: list[bytes], max_len: int) -> np.ndarray:
    """bytes 列表 → (N, L) uint8（L = 桶内最大帧长，补零；参考 converter._collect_raw）。"""
    n = len(data_bytes)
    arr = np.zeros((n, max_len), dtype=np.uint8)
    for i, d in enumerate(data_bytes):
        if d:
            arr[i, : len(d)] = np.frombuffer(d, dtype=np.uint8)
    return arr


def _choices_lookup(sd: SignalDef, raw: np.ndarray) -> tuple[np.ndarray | None, np.ndarray | None]:
    """choices 键查询 → (排序键数组, in_table 掩码)；无 choices → (None, None)。

    键匹配语义同 Python dict（31.0 == 31）：浮点信号键按 float64 比较；
    整型按符号取 int64/uint64（裁决修正 4）——与 raw 同 dtype 空间，
    searchsorted/判等不跨空间（uint64 键装负键会绕成巨大值且 int64 raw ×
    uint64 keys 经 float64 提升后键 ≥2^53 时判等错配）。
    """
    if not sd.choices:
        return None, None
    keys = np.asarray(sorted(sd.choices),
                      dtype=np.float64 if sd.is_float
                      else np.int64 if sd.is_signed else np.uint64)
    idx = np.searchsorted(keys, raw, side="left")
    ok = idx < len(keys)
    in_table = ok & (keys[np.where(ok, idx, 0)] == raw)
    return keys, in_table


def _signal_kind_vec(sd: SignalDef, in_table: np.ndarray | None) -> str:
    """向量化存储类型判定（= _signal_kind 语义，观察值全在表内判定向量化）。
    in_table 为 None（无 choices）或掩码（mux 非活跃帧为 False，同 nan 语义）。"""
    if sd.is_float or sd.scale != 1.0 or sd.offset != 0.0:
        if sd.choices and bool(np.all(in_table)):
            return "text"
        return "float"
    if sd.choices:
        return "text"
    return "int"


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


def _text_array(sd: SignalDef, raw: np.ndarray, keys: np.ndarray,
                in_table: np.ndarray) -> np.ndarray:
    """文本存储：表内值 → 定宽 UTF-8 bytes（= 最长观察值），表外/非活跃 → b''。"""
    n = len(raw)
    if not in_table.any():
        return np.zeros(n, dtype="|S1")
    texts = np.array([sd.choices[k] for k in keys.tolist()], dtype=object)
    ai = np.where(in_table)[0]
    matched = texts[np.searchsorted(keys, raw[ai], side="left")]
    # 宽度按 UTF-8 字节数（= 参考 str(v).encode("utf-8") 的字节长度；
    # 按 len(t) 字符数会对非 ASCII 文本（GBK 中文）定宽不足而截断）
    w = max((len(t.encode("utf-8")) for t in matched), default=1)
    out = np.zeros(n, dtype=f"|S{w}")
    out[ai] = np.asarray([t.encode("utf-8") for t in matched.tolist()], dtype=out.dtype)
    return out


def _store_signal(sd: SignalDef, raw: np.ndarray, active: np.ndarray,
                  keys, in_table) -> np.ndarray:
    """按存储类型落盘：int 非活跃→0；float 非活跃→nan；text 非活跃/表外→b''。"""
    n = len(raw)
    if _signal_kind_vec(sd, in_table) == "text":
        return _text_array(sd, raw, keys, in_table)
    if _signal_kind_vec(sd, in_table) == "int":
        out = np.zeros(n, dtype=_int_dtype(sd.length, sd.is_signed))
        out[active] = raw[active].astype(out.dtype)
        return out
    out = np.full(n, np.nan, dtype=np.float64)
    phys = _physical(sd, raw)
    if in_table is None:
        out[active] = phys[active]
    else:
        out[active & ~in_table] = phys[active & ~in_table]
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
    """单桶向量化解码（无 mux 或单级 mux），与逐帧参考实现逐点等价。"""
    md = b["md"]
    n = len(b["ts"])
    data_bytes = b["data"]
    lens = np.fromiter((len(d) for d in data_bytes), dtype=np.intp, count=n)
    valid = lens >= md.frame_length      # 短帧 DecodeError → 未知
    decodable = valid.copy()
    plan = _mux_plan(md)
    if plan is not None:
        raw_data = _bucket_data_array(data_bytes, int(lens.max()))
        data64 = _pad_to_64(raw_data).view("<u8")
        selector, children = plan
        sel_raw = _extract_signal(data64, selector)
        known_arr = np.array(sorted(children), dtype=sel_raw.dtype)
        bad = ~np.isin(sel_raw, known_arr)      # 无子组的 mux 值 → DecodeError → 未知
        decodable &= ~bad                        # 短帧已由 valid 排除（& 起点是 valid）
    n_unknown = n - int(decodable.sum())
    if n_unknown:
        stats.unknown_frames += n_unknown
        stats.unknown_ids.add(b["raw_id"])
    if not decodable.any():
        return []
    if plan is None:
        raw_data = _bucket_data_array(data_bytes, int(lens.max()))
        data64 = _pad_to_64(raw_data).view("<u8")
    d64 = data64[decodable]
    timestamps = np.asarray(b["ts"], dtype=np.float64)[decodable]
    n_ok = int(decodable.sum())
    values = {}
    for s in md.signals:
        if plan is not None and s.multiplexer_ids is not None:
            active = np.isin(sel_raw[decodable],
                             np.array(sorted(s.multiplexer_ids), dtype=sel_raw.dtype))
        else:
            active = np.ones(n_ok, dtype=bool)
        raw = _extract_signal(d64, s)
        keys, in_table = _choices_lookup(s, raw)
        if in_table is not None:
            in_table &= active
        values[s.name] = _store_signal(s, raw, active, keys, in_table)
    return [SignalSeries(
        channel=channel, message_name=md.name, node=md.sender_node,
        signal_names=[s.name for s in md.signals],
        timestamps=timestamps, values=values,
        units={s.name: s.unit for s in md.signals})]


def _decode_bucket_reference(dbc, channel, b, stats) -> list[SignalSeries]:
    """非向量化报文（mux 报文）回退：逐帧 cantools 解码。

    逐帧语义与方案C前完全一致（decode_message/未知帧分类/值累积/收尾类型判定）。
    """
    from cantools.database.errors import DecodeError
    from cantools.database.namedsignalvalue import NamedSignalValue

    md = b["md"]
    vals_by_sig = {s.name: [] for s in md.signals}
    ts_ok = []
    for ts, data in zip(b["ts"], b["data"]):
        try:
            # 归一化键（含 EFF 位）与 feed/参考实现对拍一致（rulings 修正 2）
            decoded = dbc.db.decode_message(b["arb"], data)
        except (KeyError, DecodeError):
            stats.unknown_frames += 1
            stats.unknown_ids.add(b["raw_id"])
            continue
        ts_ok.append(ts)
        for s in md.signals:
            vals_by_sig[s.name].append(decoded.get(s.name, float("nan")))
    if not ts_ok:
        return []
    values = {}
    for s in md.signals:
        vals = vals_by_sig[s.name]
        kind = _signal_kind(s, vals)
        if kind == "text":
            values[s.name] = np.asarray([
                str(v).encode("utf-8") if isinstance(v, NamedSignalValue) else b""
                for v in vals])
        elif kind == "int":
            values[s.name] = _clamped_int_array(vals, _int_dtype(s.length, s.is_signed))
        else:
            values[s.name] = np.asarray(
                [float("nan") if isinstance(v, NamedSignalValue) else v
                 for v in vals], dtype=np.float64)
    return [SignalSeries(
        channel=channel, message_name=md.name, node=md.sender_node,
        signal_names=[s.name for s in md.signals],
        timestamps=np.asarray(ts_ok, dtype=np.float64),
        values=values, units={s.name: s.unit for s in md.signals})]


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
        self.buckets = {}  # arb -> {"ts": [], "data": [], "arb": int, "raw_id": int, "md": MessageDef}

    def feed(self, fr: Frame) -> None:
        stats = self.stats
        stats.total_frames += 1
        arb = fr.arbitration_id | (0x80000000 if fr.is_extended else 0)
        md = self.dbc.messages.get(arb)
        if md is None or len(fr.data) < md.frame_length:
            # 未知 ID，或已知 ID 短帧（cantools DecodeError）→ 未知帧。
            # 修正（brief 对已知 ID 短帧也入桶、finish 再计未知）：逐帧参考在
            # 首个成功解码帧才建桶（短帧不入桶），桶插入顺序 = 系列顺序；
            # 短帧入桶会使该报文系列位置前移 → 与参考系列顺序不符（真实 DBC
            # 对拍失败，_assert_series_equal 按顺序 zip）。此处按参考分类逐帧
            # 预检：短帧直接计未知、不入桶；finish 的 valid 掩码保留为防御。
            stats.unknown_frames += 1
            stats.unknown_ids.add(fr.arbitration_id)
            return
        bucket = self.buckets.setdefault(arb, {
            "ts": [], "data": [], "arb": arb, "raw_id": fr.arbitration_id, "md": md,
        })
        bucket["ts"].append(fr.ts_seconds)
        bucket["data"].append(fr.data)

    def finish(self) -> tuple[list[SignalSeries], DecodeStats]:
        series = []
        for arb, b in self.buckets.items():
            md = b["md"]
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
