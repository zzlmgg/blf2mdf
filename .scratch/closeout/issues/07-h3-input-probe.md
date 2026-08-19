# 07 — H3 输入探测提速

Type: grilling
Status: open
Blocked by:

## Question

输入阶段（拖入 BLF → 通道表就绪）实测 8.55s，其中行走 ~5.9s——`probe_channels._walk`（[blf_reader.py:105](../../../core/blf_reader.py#L105)）仍是逐对象标量 Python 循环，与 H1 前的读入同构。master plan §5.2 两方案：

1. **向量化行走**：与 H1 同款——复用 `_candidates` 单类扫描 + 掩码提取 channel 字段，不构造帧；改动集中在 probe_channels 一个函数
2. **(路径, 大小, mtime) 缓存**：GUI 重拖同一文件 / 会话间复用

本票先裁决方案（二选一或叠加），再实施 + 验收。验收门：probe/list 对拍测试全绿（`tests/test_blf_reader.py`）+ 输入阶段 8.55 → ~1.5s + 全量 pytest + 对拍链。约束：通道集合语义零变化；回退 oracle 防线保持（快路径失败回退标量不 raise）。
