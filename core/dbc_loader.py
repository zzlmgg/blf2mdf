"""DBC 解析：一次一个文件，不合并。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

LOGGER = logging.getLogger(__name__)

# 归一化键的 EFF 位（Extended Frame Flag）：并入原始 id，与 cantools/can 生态一致
_EFF_BIT = 0x80000000


def normalize_id(raw_id: int, is_extended: bool) -> int:
    """归一化键（标量）：原始 id 并入 EFF 位，即 dbc.messages 键的唯一规则。

    原始 id（29 位扩展 / 11 位标准）本身不含 EFF 位；置位后与帧身份键
    对齐，标准/扩展报文共用原始 id 时仍可区分。"""
    return raw_id | (_EFF_BIT if is_extended else 0)


def normalize_ids(arbs: np.ndarray, is_ext: np.ndarray) -> np.ndarray:
    """归一化键（numpy 批量版）：与 normalize_id 逐位一致，dtype 保持 uint32。"""
    return arbs | np.where(is_ext, np.uint32(_EFF_BIT), np.uint32(0))


def classify(dbc: DbcDef, arb: int, data_len: int) -> MessageDef | None:
    """报文分类（唯一实现）：归一化键查找 + 帧长校验 → MessageDef 或 None。

    未知 ID，或 data_len < md.frame_length（短帧，cantools DecodeError）→ None。
    feed 逐帧路由与向量化路由（classify_batch）共用本规则；arb 必须为归一化
    键（normalize_id 产物）。等价性由属性测试锁定（test_dbc_loader）。"""
    md = dbc.messages.get(arb)
    if md is None or data_len < md.frame_length:
        return None
    return md


def message_table(dbc: DbcDef) -> tuple[np.ndarray, np.ndarray, list[MessageDef]]:
    """单通道长度表：(键 uint32 排序, 帧长 int64, MessageDef 表)。

    键 = 归一化键（规则见 normalize_ids），searchsorted 前提 = 严格排序；
    逐通道构造（原 converter._prep_decode_info 语义迁入，调用方包装
    {ch: message_table(dec.dbc)}）。"""
    keys = sorted(dbc.messages)
    return (
        np.asarray(keys, dtype=np.uint32),
        np.asarray([dbc.messages[k].frame_length for k in keys], dtype=np.int64),
        [dbc.messages[k] for k in keys],
    )


def classify_batch(table: tuple[np.ndarray, np.ndarray, list[MessageDef]],
                   norm: np.ndarray, data_len: np.ndarray) \
        -> tuple[np.ndarray, np.ndarray]:
    """批量分类 twin（与 classify 逐元素等价，属性测试锁定）。

    found = 归一化键在表内；valid = found 且 data_len >= frame_length
    （短帧排除）。空表 → 全 False（调用方按 ~valid 全计未知，与逐帧等价）。
    """
    keys, lens_tab, _ = table
    if len(keys) == 0:
        return (np.zeros(len(norm), dtype=bool),
                np.zeros(len(norm), dtype=bool))
    pos = np.searchsorted(keys, norm)
    ok = pos < len(keys)
    safe = np.where(ok, pos, 0)
    found = ok & (keys[safe] == norm)
    fl = np.where(ok, lens_tab[safe], np.int64(0))
    valid = found & (data_len >= fl)
    return found, valid


@dataclass
class SignalDef:
    name: str
    start_bit: int
    length: int
    scale: float
    offset: float
    unit: str
    is_signed: bool = False
    is_float: bool = False
    choices: dict | None = None     # {原始值: 文本}（DBC value table，修复项 3）
    byte_order: str = "big_endian"  # cantools: "little_endian"/"big_endian"（方案C 位提取）
    is_multiplexer: bool = False    # 是否为 mux 选择信号（方案C）
    multiplexer_ids: list[int] | None = None   # 子信号激活的 mux 值集合；None=常活跃（方案C）


@dataclass
class MessageDef:
    name: str
    sender_node: str
    signals: list[SignalDef] = field(default_factory=list)
    is_extended: bool = False
    frame_length: int = 8


@dataclass
class DbcDef:
    path: str
    db: object  # cantools Database，保留给 decoder 用
    messages: dict[int, MessageDef] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        """下拉显示名：文件名 + 所在文件夹，如 PFCAN1.dbc（A19G1）。

        同名 DBC 分散在不同项目文件夹（A19G1/AH8 均有 PFCAN1.dbc）时，
        靠文件夹名区分；通道匹配下拉与自动绑定建议共用此格式。
        """
        p = Path(self.path)
        return f"{p.name}（{p.parent.name}）"


def _detect_encoding(raw: bytes) -> str:
    """DBC 文件编码探测：项目 DBC 含 GBK 中文（CANoe 按 GBK 解析出正确文本，
    cantools 默认 latin-1 会乱码，如 'Normal£»' 应为 'Normal；'）。
    UTF-8 可整体解码 → utf-8；否则按 GBK（GBK 的中文字节序列基本非法 UTF-8）。"""
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "gbk"


def load(path: str) -> DbcDef:
    import cantools

    with open(path, "rb") as f:
        raw = f.read()
    # 兼容导出器固定缓冲写出的尾部 NUL 填充（实测 A19G1/CFCAN2.dbc 尾部
    # 734 个 \x00；cantools 把 NUL 当文本解析会报 Invalid syntax at line N）
    raw = raw.rstrip(b"\x00")
    try:
        db = cantools.database.load_string(raw.decode(_detect_encoding(raw)))
    except Exception as e:
        raise ValueError(f"failed to parse DBC '{path}': {e}") from e
    messages = {}
    seen_ids = set()
    for msg in db.messages:
        key = normalize_id(msg.frame_id, msg.is_extended_frame)
        if msg.frame_id in seen_ids:
            LOGGER.warning(
                "标准/扩展报文共用原始 id 0x%X，按 EFF 位归一化键后各自保留", msg.frame_id
            )
        seen_ids.add(msg.frame_id)
        messages[key] = MessageDef(
            name=msg.name,
            sender_node=msg.senders[0] if msg.senders else "Unknown",
            signals=[
                SignalDef(
                    name=s.name,
                    start_bit=s.start,
                    length=s.length,
                    scale=float(s.scale or 1.0),
                    offset=float(s.offset or 0.0),
                    unit=s.unit or "",
                    is_signed=bool(s.is_signed),
                    is_float=bool(s.is_float),
                    choices=({k: str(v) for k, v in s.choices.items()}
                             if s.choices else None),
                    byte_order=str(s.byte_order),
                    is_multiplexer=bool(s.is_multiplexer),
                    multiplexer_ids=(sorted(s.multiplexer_ids)
                                     if s.multiplexer_ids is not None else None),
                )
                for s in msg.signals
            ],
            is_extended=bool(msg.is_extended_frame),
            frame_length=int(msg.length),
        )
    return DbcDef(path=path, db=db, messages=messages)
