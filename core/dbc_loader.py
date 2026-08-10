"""DBC 解析：一次一个文件，不合并。"""
import logging
from dataclasses import dataclass, field
from pathlib import Path

LOGGER = logging.getLogger(__name__)


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
        key = msg.frame_id | (0x80000000 if msg.is_extended_frame else 0)
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
