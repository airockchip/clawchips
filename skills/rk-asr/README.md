## RK-ASR

### 功能简介

实现音频文件语音转录，传入音频文件，即可输出对应的文字识别结果。



### 环境要求

多格式音频解析依赖 FFmpeg，需在开发板提前安装，命令如下：

```bash
sudo apt update
sudo apt install -y ffmpeg
```



### 技能配置

将`SKILL.md`和`scripts`目录，推到3588开发板`~/.openclaw/workspace/skills/rk-asr`目录下



### 调用示例

推荐通过 QQ 机器人快速交互使用。

#### 用户输入

1. 指定板端音频路径

- 帮我转录音频文件：/home/linaro/Desktop/40s_rkdc.wav

2. 直接上传音频

- 在 QQ 聊天框发送音频文件

#### 输出说明

- 短音频（30 秒内）：直接返回纯文字转录结果

- 长音频（超过 30 秒）：生成 TXT 文档并发送，保存完整转录内容

