# -*- coding: utf-8 -*-
"""
语音问答核心：你说话 → AI 用语音回答（复用正在运行的 DSH web 会话，问 AI 答）
用法:
  python voice_chat.py --text "问题"   # 文本问答（测试用）
  python voice_chat.py                 # 回车后录音5秒再问答
被 voice_assistant.py（热键启动器）复用。
依赖: sounddevice numpy faster-whisper（模型已放 whisper-base/ 本地目录）
"""
import io, sys, os, json, time, uuid, datetime, subprocess, urllib.request, argparse, re, queue, threading

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


BASE_DIR = os.path.dirname(os.path.abspath(__file__))   # D:\1_claude_try\桌宠\voice
_WS_PARENT = os.path.dirname(BASE_DIR)                   # D:\1_claude_try\桌宠
# 源码收进「桌宠」容器目录后，工作区要再上一级
WORKSPACE = os.path.dirname(_WS_PARENT) if os.path.basename(_WS_PARENT) == "桌宠" else _WS_PARENT
SESSION_CACHE = os.path.join(BASE_DIR, ".voice_session.json")
SPEAK_PS1 = os.path.join(BASE_DIR, "speak.ps1")
DSH_URL = "http://127.0.0.1:3080"   # 旧版（web 构建）默认端口；发现失败时回退
DESKTOP_PORT = 19387                # 桌面端固定端口（其 bundle 内写死）
_LOG_CANDIDATES = (
    os.path.join(BASE_DIR, "dsh-desktop.log"),
    os.path.join(WORKSPACE, "桌宠", "voice", "dsh-desktop.log"),
    os.path.join(WORKSPACE, "deepseek-harness-next", "dsh-runtime.log"),
    os.path.join(WORKSPACE, "deepseek-harness", "dsh-runtime.log"),
)
_ENDPOINT = {"base": None, "token": None, "stamp": None, "log": None}
_PROBE = {"base": None, "ts": 0.0}
_WARNED = set()


def _discover_endpoint():
    """委托给 dsh_probe：从日志找 (base, token)，只认端口仍活着的。"""
    try:
        import dsh_probe
        b, t = dsh_probe.from_log()
        return {"base": b, "token": t}
    except Exception:
        return None


def _alive(base, timeout=2):
    """地址是否活着（200 或 401 都算活：401 说明服务在但需要 token）。"""
    try:
        urllib.request.urlopen(base + "/", timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def _base():
    """当前 DSH base URL：日志 -> 环境变量 -> 端口扫描 -> 回退 3080。"""
    try:
        import dsh_probe
        b, _t = dsh_probe.discover()
        if b:
            return b
    except Exception:
        pass
    return DSH_URL


def _is_next():
    """探测 DSH 版本：新版根路径 401（需认证），旧版 200。"""
    if _API["next"] is None:
        try:
            req = urllib.request.Request(_base() + "/", method="GET")
            with urllib.request.urlopen(req, timeout=5):
                _API["next"] = False
        except urllib.error.HTTPError as e:
            _API["next"] = (e.code == 401)
        except Exception:
            _API["next"] = False
    return bool(_API["next"])


def _warn(msg, key):
    """同一条提示只印一次，避免每次调用都把 pet.log 刷爆。"""
    if key in _WARNED:
        return
    _WARNED.add(key)
    print("[voice] " + msg)


def _warn_no_token():
    have = [lg for lg in _LOG_CANDIDATES if os.path.exists(lg)]
    _warn("未找到启动日志 token：当前桌面端像是双击启动的（stdout 没进日志）→ F2 会 401。"
          "请用 voice\\launch_desktop.py 启动/重启桌面端。已查日志: "
          + ("; ".join(have) if have else "(都不存在)"), "no-token")


def _warn_stale_token(ep=None):
    lg = (ep or {}).get("log")
    _warn("启动日志里的 token 已失效（401）：当前桌面端不是带日志启动的。"
          "请用 voice\\launch_desktop.py 重启桌面端。" + ("日志: " + lg if lg else ""),
          "stale-token:" + str(lg))


def _next_cookie():
    """新版认证：读 DSH 启动日志里的 token，用 /?token= 换会话 cookie。"""
    now = time.time()
    if _COOKIE["header"] and now - _COOKIE["ts"] < 600:
        return _COOKIE["header"]
    ep = _discover_endpoint() or {}
    base = ep.get("base") or _base()
    tok = ep.get("token") if (ep.get("base") and _alive(ep["base"])) else None
    if not tok:
        # 没有可用 token（桌面端未带日志启动）：走探活到的地址，由调用方收到 401
        _warn_no_token()
        _base()
        return None
    if _COOKIE.get("base") != base:
        _COOKIE["header"] = None      # cookie 绑定 host+port，端口变了要重新换
    try:
        import http.cookiejar
        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        op.open(base + "/?token=" + tok, timeout=20)
        for c in jar:
            _COOKIE["header"] = "%s=%s" % (c.name, c.value)
            _COOKIE["ts"] = now
            _COOKIE["base"] = base
            return _COOKIE["header"]
    except Exception as e:
        if getattr(e, "code", None) == 401:
            _warn_stale_token(ep)
        print("[voice] DSH 认证失败:", e)
    return None


def rpc(method, payload):
    """调用 DSH API（自动兼容新版/旧版协议）。"""
    if _is_next():
        if method == "session.history":   # 新版无此方法 → 用 session/page 转换
            sid = payload.get("sessionId")
            mx = payload.get("maxMessages", 200)
            cursor = 0
            try:
                for it in rpc("session.list", {}).get("items", []):
                    if it.get("sessionId") == sid:
                        cursor = int((it.get("projections") or {}).get("asOfSeq") or 0)
                        break
            except Exception:
                pass
            page = rpc("session.page", {"address": {"kind": "session", "sessionId": sid},
                                        "throughSeq": cursor or 1, "maxMessages": mx})
            recs = (page or {}).get("records") or []
            return {"events": [{"event": r.get("event")} for r in recs]}
        ep = method.replace(".", "/")
        args = dict(payload)
        if method == "session.create":
            args["agentPreset"] = _NEXT_PRESET.get(str(args.get("agentPreset")), args.get("agentPreset") or "ptc")
        elif method == "session.prompt":
            args["requestId"] = str(uuid.uuid4())
        body = {"type": "client-request", "rpcId": str(uuid.uuid4()), "method": ep,
                "payload": {"args": {_NEXT_WRAP.get(method, "request"): args}}}
        url = f"{_base()}/api/{ep}"
        hdrs = {"content-type": "application/json"}
        ck = _next_cookie()
        if ck:
            hdrs["cookie"] = ck
    else:
        body = {"type": "client-request", "rpcId": str(uuid.uuid4()),
                "method": method, "payload": payload}
        url = f"{_base()}/api/{method}"
        hdrs = {"content-type": "application/json"}
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=hdrs)
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
                _proj = it.get("projections") or {}
                _vals = _proj.get("values") if isinstance(_proj.get("values"), dict) else _proj
                p = _vals.get("contextPressure") or {}
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

def cancel_session(session_id):
    """取消会话当前正在运行的回合（用于小任务升级到主会话）。"""
    try:
        rpc("session.cancel", {"sessionId": session_id})
        return True
    except Exception as e:
        print("[voice] 取消失败:", e)
        return False


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
_TODO_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                              "DesktopTodoWidget", "data")
TODO_TASKS_FILE = os.path.join(_TODO_DATA_DIR, "tasks.json")

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
    if not os.path.exists(TODO_TASKS_FILE):
        os.makedirs(_TODO_DATA_DIR, exist_ok=True)
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
            gui = _create_session("standard")

    # voice 轻量：缓存 → 新建（minimal 极简模式：思考少、回答快）
    voice = cache.get("voice") if cache.get("voice") in ids else None
    if not voice:
        voice = _create_session("minimal")
        if voice:
            print(f"[voice] 新建轻量语音会话（极简模式） {voice}")

    if gui and voice:
        with open(SESSION_CACHE, "w", encoding="utf-8") as f:
            json.dump({"gui": gui, "voice": voice}, f, ensure_ascii=False)
    else:
        _warn("初始化会话失败：拿不到 DSH 会话（多半是 token 缺失/失效 → F2 会 401）。"
              "桌宠会继续常驻但问不了问题；请用 voice\\launch_desktop.py 重启桌面端，再重启桌宠。",
              "init-sessions-failed")
    return gui, voice


def _create_session(preset):
    """建会话；认证失败（401）等异常不许把桌宠打挂，失败就返回 None。"""
    try:
        return rpc("session.create", {"cwd": WORKSPACE, "agentPreset": preset})["sessionId"]
    except Exception as e:
        if getattr(e, "code", None) == 401:
            _warn_stale_token(_discover_endpoint())
        else:
            print("[voice] 建会话失败:", e)
        return None


def ask(session_id, text, timeout=180, on_status=None, switch_after=None):
    """提交问题，只收集提问后新增的 assistant 文本，拼接成回答。
    on_status(msg)：每轮进度回调（思考中/工具调用/进度符号），供界面显示。
    switch_after：秒数；超过该时长仍无正式回答内容时返回 '__SLOW__'
    （调用方可据此判定任务复杂，转交主会话处理）。"""
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
    started_at = time.time()
    while time.time() < deadline:
        time.sleep(1.5)
        # 时间判定：超过 switch_after 还没有正式回答内容 → 报告为慢任务
        if switch_after is not None and not parts and (time.time() - started_at) > switch_after:
            status("\n[voice] 超过 %ds 仍未完成 → 判定为复杂任务\n" % switch_after)
            return "__SLOW__"
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
                if e["type"] == "assistant/message":
                    # 新版 DSH：助手输出是整段 message（不是 chunk 流）
                    _d = e.get("data") or {}
                    _msg = _d.get("message") or {}
                    _got = False
                    for _p in (_msg.get("content") or []):
                        if isinstance(_p, dict) and _p.get("type") == "text" and _p.get("text"):
                            parts.append(_p["text"])
                            _got = True
                    if not _got:
                        # 兜底：从 stream 字段取增量块
                        _st = _d.get("stream")
                        if isinstance(_st, list):
                            for _c in _st:
                                if isinstance(_c, dict) and _c.get("type") == "text-delta" and _c.get("text"):
                                    parts.append(_c["text"])
                elif e["type"] == "assistant/chunk":
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

_INPUT_DEVICE = {"index": None, "name": None, "rate": None}   # 本次运行已验证可用的输入设备


def _input_candidates(p):
    """候选输入设备顺序：.voice_config.json 的 input_device -> 系统默认 -> 其余设备。
    其余按 DirectSound/WASAPI/MME 排序，WDM-KS 之类排最后（大多打不开）。
    本机默认输入能打开却永不供数，所以不能信默认，必须逐个验证。"""
    cands = []
    try:
        cfg = os.path.join(BASE_DIR, ".voice_config.json")
        if os.path.exists(cfg):
            with open(cfg, encoding="utf-8") as f:
                want = json.load(f).get("input_device")
            if want is not None:
                for i in range(p.get_device_count()):
                    d = p.get_device_info_by_index(i)
                    if d.get("maxInputChannels", 0) <= 0:
                        continue
                    if (isinstance(want, int) and i == want) or \
                       (isinstance(want, str) and want.lower() in str(d.get("name", "")).lower()):
                        cands.append(i)
                        break
    except Exception:
        pass
    try:
        d = p.get_default_input_device_info()
        idx = int(d.get("index"))
        if d.get("maxInputChannels", 0) > 0 and idx not in cands:
            cands.append(idx)
    except Exception:
        pass
    order = {1: 0, 2: 1, 0: 2}
    rest = []
    for i in range(p.get_device_count()):
        try:
            d = p.get_device_info_by_index(i)
        except Exception:
            continue
        if d.get("maxInputChannels", 0) <= 0 or i in cands:
            continue
        rest.append((order.get(d.get("hostApi"), 3), i))
    rest.sort()
    cands += [i for _, i in rest]
    return cands


_DEAD_STREAMS = []   # 死流：故意不关，留给它自己的读线程（关它会和阻塞中的 read 相撞）
_DEAD_DEVICES = set()  # 本次运行确认"能打开但供不出数据"的设备，不再重试
_PA = None           # 全局唯一的 PyAudio 实例：**永不 terminate**


def _pa():
    """全局唯一 PyAudio 实例，绝不 terminate。
    v1.2.4 血案：v1.2.3 每条 F2 都 new 一个 PyAudio 并在 finally 里 terminate，
    而它自己的读线程还阻塞在 stream.read() 里 —— 两个线程在 PortAudio 内部相撞，
    实测访问违例（exit -1073741819 / 0xC0000005，faulthandler 抓到
    read 与 terminate 两线程对峙），按 F2 说完话桌宠立刻闪退。
    结论：实例留着（内存很小）；流的收尾交给它自己的读线程；
    **绝不能在别的线程还阻塞于 read 时 terminate/close**。"""
    global _PA
    if _PA is None:
        import pyaudio
        _PA = pyaudio.PyAudio()
    return _PA


class _Input:
    """一条输入流 + 它的读线程。流的生命周期完全由读线程自己管：
    主线程只通过 stop_evt 请求停止，读线程在 read() 返回后自己 stop/close，
    因此永远不会出现"别的线程正阻塞在 read 里、流却被关掉"的竞态。"""

    def __init__(self, stream, rate, q, stop_evt):
        self.stream = stream
        self.rate = rate
        self.q = q
        self.stop_evt = stop_evt

    def request_stop(self):
        self.stop_evt.set()


def _open_input(p, idx, sr, wait=2.0):
    """打开 idx 并确认真的收到音频块（先按 sr，失败再按设备原生采样率）。
    用【阻塞读 + 读线程 + 队列】：本机 pyaudio 0.2.14 的回调一个都不触发，
    阻塞读却是 0.01 秒就来数据；而 get_read_available() 在本机驱动上恒为 0，
    不能用来探活。成功返回 _Input；失败返回 None（死流故意不关）。"""
    import pyaudio

    if idx is None or idx in _DEAD_DEVICES:
        return None
    rates = [sr]
    try:
        nat = int(p.get_device_info_by_index(idx).get("defaultSampleRate") or 0)
        if nat and nat != sr:
            rates.append(nat)
    except Exception:
        pass
    for rate in rates:
        q = queue.Queue()
        stop_evt = threading.Event()
        st = None
        try:
            st = p.open(format=pyaudio.paInt16, channels=1, rate=rate, input=True,
                        input_device_index=idx, frames_per_buffer=1024)
        except Exception:
            continue

        def reader(st=st, q=q, ev=stop_evt):
            while True:
                try:
                    data = st.read(1024, exception_on_overflow=False)
                except Exception:
                    break
                if ev.is_set():
                    break
                if q.qsize() < 400:
                    q.put(data)
            try:
                st.stop_stream()      # 由读线程自己收尾：此刻它已不在 read 里 → 安全
                st.close()
            except Exception:
                pass

        threading.Thread(target=reader, daemon=True).start()
        try:
            q.get(timeout=wait)
            return _Input(st, rate, q, stop_evt)
        except queue.Empty:
            _DEAD_STREAMS.append(st)   # 供不出数据：留给它的读线程，别碰
            _DEAD_DEVICES.add(idx)
            continue
    _DEAD_DEVICES.add(idx)
    return None


def record_vad(max_seconds=600, silence_seconds=1.2, sr=16000, on_state=None):
    """PyAudio 录音 + 静音检测：有声音就一直录，静默 silence_seconds 自动停止。
    max_seconds 只是异常兜底（10 分钟），正常不受限。
    关键修复：旧实现用阻塞的 stream.read()，遇到"能打开但永不供数"的设备会永远卡在
    "我在听"（本机默认输入就是这种）；上一版用读线程 + 队列解了这个，
    但退出时在有线程阻塞于 read() 的情况下 terminate/close → 必崩（见 _pa 注释）。
    本版：全局唯一 PyAudio 永不 terminate；设备先用非阻塞 get_read_available() 验活；
    录音用「读前查可读帧数」的阻塞读 —— **全程不用后台线程**，所以能安全 stop/close。
    返回 (numpy int16, sr)；失败返回 (None, sr)。on_state(msg) 说话状态回调。"""
    import numpy as np

    def state(msg):
        if on_state:
            on_state(msg)
        else:
            print(msg, flush=True)

    CHUNK = 1024
    inp = None
    use_rate = sr
    try:
        p = _pa()
        if _INPUT_DEVICE["index"] is not None:          # 复用本次运行已验证的设备
            inp = _open_input(p, _INPUT_DEVICE["index"], sr)
            if inp is None:
                _INPUT_DEVICE.update({"index": None, "name": None, "rate": None})
            else:
                use_rate = inp.rate
        if inp is None:
            for idx in _input_candidates(p):
                if idx in _DEAD_DEVICES:
                    continue
                got = _open_input(p, idx, sr)
                if got is None:
                    continue
                try:
                    name = p.get_device_info_by_index(idx).get("name")
                except Exception:
                    name = "?"
                inp, use_rate = got, got.rate
                _INPUT_DEVICE.update({"index": idx, "name": name, "rate": got.rate})
                state("[voice] 输入设备 #%d %s @%dHz" % (idx, name, got.rate))
                break
        if inp is None:
            state("[voice] ❌ 所有输入设备都收不到音频数据（能打开但没有音频块）："
                  "请换一个系统默认输入设备，或在 .voice_config.json 里加 input_device。")
            return None, sr

        frames = []
        state("[voice] 🎤 我在听，请说话…")
        talking = False
        silence = 0
        start_silence = 0
        noise = 0.0
        threshold = 100.0
        max_chunks = int(use_rate / CHUNK * max_seconds)
        silence_need = max(1, int(silence_seconds * use_rate / CHUNK))
        start_need = max(1, int(4.0 * use_rate / CHUNK))  # 4 秒内没开口就自动停
        starved = 0.0
        while len(frames) < max_chunks:
            try:
                data = inp.q.get(timeout=0.5)
            except queue.Empty:
                data = None
            if data is None:
                starved += 0.5
                if starved >= 3.0:
                    state("[voice] ⚠️ 3 秒收不到音频块，录音中止（设备被占用或已失效）")
                    if _INPUT_DEVICE.get("index") is not None:
                        _DEAD_DEVICES.add(_INPUT_DEVICE["index"])
                    break
                continue
            starved = 0.0
            a = np.frombuffer(data, dtype=np.int16).astype("float32")
            rms = float(np.sqrt(np.mean(a * a))) if a.size else 0.0
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
    except BaseException as e:      # 兜底：任何异常只记日志，绝不把桌宠打死
        state("[voice] ⚠️ 录音异常（已捕获，桌宠继续运行）：%s: %s" % (type(e).__name__, e))
        return None, sr
    finally:
        if inp is not None:
            inp.request_stop()      # 只请求停止：流的 stop/close 由读线程自己收尾
    state("[voice] ✓ 录音结束")
    if not frames:
        return None, sr
    audio = np.frombuffer(b"".join(frames), dtype=np.int16)
    if use_rate != sr and audio.size:      # 用设备原生采样率录的 → 重采样到 sr
        n = max(1, int(audio.size * sr / float(use_rate)))
        audio = np.interp(np.linspace(0, audio.size - 1, n),
                          np.arange(audio.size),
                          audio.astype("float32")).astype("int16")
    return audio, sr

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
