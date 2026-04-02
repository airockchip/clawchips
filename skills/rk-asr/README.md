## RK-ASR

### 依赖

音频格式支持依赖ffmpeg，需要现在板子上安装ffmpeg，从而支持多种音频格式。

```bash
sudo apt update
sudo apt install -y ffmpeg
```



### rockasr2 demo

首先获取编译好的rockasr2 demo

```bash
wget https://ftrg.zbox.filez.com/v2/delivery/data/95f00b0fc900458ba134f8b180b3f7a1/claw_agent/skills/rockasr2/rockasr_linux_aarch64_rk3588.zip
unzip rockasr_linux_aarch64_rk3588.zip
```

通过以下路径获取到模型文件，将3588的模型文件放到rockasr_linux_aarch64_rk3588/data目录下

rockasr2_models-rkxxxx_vXXXX.zip （rockasr2模型 链接: https://console.zbox.filez.com/l/bocR0V 提取码: rockasr2models）

`adb push rockasr_linux_aarch64_rk3588 /userdata`

将rockasr2模型和demo推到板子上，目录结构如下：

```bash
$ tree rockasr_linux_aarch64_rk3588
rockasr_linux_aarch64_rk3588
├── data
│   ├── asr_llm.data
│   ├── ctc_embedding.bin
│   ├── hotword_retrieval.data
│   ├── llmasr_adapter.data
│   ├── llmasr.bpe.model
│   ├── llmasr_ctc.data
│   ├── llmasr.rkllm
│   ├── llmasr.tokenizer.gguf
│   ├── llmasr_tokens.txt
│   └── llmasr_wte.bin
├── demo
│   └── rockasr_demo
│       ├── hot_word_list.txt
│       ├── rockasr_demo
│       ├── rockx_auth_config.json
│       └── test_zh_t10.wav
└── lib
    ├── libgomp.so.1
    ├── libonnxruntime.so
    ├── librkllmrt.so
    ├── librknn3_api.so
    ├── librknnrt.so
    ├── librockasr.so
    ├── librocktts.so
    ├── librockx2.so
    └── librockx_modules.so

4 directories, 23 files
```



### skills

将`SKILL.md`和`scripts`目录推到`~/.openclaw/workspace/skills/rk-asr`目录下

由于openclaw默认配置whisper，建议参考`TOOLS.md`将此技能配置为默认ASR技能



### auth

授权工具在ROCKX2_SDK  (ROCKX2：链接: https://console.box.lenovo.com/l/k1qDXS  提取码: rockx)目录下    

- 设备可联网

```
./rkauth_tool_bin -u xxx -p xxx -m asr
```

- 不可联网

  - 获取设备信息

  ```
  adb push rkauth_tool/xxx/xxx/rkdevice_info /data/
  adb shell
  /data/rkdevice_info /data/device.inf
  ```

  将`device.inf`文件拉回PC上

  - PC上执行授权程序

  ```
  rkauth_tool_bin -u <user> -p <password> -o ./key.lic -d device.inf -m asr
  ```

- 该命令会生成授权文件，确保程序运行时能找到授权配置文件（默认为 ./demo/rockx_auth_config.json）

