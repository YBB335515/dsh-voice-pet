# -*- coding: utf-8 -*-
"""dsh-voice-pet 一键安装：
1. 检查 Python >= 3.10
2. pip 安装依赖（faster-whisper / pyaudio / pynput / pystray / edge-tts 等）
3. 下载本地语音识别模型（faster-whisper small，默认 hf-mirror 镜像）
4. 可选：创建开机自启快捷方式（等待 DSH 就绪后自动启动桌宠）

用法:
  python install.py                 # 标准安装
  python install.py --model base    # 用小模型（约 145MB，快但略不准）
  python install.py --autostart     # 安装 + 开机自启
  python install.py --skip-model    # 跳过模型下载（已有模型时）
"""
import os, sys, subprocess, argparse, urllib.request, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_REPO = "Systran/faster-whisper-%s"
MIRRORS = ["https://hf-mirror.com", "https://huggingface.co"]
MODEL_FILES = ["config.json", "tokenizer.json", "vocabulary.txt", "model.bin"]


def log(msg):
    print("[install]", msg, flush=True)


def check_python():
    if sys.version_info < (3, 10):
        log("需要 Python 3.10+，当前 %s" % sys.version.split()[0])
        sys.exit(1)


def install_deps():
    log("安装 Python 依赖…")
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                           "-r", os.path.join(HERE, "requirements.txt")])


def download_model(name="small"):
    """下载 faster-whisper 模型到项目目录（whisper-small/ 或 whisper-base/）。"""
    dest = os.path.join(HERE, "whisper-%s" % name)
    os.makedirs(dest, exist_ok=True)
    ok = False
    for mirror in MIRRORS:
        base = "%s/%s/resolve/main/" % (mirror, MODEL_REPO % name)
        log("从 %s 下载 %s 模型…" % (mirror, name))
        try:
            for f in MODEL_FILES:
                if os.path.exists(os.path.join(dest, f)) and f != "model.bin":
                    continue
                log("  下载 %s …" % f)
                req = urllib.request.Request(base + f, headers={"User-Agent": "dsh-voice-pet/1.0"})
                with urllib.request.urlopen(req, timeout=120) as r, \
                        open(os.path.join(dest, f), "wb") as out:
                    shutil.copyfileobj(r, out)
            ok = True
            break
        except Exception as e:
            log("  失败: %s，尝试下一个源…" % e)
    if not ok:
        log("模型下载失败。可稍后重跑，或手动下载放到 %s" % dest)
        sys.exit(1)
    log("模型就绪: %s" % dest)


def setup_autostart():
    """启动文件夹快捷方式 → watcher.py（等 DSH 就绪再启动桌宠）。"""
    lnk = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                       "Start Menu", "Programs", "Startup", "DSH语音助手.lnk")
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = shutil.which("pythonw") or sys.executable
    ps = (
        "$ws = New-Object -ComObject WScript.Shell\n"
        "$lnk = $ws.CreateShortcut('%s')\n"
        "$lnk.TargetPath = '%s'\n"
        "$lnk.Arguments = '\"%s\"'\n"
        "$lnk.WorkingDirectory = '%s'\n"
        "$lnk.Save()\n" % (
            lnk.replace("'", "''"), pyw.replace("'", "''"),
            os.path.join(HERE, "watcher.py").replace("'", "''"),
            HERE.replace("'", "''"),
        )
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   timeout=30)
    log("已创建开机自启快捷方式: %s" % lnk)


def main():
    ap = argparse.ArgumentParser(description="dsh-voice-pet 安装器")
    ap.add_argument("--model", default="small", choices=["small", "base"],
                    help="语音识别模型（small 更准，base 更快）")
    ap.add_argument("--autostart", action="store_true", help="创建开机自启快捷方式")
    ap.add_argument("--skip-model", action="store_true", help="跳过模型下载")
    args = ap.parse_args()

    check_python()
    install_deps()
    if not args.skip_model:
        download_model(args.model)
    if args.autostart:
        setup_autostart()
    log("完成！启动方式：python voice_gui.py（DSH 需在 3080 端口运行）")


if __name__ == "__main__":
    main()
