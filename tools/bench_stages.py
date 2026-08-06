"""阶段计时脚本：progress_cb 回调记录各阶段耗时（方案 G 基线复核）。

用法：python tools/bench_stages.py [--parallel]
输出：各阶段 wall 耗时与合计；与 docs/2026-08-05-blf-mdf-conversion-performance.md §6 方案C 记录对比。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from conftest import DBC_DIR, sample_blf          # noqa: E402
from test_golden import BINDING                    # noqa: E402
from core.converter import convert                 # noqa: E402
from core.dbc_loader import load                   # noqa: E402

def main():
    blf = sample_blf()
    if blf is None:
        raise SystemExit("无样例 BLF")

    bindings = {}
    for ch, dbc in BINDING.items():
        p = DBC_DIR / dbc
        if p.exists():
            bindings[ch] = load(str(p))
    print(f"绑定通道: {sorted(bindings)}  DBC 数: {len(bindings)}")

    out = Path("outputs/bench_g.mdf")
    stages = []

    def cb(stage, pct):
        stages.append((stage, pct, time.perf_counter()))

    parallel = "--parallel" in sys.argv
    t0 = time.perf_counter()
    result = convert(str(blf), bindings, str(out), progress_cb=cb,
                     parallel=parallel)
    t_end = time.perf_counter()

    # 阶段耗时：读取 BLF → 各解码 → 聚合 → 写 MDF
    print(f"\n=== 总耗时 {t_end - t0:.2f}s ===")
    prev = (None, None, t0)
    for stage, pct, t in stages:
        if prev[0] is None:
            print(f"  {stage:<20} 起始")
        else:
            print(f"  {prev[0]:<20} → {stage:<20}  {t - prev[2]:7.2f}s")
        prev = (stage, pct, t)
    print(f"  完成回调后收尾                  {t_end - prev[2]:7.2f}s")
    for s in result.summaries:
        if s.bound:
            print(f"  CAN{s.channel}: 解码 {s.decoded_frames} 未知 {s.unknown_frames} 信号 {s.signal_count}")
    print(f"输出: {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    # 必须守卫：spawn 子进程会以 __mp_main__ 重执行本模块顶层代码，
    # 无守卫会递归启动子进程（WinError 32 文件占用），见方案 §5.3
    main()
