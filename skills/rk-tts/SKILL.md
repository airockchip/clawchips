---
name: rk-tts
description: 将文本转为音频，并将音频文件发送给用户。
---

# TTS技能

- 当用户需要将文字转为音频时使用
在终端中执行以下命令进行音频识别：
```bash
cd ~/.openclaw/workspace/skills/rk-tts/scripts && ./run.sh "text to tts"
```
输出音频保存在`/userdata/output.wav`，已经是绝对路径，直接将其发送给用户

