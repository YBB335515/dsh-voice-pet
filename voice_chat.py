# -*- coding: utf-8 -*-
"""
语音问答核心：你说话 → AI 用语音回答（复用正在运行的 DSH web 会话，问 AI 答）
用法:
  python voice_chat.py --text "问题"   # 文本问答（测试用）
  python voice_chat.py                 # 回车后录音5秒再问答
被 voice_assistant.py（热键启动器）复用。
依赖: sounddevice numpy faster-whisper（模型已放 whisper-base/ 本地目录）
"""
import sys, os, json, time, uuid, datetime, subprocess, urllib.request, argparse, re

# 控制台输出兜底：无法编码的字符（如 emoji）替换为 ?，不影响中文
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

def clean_for_speech(t):
    """播报文本清洗：去网址、markdown 链接/符号、emoji，压缩空白。
    网址、代码标记等不适合朗读，会影响听感。"""
    t = re.sub(r"https?://\S+|www\.\S+", " ", t)            # 网址
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)       # markdown 链接 → 只留链接文字
    t = t.replace(chr(96), "")                                  # 反引号
    t = re.sub(r"[#*_>|]{1,}", "", t)                           # 其余 markdown 符号（保留 ~ 用于温度范围）
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u2190-\u21FF]", "", t)  # emoji
    t = re.sub(r"\s+", " ", t).strip()
    return t

strip_emoji = clean_for_speech  # 兼容旧引用

# ── 同音字纠错：只把"明显说错"的词纠正成用户常用写法，正确句子一律不动 ──
# 例：之前说过"综述"，后续识别成"中术/中述/中数/中处"，自动纠正为"综述"。
# 收紧规则防误改：词库只收出现≥2次的词；完全同音才替换；常用词（吗/嘛、哪个/那个等）永不修改。
USER_VOCAB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".user_vocab.txt")

# 常用词保护表：这些词永远不被纠错（防止把正确的句子改坏）
PROTECTED_WORDS = frozenset("""
那个 哪个 一个 这个 这些 那些 什么 怎么 为什么 多少 吗 嘛 呢 吧 啊 呀 哦 嗯
了 的 地 得 在 是 有 没有 不 不是 就 就是 还 还是 也 都 很 太 真 好 可以
可能 应该 已经 现在 然后 但是 因为 所以 如果 你 我 他 她 它 我们 你们 他们
自己 说 话 问 答 想 要 能 会 加 在 加在 命令 语音 录音 时间 问题 东西 样
""".split())

_pinyin_cache = {}


def _pinyin(seg):
    """中文片段 → 无调拼音串（带缓存）。"""
    if seg in _pinyin_cache:
        return _pinyin_cache[seg]
    from pypinyin import lazy_pinyin
    p = "".join(lazy_pinyin(seg))
    _pinyin_cache[seg] = p
    return p


def _edit_distance(a, b):
    """Levenshtein 编辑距离（小字符串用）。"""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (0 if a[i - 1] == b[j - 1] else 1))
        prev = cur
    return prev[lb]


def remember_user_text(text):
    """记住用户说过的文本（累积同音纠错的词库来源）。"""
    if not text:
        return
    try:
        with open(USER_VOCAB_FILE, "a", encoding="utf-8") as f:
            f.write(text.strip() + "\n")
    except Exception:
        pass


def build_vocab(texts):
    """从历史文本提取 2-4 字中文片段 → 拼音串 → 原词。
    只保留用户说过 ≥2 次的词（偶发词不算，减少误改）。返回 (拼音→词, 高频词集合)。"""
    counter = {}
    for t in texts:
        for n in (4, 3, 2):
            for i in range(len(t) - n + 1):
                seg = t[i:i + n]
                if re.match(r"^[\u4e00-\u9fff]+$", seg):
                    counter[seg] = counter.get(seg, 0) + 1
    by_pinyin = {}
    for seg, c in counter.items():
        if c < 2:
            continue
        p = _pinyin(seg)
        if p not in by_pinyin or c > by_pinyin[p][1]:
            by_pinyin[p] = (seg, c)
    seen = {s for s, c in counter.items() if c >= 2}
    return {p: s for p, (s, _c) in by_pinyin.items()}, seen


def correct_homophones(text, texts=None, max_dist=1):
    """收紧版纠错：只把"明显说错的同音字"换成用户常用写法，正确句子一律不动。
    - 完全同音才替换（如"中术"→"综述"）
    - 近音容错：仅当原片段连用户都没说过（不在高频词里）时才考虑
    - 常用词保护：PROTECTED_WORDS 里的词永不修改（防"吗/嘛""哪个/那个"互改）"""
    if not text:
        return text
    if texts is None:
        texts = []
        if os.path.exists(USER_VOCAB_FILE):
            try:
                with open(USER_VOCAB_FILE, encoding="utf-8") as f:
                    texts = [l.strip() for l in f if l.strip()]
            except Exception:
                texts = []
    vocab, seen = build_vocab(texts)
    if not vocab:
        return text
    result = text
    for n in (4, 3, 2):  # 优先纠正长词
        i = 0
        while i <= len(result) - n:
            seg = result[i:i + n]
            if re.match(r"^[\u4e00-\u9fff]+$", seg) and seg not in PROTECTED_WORDS:
                p = _pinyin(seg)
                exact = vocab.get(p)
                if exact is not None and exact != seg:
                    result = result[:i] + exact + result[i + n:]
                    i += n
                    continue
                # 近音容错：原词自己都没说过（不在高频词库）才考虑，防误改
                if seg not in seen:
                    for vp, vseg in vocab.items():
                        if vp and p and vp[0] == p[0] and abs(len(vp) - len(p)) <= 1:
                            if _edit_distance(vp, p) <= max_dist:
                                result = result[:i] + vseg + result[i + n:]
                                i += n
                                break
            i += 1
    return result


BASE_DIR = os.path.dirname(os.path.abspath(__file__))   # D:\1_claude尝试\voice
WORKSPACE = os.path.dirname(BASE_DIR)                    # D:\1_claude尝试
SESSION_CACHE = os.path.join(BASE_DIR, ".voice_session.json")
SPEAK_PS1 = os.path.join(BASE_DIR, "speak.ps1")
DSH_URL = "http://127.0.0.1:3080"

_whisper_model = None

def rpc(method, payload):
    body = json.dumps({"type": "client-request", "rpcId": str(uuid.uuid4()),
                       "method": method, "payload": payload}).encode("utf-8")
    req = urllib.request.Request(f"{DSH_URL}/api/{method}", data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        j = json.loads(resp.read().decode("utf-8"))
    if not j.get("result", {}).get("ok"):
        raise RuntimeError(f"{method} failed: {json.dumps(j, ensure_ascii=False)[:300]}")
    return j["result"]["value"]

def check_context_pressure(session_id, threshold=0.7):
    """查会话上下文压力。返回 (是否超阈值, 当前占比)。超阈值时 AI 可能卡死，应重置。"""
    try:
        items = rpc("session.list", {})["items"]
        for it in items:
            if it["sessionId"] == session_id:
                p = (it.get("projections") or {}).get("contextPressure") or {}
                pressure = p.get("pressureTokens") or 0
                window = p.get("contextWindow") or 1
                ratio = pressure / window if window else 0.0
                return ratio >= threshold, ratio
    except Exception:
        pass
    return False, 0.0

def compact_session(session_id, target_ratio=0.5):
    """触发会话压缩（/compact 命令）：总结历史保留记忆、减小上下文占用。
    等待压力降到 target_ratio 以下（最多约 60 秒）。返回是否成功。"""
    try:
        r = rpc("session.prompt", {"sessionId": session_id, "mode": "queue",
                                   "content": [{"type": "text", "text": "/compact"}]})
        cmd = r.get("command") or {}
        print(f"[voice] 压缩命令: {cmd.get('kind')} {cmd.get('text', '')}")
    except Exception as e:
        print(f"[voice] 压缩命令发送失败: {e}")
        return False
    # 等待压缩完成（压力下降）
    for _ in range(30):
        time.sleep(2)
        try:
            _, ratio = check_context_pressure(session_id, 0.99)
            if ratio < target_ratio:
                return True
        except Exception:
            pass
    return True

def reset_session():
    """删除会话缓存并新建（词库 .user_vocab.txt 独立保留，不丢）。返回新会话 id。"""
    try:
        if os.path.exists(SESSION_CACHE):
            os.remove(SESSION_CACHE)
    except Exception:
        pass
    return ensure_session()

def ensure_session():
    """选择桌宠对话所用会话：
    1. 优先复用缓存（桌宠上次绑定的会话，重启后继续同一对话）
    2. 无缓存则继承最近活跃的 DSH 会话（桌宠对话直接进你正在用的对话，
       GUI 里能看到全部聊天备份，长任务短任务同一上下文）
    3. 兜底新建（cwd 工作区，agentPreset standard）。"""
    if os.path.exists(SESSION_CACHE):
        try:
            sid = json.load(open(SESSION_CACHE, encoding="utf-8"))["sessionId"]
            items = rpc("session.list", {})["items"]
            if any(i["sessionId"] == sid for i in items):
                return sid
        except Exception:
            pass
    # 继承最近活跃的非空白会话
    try:
        items = rpc("session.list", {})["items"]
        cands = [i for i in items if not i.get("blank")]
        if cands:
            cands.sort(key=lambda i: i.get("updatedAt") or 0, reverse=True)
            sid = cands[0]["sessionId"]
            with open(SESSION_CACHE, "w", encoding="utf-8") as f:
                json.dump({"sessionId": sid}, f, ensure_ascii=False)
            print(f"[voice] 继承最近会话 {sid}")
            return sid
    except Exception:
        pass
    created = rpc("session.create", {"cwd": WORKSPACE, "agentPreset": "standard"})
    sid = created["sessionId"]
    with open(SESSION_CACHE, "w", encoding="utf-8") as f:
        json.dump({"sessionId": sid}, f, ensure_ascii=False)
    print(f"[voice] 新建语音会话 {sid}")
    return sid

COMPLEX_KEYWORDS = [
    "帮我", "写", "生成", "创建", "修改", "删除", "安装", "配置", "分析", "整理",
    "查找", "下载", "打包", "重构", "实现", "开发", "代码", "脚本", "任务", "项目",
    "总结", "报告", "检查", "修复", "测试", "部署", "提交", "文档", "翻译", "列表",
]


def classify_task(text):
    """智能任务分类：simple（快问快答）或 complex（长任务）。
    complex → 交给 DSH 主会话（长上下文能力全）；simple → 桌宠轻量会话快答出声。"""
    t = (text or "").strip()
    if not t:
        return "simple"
    if len(t) > 40:
        return "complex"
    for kw in COMPLEX_KEYWORDS:
        if kw in t:
            return "complex"
    return "simple"


# ── 定时提醒 ──────────────────────────────────────────────────────
REMINDS_FILE = os.path.join(BASE_DIR, ".reminders.json")
TODO_TASKS_FILE = r"D:\1_claude尝试\desktop-todo-widget\data\tasks.json"


def parse_reminder(text):
    """解析定时提醒。返回 (due: datetime, content: str) 或 None。
    支持：N秒/分钟/小时后、明天X点[X分]、[今天/下午/晚上]X点[X分]。"""
    import re
    from datetime import datetime, timedelta
    t = (text or "").strip()
    if not any(k in t for k in ("提醒", "定时", "到点", "记得", "叫我")):
        return None
    now = datetime.now()
    due = None
    expr = None
    def _minutes(m2):
        if not m2:
            return 0
        if m2 == "半":
            return 30
        return int(m2)

    m = re.search(r"(\d+)\s*(秒|分钟|小时)\s*后", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        expr = m.group(0)
        delta = {"秒": timedelta(seconds=n), "分钟": timedelta(minutes=n), "小时": timedelta(hours=n)}[unit]
        due = now + delta
    else:
        m = re.search(r"明天\s*(\d{1,2})\s*点\s*(半|(?:\d{1,2})\s*分?)?", t)
        if m:
            expr = m.group(0)
            day = now + timedelta(days=1)
            due = day.replace(hour=int(m.group(1)), minute=_minutes(m.group(2)), second=0, microsecond=0)
        else:
            m = re.search(r"(?:今天|下午|晚上|早上|上午)?\s*(\d{1,2})\s*点\s*(半|(?:\d{1,2})\s*分?)?", t)
            if m:
                expr = m.group(0)
                hour = int(m.group(1))
                if ("下午" in t or "晚上" in t) and hour < 12:
                    hour += 12
                due = now.replace(hour=hour, minute=_minutes(m.group(2)), second=0, microsecond=0)
                if due < now:
                    due += timedelta(days=1)
    # "明天xxx"（无具体时间）→ 默认明天 9 点
    if due is None:
        m = re.search(r"明天", t)
        if m:
            expr = "明天"
            due = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    if due is None:
        return None
    content = t
    if expr:
        content = content.replace(expr, "")
    content = re.sub(r"^(请|麻烦|帮我|记得)?(提醒我|提醒|定时)?", "", content)
    content = content.strip(" 的，。！!?？")
    if not content:
        content = "定时提醒"
    return due, content


def add_todo_reminder(due_iso, content):
    """写入桌面待办 tasks.json（第一页追加，待办 app 每秒自动重载）。"""
    with open(TODO_TASKS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    pages = data.setdefault("pages", [])
    if not pages:
        pages.append({"name": "提醒", "tasks": []})
    page = pages[0]
    tasks = page.setdefault("tasks", [])
    new_id = max([t.get("id", 0) for t in tasks] + [0]) + 1
    tasks.append({"id": new_id, "content": content, "done": False,
                  "due": due_iso, "recurring": ""})
    with open(TODO_TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_reminder(due, content):
    """设置提醒：本地持久化（桌宠到点出声）+ 写入待办 app。"""
    data = []
    if os.path.exists(REMINDS_FILE):
        try:
            with open(REMINDS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = []
    data.append({"due": due.timestamp(), "content": content})
    with open(REMINDS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        add_todo_reminder(due.strftime("%Y-%m-%dT%H:%M:%S"), content)
    except Exception as e:
        print("[voice] 写待办失败:", e)


def pop_due_reminders():
    """返回并移除已到期的本地提醒（桌宠出声用）。"""
    if not os.path.exists(REMINDS_FILE):
        return []
    try:
        with open(REMINDS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    now = time.time()
    due = [r["content"] for r in data if r.get("due", 0) <= now]
    rest = [r for r in data if r.get("due", 0) > now]
    with open(REMINDS_FILE, "w", encoding="utf-8") as f:
        json.dump(rest, f, ensure_ascii=False, indent=2)
    return due


def ensure_voice_sessions():
    """双会话：(gui 主会话, 轻量语音会话)。
    gui：最近活跃的 DSH 会话（长任务用，继承用户对话）
    voice：桌宠专用轻量会话（简单问答用，短上下文回答快）"""
    cache = {}
    if os.path.exists(SESSION_CACHE):
        try:
            cache = json.load(open(SESSION_CACHE, encoding="utf-8"))
            if not isinstance(cache, dict):
                cache = {"gui": cache}
        except Exception:
            cache = {}
    items = []
    try:
        items = rpc("session.list", {})["items"]
    except Exception:
        pass
    ids = {i["sessionId"] for i in items}

    # gui：缓存 → 最近活跃非空白
    gui = cache.get("gui") if cache.get("gui") in ids else None
    if not gui:
        cands = [i for i in items if not i.get("blank")]
        if cands:
            cands.sort(key=lambda i: i.get("updatedAt") or 0, reverse=True)
            gui = cands[0]["sessionId"]
        else:
            gui = rpc("session.create", {"cwd": WORKSPACE, "agentPreset": "standard"})["sessionId"]

    # voice 轻量：缓存 → 新建（minimal 极简模式：思考少、回答快）
    voice = cache.get("voice") if cache.get("voice") in ids else None
    if not voice:
        created = rpc("session.create", {"cwd": WORKSPACE, "agentPreset": "minimal"})
        voice = created["sessionId"]
        print(f"[voice] 新建轻量语音会话（极简模式） {voice}")

    with open(SESSION_CACHE, "w", encoding="utf-8") as f:
        json.dump({"gui": gui, "voice": voice}, f, ensure_ascii=False)
    return gui, voice


def ask(session_id, text, timeout=180, on_status=None):
    """提交问题，只收集提问后新增的 assistant 文本，拼接成回答。
    on_status(msg)：每轮进度回调（思考中/工具调用/进度符号），供界面显示。"""
    def status(msg):
        if on_status:
            on_status(msg)
        else:
            # 控制台打印：reasoning 元组只打印文本片段
            if isinstance(msg, tuple) and msg and msg[0] == "reasoning":
                print(msg[1], end="", flush=True)
            else:
                print(msg, end="", flush=True)

    # 提交前记录基线 seq（历史里已有的事件不属于本次回答）
    try:
        base = rpc("session.history", {"sessionId": session_id, "maxMessages": 8})
        base_events = [e["event"] for e in base["events"]]
        baseline = max((e["seq"] for e in base_events), default=-1)
    except Exception:
        baseline = -1

    try:
        rpc("session.prompt", {"sessionId": session_id, "mode": "queue",
                               "content": [{"type": "text", "text": text}]})
    except Exception as e:
        status(f"\n[voice] 提交失败: {e}\n")
        return ""
    print(f"[voice] 已提问：{text}")
    status("\n[voice] AI 思考中")

    seen = baseline
    parts = []
    deadline = time.time() + timeout
    quiet = 0
    elapsed_mark = 0
    while time.time() < deadline:
        time.sleep(1.5)
        try:
            h = rpc("session.history", {"sessionId": session_id, "maxMessages": 200})
        except Exception as e:
            status(f"\n[voice] 轮询异常: {e}\n")
            break
        events = [e["event"] for e in h["events"]]
        new = [e for e in events if e["seq"] > seen]
        if new:
            seen = max(e["seq"] for e in new)
            quiet = 0
            for e in sorted(new, key=lambda x: x["seq"]):
                if e["type"] == "assistant/chunk":
                    c = e.get("data", {}).get("chunk", {})
                    ctype = c.get("type")
                    if ctype == "text-delta" and c.get("text"):
                        parts.append(c["text"])
                    elif ctype == "reasoning-delta" and c.get("text"):
                        # 实时思考流：桌面宠气泡滚动显示
                        status(("reasoning", c["text"]))
                elif e["type"] == "tool/call":
                    status(" [调用工具]")
                elif e["type"] == "tool/result":
                    status(" [工具返回]")
                elif e["type"] == "turn/end":
                    # 回合真正结束（agent 可能多 step：思考→工具→回答，只认 turn/end）
                    status(" [完成]\n")
                    return "".join(parts).strip()
            status("+")  # 有新事件（思考在推进）
        else:
            quiet += 1
            if parts and quiet >= 6:
                status(" [静默结束]\n")
                return "".join(parts).strip()
            # 每 ~6 轮（9 秒）报一次已等待时长
            if quiet % 6 == 0:
                elapsed_mark = int(time.time() - deadline + timeout)
                status(f" [已等{elapsed_mark}s]")
            else:
                status(".")
    status("\n[voice] 超时，返回已有内容\n")
    return "".join(parts).strip()

def record(duration=5, sr=16000):
    import sounddevice as sd
    import numpy as np
    print(f"[voice] 录音 {duration} 秒，请说话…")
    audio = sd.rec(int(duration * sr), samplerate=sr, channels=1, dtype="int16")
    sd.wait()
    return audio.flatten(), sr

def record_vad(max_seconds=600, silence_seconds=1.2, sr=16000, on_state=None):
    """PyAudio 录音 + 静音检测：有声音就一直录，静默 silence_seconds 自动停止。
    max_seconds 只是异常兜底（10 分钟），正常不受限。
    返回 (numpy int16, sr)；失败返回 (None, sr)。on_state(msg) 说话状态回调。"""
    import pyaudio
    import numpy as np

    def state(msg):
        if on_state:
            on_state(msg)
        else:
            print(msg, flush=True)

    CHUNK = 1024
    p = pyaudio.PyAudio()
    try:
        stream = p.open(format=pyaudio.paInt16, channels=1, rate=sr,
                        input=True, frames_per_buffer=CHUNK)
    except Exception as e:
        state(f"[voice] 麦克风打开失败: {e}")
        try:
            p.terminate()
        except Exception:
            pass
        return None, sr

    frames = []
    state("[voice] 🎤 我在听，请说话…")
    talking = False
    silence = 0
    start_silence = 0
    noise = 0.0
    threshold = 100.0
    max_chunks = int(sr / CHUNK * max_seconds)
    silence_need = int(silence_seconds * sr / CHUNK)
    start_need = int(4.0 * sr / CHUNK)  # 4 秒内没开口就自动停
    try:
        while len(frames) < max_chunks:
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
            except Exception:
                break
            a = np.frombuffer(data, dtype=np.int16).astype("float32")
            rms = float(np.sqrt(np.mean(a * a)))
            frames.append(data)
            if rms >= threshold:  # 超过背景噪声阈值才算说话（自适应，轻声也能触发）
                if not talking:
                    talking = True
                    state("[voice] 🗣️ 正在说话…")
                silence = 0
                start_silence = 0
            elif talking:
                silence += 1
                if silence >= silence_need:
                    break
            else:
                # 还没开口：动态估计背景噪声，4 秒无语音就停止
                noise = noise * 0.95 + rms * 0.05 if noise else rms
                threshold = max(100.0, noise * 2.5)
                start_silence += 1
                if start_silence >= start_need:
                    break
    finally:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        try:
            p.terminate()
        except Exception:
            pass
    state("[voice] ✓ 录音结束")
    if not frames:
        return None, sr
    return np.frombuffer(b"".join(frames), dtype=np.int16), sr

def recognize_sapi(timeout_sec=8):
    """Windows 内置语音识别（System.Speech，零依赖，实时，说完自动返回）。
    用户机器已验证可用；返回识别文本（去空格），失败/为空返回 None。"""
    ps_script = (
        "Add-Type -AssemblyName System.Speech\n"
        "try { $culture = [System.Globalization.CultureInfo]::GetCultureInfo(\"zh-CN\") } catch { exit 1 }\n"
        "$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($culture)\n"
        "$dictation = New-Object System.Speech.Recognition.DictationGrammar\n"
        "$engine.LoadGrammar($dictation)\n"
        "try { $engine.SetInputToDefaultAudioDevice() } catch { exit 2 }\n"
        "$result = $engine.Recognize([TimeSpan]::FromSeconds(%d))\n"
        "if ($result) { $result.Text } else { '' }" % timeout_sec
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=timeout_sec + 8,
            stdin=subprocess.DEVNULL,
        )
        text = proc.stdout.strip()
        if proc.returncode != 0:
            print(f"[voice] SAPI 不可用 (rc={proc.returncode})")
            return None
        if text:
            return text.replace(" ", "")
        print("[voice] SAPI 未识别到内容")
        return None
    except Exception as e:
        print(f"[voice] SAPI 异常: {e}")
        return None

def amplify(audio, target_peak=0.7, max_gain=20.0):
    """自动增益：把过小的录音放大（最多 20 倍），改善小声/轻声识别。返回 int16 数组。"""
    import numpy as np
    x = audio.astype("float32")
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak < 1:
        return audio
    gain = min(target_peak * 32767 / peak, max_gain)
    if gain <= 1.0:
        return audio
    return np.clip(x * gain, -32767, 32767).astype("int16")

def recognize_whisper(audio, sr, model_name="base", on_state=None):
    """whisper 本地模型识别（准确率高）。on_state(msg) 进度回调。"""
    def state(msg):
        if on_state:
            on_state(msg)
        else:
            print(msg, flush=True)

    global _whisper_model
    from faster_whisper import WhisperModel
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    if _whisper_model is None:
        state("[voice] 加载识别模型…")
        path = model_name
        for cand in (os.path.join(BASE_DIR, "whisper-small"),
                     os.path.join(BASE_DIR, "whisper-base")):
            if os.path.isdir(cand):
                path = cand
                break
        _whisper_model = WhisperModel(path, device="cpu", compute_type="int8")
    state("[voice] 识别中…")
    audio = amplify(audio)                     # 自动增益：小声也能听清
    audio_f = audio.astype("float32") / 32768.0  # vad_filter 需要 float32
    segments, _ = _whisper_model.transcribe(
        audio_f, language="zh", beam_size=5,
        vad_filter=True,                       # Silero VAD：过滤前后静音
        initial_prompt="以下是普通话的句子。",
    )
    return "".join(s.text for s in segments).strip()

def recognize(audio, sr, model_name="base", prefer_sapi=False):
    """识别：优先 whisper（本地模型，准确率高），失败则 Windows SAPI 兜底。"""
    if audio is not None and len(audio) > 0:
        text = recognize_whisper(audio, sr, model_name)
        if text:
            return text
        print("[voice] whisper 未识别到，改用 SAPI…")
    return recognize_sapi()

def speak(text):
    """播报：优先 edge-tts（多音色自然中文），失败回退系统语音。"""
    if not text:
        return
    print(f"[voice] AI 回答：{text}")
    spoken = clean_for_speech(text) or "好的"
    edge_script = os.path.join(BASE_DIR, "speak_edge.py")
    if os.path.exists(edge_script):
        try:
            r = subprocess.run([sys.executable, edge_script, spoken], timeout=120)
            if r.returncode == 0:
                return
        except Exception:
            pass
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", SPEAK_PS1, spoken], timeout=180)

def append_history(role, text):
    """记录一轮对话到 history.jsonl（前端用）与 conversations/<日期>.md。"""
    entry = {"time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "role": role, "text": text}
    with open(os.path.join(BASE_DIR, "history.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    conv = os.path.join(BASE_DIR, "conversations")
    os.makedirs(conv, exist_ok=True)
    with open(os.path.join(conv, datetime.datetime.now().strftime("%Y-%m-%d") + ".md"),
              "a", encoding="utf-8") as f:
        who = "你" if role == "user" else "AI"
        f.write(f"**{who}** ({entry['time']}): {text}\n\n")

def main():
    ap = argparse.ArgumentParser(description="语音问答：你说话，AI 用语音回答")
    ap.add_argument("--text", default=None, help="直接文本提问（跳过录音）")
    ap.add_argument("--listen", type=int, default=5, help="录音秒数")
    ap.add_argument("--model", default="base", help="whisper 模型 base/small/medium")
    args = ap.parse_args()
    sid = ensure_session()
    if args.text:
        text = args.text
    else:
        input("[voice] 按回车开始录音…")
        audio, sr = record(args.listen)
        text = recognize(audio, sr, args.model)
        print(f"[voice] 识别到：{text}")
        if not text:
            print("[voice] 没听清，请重试")
            return
    answer = ask(sid, text)
    if not answer:
        print("[voice] 没拿到回答（可能超时）")
        return
    append_history("user", text)
    append_history("ai", answer)
    speak(answer)

if __name__ == "__main__":
    main()
