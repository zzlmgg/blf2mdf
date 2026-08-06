"""Windows spawn 子进程启动开销实测（方案 G 成本项）。

量：N 个 worker 同时启动（冷 import numpy+cantools）到可接收任务所需 wall 时间。
用法：python tools/bench_spawn.py <workers>
"""
"""Windows spawn 子进程启动开销实测（方案 G 成本项）。

量：N 个 worker 同时启动（冷 import numpy+cantools）到可接收任务所需 wall 时间。
用法：python tools/bench_spawn.py <workers>
"""
import sys
import time
from concurrent.futures import ProcessPoolExecutor

n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
t0 = time.perf_counter()


def init():
    import numpy  # noqa: F401
    import cantools  # noqa: F401
    return None


def noop(x):
    return x


def main():
    with ProcessPoolExecutor(max_workers=n, initializer=init) as ex:
        t1 = time.perf_counter()
        print(f"pool 创建（含 {n} 个 spawn + import numpy/cantools）: {t1 - t0:.2f}s")
        for i in range(n):
            ex.submit(noop, i).result()
        t2 = time.perf_counter()
        print(f"首个任务往返（含初始化）: {t2 - t0:.2f}s")


if __name__ == "__main__":
    main()
