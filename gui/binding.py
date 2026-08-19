"""绑定决策：通道 ↔ DBC 的选择与状态推导（零 Qt import，测试免 offscreen）。

从 MainWindow._rebuild_channel_table 抽出（架构评审候选 B / M4）：行集合、
选择优先级、初始状态推导均为纯函数，全部决策分支可不经 QApplication
直接测试。绑定键为 DBC 文件路径（结构化身份）；显示名只作展示
（见 CONTEXT.md「绑定」词条）。
"""
from dataclasses import dataclass

STATE_BOUND = "已绑定"
STATE_NOT_EXPORTED = "不导出"
STATE_NO_DATA = "无数据"


@dataclass(frozen=True)
class BindingRow:
    channel: int
    binding: str | None  # DBC 文件路径；None = 不绑定
    state: str


def derive_state(in_blf: bool, binding: str | None) -> str:
    """绑定状态是 (通道是否在 BLF, 选择) 的纯函数。"""
    if not in_blf:
        return STATE_NO_DATA
    return STATE_BOUND if binding else STATE_NOT_EXPORTED


def decide_bindings(blf_channels: list[int],
                    auto_bindings: dict[int, str] | None,
                    prev: dict[int, str | None] | None,
                    dbc_paths: list[str]) -> list[BindingRow]:
    """由 BLF 通道、自动绑定建议、上次选择计算每行绑定与状态。

    行集合 = BLF 通道 ∪ auto 映射通道（映射是完整规格，日志中无数据的
    映射通道也显示，不让匹配对静默缺失）；prev 不产生行。

    选择优先级：prev（用户上次选择）→ auto 建议 → 不绑定，三态分派：
    prev 值有效（路径在 dbc_paths）= 用户微调，优先；prev[ch]=None =
    显式「不绑定」，同样压制 auto；prev 值无效（其 DBC 被移除）=
    回退 auto 重绑（现状 self.auto_bind 第 3 级兜底语义）。auto 参数
    仅在选项目时传入且彼时 prev 为空，两者从不同时出现；合一后
    auto_bindings 恒等于 self.auto_bind，非选项目场景必须让 prev 压过
    它，否则添加/移除 DBC 会覆盖用户微调。auto 路径不在 dbc_paths
    （项目缺 DBC）→ 保持不绑定。

    行按通道号升序。
    """
    valid = set(dbc_paths)
    blf_set = set(blf_channels)
    rows = []
    for ch in sorted(blf_set | set(auto_bindings or {})):
        prev_val = prev.get(ch) if prev is not None else None
        if prev is not None and ch in prev and prev_val is None:
            binding = None                      # 显式不绑定，压制 auto
        elif prev is not None and ch in prev and prev_val in valid:
            binding = prev_val                  # 用户微调优先
        elif auto_bindings is not None and ch in auto_bindings \
                and auto_bindings[ch] in valid:
            binding = auto_bindings[ch]        # auto 建议（含 prev 失效回退）
        else:
            binding = None
        rows.append(BindingRow(ch, binding,
                               derive_state(ch in blf_set, binding)))
    return rows
