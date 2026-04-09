## RK-ASR

### 环境要求

音频格式支持依赖ffmpeg，需要现在板子上安装ffmpeg，从而支持多种音频格式。

```bash
sudo apt update
sudo apt install -y ffmpeg
```



### 依赖安装

获取打包好的demo，并放在`/userdata/skills/rk-asr/rockasr_linux_aarch64_rk3588`

可在板端执行如下命令获取

```bash
wget https://ftrg.zbox.filez.com/v2/delivery/data/95f00b0fc900458ba134f8b180b3f7a1/claw_agent/skills/rk-asr/rockasr_linux_aarch64_rk3588.tgz
mkdir -p /userdata/skills/rk-asr
tar zxvf rockasr_linux_aarch64_rk3588.tgz -C /userdata/skills/rk-asr/
```



### skills

将`SKILL.md`和`scripts`目录推到`~/.openclaw/workspace/skills/rk-asr`目录下

由于openclaw默认配置whisper，建议参考当前目录下的`TOOLS.md`，修改openclaw板端配置文件`~/.openclaw/workspace/TOOLS.md`，将此技能配置为默认ASR技能



### auth

授权工具已打包在rockasr2 demo目录下 (`rockasr_linux_aarch64_rk3588/rkauth_tool_bin`)    

- 设备可联网

```
./rkauth_tool_bin -u xxx -p xxx -o /userdata/key_asr.lic -m asr
```

- 该命令会生成授权文件，确保程序运行时能找到授权配置文件（默认为 ./demo/rockx_auth_config.json）



### 调用示例

- 帮我转录音频文件：/home/linaro/Desktop/40s_rkdc.wav
- QQ对话框中发送音频文件

