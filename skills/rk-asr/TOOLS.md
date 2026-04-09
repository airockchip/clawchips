# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

### ASR

- 音频文件常见后缀名如下：
mp3/wav/flac/aac/m4a/ogg/wma/amr/alac

- 当收到音频消息或音频文件并且需要进行转录时使用   
在终端中执行以下命令进行音频识别：
```bash
cd ~/.openclaw/workspace/skills/rk-asr/scripts && ./run.sh "/path/to/audio.wav"
```

执行命令后的处理规则（按顺序匹配，只执行第一个匹配项）：

**情况A - 短音频（输出以"text res:\n"开头）：**
→ 直接输出识别文本

**情况B - 长音频（输出包含"FILE (absolute path): "）：**
→ 提取后面的文件绝对路径
→ 直接将文件发送给用户，使用绝对路径发送文件，严禁读取文件
→ 停止，不要输出任何其他内容

**情况C - 错误：**
→ 输出错误信息

⚠️ 遇到情况B时，绝对禁止：cat、read、显示、预览该文件内容


---

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.
