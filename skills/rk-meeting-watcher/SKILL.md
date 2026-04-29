---
name: rk-meeting-watcher
description: 当用户说“监听会议”并设置监听关键词时使用。 使用本地 ASR 服务进行会议实时记录，并依据设置的关键词，将相关记录反馈给用户。
---

# 会议监听

自动调用本地麦克风和ASR算法，监听会议，并根据设定的关键词，自动将相关记录发送给用户。

## 启动

根据用户的指令，例如：“请帮我监听会议，关键词：瑞芯微”，提取出关键词“瑞芯微”作为参数输入脚本中：

`cd ~/.openclaw/workspace/skills/rk-meeting-watcher/scripts && ./start.sh "瑞芯微"`

启动成功需回复用户，已开始会议监听。

## 停止

`cd ~/.openclaw/workspace/skills/rk-meeting-watcher/scripts && ./stop.sh`

停止成功需回复用户，已停止会议监听。

