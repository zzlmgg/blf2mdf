# 04 — core 收口面裁决（含搭车项）

Type: grilling
Status: open
Blocked by:

## Question

四条 core 面候选逐条裁决（做则同票落地）+ 两项搭车：

1. **worker 数 / 进度带单一来源**（L1/M5）：worker 数决策双实现（mp_finish.py:74-76 vs :157-158，env 覆盖仅 converter 路径生效）+ 解码 10→90 进度带在 converter.py:424-425 与 mp_finish.py:191-194 各自写死——收口为最小常量/函数共享，**不引入「进度管理器」抽象**（标准⑥）
2. **`_find_ccu3_root` 下沉 project_loader**（读写侧 P5）：模块自述职责与实现分离，测试跨包归属暴露错位，locality 直接改善
3. **容器流衔接骨架三份收敛**（L10）：blf_reader 尾部衔接 + ms_part 推导骨架三份复制——收敛后 blf_vector 对 blf_reader 的 4 个私有名依赖可降为正式接口
4. **read_start_time 整数化时机**（L8）：float 中转两次 vs 模块内已有 `_systemtime_ns` 纯整数路径（float 路径有 ±119ns 安全论证，属已知冗余）——现在做 or 明确搁置？

**搭车项**（review §4 Speculative「成本极低可搭车」）：并行退化显式化（make_pool 失败静默吞掉，converter.py:368-372，用户无从得知本次串行还是并行）+ 取消协议中性化（ScanCancelled 定义在 blf_reader 却是 convert 的取消信号）。

约束：改动不触碰已验证性能路径的 numpy 运算语义；每步 pytest 全量 + 对拍链可回退。
