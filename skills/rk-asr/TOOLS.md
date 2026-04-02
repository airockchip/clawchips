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

- 当收到音频消息或音频文件并且需要进行转录时，使用rk-asr技能，技能详细介绍位于`skills/rk-asr/SKILL.md`
- 音频文件常见后缀名如下：
3gp/3gpp/aa/aac/aax/ac3/acc/act/aif/aifc/aiff/alac/amr/ape/atrac/au/awb/cda/dct/dsf/dff/dss/dts/dvf/eac3/ec3/flac/gsm/iklax/ivs/m4a/m4b/m4p/m4r/mid/midi/mka/mlp/mmf/mov/mp3/mp4/mpc/msv/ogg/oga/opus/ra/ram/raw/rm/rmi/s3m/silk/snd/smv/swf/tak/tta/voc/vox/wav/wma/wmv/wv/wvc/xm

---

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.
