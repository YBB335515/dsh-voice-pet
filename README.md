# 🐋 dsh-voice-pet — DeepSeek Harness 语音桌宠

一个给 [DeepSeek Harness（DSH）](https://github.com/deepseek-ai/DeepSeek-Harness) 配的**桌面语音助手桌宠**（Windows）。按一下全局热键就能和 AI 说话，AI 思考时桌宠旁边冒泡显示实时推理，任务完成/需要审批时桌宠**开口提醒**你。

> 让 DeepSeek Harness 从"网页里的 AI"变成"坐在你桌角的 AI"。

## ✨ 特性

- 🎙️ **全局 F2 语音对话**：任何界面按下 F2 直接说话（说完停顿自动结束，不限时长），AI 语音回答
- 🖼️ **任意图片做桌宠**：放入 PNG/JPG 即用，自动抠掉白底、按屏幕 DPI 缩放渲染、锐化增强 → 小尺寸也清晰
- 💬 **阶段气泡**：录音/识别/思考/调工具时在桌宠旁冒泡——
  `🎤 我在听，请说话…` → `🗣️ 正在说话…` → `📝 识别中…` → `💭 思考 5s + 实时推理内容` → `🔧 调用工具`
- 📌 **贴边自动收缩**：拖到屏幕左右边缘 70px 内松手 → 吸附并**逆时针旋转 45° 缩进去**（露出约一半）；鼠标移到附近 70px → 滑出来
- 🔄 **对话后自动缩回**：在收缩状态下按 F2 对话，播报结束后会自动缩回边缘
- ⚡ **60 FPS 丝滑动画**：预生成旋转帧，展开 0.48s / 收缩 0.35s，无卡顿
- 🧠 **智能任务分类**：简单问题走轻量会话（极简模式，3 秒内快答出声）；复杂任务自动交给 DSH 主会话（继承你正在用的对话）
- ⏰ **定时提醒**：说"10分钟后提醒我喝水"→ 自动写入**桌面待办事项**（到点弹提醒）+ 桌宠出声提醒
- 🔔 **开口播报通知**：任务完成说"任务完成啦"；需要审批说"有任务需要你确认，去网页点允许"（与微信/手机通知同步）
- 📝 **言简意赅**：语音回答自动限制在 2-3 句内（你打字提问不受此限制）
- 🔄 **同音纠错**：结合上下文自动纠正（如"中术"→"综述"），常用词保护不误改
- 🔊 **多音色播报**：微软 edge-tts（晓晓/云希/云扬/晓伊），断网自动回退系统语音
- 🚀 **开机自启**：等待 DSH 就绪后再拉起桌宠（不会因开机顺序失败）；DSH 未启动时还会自动拉起 DSH
- 📋 **对话记录**：桌宠窗口内记录 + 本地浏览器前端（http://127.0.0.1:8899）

## 🎮 交互一览

| 操作 | 效果 |
|---|---|
| **F2**（全局热键） | 开始说话；播报中按 F2 = 打断并直接说下一句 |
| **F3** | 退出桌宠 |
| **按住拖动** | 移动桌宠；拖到屏幕左右边缘 70px 内松手 → 吸附 + 逆时针 45° 缩进去 |
| **鼠标移到收缩的桌宠附近 70px** | Q 弹滑出，恢复完整形象 |
| **双击桌宠** | 打开对话记录窗口（关闭 = 隐藏） |
| **托盘图标右键** | 显示/隐藏、对话记录、**开机自启**、对话记录前端、退出 |

## 🚀 快速开始

### 前提
- Windows 10/11，Python 3.10+，Node.js 18+
- [DeepSeek Harness](https://github.com/deepseek-ai/DeepSeek-Harness) 已安装并在 **3080 端口**运行

### 方式一：npm 一键

```bash
npx dsh-voice-pet install            # 装 Python 依赖 + 下载语音识别模型
npx dsh-voice-pet start              # 启动桌宠
npx dsh-voice-pet install --autostart  # 附带开机自启
```

### 方式二：源码运行

```bash
git clone https://github.com/YBB335515/dsh-voice-pet
cd dsh-voice-pet
python install.py        # 依赖 + 模型（约 460MB，可用 --model base 换小的）
python voice_gui.py      # 启动桌宠
```

## ⚙️ 配置

配置文件 `.voice_config.json`（放在脚本同目录）：

```json
{
  "voice": "zh-CN-XiaoxiaoNeural",
  "pet_image": "D:\\path\\to\\your\\pet.png",
  "pet_size": 150,
  "pet_crop_top": 0,
  "pet_crop_bottom": 0,
  "pet_crop_right": 0,
  "white_threshold": 244
}
```

| 字段 | 说明 |
|---|---|
| `voice` | TTS 音色：`zh-CN-XiaoxiaoNeural` 晓晓(女) / `YunxiNeural` 云希(男) / `YunyangNeural` 云扬(男) / `XiaoyiNeural` 晓伊(女) |
| `pet_image` | 桌宠形象图片路径（PNG/JPG，白底会自动抠成透明）。**留空则自动用脚本目录下的第一张 PNG/JPG** |
| `pet_size` | 显示尺寸（逻辑像素）。实际按屏幕缩放比例渲染（150% 屏 → 用 1.5 倍物理像素，更清晰） |
| `pet_crop_top` / `pet_crop_bottom` / `pet_crop_right` | 裁掉图片上方/下方/右侧的百分比（用于去掉图上文字、水印等） |
| `white_threshold` | 白底抠图阈值（默认 244）。背景越白可调越高；抠不干净就提高，抠过头就降低 |

### 动画参数（改 `voice_gui.py` 顶部）

```python
EXPAND_FRAMES = 30     # 展开时长 = 30 帧 × 16ms ≈ 0.48 秒
COLLAPSE_FRAMES = 22   # 收缩时长 ≈ 0.35 秒
FRAME_MS = 16          # 帧间隔，16ms ≈ 60 FPS（不建议再小）
DOCK_ANGLE = 45        # 收缩时的旋转角度（正数 = 逆时针）
DOCK_VISIBLE = 100     # 收缩后至少露出多少（逻辑像素）
```

### 任务完成 / 审批播报（可选）

桌宠内置 webhook 接收器（`http://127.0.0.1:8898/hook`）。若你的 DSH 装了通知插件（如 dsh-notify-phone），把它的 `webhookUrl` 指向该地址，DSH 的回合完成/待审批事件就会让桌宠开口播报。

## 🏗️ 工作原理

```
麦克风 → PyAudio 录音（VAD 静音自动停止，不限时长）
      → faster-whisper 本地识别（自动增益，小声也能听清）
      → 同音纠错（上下文词库 .user_vocab.txt）
      → 智能分类：简单 → 轻量会话（minimal） / 复杂 → DSH 主会话
      → DSH HTTP API（session.prompt）提问，轮询 history 获取
        实时推理流（气泡显示）+ 最终回答
      → edge-tts 语音播报（失败回退系统语音）
      → 对话记录：窗口 + history.jsonl + 浏览器前端
```

不修改 DSH 源码：桌宠使用与浏览器相同的 DSH Web API 交互。

## 📁 项目结构

```
dsh-voice-pet/
├── voice_gui.py       # 桌宠主程序（tkinter 窗口 + 托盘 + 全局热键 + 气泡 + 动画 + webhook）
├── voice_chat.py      # 核心库（录音 / 识别 / 纠错 / DSH 问答 / 会话管理 / 定时提醒）
├── speak_edge.py      # edge-tts 多音色播报（含系统语音回退）
├── watcher.py         # 开机自启启动器（等待 DSH 就绪，必要时拉起 DSH）
├── web_server.py      # 对话记录前端服务器（8899）
├── web/index.html     # 对话记录页面
├── install.py         # 一键安装（依赖 + 模型 + 可选自启）
├── cli.js             # npx dsh-voice-pet 命令行
├── scan_devices.py    # 麦克风设备诊断
└── requirements.txt
```

## ❓ 常见问题

- **桌宠很糊**：确认 `pet_size` 不要太小（150 起步），程序已内置高 DPI 渲染 + 锐化；原图分辨率越高越清晰
- **抠图有白边/抠不干净**：调整 `white_threshold`（提高更狠）
- **听不到我说话**：跑 `npx dsh-voice-pet doctor` 扫描麦克风；检查 Windows 麦克风权限
- **识别不准**：清晰慢速说话；说错的同音词先说对一遍（会进词库自动纠错）
- **桌宠不会缩进去**：必须拖到**屏幕左右边缘 70px 内**松手，它才吸附收缩
- **播报没声音**：检查音量；edge-tts 需要网络（无网自动回退系统语音）
- **改了代码没生效**：桌宠是常驻进程，改完要**重启桌宠**（托盘退出 → 重新启动）

## 📄 License

[MIT](LICENSE) © 2026 YBB335515
