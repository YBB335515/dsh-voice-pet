# -*- coding: utf-8 -*-
"""edge-tts 播报：合成 mp3 → Windows mciSendString 播放（零额外依赖，可被 kill 打断）。
失败自动回退系统语音（SAPI），保证有声。
用法: python speak_edge.py "文本" [音色名]
音色: zh-CN-XiaoxiaoNeural(晓晓·女) / zh-CN-YunxiNeural(云希·男) /
      zh-CN-YunyangNeural(云扬·男新闻) / zh-CN-XiaoyiNeural(晓伊·女)"""
import sys, os, asyncio, tempfile, json, ctypes, time, subprocess
import edge_tts

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".voice_config.json")
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


def load_voice():
    try:
        if os.path.exists(CONFIG):
            with open(CONFIG, encoding="utf-8") as f:
                return json.load(f).get("voice", DEFAULT_VOICE)
    except Exception:
        pass
    return DEFAULT_VOICE


async def synth(text, voice, out):
    tts = edge_tts.Communicate(text, voice)
    await tts.save(out)


def play_mp3(path):
    """用 winmm mciSendString 播放 mp3 并等待完成。失败返回 False。"""
    try:
        winmm = ctypes.windll.winmm
        if winmm.mciSendStringW('open "%s" type mpegvideo alias dshv' % path, None, 0, None) != 0:
            return False
        if winmm.mciSendStringW('play dshv', None, 0, None) != 0:
            return False
        for _ in range(400):
            buf = ctypes.create_unicode_buffer(128)
            winmm.mciSendStringW('status dshv mode', buf, 128, None)
            if buf.value.strip() in ('stopped', ''):
                break
            time.sleep(0.3)
        winmm.mciSendStringW('close dshv', None, 0, None)
        return True
    except Exception:
        return False


def sapi_speak(text):
    """Windows 系统语音兜底（保证有声）。"""
    ps = (
        "Add-Type -AssemblyName System.Speech\n"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        "$zh = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'zh-*' } | Select-Object -First 1\n"
        "if ($zh) { $s.SelectVoice($zh.VoiceInfo.Name) }\n"
        "$s.Speak('%s')\n" % text.replace("'", "''")
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=150)
        return True
    except Exception:
        return False


def main():
    text = sys.argv[1] if len(sys.argv) > 1 else ""
    if not text:
        return
    voice = sys.argv[2] if len(sys.argv) > 2 else load_voice()
    try:
        tmp = os.path.join(tempfile.gettempdir(), "dsh_voice_tts.mp3")
        asyncio.run(synth(text, voice, tmp))
        if play_mp3(tmp):
            return
    except Exception:
        pass
    # 兜底：系统语音（保证有声）
    if not sapi_speak(text):
        sys.exit(1)


if __name__ == "__main__":
    main()
