# -*- coding: utf-8 -*-
"""开机自启启动器（全自动）：
1. 探测 DSH(3080)；没起 → 自动运行「启动 DeepSeek Harness.bat」拉起来（隐藏窗口）
2. 循环等待 DSH 就绪（每 5 秒，最多 10 分钟）
3. 就绪后启动语音助手鲸鱼桌宠（隐藏）
"""
import os, sys, time, urllib.request, subprocess

VOICE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(VOICE_DIR)
DSH_URL = "http://127.0.0.1:3080/"
DSH_START_BAT = os.path.join(WORKSPACE, "deepseek-harness", "启动 DeepSeek Harness.bat")
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def dsh_ready():
    try:
        with urllib.request.urlopen(DSH_URL, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def start_dsh():
    """隐藏拉起 DeepSeek Harness（bat 自带"已运行则只开浏览器"判断）。"""
    if not os.path.exists(DSH_START_BAT):
        return False
    try:
        subprocess.Popen(["cmd", "/c", "call", DSH_START_BAT],
                         cwd=os.path.dirname(DSH_START_BAT),
                         creationflags=NO_WINDOW)
        return True
    except Exception:
        return False


def start_voice():
    os.chdir(VOICE_DIR)
    subprocess.Popen([sys.executable, os.path.join(VOICE_DIR, "voice_gui.py")],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=NO_WINDOW)


def main():
    if not dsh_ready():
        start_dsh()  # DSH 没起：自动拉起
    for _ in range(120):  # 最多等 10 分钟
        if dsh_ready():
            start_voice()
            return
        time.sleep(5)
    # 超时：DSH 无法就绪（bat 可能弹了错误提示），桌宠不启动


if __name__ == "__main__":
    main()
