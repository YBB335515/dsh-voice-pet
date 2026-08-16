# 🐋 dsh-voice-pet — DeepSeek Harness 语音桌宠

一个给 [DeepSeek Harness（DSH）](https://github.com/deepseek-ai/DeepSeek-Harness) 配的**桌面语音助手桌宠**（Windows）。按一下全局热键直接和 AI 对话，AI 干活时桌宠冒泡显示实时思考进度，任务完成/需要审批时桌宠**开口说话**提醒你。

> 让 DeepSeek Harness 从"网页里的 AI"变成"坐在你桌角的 AI"。

## ✨ 特性

- 🎙️ **全局 F2 语音对话**：任何界面按下 F2，直接说话，AI 语音回答（多音色 edge-tts，断网自动回退系统语音）
- 🐋 **桌面宠物形态**：透明置顶小鲸鱼，可拖动、状态表情随环节变化
- 💭 **思考气泡**：AI 思考时桌宠旁冒泡，**滚动显示实时推理内容 + 思考计时**，不担心"是不是卡住了"
- ⚡ **智能任务分类**：简单问题轻量会话快速出声；复杂任务自动交给 DSH 主会话（继承你正在用的对话）
- 🔔 **开口提醒**：任务完成桌宠说"任务完成啦"；需要审批桌宠说"有任务需要你确认，去网页点允许"（与微信/手机通知同步）
- 📝 **言简意赅**：语音回答自动限制 2-3 句内（打字提问不受限）
- 🔄 **同音纠错**：结合上下文自动纠正（如"中术"→"综述"），常用词保护不误改
- 🚀 **开机自启**：等待 DSH 就绪后自动拉起（不会因开机顺序失败）
- 📋 **对话记录**：窗口内记录 + 本地浏览器前端（8899 端口）

## 🐋 桌宠状态一览

\`\`\`
🐋 待命 → 🎤 我在听 → 🗣️ 正在说话 → 📝 识别中
     → 🤔 思考（气泡滚动显示推理 + 计时）→ 🔧 调用工具
     → 📢 语音播报 → 🐋 待命
特殊：🔔 需要审批 | 🎉 任务完成 | 😵 出错
\`\`\`

## 🚀 快速开始

### 前提
- Windows 10/11，Python 3.10+，Node.js 18+
- [DeepSeek Harness](https://github.com/deepseek-ai/DeepSeek-Harness) 已安装并在 **3080 端口**运行

### 方式一：npm 一键（推荐）

\`\`\`bash
# 1. 安装（Python 依赖 + 自动下载语音模型）
npx dsh-voice-pet install

# 2. 启动桌宠
npx dsh-voice-pet start

# 可选：安装 + 开机自启
npx dsh-voice-pet install --autostart
\`\`\`

### 方式二：源码运行

\`\`\`bash
git clone https://github.com/YBB335515/dsh-voice-pet
cd dsh-voice-pet
python install.py            # 装依赖 + 下载模型
python voice_gui.py          # 启动桌宠
\`\`\`

## 🎮 使用

| 操作 | 说明 |
|---|---|
| **F2**（全局） | 开始说话（说完停顿自动结束，不限时长）；AI 播报中按 F2 = 打断并直接说下一句 |
| **F3** | 退出 |
| 单击桌宠 | 无操作（防误触） |
| 双击桌宠 | 展开完整对话记录窗口 |
| 按住拖动 | 移动桌宠位置 |
| 托盘图标 | 显示/隐藏、对话记录、开机自启开关、退出 |

## ⚙️ 配置

| 配置 | 位置 | 说明 |
|---|---|---|
| 音色 | \`.voice_config.json\` | \`{"voice": "zh-CN-XiaoxiaoNeural"}\`。可选：Xiaoxiao（晓晓·女）、Yunxi（云希·男）、Yunyang（云扬·男）、Xiaoyi（晓伊·女） |
| 语音模型 | \`install.py --model\` | \`small\`（默认，准）/ \`base\`（快，约 145MB） |
| 同音纠错词库 | \`.user_vocab.txt\` | 自动积累，出现 ≥2 次的词才参与纠错 |
| 对话记录前端 | http://127.0.0.1:8899 | 浏览器自动刷新 |

### 任务完成 / 审批通知（可选）

桌宠内置 webhook 接收器（127.0.0.1:8898）。若你的 DSH 装了通知插件（如 dsh-notify-phone），把它的 \`webhookUrl\` 指向 \`http://127.0.0.1:8898/hook\`，DSH 的回合完成/待审批事件就会让桌宠开口提醒。

## 🏗️ 工作原理

\`\`\`
麦克风 → PyAudio VAD 录音（静音自动停，不限时长）
      → faster-whisper 本地识别（自动增益，小声也能听清）
      → 同音纠错（上下文词库）
      → 智能分类：简单 → 轻量语音会话 / 复杂 → DSH 主会话
      → 通过 DSH HTTP API（session.prompt）提问，轮询 history 获取
        实时思考流（气泡显示）+ 最终回答
      → edge-tts 语音播报（失败回退系统 TTS）
      → 对话记录：窗口 + history.jsonl + 本地前端
\`\`\`

不修改 DSH 源码：桌宠通过 DSH 的 Web API（同浏览器使用的 JSON-RPC 接口）交互。

## 📁 项目结构

\`\`\`
dsh-voice-pet/
├── voice_gui.py        # 桌宠主程序（tkinter + pystray + 全局热键 + webhook）
├── voice_chat.py       # 核心库（录音/识别/纠错/DSH 问答/会话管理）
├── speak_edge.py       # edge-tts 多音色播报（含系统语音回退）
├── watcher.py          # 开机自启启动器（等 DSH 就绪再启动桌宠）
├── web_server.py       # 对话记录前端服务器（8899）
├── web/index.html      # 对话记录页面
├── install.py          # 一键安装（依赖 + 模型 + 自启）
├── cli.js              # npx dsh-voice-pet 命令行
├── scan_devices.py     # 麦克风/音频设备诊断
└── requirements.txt    # Python 依赖
\`\`\`

## ❓ 常见问题

- **听不到我说话**：跑 \`npx dsh-voice-pet doctor\` 扫描麦克风；检查 Windows 麦克风权限
- **识别不准**：大声清晰说；说错过的同音词先说对一遍（进词库自动纠错）；可换 \`small\` 模型
- **没声音**：检查音量；edge-tts 需要网络（无网自动回退系统语音）
- **AI 一直思考**：会话上下文快满时 DSH 会自动压缩（保留记忆）；聊天记录见前端
- **开机自启失败**：确认 DSH 与桌宠都在启动项；watcher 最多等 DSH 10 分钟

## 📄 License

[MIT](LICENSE) © 2026 YBB335515
