"""DBC 解析：一次一个文件，不合并。"""
from dataclasses import dataclass, field


@dataclass
class SignalDef:
    name: str
    start_bit: int
    length: int
    scale: float
    offset: float
    unit: str


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


def load(path: str) -> DbcDef:
    import cantools

    db = cantools.database.load_file(path)
    messages = {}
    for msg in db.messages:
        messages[msg.frame_id] = MessageDef(
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
                )
                for s in msg.signals
            ],
            is_extended=bool(msg.is_extended_frame),
            frame_length=int(msg.length),
        )
    return DbcDef(path=path, db=db, messages=messages)
