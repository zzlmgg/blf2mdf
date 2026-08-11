"""一次性验证：模拟"别人机器"（干净 PATH，无 conda/Anaconda）启动冻结 exe。

验证目标：
1. exe 不依赖构建机环境 PATH 中的任何 DLL（VC 运行库已内置、ICU 走 System32）
2. 最小发布布局（exe + inputs/dbc_ccu3.0）下 GUI 正常出现

用法：python tools/verify_clean_env.py <exe路径> [超时秒数]
"""
import subprocess
import sys
import time
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

MINIMAL_PATH = "C:\\Windows\\System32;C:\\Windows"  # 无 conda、无 PowerShell、无任何用户 PATH

exe = sys.argv[1]
timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 25.0

env = {
    "PATH": MINIMAL_PATH,
    "SystemRoot": "C:\\Windows",
    "TEMP": "C:\\Windows\\Temp",
    "TMP": "C:\\Windows\\Temp",
}
proc = subprocess.Popen([exe], env=env)
print(f"launched pid={proc.pid}")


def blf2mdf_pids():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq blf2mdf.exe", "/FO", "CSV"],
                         capture_output=True, text=True).stdout
    return sorted({int(line.split('"')[3]) for line in out.splitlines()
                   if "blf2mdf.exe" in line})


def windows_of(pid):
    hwnds = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value != pid:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value:
            hwnds.append(buf.value)
        return True

    user32.EnumWindows(cb, 0)
    return hwnds


target = None
t0 = time.time()
while time.time() - t0 < timeout:
    if proc.poll() is not None:
        print(f"FAIL: exe 提前退出 rc={proc.returncode}")
        sys.exit(1)
    for pid in blf2mdf_pids():
        wins = windows_of(pid)
        if wins:
            target = (pid, wins)
            break
    if target:
        break
    time.sleep(0.5)

# 杀掉所有相关进程（干净退出）
subprocess.run(["taskkill", "/F", "/IM", "blf2mdf.exe"], capture_output=True)
proc.wait(timeout=5)

if target:
    pid, wins = target
    print(f"OK: pid={pid} 窗口={wins}")
    if any("BLF" in w for w in wins):
        print("PASS: 主窗口可见（干净 PATH + 最小发布布局）")
        sys.exit(0)
    print("WARN: 有窗口但非主窗口标题")
    sys.exit(2)
print(f"FAIL: 超时 {timeout}s 未见窗口（进程存活 {proc.poll() is None}）")
sys.exit(1)
