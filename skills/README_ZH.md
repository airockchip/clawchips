# Skills 目录

## 算法能力 Skill


| Skill | 目录 | 功能 | 典型用途 |
|---|---|---|---|
| `rk-asr` | `rk-asr/` | 将音频文件转写为文本。 | 用于语音转文字、音频转录或语音识别结果获取。 |
| `rk-tts` | `rk-tts/` | 将文本转换为音频。 | 用于文本转语音、生成音频或直接板端朗读文本。 |
| `rk-vl` | `rk-vl/` | 基于VLM模型对图像进行自然语言的目标检测识别，支持摄像头持续监控和提醒。 | 用于从图片或摄像头中检测、监控包裹快递、老人跌到等自然语言描述目标。 |
| `rk-rag` | `rk-rag/` | 构建并查询知识库。 | 用于将文档导入本地向量库，并基于指定知识库进行检索问答。 |
| `rk-meeting-watcher` | `rk-meeting-watcher/` | 实时监听会议语音，通过 ASR 转写内容，匹配配置的关键词，并在命中后发送提醒。 | 用于监听会议中的重要关键词，并及时收到提醒通知。 |


**说明**：所需的算法服务在ClawChips的固件中已经内置，安装环境请参考[ClawChips_Quick_Start](../ClawChips_Quick_Start.md)。

## 其他 Skill


| Skill                     | 目录                         | 功能                                                     | 典型用途                                                            |
| ------------------------- | -------------------------- | ------------------------------------------------------ | --------------------------------------------------------------- |
| `rk-adb`                  | `rk-adb/`                  | 通过本地网络 ADB 或远程 USB-over-SSH 工作流连接 Rockchip Android 设备。 | 用于 `adb shell`、`push/pull`、`logcat`、`setprop`、`reboot` 和设备检查任务。 |
| `rk-model-benchmark`      | `rk-model-benchmark/`      | 在 RK1820、RK1828 等 Rockchip NPU 设备上评测 LLM 和 CNN 模型。     | 用于推理性能测试、FPS 检查、延迟测量、吞吐分析和 NPU 频率实验。                            |
| `rk-hwc-troubleshooting`  | `rk-hwc-troubleshooting/`  | 通过参考资料驱动的排障步骤诊断 Rockchip Android HWC 和显示链路问题。          | 用于黑屏、闪烁、画面异常、掉帧、崩溃以及其他图形或显示问题。                                  |
| `rk-binary-image-decoder` | `rk-binary-image-decoder/` | 根据文件名参数解析原始二进制图像缓冲区，并解码为可查看的图片文件。                      | 用于将 binary dump 转换为图片，便于检查或分享。                                  |
| `rk-remind`               | `rk-remind/`               | 创建定时提醒，并在触发时通过 QQ 发送提醒消息。                              | 用于从 QQ 聊天命令创建一次性或周期性提醒。                                         |


