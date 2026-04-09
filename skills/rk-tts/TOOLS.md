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
```

### TTS

- 当用户需要将文字转为音频时使用
在终端中执行以下命令进行音频识别：
```bash
cd ~/.openclaw/workspace/skills/rk-tts/scripts && ./run.sh "text to tts"
```
输出音频保存在`/userdata/output.wav`，已经是绝对路径，直接将其发送给用户

---

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.
