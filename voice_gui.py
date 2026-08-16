# -*- coding: utf-8 -*-
"""
DSH 鲸鱼桌宠（语音助手）— 透明置顶小宠物 + 思考气泡 + 全局 F2
- 桌宠：🐋 鲸鱼 emoji，透明无边框置顶，可拖动；状态表情随环节变化
- 气泡：AI 思考时桌宠旁冒泡，滚动显示实时推理内容 + 思考计时
- 点击桌宠：展开完整对话窗口（双方记录）；关闭=隐藏
- F2 全局说话 / F3 退出 / 托盘菜单 / 开机自启
- 思考完成 → 语音播报回答
"""
import sys, os, queue, threading, json, subprocess, winsound, webbrowser, time
import tkinter as tk
from tkinter import font as tkfont
from pynput import keyboard
import voice_chat as vc

if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
    except Exception:
        pass

APP_NAME = "DshVoiceAssistant"
REG_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
PET_BG = "#010203"     # 桌宠透明背景色
BUBBLE_BG = "#040506"  # 气泡透明背景色

ui_q = queue.Queue()
hotkey_q = queue.Queue()
pet_root = None
pet_label = None
bubble_root = None
bubble_label = None
chat_root = None
chat_text = None
status_var = None
sid = None
busy = False
tts_proc = None
tray_icon = None
autostart_state = False
phase = "idle"
think_start = None
reason_buf = ""
think_idx = 0
speak_idx = 0
drag_start = None

EMOJI = {
    "idle": "🐋",
    "listen": "🎤",
    "talking": "🗣️",
    "recognize": "📝",
    "thinking": ["🤔", "💭", "😐", "💭"],
    "tool": "🔧",
    "speaking": ["📢", "🔊"],
    "error": "😵",
    "approval": "🔔",
    "done": "🎉",
}


def beep(freq, dur):
    try:
        winsound.Beep(freq, dur)
    except Exception:
        pass


def kill_tts():
    global tts_proc
    if tts_proc is not None:
        try:
            if tts_proc.poll() is None:
                tts_proc.kill()
        except Exception:
            pass
        tts_proc = None


def speak_bg(text):
    global tts_proc
    kill_tts()
    if not text:
        return
    spoken = vc.clean_for_speech(text) or "好的"
    edge_script = os.path.join(vc.BASE_DIR, "speak_edge.py")
    if os.path.exists(edge_script):
        # edge-tts 多音色（晓晓等），进程可被 F2 kill 打断
        tts_proc = subprocess.Popen(
            [sys.executable, edge_script, spoken],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        tts_proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", vc.SPEAK_PS1, spoken],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def on_press(key):
    try:
        if key == keyboard.Key.f2:
            hotkey_q.put("speak")
        elif key == keyboard.Key.f3:
            hotkey_q.put("quit")
    except Exception:
        pass


def _startup_lnk_path():
    """开机自启入口：启动文件夹里的快捷方式。"""
    return os.path.join(os.environ.get("APPDATA", ""),
                        "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
                        "DSH语音助手.lnk")


def _pythonw_exe():
    exe = sys.executable.replace("python.exe", "pythonw.exe")
    if os.path.exists(exe):
        return exe
    import shutil
    found = shutil.which("pythonw")
    return found or sys.executable


def is_autostart():
    return os.path.exists(_startup_lnk_path())


def set_autostart(enabled):
    lnk_path = _startup_lnk_path()
    if enabled:
        # 用 WScript.Shell 创建快捷方式（不依赖 pywin32）
        import subprocess as _sp
        watcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watcher.py")
        ps = (
            "$ws = New-Object -ComObject WScript.Shell\n"
            "$lnk = $ws.CreateShortcut('%s')\n"
            "$lnk.TargetPath = '%s'\n"
            "$lnk.Arguments = '\"%s\"'\n"
            "$lnk.WorkingDirectory = '%s'\n"
            "$lnk.Save()\n" % (
                lnk_path.replace("'", "''"),
                _pythonw_exe().replace("'", "''"),
                watcher.replace("'", "''"),
                os.path.dirname(os.path.abspath(__file__)).replace("'", "''"),
            )
        )
        _sp.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=30)
    else:
        try:
            os.remove(lnk_path)
        except OSError:
            pass


def toggle_autostart():
    global autostart_state
    new = not autostart_state
    try:
        set_autostart(new)
        autostart_state = new
        ui_q.put(("phase", "idle", "已开启开机自启" if new else "已关闭开机自启"))
    except Exception as e:
        ui_q.put(("phase", "error", "自启设置失败: %s" % e))


# ── 对话 worker ───────────────────────────────────────────────────
def run_dialog():
    global busy, gui_sid, voice_sid
    if busy:
        ui_q.put(("phase", "error", "上一轮进行中，请稍候…"))
        return
    busy = True
    try:
        kill_tts()
        beep(1200, 120)
        ui_q.put(("phase", "listen", "🎤 我在听，请说话…"))

        def rec_state(m):
            if "正在说话" in m:
                ui_q.put(("phase", "talking", m))
            elif "录音结束" in m:
                ui_q.put(("phase", "recognize", "📝 识别中…"))
            else:
                ui_q.put(("phase", "listen", m))

        audio, sr = vc.record_vad(on_state=rec_state)
        if audio is None or len(audio) == 0:
            ui_q.put(("phase", "idle", "没听到声音"))
            return
        beep(700, 120)
        ui_q.put(("phase", "recognize", "📝 识别中…"))
        text = vc.recognize_whisper(audio, sr)
        if not text:
            ui_q.put(("phase", "recognize", "改用系统识别…"))
            text = vc.recognize_sapi(6)
        if not text:
            ui_q.put(("phase", "idle", "没听清，再按 F2"))
            return
        corrected = vc.correct_homophones(text)
        vc.remember_user_text(corrected)
        ui_q.put(("msg", ("你", corrected)))
        vc.append_history("user", corrected)
        try:
            need, ratio = vc.check_context_pressure(voice_sid, 0.7)
            if need:
                ui_q.put(("phase", "thinking", "上下文 %.0f%% · 自动压缩中…" % (ratio * 100)))
        except Exception:
            pass
        # 智能任务分类：简单 → 轻量会话快答出声；复杂 → DSH 主会话（继承对话）
        task = vc.classify_task(corrected)
        if task == "complex":
            ui_q.put(("phase", "thinking", "🔀 复杂任务 → 交给 DSH 主会话"))
            target = gui_sid
        else:
            ui_q.put(("phase", "thinking", "⚡ 简单任务 → 快速回答"))
            target = voice_sid
        # 语音模式前缀：回答言简意赅（语音播报不宜长）
        voice_text = ("（这是语音对话：请用简短口语直接回答，不超过两三句话，"
                      "不要用列表、标题、代码块、markdown 符号或 emoji，直接说答案。先简短口头总结，"
                      "需要给细节时口头说完后简短补充。）\n" + corrected)
        answer = vc.ask(target, voice_text, on_status=on_ask_status)
        if not answer:
            ui_q.put(("phase", "idle", "没拿到回答"))
            return
        vc.append_history("ai", answer)
        ui_q.put(("msg", ("AI", answer)))
        ui_q.put(("phase", "speaking", "🔊 播报中（F2 打断）"))
        speak_bg(answer)
    except Exception as e:
        ui_q.put(("phase", "error", "出错: %s" % e))
    finally:
        busy = False
        ui_q.put(("phase", "idle", "就绪 · 按 F2 说话"))


def on_ask_status(m):
    if isinstance(m, tuple) and m and m[0] == "reasoning":
        ui_q.put(("reasoning", m[1]))
    elif isinstance(m, str):
        if "调用工具" in m:
            ui_q.put(("phase", "tool", "🔧 调用工具中…"))
        elif "工具返回" in m:
            ui_q.put(("phase", "thinking", "💭 继续思考…"))
        elif "完成" in m:
            pass
        # 其余进度符号（+ . 已等）忽略
    else:
        ui_q.put(("phase", "thinking", str(m)))


# ── 桌宠 UI ───────────────────────────────────────────────────────
def set_phase_ui(p, text):
    global phase
    phase = p
    emo = EMOJI.get(p, EMOJI["idle"])
    if isinstance(emo, list):
        emo = emo[0]
    pet_label.configure(text=emo)
    show_bubble(p, text)


def show_bubble(p, text):
    global think_start
    if p in ("thinking", "tool", "recognize"):
        if think_start is None:
            think_start = time.time()
        bubble_root.deiconify()
    else:
        if text:
            bubble_label.configure(text=text)
            bubble_root.deiconify()
        else:
            bubble_root.withdraw()
        think_start = None
        reason_buf_clear()


def reason_buf_clear():
    global reason_buf
    reason_buf = ""


def poll_queue():
    try:
        while True:
            item = ui_q.get_nowait()
            kind = item[0]
            if kind == "phase":
                set_phase_ui(item[1], item[2])
            elif kind == "reasoning":
                add_reasoning(item[1])
            elif kind == "msg":
                add_chat(item[1][0], item[1][1])
            elif kind == "notify":
                nkind, title, text = item[1], item[2], item[3]
                if nkind == "approval":
                    # 提取工具名（notify-phone 的 body 格式：工具: xxx）
                    tool = ""
                    for line in (text or "").split("\n"):
                        if "工具" in line:
                            tool = line.split(":", 1)[-1].strip()
                            break
                    notify_bubble("🔔 需要审批！去网页点允许\n" + title + "\n" + text[:120], "🔔", 15000)
                    beep(900, 200)
                    beep(1300, 200)
                    # 语音说话
                    speak_bg("有任务需要你确认" + ("，" + tool if tool else "") +
                             "，去 DeepSeek Harness 网页点一下允许。")
                elif nkind == "done":
                    notify_bubble("✅ " + title + "\n" + text[:120], "🎉", 8000)
                    beep(1500, 150)
                    beep(1900, 150)
                    # 语音说话
                    speak_bg("任务完成啦。")
                else:
                    notify_bubble(title + "\n" + text[:120], None, 6000)
    except queue.Empty:
        pass
    pet_root.after(100, poll_queue)


def add_reasoning(chunk):
    global reason_buf
    reason_buf = (reason_buf + chunk)[-320:]
    if phase not in ("thinking", "tool"):
        ui_q.put(("phase", "thinking", "💭 AI 思考中"))
    update_bubble()


def update_bubble():
    if bubble_root is None:
        return
    title = ""
    if think_start is not None:
        secs = int(time.time() - think_start)
        title = "💭 思考 %ds\n" % secs
    bubble_label.configure(text=title + reason_buf)
    # 气泡跟随桌宠
    px = pet_root.winfo_x()
    py = pet_root.winfo_y()
    bw = bubble_root.winfo_reqwidth()
    bh = bubble_root.winfo_reqheight()
    bubble_root.geometry("+%d+%d" % (px + 70, py - bh - 12))


def animate():
    global think_idx, speak_idx
    if phase == "thinking":
        think_idx = (think_idx + 1) % len(EMOJI["thinking"])
        pet_label.configure(text=EMOJI["thinking"][think_idx])
    elif phase == "speaking":
        speak_idx = (speak_idx + 1) % len(EMOJI["speaking"])
        pet_label.configure(text=EMOJI["speaking"][speak_idx])
    if phase in ("thinking", "tool"):
        update_bubble()
    pet_root.after(500, animate)


# 拖动 / 点击
def on_press_mouse(e):
    global drag_start
    drag_start = (e.x_root, e.y_root)


def on_drag(e):
    global drag_start
    if drag_start is None:
        return
    dx = e.x_root - drag_start[0]
    dy = e.y_root - drag_start[1]
    pet_root.geometry("+%d+%d" % (pet_root.winfo_x() + dx, pet_root.winfo_y() + dy))
    drag_start = (e.x_root, e.y_root)
    update_bubble()


last_click_time = 0


def on_release(e):
    global drag_start, last_click_time
    moved = False
    if drag_start is not None:
        moved = (abs(e.x_root - drag_start[0]) + abs(e.y_root - drag_start[1])) > 6
    drag_start = None
    if not moved:
        # 双击展开对话窗口（单击不动作，拖动优先不误触）
        now = time.time()
        if now - last_click_time < 0.4:
            toggle_chat()
        last_click_time = now


# ── 对话窗口 ───────────────────────────────────────────────────────
def add_chat(who, text):
    chat_text.insert(tk.END, who + ": " + text + "\n\n")
    chat_text.see(tk.END)


def toggle_chat():
    if chat_root.state() == "normal" and chat_root.winfo_viewable():
        chat_root.withdraw()
    else:
        chat_root.deiconify()
        chat_root.lift()
        chat_root.focus_force()


def load_history():
    try:
        p = os.path.join(vc.BASE_DIR, "history.jsonl")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    e = json.loads(line)
                    who = "你" if e.get("role") == "user" else "AI"
                    add_chat(who, e.get("text", ""))
    except Exception:
        pass


def handle_hotkey():
    try:
        while True:
            cmd = hotkey_q.get_nowait()
            if cmd == "speak":
                pet_root.deiconify()
                threading.Thread(target=run_dialog, daemon=True).start()
            elif cmd == "quit":
                quit_app()
    except queue.Empty:
        pass
    pet_root.after(50, handle_hotkey)


def start_webhook_server():
    """本地 webhook：接收 DSH notify-phone 的回合完成/审批推送 → 桌宠提醒。"""
    import http.server
    import socketserver

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                title = str(body.get("title", ""))
                text = str(body.get("body", ""))
                if "审批" in title or "审批" in text:
                    ui_q.put(("notify", "approval", title, text))
                elif "完成" in title or "结束" in title:
                    ui_q.put(("notify", "done", title, text))
                else:
                    ui_q.put(("notify", "info", title, text))
            except Exception:
                pass
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 8898), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def notify_bubble(text, emo=None, hold_ms=8000):
    """通知气泡：显示 text，hold_ms 毫秒后恢复待机。"""
    if emo:
        pet_label.configure(text=emo)
    bubble_label.configure(text=text)
    bubble_root.deiconify()
    update_bubble()
    pet_root.after(hold_ms, restore_idle)


def restore_idle():
    global phase
    if phase in ("idle",) and not busy:
        pet_label.configure(text=EMOJI["idle"])
        bubble_root.withdraw()


def quit_app():
    kill_tts()
    try:
        if tray_icon is not None:
            tray_icon.stop()
    except Exception:
        pass
    try:
        pet_root.destroy()
    except Exception:
        pass
    os._exit(0)


def build_pet():
    global pet_root, pet_label, bubble_root, bubble_label, chat_root, chat_text, status_var, tray_icon, autostart_state
    pet_root = tk.Tk()
    pet_root.title("语音助手")
    pet_root.overrideredirect(True)
    pet_root.wm_attributes("-topmost", True)
    pet_root.wm_attributes("-transparentcolor", PET_BG)
    pet_root.configure(bg=PET_BG)
    # 默认右下角
    sw = pet_root.winfo_screenwidth()
    sh = pet_root.winfo_screenheight()
    pet_root.geometry("+%d+%d" % (sw - 150, sh - 170))

    pet_label = tk.Label(pet_root, text=EMOJI["idle"], bg=PET_BG,
                         font=("Segoe UI Emoji", 52))
    pet_label.pack(padx=10, pady=10)
    pet_label.bind("<ButtonPress-1>", on_press_mouse)
    pet_label.bind("<B1-Motion>", on_drag)
    pet_label.bind("<ButtonRelease-1>", on_release)

    # 气泡
    bubble_root = tk.Toplevel(pet_root)
    bubble_root.overrideredirect(True)
    bubble_root.wm_attributes("-topmost", True)
    bubble_root.wm_attributes("-transparentcolor", BUBBLE_BG)
    bubble_root.configure(bg=BUBBLE_BG)
    bubble_label = tk.Label(bubble_root, text="", bg="white", fg="#333",
                            font=("Microsoft YaHei", 10), wraplength=250,
                            justify="left", padx=12, pady=10)
    bubble_label.pack()
    bubble_root.withdraw()

    # 对话窗口（点击桌宠展开）
    chat_root = tk.Toplevel(pet_root)
    chat_root.title("🎙️ 语音助手 · 对话记录")
    chat_root.geometry("520x600")
    chat_root.minsize(400, 420)
    chat_root.configure(bg="#f6f7f9")
    chat_root.protocol("WM_DELETE_WINDOW", chat_root.withdraw)
    header = tk.Label(chat_root, text="🎙️ 语音助手 · 对话记录（F2 说话 / F3 退出）",
                      bg="#4c6ef5", fg="white", pady=9,
                      font=(tkfont.nametofont("TkDefaultFont").actual("family"), 12, "bold"))
    header.pack(fill=tk.X)
    frame = tk.Frame(chat_root, bg="#f6f7f9")
    frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)
    chat_text = tk.Text(frame, wrap=tk.WORD, bg="white", fg="#222",
                        font=("Microsoft YaHei", 11), relief=tk.FLAT, padx=12, pady=10)
    scroll = tk.Scrollbar(frame, command=chat_text.yview)
    chat_text.configure(yscrollcommand=scroll.set)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    chat_text.pack(fill=tk.BOTH, expand=True)
    status_var = tk.StringVar(value="就绪 · 按 F2 说话")
    tk.Label(chat_root, textvariable=status_var, bg="#e9ecef", fg="#495057",
             font=("Microsoft YaHei", 10), anchor=tk.W, padx=10, pady=6).pack(fill=tk.X)
    load_history()
    chat_root.withdraw()  # 默认隐藏

    # 托盘
    try:
        import pystray
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([4, 4, 60, 60], radius=14, fill="#4c6ef5")
        d.ellipse([18, 22, 46, 50], fill="white")
        d.rectangle([25, 30, 39, 33], fill="#4c6ef5")
        autostart_state = is_autostart()
        menu = pystray.Menu(
            pystray.MenuItem("显示 / 隐藏桌宠", lambda i, item=None: pet_root.after(0, toggle_pet), default=True),
            pystray.MenuItem("对话记录", lambda i, item=None: pet_root.after(0, toggle_chat)),
            pystray.MenuItem("开机自启", lambda i, item=None: pet_root.after(0, toggle_autostart),
                             checked=lambda i: autostart_state),
            pystray.MenuItem("对话记录前端", lambda i, item=None: webbrowser.open("http://127.0.0.1:8899")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda i, item=None: pet_root.after(0, quit_app)),
        )
        tray_icon = pystray.Icon("dsh_pet", img, "DSH 鲸鱼桌宠", menu=menu)
        threading.Thread(target=tray_icon.run, daemon=True).start()
    except Exception as e:
        print("托盘不可用:", e)

    pet_root.after(100, poll_queue)
    pet_root.after(500, animate)
    pet_root.after(50, handle_hotkey)


def toggle_pet():
    if pet_root.winfo_viewable():
        pet_root.withdraw()
    else:
        pet_root.deiconify()


def main():
    global gui_sid, voice_sid
    gui_sid, voice_sid = vc.ensure_voice_sessions()
    start_webhook_server()  # 接收 DSH 通知（回合完成/待审批）
    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    build_pet()
    pet_root.mainloop()


if __name__ == "__main__":
    main()
