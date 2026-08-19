# 07 — H3 输入探测提速

Type: grilling
Status: closed (2026-08-19)
Assignee: claude (claimed 2026-08-19)
Blocked by:

## Question

输入阶段（拖入 BLF → 通道表就绪）实测 8.55s，其中行走 ~5.9s——`probe_channels._walk`（[blf_reader.py:105](../../../core/blf_reader.py#L105)）仍是逐对象标量 Python 循环，与 H1 前的读入同构。master plan §5.2 两方案：

1. **向量化行走**：与 H1 同款——复用 `_candidates` 单类扫描 + 掩码提取 channel 字段，不构造帧；改动集中在 probe_channels 一个函数
2. **(路径, 大小, mtime) 缓存**：GUI 重拖同一文件 / 会话间复用

本票先裁决方案（二选一或叠加），再实施 + 验收。验收门：probe/list 对拍测试全绿（`tests/test_blf_reader.py`）+ 输入阶段 8.55 → ~1.5s + 全量 pytest + 对拍链。约束：通道集合语义零变化；回退 oracle 防线保持（快路径失败回退标量不 raise）。

## Resolution (2026-08-19)

用户裁定 Q1-Q2「按推荐」+ Q3「(a) 收 H3」：

- **方案**：H3 方案 1（向量化行走）单独实施；方案 2（(路径,大小,mtime) 缓存）不做（对首拖零改善，重复拖收益 ~1.5s 不值新机制，与 H2b/H2c 否决先例同构）
- **实施**：`probe_channels` 新增 `_probe_fast` 向量化快路径——复用 `blf_vector._candidates` 单类扫描 + `_u32_at_v`/`_u16_at_v`/`_u32_at`/`_u16_at`/`_u8_at` 掩码提取（函数级惰性 import，与 blf_vector:461 对向引用同构）；窗口状态机 + 重叠定理双探针 + 终止分型按 probe 裁剪标量语义（版本头/消息体截断 → channel 读 struct.error → 尾部；与 `_parse_fast` 的 emit 级回退不同）；任一条件不满足返回 None 回退标量（回退 oracle 不 raise）；`cancel_cb` 按每 1024 轮检查（同标量检查点）
- **验收**：
  - probe/list 对拍测试 4 passed（tests/test_blf_reader.py）
  - 全量 pytest **291 passed**（基线 291，零回归）
  - 对拍链：compare_two_mdf vs 20260817 基线 **exit 0（230 组逐位一致）**；full_compare vs CANoe **70/70 组、3434 处已知真实差异 = 基线同分**
  - 行走归因（cProfile）：**~6.3s → ~0.24s（96% 降幅）**
  - 输入阶段（AHT 82MB 本机）：冷进程 **12.32s → 6.22s**（其中 can→asammdf import ~4s，GUI 启动经 mdf_writer 已缓存，拖入只残余首拖 ~0.5s）；**GUI 热口径 ~2.2s** = zlib 1.78s（硬下限）+ 行走 0.24s + 读入 ~0.2s
  - 通道集合语义零变化：14 路一致
- **未达成项（用户裁决收口 Q3-a）**：验收门「输入阶段 ~1.5s」不可达——zlib 1.78s 为输入阶段硬下限（v2 目标按当时 zlib 1.2s 贴线估，本机漂移后 1.78s）；H7c 重叠对 probe 无效（行走 0.24s 无可藏期）；zlib 残余与 can→asammdf import 4s 记入 map Not yet specified（挂性能线名下）
- 验收脚本 `.scratch/closeout/accept-07/`（accept_probe.py 计时 + accept_chain.py 对拍链）留档
