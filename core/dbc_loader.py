"""DBC 解析：一次一个文件，不合并。"""
import logging
from dataclasses import dataclass, field

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


def _detect_encoding(path: str) -> str:
    """DBC 文件编码探测：项目 DBC 含 GBK 中文（CANoe 按 GBK 解析出正确文本，
    cantools 默认 latin-1 会乱码，如 'Normal£»' 应为 'Normal；'）。
    UTF-8 可整体解码 → utf-8；否则按 GBK（GBK 的中文字节序列基本非法 UTF-8）。"""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "gbk"


def load(path: str) -> DbcDef:
    import cantools

    try:
        db = cantools.database.load_file(path, encoding=_detect_encoding(path))
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
                )
                for s in msg.signals
            ],
            is_extended=bool(msg.is_extended_frame),
            frame_length=int(msg.length),
        )
    return DbcDef(path=path, db=db, messages=messages)
