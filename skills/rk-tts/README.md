## RK-TTS

### 依赖安装

获取打包好的demo，并放在`/userdata/skills/rk-tts/rocktts_linux_aarch64_rk3588`。

该目录中需要包含原有`rocktts_demo`、用于HTTP方式调用的`tts_server`，以及server管理脚本`start_tts_server.sh`和`stop_tts_server.sh`。

可在板端执行如下命令获取

```bash
wget https://ftrg.zbox.filez.com/v2/delivery/data/95f00b0fc900458ba134f8b180b3f7a1/claw_agent/skills/rk-tts/rocktts_linux_aarch64_rk3588.tgz
mkdir -p /userdata/skills/rk-tts
tar zxvf rocktts_linux_aarch64_rk3588.tgz -C /userdata/skills/rk-tts/
```



### skills

将`SKILL.md`和`scripts`目录推到`~/.openclaw/workspace/skills/rk-tts`目录下



### auth

授权工具已打包在rocktts demo目录下(`rocktts_linux_aarch64_rk3588/rkauth_tool_bin`)

- 设备联网后，在板子上执行

```
./rkauth_tool_bin -u xxx -p xxx -o /userdata/key_tts.lic -m tts
```

- 该命令会生成授权文件，确保程序运行时能找到授权配置文件（默认为 ./demo/rocktts_demo/rockx_auth_config.json）



### 调用示例

- 帮我把下面这段话转成音频：
  "夜幕笼罩着古老的城堡，月光透过彩色玻璃在地面投下斑驳光影。主人公手握烛台，沿着螺旋石阶缓缓下行，靴底与青苔覆盖的台阶摩擦发出细微声响"

- 直接朗读这段话：
  "Surely we ought to hold fast to life, for it is wondrous, and full of a beauty that breaks through every pore of God’s own earth."
