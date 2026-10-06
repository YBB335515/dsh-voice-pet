# -*- coding: utf-8 -*-
"""带「隐藏控制台」启动 DSH 桌面端。

为什么需要它：Windows ACL 沙箱给子进程的是受限令牌（Low 完整性 + write-restricted）。
这种令牌下，子进程若无处继承控制台（父进程没控制台），Windows 会尝试新建控制台 →
以 STATUS_DLL_INIT_FAILED (0xC0000142) 失败。桌面端是 GUI 进程、默认没有控制台，
于是沙箱里的命令（pwsh/python/cmd 等控制台程序）全都起不来。

本启动器用 CREATE_NEW_CONSOLE 给桌面端分配一个控制台（窗口隐藏），
stdout/stderr 同时重定向到 voice\dsh-desktop.log（桌宠据此自动取得启动 token）。
"""
import os
import subprocess
import sys
import time

VOICE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(VOICE_DIR)
DESKTOP_DIR = os.path.join(WORKSPACE, "dsh桌面端")
DESKTOP_EXE = os.path.join(DESKTOP_DIR, "DeepSeek Harness.exe")
LOG = os.path.join(VOICE_DIR, "dsh-desktop.log")

CREATE_NEW_CONSOLE = 0x00000010
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008


def launch(log_path=LOG):
    """启动桌面端：隐藏的新控制台 + 输出重定向到 log_path。返回 Popen 或 None。"""
    if not os.path.exists(DESKTOP_EXE):
        print("找不到桌面端:", DESKTOP_EXE)
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE：控制台窗口不显示，但控制台对象存在
    logf = open(log_path, "ab", buffering=0)
    logf.write(("\n=== launch %s (CREATE_NEW_CONSOLE) ===\n" % time.strftime("%Y-%m-%d %H:%M:%S")).encode("utf-8"))
    p = subprocess.Popen([DESKTOP_EXE], cwd=DESKTOP_DIR,
                         stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         creationflags=CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP,
                         startupinfo=si, close_fds=True)
    return p


if __name__ == "__main__":
    p = launch()
    if p:
        print("已启动桌面端 pid=%d（控制台已分配但隐藏），日志: %s" % (p.pid, LOG))
