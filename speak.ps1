# AI 说话脚本（Windows 中文 TTS）
# 用法:
#   powershell -ExecutionPolicy Bypass -File speak.ps1 "要说的内容"
#   powershell -ExecutionPolicy Bypass -File speak.ps1 "你好" -rate 1
param(
    [string]$text = "你好，我是你的 AI 助手。",
    [int]$rate = 0   # -10 .. 10 语速
)
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
# 优先中文语音，没有则用默认
$zh = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'zh-*' } | Select-Object -First 1
if ($zh) { $s.SelectVoice($zh.VoiceInfo.Name) }
$s.Rate = $rate
$s.Speak($text)
Write-Output "SPOKEN: $text"
