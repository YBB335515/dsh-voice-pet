#!/usr/bin/env node
// dsh-voice-pet 命令行工具
const { spawnSync } = require("node:child_process");
const path = require("node:path");

const ROOT = __dirname;
const PY = process.env.PYTHON || "python";
const cmd = process.argv[2] || "help";

function py(args) {
  const r = spawnSync(PY, args, { stdio: "inherit", cwd: ROOT });
  process.exit(r.status ?? 1);
}

switch (cmd) {
  case "install":
    py([path.join(ROOT, "install.py"), ...process.argv.slice(3)]);
    break;
  case "start":
    py([path.join(ROOT, "voice_gui.py")]);
    break;
  case "doctor":
    py([path.join(ROOT, "scan_devices.py")]);
    break;
  default: {
    const help = [
      "dsh-voice-pet — DeepSeek Harness 语音桌宠",
      "",
      "用法:",
      "  npx dsh-voice-pet install             一键安装（Python 依赖 + 语音模型）",
      "  npx dsh-voice-pet install --autostart  安装 + 开机自启",
      "  npx dsh-voice-pet install --model base 用 base 模型（约 145MB，更快）",
      "  npx dsh-voice-pet start               启动鲸鱼桌宠（需 DSH 在 3080 运行）",
      "  npx dsh-voice-pet doctor              诊断（麦克风/音频设备扫描）",
      "",
      "前提:",
      "  1. DeepSeek Harness 已启动（http://127.0.0.1:3080）",
      "  2. Python 3.10+ 在 PATH 中",
      "  3. Windows 10/11（使用系统 TTS 与麦克风）",
    ];
    console.log(help.join("\n"));
  }
}
