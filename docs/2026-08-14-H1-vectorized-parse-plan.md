# H1 实施计划：BLF 解析 + 帧路由向量化（含 H2/H4 配套）

日期：2026-08-14
状态：**设计完成（含设计审查）；待确认后动代码**
依据：`docs/2026-08-14-blf-mdf-conversion-performance-v2.md`（第二轮性能报告，根因 1/2/4）
前提：**输出与现实现逐位一致**（compare_two_mdf 逐位、full_compare vs CANoe 与基线同分、82+golden 测试全绿）

## 1. 范围与目标

| 项 | 内容 |
|---|---|
| 主攻 | H1：BLF 解析（对象行走）与 convert 帧路由向量化——读入 38.3s → 预估 4-6s |
| 配套 | H2：桶存储 list[bytes] → (N,L) uint8 数组（并行解码 IPC 减负，10.5s → ~5s）；H4：统计输入直接数组化（1.84s → ~0.5s） |
| 不含 | H3（probe 向量化，可复用本设计容器枚举）、H5（压缩级别，需求裁决）、H6（pool 重叠化，独立小步） |
| 总目标 | 转换 62.0s → 预估 22-28s（82MB 样例），输入阶段不变（8.55s，属 H3） |

**总体架构决策：不重写、只加层。**
- 新增 `core/blf_vector.py`：向量化解析器（快路径）+ 回退（现行走逻辑，原样复用）；
- `core/converter.py`：读入循环切换为「按容器批量路由」（统计/解码/原始三路）；
- `core/decoder.py`：桶统一为数组表示（feed 旧路径在 finish 时归一化，一次转换）；
- **`_iter_frames`/`iter_all_messages`/`list_channels`/`scan_channels`/`probe_channels` 原样保留**——它们是对拍参考（oracle）与既有 82 项测试的覆盖对象。

## 2. 语义复刻清单（正确性契约）

以下每一条都是硬约束，任何偏差都会被对拍链捕获。来源：`core/blf_reader.py` 现行 `_walk`（逐行核对）+ python-can 4.6.1 `can/io/blf.py` 参考语义（实测验证）。

### 2.1 对象行走与窗口（最容易错的一条）

- 候选 = 容器内所有字节位置 p 满足 `data[p:p+4] == b"LOBJ"`（小端 uint32 `0x4A424F4C`）。
- 行走：`pos` 从 0 起，每步在窗口 `[pos, pos+8)` 内找**第一个**候选。
- **实测验证（CPython bytes.index 语义）**：`data.index(b"LOBJ", pos, pos+8)` 要求匹配**完整落在 `[start, end)` 内** → 命中条件为 `p ≤ pos + 4`（不是 `p < pos+8`）。窗口与尾部判断的不对称：**命中界 +4，尾部界 +8**。
- 窗口内无候选（ValueError）：`pos + 8 > max_pos` → 尾部 `data[obj_start:]`；否则 `raise BLFParseError("Could not find next object")`。
- `obj_start` = 本步搜索前的 `pos`（= 上一对象末尾）；**所有失败路径的尾部都从 `obj_start` 起**（含截断对象本身）。

### 2.2 对象头

- 基础头（16B，`<4sHHLL`）：header_size(u16@+4)、header_version(u16@+6)、obj_size(u32@+8)、obj_type(u32@+12)。
- `next_pos = pos + obj_size`；`next_pos > max_pos` → 尾部（对象跨容器，留待下一容器衔接）。
- 版本 1：V1 头 16B，flags=u32@+16，rel=u64@+24；版本 2：V2 头 24B，flags=**u8**@+16，rel=u64@+24。
- 时间单位：`flags == 1`（精确相等，非位测试）→ rel×10000（10µs 单位）；否则 rel 按 1ns 计。
- 未知版本：整体跳过（pos = 对象末尾），**不做**版本头解包、不发射。
- 版本头解包 struct.error → **未捕获异常**（现行实现行为，非尾部）：obj_size 虚假偏小时可达。

### 2.3 四类消息对象字段（偏移均相对「对象头末尾」，即 c+hsz，hsz = 32(v1)/40(v2)）

| 类型 | 结构 | channel | flags/远程 | dlc | can_id | is_fd | data 区 |
|---|---|---|---|---|---|---|---|
| CAN_MESSAGE(1)/CAN_MESSAGE2(86) | `<HBBL8s` | u16@0 −1 | u8@2 & 0x80 | u8@3（原值，**不** dlc2len） | u32@4 | False | @8，长 min(dlc,8) |
| CAN_ERROR_EXT(73) | `<HHLBBBxLLH2x8s` | u16@0 −1 | —（远程 False） | u8@10（原值） | u32@16 | False | @24，长 min(dlc,8)，is_error=True |
| CAN_FD_MESSAGE(100) | `<HBBLLBBB5x64s` | u16@0 −1 | u8@2 & 0x80 | dlc2len(u8@3) | u32@4 | u8@13 & 0x1 | @20，长 min(valid_bytes(u8@14),64) |
| CAN_FD_MESSAGE_64(101) | `<BBBBLLLLLLLHBBL` | u8@0 −1 | fd_flags(u32@12) & 0x0010 | dlc2len(u8@1) | u32@4 | fd_flags & 0x1000 | @40 |

- `arbitration_id = can_id & 0x1FFFFFFF`；`is_extended = bool(can_id & 0x80000000)`。
- FD64 数据长度（python-can issue:1905）：`dfl = min(valid_bytes(u8@2), (ext_data_offset(u16@37) or obj_size) − header_size − 40)`；`msg_data = data[msg_off : msg_off + dfl]`（**切片按容器尾截断**）再 `.ljust(valid_bytes, b"\x00")` → **最终长度 = valid_bytes（可能 >64，≤255）**。
- dlc2len 表：`[0,1,2,3,4,5,6,7,8,12,16,20,24,32,48,64]`（dlc>15 → 64，python-can `can/util.py` 实测）。
- 消息结构体解包 struct.error → **尾部**（容器级兜底，从 obj_start 起）。

### 2.4 时间戳（整数 ns 构造，与 CANoe 同构）

`ts_seconds = float(ms_part + rel_ns) × 1e-9`，`ms_part = start_ns − (start_ns//1e9)×1e9`（文件级常量，SYSTEMTIME 毫秒整数推得）。
向量化复刻：`(ms_part + rel_ns).astype(np.float64) * 1e-9`。**逐位一致的前提：`ms_part + rel_ns ≤ 2^53`**（int→float64 精确），超出即回退标量路径（Python 大整数→float 与 numpy 均正确舍入但 int64 会溢出，不冒险）。

### 2.5 容器层

- 文件头 LOGG 校验、`seek(header[1])`、逐容器：base 16B 读入 → signature 校验 → obj_data 读入 → `obj_size % 4` 填充字节 → 非 LOG_CONTAINER 跳过 → `LOG_CONTAINER_STRUCT`（`<H6xL4x`）method → NO_COMPRESSION / ZLIB_DEFLATE（zlib.decompress）/ 未知压缩跳过。
- 尾部衔接：上一容器尾部 + 本容器数据拼接后行走；**尾部可能为任意字节**（含跨容器对象的前半截、非对象垃圾）——拼接后从头解析，垃圾若含 "LOBJ" 会按假阳性对象处理（与现行一致）。
- 帧全局顺序 = 容器序 × 容器内对象序（BLF 时间序）。
- progress_cb：逐容器按字节位置（与现行一致）；cancel_cb：见 §7 语义差异。

### 2.6 路由与确定性契约

- 桶插入序 = **报文首现序**（feed 中 `setdefault` 首次出现序，`decoder.py:402` 契约）→ 系列顺序；桶内帧保持文件序（时间序）。
- 未知帧分类：`total_frames` = 通道全部帧；`md is None` 或 `len(data) < md.frame_length` → unknown（**不入桶**）；`unknown_ids` 收集原始 arbitration_id（不含 EFF 位）。
- DBC 键契约：归一化 `arb | (0x80000000 if is_extended)`（`dbc_loader.py:77`）。
- 统计收集：全部通道全部帧（0-15 通道聚合，与绑定无关）；通道 ≥16 收集但聚合不用（输出不可见，向量化版跳过收集，见 §5）。
- 并行解码（方案 G）：LPT 分派、失败回退、feed 期统计在父进程——结构不变，只换桶内容表示。

## 3. 架构与数据流

```
                    core/blf_vector.py（新）
  BLF ── _iter_containers（抽取自 _iter_frames，逐容器产出 data）──┐
        │                                    ┌── 快路径 _parse_fast ──→ ContainerFrames
        └────────────────────────────────────┤   （条件不满足时）
                                             └── 回退 _walk_container（现行标量行走）→ Frame[] → 数组

  converter（新读入循环，按容器批量路由）
  ContainerFrames ──┬─ 统计路由：ch<16 全部帧 → stats_chunks[ch]（ts/ext/rem/err 块）
                    ├─ 解码路由：绑定通道 × searchsorted 判已知 × 帧长预检
                    │      → dec_chunks[ch]（arb/ts/dlen/doff/data8 块）+ feed 统计（total/unknown）
                    └─ 原始路由（raw_export 时）→ raw_chunks[ch]

  桶组装（converter，读入后）：按首现序建 array 桶 {ts, data(N,L), lens, arb, raw_id, md}
  解码：finish_all（并行）/ finish（串行）—— 入口不变，桶内容为数组（IPC 减负 = H2）
  统计：np.concatenate 各通道块 → aggregate_channel 直接吃数组（= H4）
```

新增 `ContainerFrames`（每容器一个）：

```python
@dataclass
class ContainerFrames:
    channel:  np.ndarray   # int64 (N,) 0-based
    ts:       np.ndarray   # float64 (N,)
    arb:      np.ndarray   # uint32 (N,)（不含 EFF 位）
    is_ext:   np.ndarray   # bool (N,)
    is_remote: np.ndarray  # bool (N,)
    is_error: np.ndarray   # bool (N,)
    is_fd:    np.ndarray   # bool (N,)
    dlc:      np.ndarray   # uint8 (N,)（经典=原值；FD=dlc2len，与 Frame.dlc 同语义）
    data8:    np.ndarray   # uint8 (M,) 打包载荷（FD64 已含补零）
    data_off: np.ndarray   # int64 (N,) 帧载荷在 data8 中的起点
    data_len: np.ndarray   # int64 (N,)（= len(Frame.data)）
```

## 4. 快路径算法（逐容器，全向量化）

### 4.1 候选扫描（任意对齐）

```python
b8 = np.frombuffer(data, dtype=np.uint8)          # O(1) 视图，不拷贝
cand = []
for o in range(4):                                # 4 个对齐偏移覆盖全部字节位置
    n = (len(data) - o) // 4
    if n > 0:
        v = np.frombuffer(data[o:o + 4*n], dtype=np.uint32)   # frombuffer 需长度整除 4，先截齐
        cand.append(np.nonzero(v == 0x4A424F4C)[0].astype(np.int64) * 4 + o)
c = np.concatenate(cand) if cand else empty; np.sort(c)
```

字段提取用**移位组装**（显式小端，机器无关；`b8` 逐字节 gather，兼容任意对齐与尾部长度的缓冲）：

```python
def u32_at(p):  # p: int64 数组（已裁剪到安全域）
    b = b8[p].astype(np.uint32)                  # 必须先转宽类型再移位（numpy 移位不自动提升）
    return b | (b8[p+1].astype(np.uint32) << 8) | (b8[p+2].astype(np.uint32) << 16) | (b8[p+3].astype(np.uint32) << 24)
# u64_at 同法（uint64 逐字节移位）；u16 取 u32 低 16 位；u8 直接 b8[p]
```

条件评估期的所有位置一律**裁剪**（`np.clip(p, 0, max_pos − 字段长)`），越界处读出垃圾值由有效性掩码屏蔽——裁剪保证永不 IndexError。

### 4.2 快路径条件（任一不满足 → 本容器整体回退 §5）

记 `e = c + obj_size`，`N = len(c)`：

1. `N == 0`：`max_pos < 8` → 尾部 `data[0:]`；否则 raise BLFParseError（直接终结，无需回退）。
2. `c[0] ≤ 4`（首对象命中窗口 `[0,8)` 的 +4 界；`max_pos < 8` 时自动成立）。
3. 相邻候选窗口（防假阳性/空洞）：`∀ i<N−1: e[i] ≤ c[i+1] ≤ e[i]+4`。
4. 非末候选全部有效：`valid[i] = base_fit ∧ (unk ∨ ver_fit) ∧ (unk ∨ msg_fit) ∧ obj_fit`，其中
   - `base_fit = c+16 ≤ max_pos`；`obj_fit = e ≤ max_pos`
   - `ver_fit = unk ∨ (v1 ∧ c+32 ≤ max_pos) ∨ (v2 ∧ c+40 ≤ max_pos)`（版本头解包可及）
   - `msg_fit = unk ∨` 按类型：cls `c+hsz+16`；err `c+hsz+32`；fd `c+hsz+84`；fd64 `c+hsz+40`；其他类型恒真
5. 末候选分型：`~base_fit[last] ∨ ~obj_fit[last] ∨ (ver_fit[last] ∧ msg_fit[last])`——前两者是**常态**（跨容器对象/容器尾截断），快路径直接处理；末候选版本头/消息体截断（虚假 obj_size 的罕见损坏）→ 回退。
6. 时间守卫：`∀ 候选: rel ≤ rel_max`，`rel_max = (2^53 − ms_part) // (10000 if flags==1 else 1)`（按单位分判，杜绝 `rel×10000` 的 uint64 回绕假守卫）。

### 4.3 发射与载荷打包

- 处理数 `K = N if valid[last] else N−1`；发射掩码 `emit = proc[:K] ∧ (v1∨v2) ∧ (t_cls∨t_err∨t_fd∨t_fd64)`。
- 各字段按 §2.3 偏移逐类型提取 → 散射进全长数组 → 按 emit 序取出（候选序 = 对象序 = 时间序）。
- 时间戳：`ts = (ms_part + rel_ns[emit]).astype(np.float64) * 1e-9`（守卫 6 保证精确）。
- 载荷打包（row/col 技巧，O(M)，M = 容器总载荷字节）：
  ```python
  lens = data_len[emit]; starts = np.concatenate(([0], np.cumsum(lens)))
  row = np.repeat(np.arange(len(emit)), lens)
  col = np.arange(starts[-1]) - np.repeat(starts[:-1], lens)
  data8 = np.zeros(starts[-1], np.uint8)
  fill = col < gather_len[row]        # FD64: gather_len=可用字节(已按容器尾截断)，其余=len
  data8[fill] = b8[src_off[row][fill] + col[fill]]
  ```
  - 各类源区均在对象内（msg_fit 保证 + obj_fit 保证），gather 不越界；
  - FD64 可用字节 = `min(dfl, max_pos − off)`（复刻 Python 切片的容器尾截断），其余补零（ljust 语义）；`dfl` 可为负（→ 0，全零帧）。
- 数据字节与参考逐位相同：经典/错误 = `data[:min(dlc,8)]`；FD = `data[:min(vb,64)]`；FD64 = 截断+补零。

### 4.4 尾部（与 §2.1 逐条对应）

- `valid[last]`：处理后终态搜索 `[e[last], e[last]+8)` 无候选 → `e[last]+8 > max_pos` → 尾部 `data[e[last]:]`；否则 raise BLFParseError（对象后 ≥8 字节非 LOBJ 垃圾）。
- `~base_fit[last] ∨ ~obj_fit[last]`：尾部 = `data[e[last−1]:]`（N==1 时 `data[0:]`），末候选不发射。
- 尾部字节原样交回容器循环，衔接下一容器（§2.5）。

## 5. 回退路径（容器级）

`_walk_container(data, ms_part, cancel_cb) -> (list[Frame], tail)`：从现行 `_iter_frames._walk` **原样抽取**的模块级函数（连同每 1024 对象的取消检查）。快路径条件不满足的容器走它，再经一次性列表推导转数组（逐帧 Python 仅发生在罕见容器）。回退 = 现行行为逐字节复刻，是快路径正确性的最终兜底。

`iter_container_frames(path, progress_cb=None, cancel_cb=None, _force_fallback=False)`：容器枚举（§2.5）+ 逐容器快/回退选择。`_force_fallback` 仅供测试对拍。

## 6. converter 路由重构（读入循环 + 桶组装）

### 6.1 预计算

```python
dbc_info = {ch: (np.array(sorted(dbc.messages), dtype=np.uint32),   # 键（归一化）
                 np.array([md.frame_length for md in 按键序], dtype=np.int64),
                 按键序的 MessageDef 列表)}
```

### 6.2 读入循环（每容器一个 Python 小步，无逐帧 Python）

```python
for cf in blf_vector.iter_container_frames(blf_path, progress_cb=read_cb, cancel_cb=cancel_cb):
    # 统计路由（全部帧；聚合只用 0-15，≥16 通道跳过收集——输出不可见，见 §2.6）
    if stats_export:
        for ch in np.unique(cf.channel):
            if ch >= 16: continue
            m = cf.channel == ch
            stats_chunks[int(ch)].append((cf.ts[m], cf.is_ext[m], cf.is_remote[m], cf.is_error[m]))
    # 解码路由
    for ch in decoder_channels:
        m = cf.channel == ch
        if not m.any(): continue
        st = feed_stats[ch]; st.total_frames += int(m.sum())
        arb_n = (cf.arb[m] | (cf.is_ext[m].astype(np.uint32) << 31)).astype(np.uint32)
        keys, flen, _ = dbc_info[ch]
        idx = np.searchsorted(keys, arb_n); idxc = np.minimum(idx, len(keys) - 1)
        found = (idx < len(keys)) & (keys[idxc] == arb_n)
        ok = found & (cf.data_len[m] >= flen[idxc])
        bad = ~ok
        if bad.any():
            st.unknown_frames += int(bad.sum())
            st.unknown_ids.update(np.unique(cf.arb[m][bad]).tolist())
        if ok.any():
            dec_chunks[ch].append((arb_n[ok], cf.ts[m][ok], cf.data_len[m][ok],
                                   cf.data_off[m][ok], cf.data8))
    # 原始路由（raw_export 时；_collect_raw 语义向量化，§6.4）
    ...
# 取消检查点：每容器（见 §7）；读毕 _check_cancel(cancel_cb) 保持
```

### 6.3 桶组装（读入后，逐通道）

```python
# 首现序（系列顺序契约）：按 chunk 文件序扫描，unique(return_index) 按首现排序
order = []
for arb_c, *_ in dec_chunks[ch]:
    uniq, fi = np.unique(arb_c, return_index=True)
    for a in uniq[np.argsort(fi)]:                      # 值序≠首现序，必须按 fi 排序
        if int(a) not in order: order.append(int(a))
# 逐桶：ts/lens 跨 chunk 连接；数据用 row/col 技巧逐 chunk 汇入 (N, L)
# L = lens.max()（补零语义与 _bucket_data_array 一致）
# raw_id = a & 0x1FFFFFFF；md 由 keys searchsorted 定位
decoders[ch].buckets = {a: {"ts": ts_b, "data": data_b, "lens": lens_b,
                            "arb": a, "raw_id": ..., "md": md}, ...}   # 插入序 = 首现序
decoders[ch].stats = feed_stats[ch]                    # feed 期统计（与现行 feed 同值）
```

### 6.4 原始帧收集（raw_export=True）

`_collect_raw` 逻辑向量化：known_ids（归一化集合）→ isin 过滤 → unknown 计数/原始 id 集合 → RawGroup 数组直构；数据阵 (n, max_dlc) 用 row/col 技巧填充，**填充长度按 numpy 切片 clamp 语义与现行 `data_array[i, :len(d)]` 逐位一致**（len(d) > max_dlc 时截断）。`_collect_raw` 原函数删除（仅 convert 使用）。

## 7. 行为差异声明（有意为之，逐条论证）

| 差异 | 内容 | 论证 |
|---|---|---|
| 取消粒度 | 读入期 cancel_cb 从「每 1024 对象」改为「每容器」 | 82MB 样例 58 容器 ≈ 0.5s 延迟；GUI 契约「取消按钮置位 → 读取抛 ScanCancelled」不变；既有取消测试（cancel_cb 恒 True/False）语义不变 |
| 统计跳过 ≥16 通道 | stats_chunks 不收集 channel ≥ 16 的帧 | 聚合只遍历 `STAT_CHANNELS = 0..15`，收集内容不进入输出——输出逐位不变，仅省内存 |
| 进度回调 | 不变（逐容器字节位置） | 与 list_channels 同语义 |
| 其余 | 无 | 帧流、桶内容、统计、解码、写 MDF 全部逐位 |

## 8. 分步实施（每步独立验收、可回退）

| 步 | 内容 | 验收 | 风险 |
|---|---|---|---|
| **0** | 纯重构：`_iter_containers`（容器枚举）+ `_walk_container`（标量行走）抽取为模块级；`_iter_frames`/`probe_channels` 行为不变 | 既有 82 项测试全绿 | 无（机械抽取） |
| **1** | 新增 `core/blf_vector.py`：候选扫描 + 快路径 + 回退接线 + `iter_container_frames`；**convert 未动** | 新增属性测试（构造容器对拍）+ 样例全量对拍（快路径 vs 标量逐帧逐位）绿；既有 82 项绿 | 低（纯新增，对拍锁定） |
| **2** | `decoder.py` 数组桶：`_finish_bucket_vectorized`/`_decode_bucket_reference` 读数组表示；`ChannelDecoder.finish()` 将 feed 型 list 桶一次性归一化（测试/旧路径用）；`mp_finish.bucket_bytes` 改数组 nbytes | test_decoder_vectorized / test_parallel_decode / 82 项绿（feed 路径输出逐位不变） | 低（输出不可见） |
| **3** | `converter.py` 切换向量化路由 + 桶组装 + raw 收集向量化（主验收步） | compare_two_mdf vs 旧基线 `%TEMP%\AHT_bench.mdf`（12.9MB，§7 已有）退出码 0；full_compare vs CANoe 参考与基线同分；test_golden 3 项绿；基准计时 62s → 22-28s | 中（对拍兜底） |
| **4** | 收尾：删除开发期 A/B 钩子（若有）、性能报告 v3 更新、README、提交 | 全量测试 + 工具链复跑 | 无 |

每步一个提交；任一步验收失败即停（systematic-debugging 纪律：定位→最小修正→重验，不叠加猜测）。

## 9. 验证与对拍验收

### 9.1 新增测试（tests/test_blf_vector.py）

1. **属性对拍（快路径 == 标量）**：手工构造容器字节（不经 python-can writer，覆盖其 writer 不产生的布局）：
   - 四类消息混排、未知版本对象、未知对象类型（GLOBAL_MARKER 等）、消息载荷含 "LOBJ" 假阳性（对象体内 / 窗口内两种位置）、相邻对象间 0-7 字节空洞/垃圾、≥8 字节尾垃圾（→ BLFParseError 对拍）、跨容器截断（任意字节处）、版本头截断（→ 未捕获 struct.error 对拍）、flags==1 的 10µs 单位、dlc 越界（9-15/255）、FD64 valid_bytes 超可用（补零）、ext_data_offset≠0、空容器、尾部长度 1..3 字节衔接（对齐偏移）。
   - 断言：帧数、逐帧字段（channel/ts 逐位 float64 相等/arb/ext/remote/err/fd/dlc/data 字节）与标量输出**全等**；尾部字节全等；异常路径同型。
2. **样例全量对拍**：`iter_container_frames` 全流 == `list(_iter_frames)` 逐帧逐位（ts 用 `==` 精确比较，不用容差）；断言样例全部容器命中快路径（记录命中率）；`_force_fallback=True` 全流同结果（回退接线验证）。
3. **桶组装对拍**：向量化路由产出的桶 vs feed 产出的桶（list 归一化后）逐桶逐位一致（ts/lens/data 数组相等），覆盖首现序（含跨容器首现的报文）。
4. **raw 收集对拍**：raw_export=True 下向量化 _collect_raw == 旧实现（Frame 逐帧构造）逐位。

### 9.2 既有测试与工具链（全部保持绿/同分）

| 手段 | 断言 |
|---|---|
| `pytest`（82 项 + golden 3 项） | 全绿（旧路径未被删除，是天然回归网） |
| `tools/compare_two_mdf.py <新输出> <AHT_bench.mdf>` | 退出码 0（新旧自产逐位一致） |
| `tools/full_compare.py <新输出> <CANoe 参考 AHT.mdf>` | 与基线报告同分（160 组 × 601 点） |
| `tools/bench_*` / `%TEMP%\blf_bench.py` 同款脚本 | 读入 ≤6s、总耗时 22-28s、内存峰值 ≤ 基线 |

### 9.3 复现环境

`& C:\ProgramData\anaconda3\python.exe <脚本>`（PATH 上的 python 是商店空壳）；样例 `inputs/blf/AHT_ACFCANPUB_*.blf`；转换参数与 GUI 完全一致（parallel=True、raw_export=False、stats_export=True）。

## 10. 性能预算（82MB 样例，逐项估）

| 阶段 | 现状 | 目标 | 依据 |
|---|---|---|---|
| zlib 解压 | 1.46s | 1.46s | C 扩展，不可省 |
| 候选扫描 + 字段提取 + 打包 | 38.3s 中的 ~21.5s | ~0.6s | 每容器 ~10 万候选 × ~15 次向量 gather（裁剪安全域），58 容器 |
| 路由（统计/解码分通道 + searchsorted） | 38.3s 中的 ~15.3s | ~1-1.5s | 每容器 16+10 次 O(N) 掩码 gather + 10 次 searchsorted |
| 桶组装 + 统计连接 | —（含在上项） | ~0.5s | 45MB 总载荷 row/col 汇入，纯数组操作 |
| **读入合计** | **38.28s** | **~4s** | |
| 解码墙钟（并行） | 10.53s | ~5-6s | IPC 从 ~215MB 个对象 pickle 降为数组连续拷贝（ts 8B + data ~8-12B/帧 ≈ 90-100MB） |
| 统计聚合 | 1.84s | ~0.5s | 输入已是数组，省 list→array 转换 |
| 写 MDF | 8.18s | 8.18s | 不变 |
| 杂项（pool 预热等） | ~3.2s | ~3.2s | 不变（H6 另做） |
| **总计** | **62.0s** | **~22-25s** | |

内存：读入期持有 = 每容器 transient（~10MB 级）+ 分通道 chunks（浮点 8B + 打包数据 ~45MB 总量）+ 桶数组（≤ 基线 list 桶内存，且省 Frame 对象）；峰值预估 ≤ 基线，实测记录。

## 11. 风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| 1 | 快路径条件漏掉某条语义 → 输出漂移 | 回退=现行实现；属性对拍覆盖 §9.1.1 全清单；82MB 样例 + golden + full_compare 三重兜底 |
| 2 | 窗口 +4/+8 不对称搞错（已实测确认） | §2.1 写明；属性测试专测空洞/垃圾布局 |
| 3 | numpy 移位/类型提升陷阱（uint8<<24 回绕、int64 溢出） | 提取助手中显式先转宽类型；时间守卫按单位分判；代码注释点名 |
| 4 | FD64 可用长度/补零边界 | 容器尾截断 clamp 逐位复刻 Python 切片；属性测试专测 |
| 5 | 首现序被 np.unique 值序破坏 | 按 `return_index` 排序（§6.3）；桶序对拍测试 + compare_two_mdf（组序） |
| 6 | 大 L 稀疏（单 FD64 255B 帧拉宽整桶 (N,255)） | 与现行 `_bucket_data_array` 行为相同，非新风险；如实测恶化再议（不阻塞 H1） |
| 7 | 取消延迟恶化（单容器巨文件） | 82MB 样例容器 ~0.5s；文档声明；极端单容器文件可后续加容器内分段检查（不在 H1） |

## 12. 设计审查记录（设计完成后自审，已修正入稿）

| # | 发现 | 处置 |
|---|---|---|
| 1 | 快路径窗口上界最初写为 `c < e+8`——**错**。实测 CPython `bytes.index(sub, start, end)` 要求匹配完整落在 `[start,end)` 内，命中界为 `c ≤ e+4`；尾部判断仍用 `pos+8 > max_pos`（+4/+8 不对称） | §2.1 修正为 +4 界，列为契约首条 |
| 2 | `rel×10000` 在 uint64 下可回绕，回绕后守卫假通过 → 错误时间戳 | 守卫改为按 flags 单位分判 `rel ≤ rel_max`（§4.2.6） |
| 3 | FD64 可用长度最初取 `dfl` 原值 gather——虚假 `header_size` 字段可使 `off+dfl` 越出对象（IndexError）或越出容器尾 | 增加 `min(dfl, max_pos − off)` 截断，逐位复刻 Python 切片 clamp（§4.3） |
| 4 | 末候选「无效」最初一律按尾部处理——遗漏版本头截断的未捕获 struct.error 分型 | §4.2.5 分型：base/obj 失败（常态）快路径处理；ver/msg 截断回退（由标量路径复刻 raise/尾部） |
| 5 | 版本头解包 struct.error 在现行 `_walk` 中**未捕获**（区别于消息体截断的容器级兜底）——快路径条件必须区分 | 纳入 §2.2 与 §4.2.4（ver_fit 失败 → 回退） |
| 6 | `header_size` 字段（u16@+4）≠ 实际版本头大小 hsz（32/40）：FD64 dfl 公式用前者 | §2.3 明确公式用字段值，msg_fit 用 hsz |
| 7 | np.unique 默认按值排序，直接扫描会破坏首现序 | §6.3 按 return_index 排序 |
| 8 | 时间戳守卫最初以 `rel_ns < 2^53` 单界表达，未覆盖 10µs 单位与 ms_part 上界 | 改为 `(2^53 − ms_part) // 单位` 分判 |
| 9 | 取消粒度变化未在方案中显式声明 | §7 差异声明表 |
| 10 | 桶数据 (N,L) 大 L 稀疏内存问题 | §11.6：与现行同构，非新风险 |

## 13. 待确认的决策点

1. 开发期是否保留 `BLF_LEGACY_READ` 环境开关做 A/B（Step 3 验收后删除）——建议保留至验收完成；
2. 取消粒度松弛（每容器）是否接受——建议接受（延迟 ~0.5s，GUI 无感）；
3. 本文档确认后按 Step 0 → 4 顺序实施，每步独立提交与验收。
