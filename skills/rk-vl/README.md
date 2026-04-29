## RK-VL

### 功能简介

基于VLM（视觉语言大模型）实现自由描述目标检测。支持对单张图片检测“包裹”“快递盒”“桌上的红色杯子”等自然语言目标，也支持持续监控摄像头，在目标出现时通过 QQ 机器人提醒用户。



### 环境要求

需提前启动ModelHub服务，默认地址和模型如下：

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8000
export OPENAI_MODEL=qwen3-vl-2b
```

摄像头检测依赖 Python OpenCV，需在开发板提前安装：

```bash
python3 -m pip install opencv-python
```

监控提醒依赖 `openclaw` 命令和 QQBot 最近联系人记录，默认读取：

```text
~/.openclaw/qqbot/data/known-users.json
```


### 技能配置

将 `SKILL.md` 和 `scripts` 目录，推到 3588 开发板 `~/.openclaw/workspace/skills/rk-vl` 目录下。


### 调用示例

推荐通过 QQ 机器人快速交互使用。

#### 用户输入

1. 指定图片路径做单次检测

- 帮我检测图片 `/home/linaro/Desktop/door.jpg` 里有没有包裹
- 帮我看看 `/tmp/test.jpg` 里有没有桌上的红色杯子

2. 启动摄像头持续监控

- 帮我监控摄像头，有出现包裹的时候提醒我
- 请帮我监控门口有快递盒出现时提醒我
- 帮我盯着摄像头，看到桌上的红色杯子就通知我

3. 停止或查询监控

- 取消摄像头监控
- 停止监控包裹
- 查看当前摄像头监控状态

#### 命令行调用

进入 `scripts` 目录后，可直接执行脚本：

```bash
python3 ./detect_target.py --image /tmp/test.jpg --query "包裹"
```

启动监控：

```bash
./watch.sh start 包裹
```

停止监控：

```bash
./watch.sh stop
```

查询状态：

```bash
./watch.sh status
```



### 输出说明

- 单次检测：标准输出为 JSON；检测到目标时返回 `{"matches":[...]}`，未检测到目标时返回 `{}`。
- 摄像头监控：启动成功后返回当前监控的目标描述，后台持续检测直到用户取消。
- 目标提醒：首次检测到目标会立即提醒；同一目标默认 2 分钟内不重复提醒，持续存在超过 10 分钟会再次提醒。
- 提醒消息会携带检测图片，格式为 `检测到目标“包裹”，图像如下：<qqfile>输出图片绝对路径</qqfile>`。



### 注意事项

- 目标描述不限制固定类别，会原样传给视觉模型。
- 同时只保留一个全局监控任务，新监控会覆盖旧监控。
- 默认每 2 秒检测一次，连续 2 次未命中后视为目标离开。
- 如果需要指定摄像头，可通过 `RK_VL_CAMERA_DEVICE` 或 `RK_VL_CAMERA_INDEX` 配置。
- 如果没有检测到目标，应直接告知未检测到目标，不能虚构结果。
