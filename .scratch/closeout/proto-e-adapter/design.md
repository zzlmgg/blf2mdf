# Proto E — ContainerFrames 帧序列转换 adapter（设计原型）

> **PROTOTYPE — 决策用草图，非落地代码。** 裁决「做」后按 §3 落地；
> 裁决「不做」本文件留存为决策依据。对应 ticket
> [01 — E: ContainerFrames → 帧序列转换 adapter](../issues/01-e-containerframes-adapter.md)。

## 1. 本原型回答的问题

**为 ContainerFrames 的 packed/scattered 双契约成型「帧序列转换 adapter」是否值得？其形状、接口、测试面收敛应如何？**

判定依据（review §4 候选 E，唯一未落地 Strong）：H7b 一次表示变更迫使 test_blf_vector
7 处测试更新（master plan §5.1 逐处实证）；adapter 应同时简化 converter（`_bucket_block`
消费面）与测试两侧的帧语义重建。约束：scattered 补零语义是消费者契约，不得改变逐位一致
结果；不新增无谓抽象（标准 ⑥）。

## 2. 现状盘点（代码级核对）

**双契约**（[blf_vector.py:34-54](file:///e:/projects/blf_dbc/core/blf_vector.py#L34-L54)）：

| | packed（回退路径 `_frames_to_container`） | scattered（快路径 `_parse_fast`） |
|---|---|---|
| `data8` | 定跨距打包块 | 容器字节零拷贝视图 |
| `data_off` | 块内起点 | 容器内绝对偏移 |
| `glen` | `None` | 实际字节数（FD64 可 < data_len） |
| 补零语义 | `col >= lens` 掩码 | `col >= glen` 掩码 |

**消费面**（master plan §5.1 grep + 本次复核）：`data8/data_off/data_len/glen/scattered`
的消费方仅两处——

1. [converter.py:35-59](../../../core/converter.py#L35-L59) `_bucket_block`（解码桶与 raw
   共用），双分支掩码 :52-58
2. [tests/test_blf_vector.py](../../../tests/test_blf_vector.py)：`_payload` helper
   :114-120（唯一测试侧分支点），`_assert_eq` :137 与 `_assert_streams_equal` :546 经它重建，
   5 处直接断言（:345/:367/:377/:383/:396）全部走 `_payload`；:366/:395 断言 `glen`
   表示字段本身

**关键观察**：双契约的泄漏点**只有载荷访问**。`channel/ts/arb/is_ext/.../dlc` 本已是统一
数组、零分支。所以「帧序列转换」的不统一处 = 载荷一项，adapter 的真实职责 = **载荷语义归一**。

## 3. 设计（形状 + 接口）

### 3.0 形状裁决：不做包装类，不做新模块

- **不做 `FrameSeq` 包装类 / Frame 对象序列**：converter 用数组向量化消费（路由/统计/
  桶 gather 全部数组操作），帧对象序列只服务测试——一个消费方 = 假设接缝（review 词汇），
  且重引入转换层，浅模块，违反标准 ⑥。
- **不做 eager 重打包**（scattered → packed 一次性归一）：H7b 删除的正是容器级 362MB
  打包拷贝；adapter 必须保持零拷贝视图语义。
- **adapter = ContainerFrames 的载荷访问面**（方法）+ 一条表示不变量。物理布局仍双
  （性能差异），**消费语义归一为一条规则**。

### 3.1 表示归一（生产者侧，2 行）

不变量：**`glen` 恒存在，packed 路径 `glen == data_len`**（真实字节数 = 整行）。
消费规则唯一：*第 i 帧载荷 = `data8[data_off[i] : data_off[i]+glen[i]]` 真实字节，
行尾补零至 `data_len[i]`*。

```python
# blf_vector.py _frames_to_container：packed 路径补 glen（共享数组，零拷贝）
return ContainerFrames(..., data8=data8, data_off=starts[:-1].copy(),
                       data_len=lens, glen=lens)
```

- `scattered` 字段**退役删除**：归一后消费方零分支、零引用（deletion test 通过）；
  `glen` 转必填（`_empty_container_frames` 补 `glen=np.empty(0, np.int64)`）。
- docstring 改写：双物理布局是性能差异，消费契约单一。

### 3.2 统一载荷访问面（adapter，~17 行，ContainerFrames 方法）

```python
# blf_vector.py ContainerFrames 增两方法（唯一载荷语义实现）
def payload_block(self, sel) -> np.ndarray:
    """sel 帧子集 → (N, L) 定宽载荷块，补零就位（行尾 ljust 语义）。

    不变量：glen 恒存在、glen ≤ data_len、data_off + glen ≤ len(data8)
    （生产者保证）；本方法消费侧零分支。向量化零拷贝——与现 scattered
    分支同运算，逐位相同。
    """
    lens = self.data_len[sel]
    n = len(lens)
    if n == 0:
        return np.zeros((0, 0), dtype=np.uint8)
    L = int(lens.max())
    idt = np.int32 if self.data8.size < 2 ** 31 else np.int64
    src2 = self.data_off[sel].astype(idt)[:, None] + np.arange(L, dtype=idt)
    block = np.take(self.data8, src2, mode="clip")
    if not bool(np.all(self.glen[sel] == L)):   # 全满桶跳过掩码（同现优化）
        block[np.arange(L)[None, :] >= self.glen[sel][:, None]] = 0
    return block

def payload(self, i) -> bytes:
    """单帧载荷（测试面）：真实字节 + 行尾补零（ljust 语义）。"""
    o = int(self.data_off[i]); n = int(self.data_len[i]); g = int(self.glen[i])
    return bytes(self.data8[o:o + g]) + b"\x00" * (n - g)
```

### 3.3 消费面改造

```python
# converter.py _bucket_block：:52-58 双分支 → 1 行
ts, lens, block = cf.ts[sel], cf.data_len[sel], cf.payload_block(sel)
```

`_bucket_block` 签名（返回三元组）不动——converter 两处调用点（:112/:134）零改动。
测试 `_payload` → 删双分支，改调 `cf.payload(i)`（或直接在各断言处调 `cf.payload(i)`）。

## 4. 测试面收敛（量化）

| 位置 | 现状 | adapter 后 |
|---|---|---|
| `_payload`（:114-120） | 双分支重建（7 行） | 1 行调用（或删除，断言直调） |
| `_assert_eq` / `_assert_streams_equal`（:137/:546） | 经 `_payload` | 不变（或直调 `cf.payload(i)`） |
| 5 处直接断言（:345/:367/:377/:383/:396） | 经 `_payload` | 不变 |
| glen 表示断言（:366/:395） | 表示字段断言 | 保留（语义有效） |
| `_bucket_block`（converter:52-58） | 双分支 6 行 | 1 行调用 |

**收敛的量化**：H7b 的成本 = 一次表示变更迫使 7 处测试更新。adapter 后，表示变更（如
H7c 重叠视图、物理布局再调整）触达面 = `payload_block` + 生产者字段维护（1-2 处），
**测试面与 converter 消费面零改动**——7 处成本 → 1 处。

## 5. 逐位一致论证（对拍链判定能力不削弱）

1. **掩码恒等**：packed 掩码 `col >= lens` ≡ 归一后 `col >= glen`（packed 时 glen == lens）；
   skip 优化条件 `np.all(lens == L)` ≡ `np.all(glen == L)`。快路径运算与现 scattered
   分支逐位相同（同 take/clip/掩码）。
2. **判定目标不变**：测试仍以 `_walk_container` 的 `Frame.data` 为独立 oracle
   （python-can struct 独立解析）。adapter bug 会在 `payload == f.data` 断言中暴露——
   共享实现被测试消费不构成自证（对拍链判定能力是「比较目标」的性质，不是「实现来源」）。
3. **性能零变化**：快路径无新拷贝、无新分支；`_frames_to_container` 仅回退路径共享
   `lens` 数组（零拷贝）。
4. **防护同现**：`mode="clip"` 越界读 + glen 掩码兜底的不变式沿用现 scattered 分支，
   未改语义。

## 6. 风险与代价

- 表示面：`glen` 必填化触 `_empty_container_frames`（1 行）；`scattered` 删除触
  `_parse_fast` 发射段（删 1 参数行）。
- 放置决策：方法 on ContainerFrames（推荐，不变量属于该类型）vs 模块函数
  （保数据载体纯）——见待裁决点 2。
- 无性能风险；回归网齐全（快/回退对拍 + FD64 边界 + 230 组对拍 + full_compare）。
- 等价性由 `check.py` 预验证（见下）。

## 7. 验收（裁决「做」后执行）

1. 先跑 `.scratch/closeout/proto-e-adapter/check.py` 等价性预验证（已随原型提供）
2. TDD：`payload_block`/`payload` + glen 归一落地 → 消费面（`_bucket_block`/测试）切换
3. `pytest tests/test_blf_vector.py`（快/回退两路对拍）→ 全量 pytest（289+ 全绿）
4. AHT 样例转换 + `compare_two_mdf` 230 组 exit 0 + `full_compare` 同分（逐位不变）

## 8. 待裁决点（HITL）

1. **采纳与否**：成本 = ~17 行方法 + 2 行生产者归一；收益 = 双分支消失、表示变更
   触达面 7 处 → 1 处。否决理由若成立（如「双实现重建是有意为之的 oracle 冗余」），
   写入 map Out of scope。
2. **（采纳后）`scattered` 删除确认**：当前消费方零引用；删除后物理布局差异仅存于
   docstring。
3. **（采纳后）方法 vs 模块函数**：推荐方法（不变量属于类型）。
