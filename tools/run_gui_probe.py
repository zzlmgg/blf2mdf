"""运行冻结 GUI 探针并实时监控（临时）：干净 PATH 启动，周期性打印
探针 stderr + frozen_gui_probe.exe 进程数（worker spawn 时会 >1），
130s 兜底杀进程（探针内部 90s 超时正常应先行退出）。"""
import os
import subprocess
import sys
import time

EXE = r"E:\projects\blf_dbc\dist_probe2\frozen_gui_probe.exe"
OUT = r"E:\projects\blf_dbc\build_probe2\probe_run.log"

env = dict(os.environ)
env["PATH"] = r"C:\Windows\System32;C:\Windows"

with open(OUT, "wb") as log:
    p = subprocess.Popen([EXE], stdout=log, stderr=subprocess.STDOUT, env=env)

t0 = time.time()
while p.poll() is None and time.time() - t0 < 130:
    time.sleep(5)
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq frozen_gui_probe.exe"],
                       capture_output=True, text=True)
    n = sum(1 for ln in r.stdout.splitlines()
            if "frozen_gui_probe.exe" in ln and ln.strip())
    tail = open(OUT, "rb").read().decode("utf-8", "replace").splitlines()
    print(f"[RUNNER] t={time.time()-t0:.0f}s processes={n} last_log: {tail[-2:] if tail else 'EMPTY'}",
          flush=True)

if p.poll() is None:
    print(f"[RUNNER] 超时仍在运行 → 终止; 最后日志:")
    tail = open(OUT, "rb").read().decode("utf-8", "replace").splitlines()
    print("\n".join(tail[-15:]))
    subprocess.run(["taskkill", "/F", "/IM", "frozen_gui_probe.exe"],
                   capture_output=True)
    sys.exit(2)

print(f"[RUNNER] 探针退出 rc={p.returncode} t={time.time()-t0:.1f}s; 完整日志:")
print(open(OUT, "rb").read().decode("utf-8", "replace"))
