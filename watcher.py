# -*- coding: utf-8 -*-
"""开机自启启动器（桌面端 / 网页版通吃）

1. 找 DSH：
   - 桌面端没在跑 → 用「重定向日志」的方式拉起 DeepSeek Harness.exe，
     它会打印一行带 token 的启动地址（printUrl 默认开），供桌宠自动认证；
   - 找不到桌面端 → 回退旧版「启动 DeepSeek Harness.bat」。
2. 等 DSH 就绪（候选端口有 HTTP 响应即可，401 也算就绪）。
3. 就绪后启动语音桌宠（隐藏窗口）。
"""
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

VOICE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(VOICE_DIR)
DESKTOP_DIR = os.path.join(WORKSPACE, "dsh桌面端")
DESKTOP_EXE = os.path.join(DESKTOP_DIR, "DeepSeek Harness.exe")
DESKTOP_LOG = os.path.join(VOICE_DIR, "dsh-desktop.log")
LEGACY_BAT = os.path.join(WORKSPACE, "deepseek-harness", "启动 DeepSeek Harness.bat")
PROBE_URLS = ("http://127.0.0.1:19387/", "http://127.0.0.1:3080/")
NO_WINDOW = 0x08000000
NEW_GROUP = 0x00000200


def any_dsh_ready():
    """任一候选地址有 HTTP 响应就算就绪（401 也说明服务已起）。"""
    for u in PROBE_URLS:
        try:
            urllib.request.urlopen(u, timeout=3)
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            continue
    return False


def desktop_running():
    """桌面端进程是否在跑（进程名含空格，用 tasklist 过滤）。"""
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq DeepSeek Harness.exe", "/NH"],
            creationflags=NO_WINDOW, stderr=subprocess.DEVNULL)
        return b"DeepSeek Harness.exe" in out
    except Exception:
        return False


def start_desktop():
    """带日志启动桌面端：日志里会出现 dsh web: http://127.0.0.1:PORT/?token=..."""
    if not os.path.exists(DESKTOP_EXE):
        return False
    try:
        logf = open(DESKTOP_LOG, "ab", buffering=0)
        logf.write(("\n=== watcher start %s ===\n" % time.strftime("%Y-%m-%d %H:%M:%S")).encode("utf-8"))
        subprocess.Popen([DESKTOP_EXE], cwd=DESKTOP_DIR,
                         stdout=logf, stderr=subprocess.STDOUT,
                         creationflags=NO_WINDOW | NEW_GROUP)
        return True
    except Exception:
        return False


def start_legacy_dsh():
    if not os.path.exists(LEGACY_BAT):
        return False
    try:
        subprocess.Popen(["cmd", "/c", "call", LEGACY_BAT],
                         cwd=os.path.dirname(LEGACY_BAT), creationflags=NO_WINDOW)
        return True
    except Exception:
        return False


def start_voice():
    os.chdir(VOICE_DIR)
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        cand = exe[:-11] + "python.exe"
        if os.path.exists(cand):
            exe = cand
    subprocess.Popen([exe, os.path.join(VOICE_DIR, "voice_gui.py")],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=NO_WINDOW)


def main():
    # 1) DSH 没起就拉起（优先桌面端；桌面端不存在再回退旧版 bat）
    if not any_dsh_ready():
        if not start_desktop():
            start_legacy_dsh()
    # 2) 等就绪（最多 10 分钟）
    for _ in range(120):
        if any_dsh_ready():
            break
        if not desktop_running() and not os.path.exists(LEGACY_BAT):
            break
        time.sleep(5)
    else:
        return  # 超时：不启动桌宠
    if not any_dsh_ready():
        return
    # 3) 起桌宠
    start_voice()


if __name__ == "__main__":
    main()
