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
        # 隐藏控制台窗口
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
        # 高 DPI 感知：按物理像素渲染，避免 Windows 拉伸位图导致模糊
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
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
_self_turn_until = 0.0  # （保留）桌宠自己发起的回合窗口
_self_turn_sessions = {}  # 会话 id -> 过期时间：这些会话由桌宠发起，其"任务完成"不再语音播报
_pet_mode = "full"      # 显示模式：'full' 完整形象 / 'portrait' 贴边头像（由几何位置判断，不用易失配的布尔标志）
_leave_since = 0.0
_lock_expand = False    # True 时禁止收缩（F2 对话/思考/回答期间全身露出）
_restore_dock_after = False  # F2 前是收缩态 → 对话播报结束后自动收缩回去
_status_text = ""            # 气泡当前显示的状态文字（录音/识别等阶段）
_last_scale = 1.0       # 上一次形象缩放（避免重复重建图片）
_hover = False          # 鼠标是否悬停在桌宠上
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


# ── 桌宠形象：用 voice 目录下的图片（自动抠白底 → 透明），支持旋转/缩放 ──
import glob as _glob

_pet_photos = {}
_portrait_photos = {}
_PET_SRC = None


def _load_portrait_rgba(size):
    """头部头像：裁出角色上部（头+肩）区域的透明图，用于贴边收缩后显示。"""
    from PIL import Image, ImageFilter
    im = _load_pet_rgba(int(size * 1.6))  # 先取大图再裁
    if im is None:
        return None
    try:
        w, h = im.size
        # 头部区域：横向 20%-80%、纵向 15%-80%（含头/脸/饭碗，实测最佳构图）
        box = (int(w * 0.20), int(h * 0.15), int(w * 0.80), int(h * 0.80))
        head = im.crop(box)
        head.thumbnail((size, size), Image.LANCZOS)
        from PIL import ImageFilter, ImageEnhance
        head = head.filter(ImageFilter.UnsharpMask(radius=0.9, percent=150, threshold=2))
        return head
    except Exception as e:
        print("[voice] 头像裁剪失败:", e)
        return None


def portrait_photo(angle=0, size=110):
    """头像 PhotoImage（收缩态贴边显示用），带缓存。"""
    key = (int(angle), size)
    if key in _portrait_photos:
        return _portrait_photos[key]
    from PIL import Image, ImageTk
    im = _load_portrait_rgba(size)
    if im is None:
        return pet_photo(angle)
    if angle:
        im = im.rotate(angle, resample=Image.BICUBIC, expand=True)
    ph = ImageTk.PhotoImage(im)
    _portrait_photos[key] = ph
    return ph


def _find_pet_source():
    """找桌宠形象图：.voice_config.json 的 pet_image 优先 → PNG → 第一张 jpg。"""
    global _PET_SRC
    if _PET_SRC and os.path.exists(_PET_SRC):
        return _PET_SRC
    base = os.path.dirname(os.path.abspath(__file__))
    try:
        cfg = os.path.join(base, ".voice_config.json")
        if os.path.exists(cfg):
            with open(cfg, encoding="utf-8") as f:
                p = json.load(f).get("pet_image")
                if p and os.path.exists(p):
                    _PET_SRC = p
                    return p
    except Exception:
        pass
    for pat in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        found = sorted(_glob.glob(os.path.join(base, pat)))
        if found:
            _PET_SRC = found[0]
            return _PET_SRC
    return None


def _load_pet_rgba(size=240):
    """加载形象图：白底抠透明 + 裁掉空白边 + 等比缩放。"""
    import numpy as np
    from PIL import Image
    path = _find_pet_source()
    if not path:
        return None
    try:
        im = Image.open(path).convert("RGBA")
        # 可选裁剪（去文字/水印）：pet_crop_top / pet_crop_bottom / pet_crop_right（百分比）
        try:
            cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".voice_config.json")
            if os.path.exists(cfg):
                with open(cfg, encoding="utf-8") as f:
                    jc = json.load(f)
                ct = float(jc.get("pet_crop_top", 0) or 0)
                cb = float(jc.get("pet_crop_bottom", 0) or 0)
                cr = float(jc.get("pet_crop_right", 0) or 0)
                w0, h0 = im.size
                top = int(h0 * ct / 100.0) if ct > 0 else 0
                bottom = h0 - (int(h0 * cb / 100.0) if cb > 0 else 0)
                right = w0 - (int(w0 * cr / 100.0) if cr > 0 else 0)
                if top or bottom != h0 or right != w0:
                    im = im.crop((0, top, right, bottom))
        except Exception:
            pass
        arr = np.array(im)
        # 近白阈值：244（浅灰渐变背景也能抠掉）
        thr = 244
        try:
            cfg2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".voice_config.json")
            if os.path.exists(cfg2):
                with open(cfg2, encoding="utf-8") as f2:
                    thr = int(json.load(f2).get("white_threshold", 244))
        except Exception:
            pass
        mask = (arr[:, :, 0] > thr) & (arr[:, :, 1] > thr) & (arr[:, :, 2] > thr)
        arr[mask, 3] = 0
        im = Image.fromarray(arr)
        bbox = im.getbbox()
        if bbox:
            im = im.crop(bbox)
        # 高质量缩小：分步 LANCZOS + 锐化，让小尺寸也锐利
        if im.width > size:
            mid = max(size * 2, 2)
            if im.width > mid:
                im = im.resize((mid, max(1, int(im.height * mid / im.width))), Image.LANCZOS)
            im = im.resize((size, max(1, int(im.height * size / im.width))), Image.LANCZOS)
        # 极致清晰：锐化 + 轻微对比/饱和增强（动漫图小尺寸更鲜明）
        from PIL import ImageFilter, ImageEnhance
        try:
            im = im.filter(ImageFilter.UnsharpMask(radius=1.0, percent=160, threshold=2))
            im = ImageEnhance.Contrast(im).enhance(1.06)
            im = ImageEnhance.Color(im).enhance(1.10)
        except Exception:
            pass
        return im
    except Exception as e:
        print("[voice] 形象图加载失败:", e)
        return None


def _dpi_scale():
    """屏幕缩放比例（1.5 = 150%）。DPI 感知进程里按真实 DPI 渲染 → 更清晰。"""
    try:
        import ctypes
        try:
            dpi = ctypes.windll.user32.GetDpiForSystem()
            if dpi and dpi > 0:
                return dpi / 96.0
        except Exception:
            pass
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi and dpi > 0:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0


def _px(v):
    """逻辑像素 → 物理像素（按屏幕缩放比例，保证各尺寸判断一致）。"""
    return int(round(v * _dpi_scale()))


def _pet_base_size():
    """形象渲染尺寸（物理像素）= 配置的逻辑尺寸 × 屏幕缩放。
    例：配置 100 + 150% 屏 → 渲染 150px，视觉上是 100px 但更清晰。"""
    user = 100
    try:
        cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".voice_config.json")
        if os.path.exists(cfg):
            with open(cfg, encoding="utf-8") as f:
                user = int(json.load(f).get("pet_size", 100))
    except Exception:
        pass
    user = max(48, min(800, user))
    return int(round(user * _dpi_scale()))


def pet_photo(angle=0, scale=1.0):
    """桌宠形象 PhotoImage（图片优先，失败回退手绘鲸鱼）。带缓存。"""
    key = (int(angle), round(scale, 2))
    if key in _pet_photos:
        return _pet_photos[key]
    from PIL import Image, ImageTk
    base = _load_pet_rgba(int(_pet_base_size() * scale))
    if base is None:
        return whale_photo(angle)  # 没有图片时回退手绘鲸鱼
    img = base
    if angle:
        img = img.rotate(angle, resample=Image.BICUBIC, expand=True)
    ph = ImageTk.PhotoImage(img)
    _pet_photos[key] = ph
    return ph


# ── 手绘鲸鱼（无图片时的回退形象）──
_whale_photos = {}


def whale_photo(angle=0, size=100):
    """生成鲸鱼图形 PhotoImage（蓝色 DSH 风格，可指定旋转角度）。"""
    key = (int(angle), size)
    if key in _whale_photos:
        return _whale_photos[key]
    from PIL import Image, ImageDraw, ImageTk
    H = int(size * 1.28)  # 上方留白给状态小图标
    img = Image.new("RGBA", (size, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = size / 2
    cy = H - size / 2 - 2
    r = size * 0.36
    body = "#4c6ef5"
    dark = "#2b3a8f"
    # 尾巴
    d.polygon([(cx + r * 0.7, cy - r * 0.1), (cx + r * 1.75, cy - r * 1.05),
               (cx + r * 1.35, cy + r * 0.15), (cx + r * 1.8, cy + r * 1.0),
               (cx + r * 0.7, cy + r * 0.35)], fill=body)
    # 身体
    d.ellipse([cx - r * 1.15, cy - r * 0.85, cx + r * 1.05, cy + r * 0.9], fill=body)
    # 腹部亮色
    d.ellipse([cx - r * 0.75, cy + r * 0.1, cx + r * 0.7, cy + r * 0.95], fill="#a9c0ff")
    # 眼睛
    ex, ey = cx - r * 0.45, cy - r * 0.1
    d.ellipse([ex - r * 0.17, ey - r * 0.17, ex + r * 0.17, ey + r * 0.17], fill="white")
    d.ellipse([ex - r * 0.09, ey - r * 0.07, ex + r * 0.09, ey + r * 0.09], fill="#0b1020")
    # 嘴线
    d.arc([cx - r * 0.95, cy - r * 0.35, cx + r * 0.45, cy + r * 0.75],
          start=15, end=85, fill=dark, width=max(2, int(size * 0.02)))
    if angle:
        img = img.rotate(angle, resample=Image.BICUBIC, center=(cx, cy))
    ph = ImageTk.PhotoImage(img)
    _whale_photos[key] = ph
    return ph


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
    global busy, gui_sid, voice_sid, _lock_expand
    if busy:
        ui_q.put(("phase", "error", "上一轮进行中，请稍候…"))
        return
    busy = True
    _lock_expand = True   # 对话期间全身露出，不收缩
    try:
        expand_pet()
    except Exception:
        pass
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
        # 定时提醒：先检查是否是"提醒我xxx"类请求
        try:
            rem = vc.parse_reminder(corrected)
            if rem:
                due, content = rem
                vc.add_reminder(due, content)
                when = due.strftime("%H:%M")
                ui_q.put(("msg", ("你", corrected)))
                vc.append_history("user", corrected)
                ui_q.put(("msg", ("AI", "好的，%s 提醒你%s" % (due.strftime("%m-%d %H:%M"), content))))
                vc.append_history("ai", "好的，%s 提醒你%s" % (due.strftime("%m-%d %H:%M"), content))
                ui_q.put(("phase", "idle", "⏰ 已设置 %s 提醒%s" % (when, content)))
                speak_bg("好的，%s 提醒你%s" % (when, content))
                return
        except Exception as ex:
            print("[voice] 提醒解析异常:", ex)

        global _self_turn_until
        _self_turn_until = time.time() + 900  # 本轮对话期间忽略"任务完成"语音通知
        # 语音模式前缀：所有语音回答都要简短（不论简单/复杂任务）
        voice_text = ("（这是语音对话：请用简短口语直接回答，不超过两三句话，"
                      "不要用列表、标题、代码块、markdown 符号或 emoji，直接说答案。先简短口头总结，"
                      "需要给细节时口头说完后简短补充。）\n" + corrected)
        SWITCH_AFTER = 30  # 秒：超过它还没答完 → 判定为复杂任务

        task = vc.classify_task(corrected)
        if task == "complex":
            ui_q.put(("phase", "thinking", "🔀 复杂任务 → 交给 DSH 主会话"))
            target = gui_sid
            _self_turn_sessions[target] = time.time() + 1800
            answer = vc.ask(target, voice_text, on_status=on_ask_status)
        else:
            ui_q.put(("phase", "thinking", "⚡ 简单任务 → 快速回答"))
            target = voice_sid
            _self_turn_sessions[target] = time.time() + 600
            answer = vc.ask(target, voice_text, on_status=on_ask_status,
                            switch_after=SWITCH_AFTER)
            if answer == "__SLOW__":
                # 时间判定：30 秒还没答完 → 这是复杂任务，转主会话处理
                ui_q.put(("phase", "thinking",
                          "🐢 已思考 %ds，判定为复杂任务 → 交给 DSH 主会话" % SWITCH_AFTER))
                vc.cancel_session(voice_sid)  # 取消轻量会话的慢回合
                target = gui_sid
                _self_turn_sessions[target] = time.time() + 1800
                answer = vc.ask(target, voice_text, on_status=on_ask_status)
        _self_turn_sessions[target] = time.time() + 30   # 回合结束后 30 秒内仍忽略（事件到达延迟）
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
        pet_root.after(4000, _unlock_expand)
        if _restore_dock_after:
            pet_root.after(1000, _auto_recollapse_check)  # 播报结束后缩回边缘


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
    # 形象只用图片（不要颜文字）；状态信息在气泡/对话窗口里显示
    try:
        pet_label.configure(image=pet_photo(0))
    except Exception:
        pass
    show_bubble(p, text)


def show_bubble(p, text):
    """只在思考/工具/识别时弹气泡；待机(idle)等状态不显示（否则会有一块白方块）。"""
    global think_start
    if p in ("thinking", "tool", "recognize", "listen", "talking"):
        if think_start is None:
            think_start = time.time()
        global _status_text
        _status_text = text or _status_text
        bubble_label.configure(text=_status_text)
        bubble_root.deiconify()
        try:
            update_bubble()
        except Exception:
            pass
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
                    # 来自"桌宠自己发起的会话" → 只显示气泡（桌宠已回答过，不重复说）
                    # 来自你 GUI 里跑的任务 → 🎉 + 语音"任务完成啦"
                    sid_in = ""
                    for line in (text or "").split("\n"):
                        if "会话" in line:
                            sid_in = line.split(":", 1)[-1].strip()
                            break
                    from_self = bool(sid_in and _self_turn_sessions.get(sid_in, 0) > time.time())
                    if from_self:
                        notify_bubble("✅ " + title + "\n" + text[:120], None, 4000)
                    else:
                        notify_bubble("✅ " + title + "\n" + text[:120], "🎉", 8000)
                        beep(1500, 150)
                        beep(1900, 150)
                        speak_bg("任务完成啦。")
                else:
                    notify_bubble(title + "\n" + text[:120], None, 6000)
            elif kind == "remind":
                # 定时提醒到点：气泡 + 提示音 + 语音说话
                content = item[1]
                notify_bubble("⏰ 提醒：" + content, "⏰", 10000)
                beep(1000, 300)
                speak_bg("提醒，" + content)
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
    """气泡内容：思考阶段显示"思考 Ns + 实时推理"，其他阶段显示状态文字。"""
    if bubble_root is None:
        return
    if phase in ("thinking", "tool"):
        title = ""
        if think_start is not None:
            secs = int(time.time() - think_start)
            title = "💭 思考 %ds\n" % secs
        bubble_label.configure(text=title + reason_buf)
    else:
        bubble_label.configure(text=_status_text)
    # 气泡跟随桌宠
    px = pet_root.winfo_x()
    py = pet_root.winfo_y()
    bw = bubble_root.winfo_reqwidth()
    bh = bubble_root.winfo_reqheight()
    bubble_root.geometry("+%d+%d" % (px + max(80, pet_root.winfo_width() // 2), py - bh - 12))


def animate():
    """状态小图标轮换 + 形象缩放动画（呼吸 / 思考脉动 / 播报脉动 / 悬停放大）。"""
    global think_idx, speak_idx, _last_scale
    import math
    if phase == "thinking":
        think_idx = (think_idx + 1) % len(EMOJI["thinking"])
    elif phase == "speaking":
        speak_idx = (speak_idx + 1) % len(EMOJI["speaking"])

    # 形象保持稳定尺寸（不做呼吸/脉动/缩放的动画）
    if phase in ("thinking", "tool"):
        update_bubble()
    pet_root.after(250, animate)


def _set_hover(v):
    global _hover
    _hover = v


# 拖动 / 点击
_press_pos = None   # 按下时的鼠标位置（判定"是否拖动过"，不随移动更新）
_last_pos = None    # 上一次移动位置（算增量用）


def on_press_mouse(e):
    global drag_start, _press_pos, _last_pos
    drag_start = (e.x_root, e.y_root)   # 非 None 即"拖动中"
    _press_pos = (e.x_root, e.y_root)
    _last_pos = (e.x_root, e.y_root)


def on_drag(e):
    global _last_pos
    if drag_start is None:
        return
    dx = e.x_root - _last_pos[0]
    dy = e.y_root - _last_pos[1]
    pet_root.geometry("+%d+%d" % (pet_root.winfo_x() + dx, pet_root.winfo_y() + dy))
    _last_pos = (e.x_root, e.y_root)
    update_bubble()


last_click_time = 0


def on_release(e):
    global drag_start, last_click_time, _press_pos
    moved = False
    if _press_pos is not None:
        moved = (abs(e.x_root - _press_pos[0]) + abs(e.y_root - _press_pos[1])) > _px(6)
    _press_pos = None
    drag_start = None
    if moved:
        # 拖动结束：靠近左右边缘 70px 内 → 吸附贴边并立刻收缩成头像
        global _leave_since
        try:
            snap = _px(70)
            px, py = pet_root.winfo_x(), pet_root.winfo_y()
            pw = pet_root.winfo_width()
            sw = pet_root.winfo_screenwidth()
            side = None
            if px <= snap:
                side = "left"
            elif px + pw >= sw - snap:
                side = "right"
            if side:
                _leave_since = 0
                pet_root.geometry("+%d+%d" % ((0 if side == "left" else max(0, sw - pw)), py))
                pet_root.after(300, collapse_pet)  # 吸附后立刻收缩
        except Exception:
            pass
        return
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
    global _restore_dock_after
    try:
        while True:
            cmd = hotkey_q.get_nowait()
            if cmd == "speak":
                _restore_dock_after = (_pet_mode == "portrait")  # 记住：对话前是否收缩态
                pet_root.deiconify()
                expand_pet()  # 若在边缘收缩则展开
                threading.Thread(target=run_dialog, daemon=True).start()
            elif cmd == "quit":
                quit_app()
    except queue.Empty:
        pass
    pet_root.after(50, handle_hotkey)


def check_reminders():
    """定时提醒循环：每 5 秒检查本地提醒队列，到点推送提醒。"""
    try:
        due = vc.pop_due_reminders()
        for content in due:
            ui_q.put(("remind", content))
    except Exception:
        pass
    pet_root.after(5000, check_reminders)


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


DOCK_VISIBLE = 100     # 收缩后露在外面的宽度（px）
DOCK_ANGLE = 45        # 逆时针旋转 45°（PIL 正角度 = 逆时针）
DOCK_FRAMES = 7        # 动画帧数
DOCK_FRAME_MS = 35     # 每帧间隔


def _ease_inout(t):
    """smoothstep：慢→快→慢，自然。"""
    return 3 * t * t - 2 * t * t * t


def _ease_back(t, s=1.1):
    """带轻微过冲的回弹（Q 弹感）。"""
    return 1 + (s + 1) * (t - 1) ** 3 + s * (t - 1) ** 2


def _slide_anim(x0, x1, a0, a1, y, frame=0, ease="inout", frames=None, ms=None):
    """收缩/展开动画：位置与旋转角度同步缓动（可自定义时长）。"""
    frames = DOCK_FRAMES if frames is None else frames
    ms = FRAME_MS if ms is None else ms
    if frame > frames:
        pet_label.configure(image=pet_photo(a1))
        pet_root.geometry("+%d+%d" % (x1, y))
        return
    t = frame / float(frames)
    e = _ease_back(t) if ease == "back" else _ease_inout(t)
    x = int(round(x0 + (x1 - x0) * e))
    ang = a0 + (a1 - a0) * min(1.0, max(0.0, e))
    pet_label.configure(image=anim_photo(ang))   # 预生成帧，无计算开销
    pet_root.geometry("+%d+%d" % (x, y))
    pet_root.after(ms, lambda: _slide_anim(x0, x1, a0, a1, y, frame + 1, ease, frames, ms))


def _unlock_expand():
    global _lock_expand
    _lock_expand = False


def _auto_recollapse_check():
    """等对话与语音播报都结束后，若对话前是收缩态 → 自动收缩回边缘。"""
    global _restore_dock_after, _lock_expand
    if not _restore_dock_after:
        return
    if busy or (tts_proc is not None and tts_proc.poll() is None):
        pet_root.after(800, _auto_recollapse_check)
        return
    _restore_dock_after = False
    _lock_expand = False
    pet_root.after(1200, _do_recollapse)


def _do_recollapse():
    """对话结束后缩回边缘：先主动吸附到最近的左右边缘，再收缩（不看当前位置是否已贴边）。"""
    global _pet_mode
    if _pet_mode != "full":
        return
    try:
        pw = pet_root.winfo_reqwidth()
        sw = pet_root.winfo_screenwidth()
        x = pet_root.winfo_x()
        y = pet_root.winfo_y()
        # 主动吸附到最近的左右边缘
        if x + pw // 2 < sw // 2:
            pet_root.geometry("+0+%d" % y)
        else:
            pet_root.geometry("+%d+%d" % (max(0, sw - pw), y))
        pet_root.update_idletasks()
        collapse_pet()
    except Exception as e:
        print("[pet] recollapse err:", e, flush=True)


def _snap_after_expand():
    """展开完成后按最终图片尺寸重新贴边：动画用固定画布大图，结束时切回紧凑图，
    宽度变化会让窗口离开边缘几十像素，这里校正回来（否则无法再次收缩）。"""
    if _pet_mode != "full":
        return
    try:
        pet_label.configure(image=pet_photo(0))   # 确保切回紧凑图（动画帧是大画布）
        pet_root.update_idletasks()
        pw = pet_root.winfo_reqwidth()
        sw = pet_root.winfo_screenwidth()
        x = pet_root.winfo_x()
        y = pet_root.winfo_y()
        if x + pw // 2 < sw // 2:
            pet_root.geometry("+0+%d" % y)
        else:
            pet_root.geometry("+%d+%d" % (max(0, sw - pw), y))
    except Exception:
        pass


def _dock_side_now():
    """当前贴的是哪一边（按窗口位置几何判断）：'left' / 'right' / None。"""
    try:
        px = pet_root.winfo_x()
        pw = pet_root.winfo_width()
        sw = pet_root.winfo_screenwidth()
        tol = _px(14)
        if px <= tol:
            return "left"
        if px + pw >= sw - tol:
            return "right"
    except Exception:
        pass
    return None


def collapse_pet():
    """收缩：完整形象逆时针转 45° 滑出屏幕，露出约一半（不用裁剪头像）。"""
    global _pet_mode
    if _pet_mode == "portrait" or _lock_expand:
        return
    side = _dock_side_now()
    if side is None:
        return
    try:
        bubble_root.withdraw()
    except Exception:
        pass
    try:
        x0 = pet_root.winfo_x()
        y0 = pet_root.winfo_y()
        pet_label.configure(image=anim_photo(DOCK_ANGLE))  # 预生成旋转帧（尺寸恒定）
        pet_root.update_idletasks()
        pw = pet_root.winfo_reqwidth()
        ph = pet_root.winfo_reqheight()
        sw = pet_root.winfo_screenwidth()
        sh = pet_root.winfo_screenheight()
        y = max(0, min(sh - ph, y0))
        expose = max(_px(DOCK_VISIBLE), pw // 2)           # 露出约一半
        x1 = (expose - pw) if side == "left" else (sw - expose)
        _pet_mode = "portrait"
        _slide_anim(x0, x1, 0, DOCK_ANGLE, y, frames=COLLAPSE_FRAMES, ease="inout")
    except Exception as e:
        print("[pet] collapse err:", e, flush=True)


EXPAND_FRAMES = 30   # 展开动画 38 帧 × 16ms ≈ 0.6 秒（60fps，丝滑且看得清）
COLLAPSE_FRAMES = 22 # 收缩 22 帧 × 16ms ≈ 0.35 秒
FRAME_MS = 16        # 每帧 16ms ≈ 60 FPS

# 预生成的动画帧（固定画布，尺寸恒定 → 动画无重排、无卡顿）
_anim_frames = {}
_anim_canvas_size = 0


def prebuild_anim_frames():
    """启动时预生成 0°→45° 的旋转帧（每 3° 一帧，固定画布）。"""
    global _anim_canvas_size
    import math
    from PIL import Image, ImageTk
    base = _load_pet_rgba(_pet_base_size())
    if base is None:
        return
    d = int(math.ceil(max(base.width, base.height) * 1.42)) | 1  # 奇数尺寸，保证中心对齐
    _anim_canvas_size = d
    step = max(1, DOCK_ANGLE // 15)   # 45/15 = 3°
    for ang in range(0, DOCK_ANGLE + 1, step):
        canvas = Image.new("RGBA", (d, d), (0, 0, 0, 0))
        rot = base.rotate(ang, resample=Image.BICUBIC, expand=False)
        canvas.paste(rot, ((d - rot.width) // 2, (d - rot.height) // 2), rot)
        _anim_frames[ang] = ImageTk.PhotoImage(canvas)
    print("[pet] prebuilt %d anim frames (%dx%d)" % (len(_anim_frames), d, d), flush=True)


def anim_photo(angle):
    """取预生成的动画帧（量化到步长），无运行时 PIL 计算。"""
    step = max(1, DOCK_ANGLE // 15)
    a = int(round(angle / float(step))) * step
    a = max(0, min(DOCK_ANGLE, a))
    return _anim_frames.get(a) or pet_photo(a)


def expand_pet():
    """展开：从 45° 慢慢转正并滑回屏幕内（2 秒）。"""
    global _pet_mode
    if _pet_mode != "portrait":
        return
    try:
        x0 = pet_root.winfo_x()
        y0 = pet_root.winfo_y()
        left = x0 < 0
        _pet_mode = "full"
        pet_label.configure(image=anim_photo(0))
        pet_root.update_idletasks()
        pw = pet_root.winfo_reqwidth()
        sw = pet_root.winfo_screenwidth()
        sh = pet_root.winfo_screenheight()
        ph = pet_root.winfo_reqheight()
        y = max(0, min(sh - ph, y0))
        x1 = 0 if left else max(0, sw - pw)
        _slide_anim(x0, x1, DOCK_ANGLE, 0, y,
                    frames=EXPAND_FRAMES, ease="inout")
        pet_root.after(int(EXPAND_FRAMES * FRAME_MS) + 150, _snap_after_expand)
    except Exception as e:
        print("[pet] expand err:", e, flush=True)


def edge_watch():
    """收缩态 + 鼠标靠近 70px → 展开。
    收缩只由"拖动到边缘吸附"触发（不做自动收缩，避免位置误判导致意外缩进去）。"""
    if drag_start is None:
        try:
            if _pet_mode == "portrait":
                px, py = pet_root.winfo_x(), pet_root.winfo_y()
                pw, ph = pet_root.winfo_width(), pet_root.winfo_height()
                zone = _px(70)
                vpad = _px(70)
                mx, my = pet_root.winfo_pointerx(), pet_root.winfo_pointery()
                if (px - zone <= mx <= px + pw + zone) and (py - vpad <= my <= py + ph + vpad):
                    expand_pet()
        except Exception as e:
            print("[pet] edge_watch err:", e, flush=True)
    pet_root.after(250, edge_watch)


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
    pet_label = tk.Label(pet_root, image=pet_photo(0), text="", bg=PET_BG)  # 纯图片，无颜文字
    pet_label.pack(padx=10, pady=10)
    pet_label.bind("<Enter>", lambda e: _set_hover(True))
    pet_label.bind("<Leave>", lambda e: _set_hover(False))
    pet_label.bind("<ButtonPress-1>", on_press_mouse)
    pet_label.bind("<B1-Motion>", on_drag)
    pet_label.bind("<ButtonRelease-1>", on_release)

    # 默认位置：右下角（必须在图片创建后按真实尺寸定位，否则图片会超出屏幕）
    pet_root.update_idletasks()
    sw = pet_root.winfo_screenwidth()
    sh = pet_root.winfo_screenheight()
    pw = pet_root.winfo_reqwidth()
    ph = pet_root.winfo_reqheight()
    pet_root.geometry("+%d+%d" % (max(0, sw - pw - 50), max(0, sh - ph - 90)))
    print("[pet] img=%s window=%dx%d screen=%dx%d pos=%d,%d" % (
        _find_pet_source(), pw, ph, sw, sh, max(0, sw - pw - 50), max(0, sh - ph - 90)), flush=True)

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
        img = None
        # 优先用桌宠形象图做托盘图标（白底抠透明，居中缩放到 64x64）
        _src = _find_pet_source()
        if _src:
            try:
                import numpy as np
                im = Image.open(_src).convert("RGBA")
                arr = np.array(im)
                mask = (arr[:, :, 0] > 238) & (arr[:, :, 1] > 238) & (arr[:, :, 2] > 238)
                arr[mask, 3] = 0
                im = Image.fromarray(arr)
                bbox = im.getbbox()
                if bbox:
                    im = im.crop(bbox)
                im.thumbnail((60, 60), Image.LANCZOS)
                canvas = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
                canvas.paste(im, ((64 - im.width) // 2, (64 - im.height) // 2), im)
                img = canvas
            except Exception as e:
                print("[voice] 托盘图标加载失败:", e)
        if img is None:  # 回退：手绘图标
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

    prebuild_anim_frames()   # 预生成旋转动画帧（避免动画时卡顿）
    pet_root.after(100, poll_queue)
    pet_root.after(500, animate)
    pet_root.after(50, handle_hotkey)
    pet_root.after(5000, check_reminders)
    pet_root.after(600, edge_watch)  # 贴边自动收缩


def toggle_pet():
    if pet_root.winfo_viewable():
        pet_root.withdraw()
    else:
        pet_root.deiconify()


def single_instance():
    """Windows 互斥体：保证只有一个桌宠实例（防开机自启 + bat 双开）。"""
    try:
        import ctypes
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "dsh_voice_pet_mutex")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return False
        return True
    except Exception:
        return True


def main():
    global gui_sid, voice_sid
    if not single_instance():
        print("桌宠已在运行，本实例自动退出")
        return
    try:
        gui_sid, voice_sid = vc.ensure_voice_sessions()
    except Exception as e:
        # 任何初始化异常都不该打死桌宠（典型：token 缺失/失效导致 401）
        print("[voice] 初始化会话异常（桌宠继续常驻）:", e)
        gui_sid = voice_sid = None
    start_webhook_server()  # 接收 DSH 通知（回合完成/待审批）
    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    build_pet()
    pet_root.mainloop()


if __name__ == "__main__":
    main()
