# -*- coding: utf-8 -*-
"""开机自启启动器（桌面端 / 网页版通吃）

1. 找 DSH：
   - 桌面端没在跑 → 用「重定向日志」的方式拉起 DeepSeek Harness.exe（launch_desktop
     自动发现系统安装版/旧工作区版），它会打印一行带 token 的启动地址
     （printUrl 默认开），供桌宠自动认证；
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
_WS_PARENT = os.path.dirname(VOICE_DIR)
# 源码收进「桌宠」容器目录后，工作区要再上一级
WORKSPACE = os.path.dirname(_WS_PARENT) if os.path.basename(_WS_PARENT) == "桌宠" else _WS_PARENT
DESKTOP_LOG = os.path.join(VOICE_DIR, "dsh-desktop.log")
LEGACY_BAT = os.path.join(WORKSPACE, "deepseek-harness", "启动 DeepSeek Harness.bat")
NO_WINDOW = 0x08000000
NEW_GROUP = 0x00000200


def any_dsh_ready():
    """任意端口上的 DSH 可用即视为就绪（端口每次启动都可能变化）。"""
    try:
        import dsh_probe
        b, _t = dsh_probe.discover(refresh=True)
        return bool(b)
    except Exception:
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
    """启动桌面端：必须走 launch_desktop（分配隐藏控制台 + 日志重定向）。

    隐藏控制台是必须的：受限令牌下无控制台继承的 GUI 子进程会以
    STATUS_DLL_INIT_FAILED (0xC0000142) 死掉。
    """
    try:
        import launch_desktop
        if not launch_desktop.find_desktop_exe():
            return False
        return bool(launch_desktop.launch(DESKTOP_LOG))
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
