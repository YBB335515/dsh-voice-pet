# -*- coding: utf-8 -*-
"""发现本机 DSH 的 HTTP 地址与启动 token。

端口每次启动都可能变（19387 / 64290 / ...），token 每进程随机，二者都不能写死。
顺序：启动日志 -> DSH_WEB_URL -> 扫描 127.0.0.1 监听端口（按 DSH 特征识别）。
"""
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE_DIR)
LOG_CANDIDATES = (
    os.path.join(BASE_DIR, "dsh-desktop.log"),
    os.path.join(ROOT, "voice", "dsh-desktop.log"),
    os.path.join(ROOT, "deepseek-harness-next", "dsh-runtime.log"),
    os.path.join(ROOT, "deepseek-harness", "dsh-runtime.log"),
)
TOKEN_RE = re.compile(r"dsh web:\s*(http://127\.0\.0\.1:(\d+))/?\?token=([A-Za-z0-9_\-]+)")
NO_WINDOW = 0x08000000
_cache = {"log_stamp": None, "base": None, "token": None, "scan": None, "scan_ts": 0.0}


def alive(base, timeout=2):
    """端口上是否有 HTTP 服务（401 也算活着）。"""
    try:
        urllib.request.urlopen(base + "/", timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def looks_like_dsh(base, timeout=2):
    """DSH 特征：401 unauthorized，或页面里带 __DSH_BOOT__。"""
    try:
        with urllib.request.urlopen(base + "/", timeout=timeout) as r:
            body = r.read(4096).decode("utf-8", "replace")
        return "__DSH_BOOT__" in body or "DeepSeek Harness" in body
    except urllib.error.HTTPError as e:
        try:
            body = e.read(200).decode("utf-8", "replace").strip().lower()
        except Exception:
            body = ""
        return e.code == 401 and ("unauthorized" in body or "dsh web" in body or "authentication required" in body)
    except Exception:
        return False


def from_log():
    """从启动日志取 (base, token)：只认端口仍然活着的最后一条。"""
    newest = None
    for lg in LOG_CANDIDATES:
        try:
            if os.path.exists(lg):
                mt = os.path.getmtime(lg)
                if newest is None or mt > newest[0]:
                    newest = (mt, lg)
        except Exception:
            pass
    if newest is None:
        return None, None
    mt, lg = newest
    if _cache["log_stamp"] == (mt, lg):
        return _cache["base"], _cache["token"]
    base = token = None
    try:
        with open(lg, encoding="utf-8", errors="replace") as f:
            hits = TOKEN_RE.findall(f.read())
        for h in reversed(hits):
            if alive(h[0]):
                base, token = h[0], h[2]
                break
    except Exception:
        pass
    _cache.update({"log_stamp": (mt, lg), "base": base, "token": token})
    return base, token


def scan():
    """扫描 127.0.0.1 监听端口，找出像 DSH 的那个（缓存 60 秒）。"""
    now = time.time()
    if _cache["scan"] is not None and now - _cache["scan_ts"] < 60:
        return _cache["scan"]
    path = os.path.join(tempfile.gettempdir(), "dsh_port_scan.txt")
    ports = set()
    try:
        with open(path, "w", encoding="utf-8", errors="replace") as f:
            subprocess.run(["netstat", "-ano"], stdout=f, stderr=subprocess.DEVNULL,
                           creationflags=NO_WINDOW, timeout=20)
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "LISTENING" not in line:
                    continue
                parts = line.split()
                if len(parts) < 2 or not parts[1].startswith("127.0.0.1:"):
                    continue
                try:
                    ports.add(int(parts[1].rsplit(":", 1)[1]))
                except Exception:
                    pass
    except Exception:
        pass
    found = None
    for p in sorted(ports):
        b = "http://127.0.0.1:%d" % p
        if looks_like_dsh(b):
            found = b
            break
    _cache.update({"scan": found, "scan_ts": now})
    return found


def discover(refresh=False):
    """返回 (base, token)；token 可能为 None（桌面端未带日志启动）。"""
    if refresh:
        _cache.update({"log_stamp": None, "scan": None, "scan_ts": 0.0})
    base, token = from_log()
    if base:
        return base, token
    env = os.environ.get("DSH_WEB_URL")
    if env:
        b = env.rstrip("/")
        if alive(b):
            return b, None
    b = scan()
    if b:
        return b, None
    return None, None
