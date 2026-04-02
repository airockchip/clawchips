---
name: model-benchmark
description: 当用户询问 RK1820/RK1828 设备上的模型性能、benchmark、推理速度、FPS、TTFT、TPS、吞吐、延迟、单核或多核性能，或要求在板子上实测 LLM、CNN 模型时使用。
---

# Rockchip NPU 模型性能测试技能

此技能只做两件事：

1. 先检查环境
2. 环境满足后执行模型性能测试

## 适用场景

当用户请求以下内容时触发：

- 告诉我某模型性能
- 测试某模型推理速度
- benchmark
- FPS / TTFT / TPS / 吞吐 / 延迟
- 单核 / 8核性能
- 在板子上跑一下某模型

## 支持芯片

| 设备树芯片 | 显示型号 | NPU核心数 | NPU频率范围 |
|-----------|---------|----------|------------|
| RK1820 | RK1820 | 8核 | 400-1000 MHz |
| RK1828 | RK1820 | 8核 | 400-1000 MHz |

说明：RK3588 运行 `rknn3_llm_demo`、`rknn3_cnn_demo` 时统一显示为 RK1820。

## 固定规则

### 1. 只使用以下程序

- LLM：`/userdata/aicp_test_aarch64/rknn3_llm_demo`
- CNN：`/userdata/aicp_test_aarch64/rknn3_cnn_demo`

不要使用 `rknn_benchmark`。

### 2. 设备端固定目录

- 工作目录：`/userdata/aicp_test_aarch64`
- 模型目录：`/userdata/aicp_test_aarch64/models`
- 动态库目录：`/userdata/aicp_test_aarch64/lib`
- 测试图片：`/userdata/aicp_test_aarch64/test.jpg`

### 3. 网络设备地址规则

如果用户提供的是纯 IP，没有端口，必须自动补成 `:5555` 后再执行 `adb connect`。

示例：

- `192.168.1.10` -> `192.168.1.10:5555`
- `192.168.1.10:5555` -> 保持不变

### 4. 固定资源下载前缀

测试程序和 `test.jpg` 使用以下固定前缀下载：

```text
https://ftrg.zbox.filez.com/v2/delivery/userdata/95f00b0fc900458ba134f8b180b3f7a1/claw_agent/skills/rk-model-benchmark/
```

可直接拼接的资源：

- `rknn3_llm_demo`
- `rknn3_cnn_demo`
- `test.jpg`
- `rknn-smi`

模型文件和 `lib` 不走固定前缀下载，缺失时提示用户去网盘手动下载：

```text
https://console.box.lenovo.com/l/wJLAwi 提取码：rknn
```

## 执行流程

## 第一步：检查环境

先检查环境，再决定是否执行测试。

### 1. 检查 adb

```bash
adb version
```

如果失败，提示：

```text
当前环境未安装或无法使用 adb，无法进行设备实测。请先安装 adb 并确认命令可用后再重试。
```

### 2. 确定设备

如果用户指定了设备：

1. 判断是否为纯 IP
2. 如果是纯 IP，自动补 `:5555`
3. 执行连接

```bash
adb connect <device_id>
adb devices
```

如果用户未指定设备：

```bash
adb devices
```

从已连接设备中选择第一个能正常响应 `rknn-smi info` 的设备。

### 3. 检查 NPU

```bash
adb -s <device_id> shell "rknn-smi info"
```

如果命令不存在，先自动下载并安装：

```bash
mkdir -p /tmp/aicp_downloads
curl -L "<prefix>/rknn-smi" -o /tmp/aicp_downloads/rknn-smi
chmod +x /tmp/aicp_downloads/rknn-smi
adb -s <device_id> push /tmp/aicp_downloads/rknn-smi /usr/bin/rknn-smi
adb -s <device_id> shell "chmod +x /usr/bin/rknn-smi"
```

安装后重新执行检查：

```bash
adb -s <device_id> shell "rknn-smi info"
```

如果仍然失败（NPU 驱动不可用），停止测试并提示：

```text
未检测到可用的 RK1820 NPU，当前设备无法执行模型性能测试。
请确认设备已连接、NPU 正常工作，或更换目标设备后重试。
```

### 4. 检查工作目录和依赖

```bash
adb -s <device_id> shell "ls -la /userdata/aicp_test_aarch64"
adb -s <device_id> shell "ls -la /userdata/aicp_test_aarch64/models || mkdir -p /userdata/aicp_test_aarch64/models"
adb -s <device_id> shell "ls -la /userdata/aicp_test_aarch64/lib"
```

如果 `lib` 缺失，停止测试并提示：

```text
设备上缺少运行依赖目录 /userdata/aicp_test_aarch64/lib，无法继续测试。
请从以下地址下载并补齐后重试：
https://console.box.lenovo.com/l/wJLAwi 提取码：rknn
```

### 5. 检查测试程序

```bash
adb -s <device_id> shell "ls -la /userdata/aicp_test_aarch64/rknn3_llm_demo"
adb -s <device_id> shell "ls -la /userdata/aicp_test_aarch64/rknn3_cnn_demo"
```

如果缺失，提示用户可下载并推送：

```text
设备上缺少测试程序。可按固定前缀下载对应文件后推送到 /userdata/aicp_test_aarch64/。
如需代下载，只能下载测试程序和 test.jpg；模型文件与 lib 仍需手动准备。
```

下载示例：

```bash
mkdir -p /tmp/aicp_downloads
curl -L "<prefix>/rknn3_cnn_demo" -o /tmp/aicp_downloads/rknn3_cnn_demo
chmod +x /tmp/aicp_downloads/rknn3_cnn_demo
adb -s <device_id> push /tmp/aicp_downloads/rknn3_cnn_demo /userdata/aicp_test_aarch64/
```

### 6. 检查模型

根据模型类型检查所需文件：

**LLM 模型需要四个文件：**
```bash
adb -s <device_id> shell "ls -la /userdata/aicp_test_aarch64/models/<model_name>.rknn"
adb -s <device_id> shell "ls -la /userdata/aicp_test_aarch64/models/<model_name>.weight"
adb -s <device_id> shell "ls -la /userdata/aicp_test_aarch64/models/<model_name>.tokenizer.gguf"
adb -s <device_id> shell "ls -la /userdata/aicp_test_aarch64/models/<model_name>.embed.bin"
```

如果缺少任何一个文件，提示：
```text
LLM 模型 <model_name> 的文件不完整。需要以下 4 个文件都存在：
1. <model_name>.rknn
2. <model_name>.weight
3. <model_name>.tokenizer.gguf
4. <model_name>.embed.bin

请从以下地址完整下载后推送到 /userdata/aicp_test_aarch64/models/：
https://console.box.lenovo.com/l/wJLAwi 提取码：rknn
```

**CNN 模型需要两个文件：**
```bash
adb -s <device_id> shell "find /userdata/aicp_test_aarch64/models -iname '*<model_name>*' \( -name '*.rknn' -o -name '*.weight' \) 2>/dev/null"
```

如果缺少 `.rknn` 或 `.weight`，提示：
```text
设备上未找到可用模型 <model_name>，当前无法执行测试。
请从以下地址下载模型后推送到 /userdata/aicp_test_aarch64/models/：
https://console.box.lenovo.com/l/wJLAwi 提取码：rknn
```

### 7. CNN 额外检查 `test.jpg`

```bash
adb -s <device_id> shell "ls -la /userdata/aicp_test_aarch64/test.jpg"
```

如果缺失，可按固定前缀下载后推送：

```bash
mkdir -p /tmp/aicp_downloads
curl -L "<prefix>/test.jpg" -o /tmp/aicp_downloads/test.jpg
adb -s <device_id> push /tmp/aicp_downloads/test.jpg /userdata/aicp_test_aarch64/test.jpg
```

### 环境检查结论

**重要提示（特别是 LLM 模型）：**

环境检查务必严格执行以下顺序：
1. ✅ adb 连接正常
2. ✅ NPU 驱动可用（`rknn-smi info` 成功）
3. ✅ `/userdata/aicp_test_aarch64/lib` 目录存在
4. ✅ 测试程序存在（对应的 `rknn3_*_demo`）
5. ✅ **LLM 模型需要 4 个文件完整** (`.rknn` + `.weight` + `.tokenizer.gguf` + `.embed.bin`)

**检查决策：**
- 任一检查不满足：**停止测试**，并明确告诉用户缺什么、怎么补
- 全部满足：进入第二步，执行性能测试

**常见问题排查：**
- LLM 缺少 tokenizer 或 embed 文件 → 测试程序无法启动
- 缺少 `.weight` 文件 → 模型无法加载
- lib 目录缺失 → 动态链接失败，程序无法运行

## 第二步：执行性能测试

### 1. 设置性能模式

```bash
adb -s <device_id> shell "echo performance | tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"
adb -s <device_id> shell "rknn-smi set -d 0 -c 0 -t npu_freq -s 1000"
adb -s <device_id> shell "rknn-smi info"
```

支持的 NPU 频率：`400 600 700 800 900 1000` MHz。

### 2. 按模型类型执行命令

#### LLM

**必需参数：**
- `-m <model>.rknn` - 模型文件（必需）
- `-w <model>.weight` - 权重文件（必需）
- `-tk <model>.tokenizer.gguf` - Tokenizer 文件（**必需**，缺失则无法正确推理）
- `-em <model>.embed.bin` - Embedding 文件（**必需**，缺失则无法正确推理）

**可选参数及默认值：**
- `-ctx 16384` - 上下文长度，默认 16384
- `-ni 128` - 输入令牌数 (Prefill)，默认 128
- `-no 128` - 输出令牌数 (Decode)，默认 128  
- `-cl 3` - 循环测试次数，默认 3

**完整命令：**

```bash
adb -s <device_id> shell "cd /userdata/aicp_test_aarch64 && export LD_LIBRARY_PATH=/userdata/aicp_test_aarch64/lib:\$LD_LIBRARY_PATH && ./rknn3_llm_demo -m ./models/<model>.rknn -w ./models/<model>.weight -tk ./models/<model>.tokenizer.gguf -em ./models/<model>.embed.bin -ctx 16384 -ni 128 -no 128 -cl 3"
```

**注意：** 若缺少 `-tk` 或 `-em`，程序会因为无法加载必要文件而出错。必须确保这两个文件存在于 `/userdata/aicp_test_aarch64/models/` 目录中。

#### CNN

```bash
adb -s <device_id> shell "cd /userdata/aicp_test_aarch64 && export LD_LIBRARY_PATH=/userdata/aicp_test_aarch64/lib:\$LD_LIBRARY_PATH && ./rknn3_cnn_demo -m ./models/<model>.rknn -w ./models/<model>.weight -i /userdata/aicp_test_aarch64/test.jpg -cl <loop_count>"
```

### 3. 可选频率扫描

如果用户要求做频率 benchmark，则对每个频率重复执行：

```bash
adb -s <device_id> shell "rknn-smi set -d 0 -c 0 -t npu_freq -s <freq>"
sleep 1
```

然后重新运行对应模型测试命令并记录结果。

### 4. 核心掩码值参考

RK1820/RK1828 为 8 核，核心掩码参考：

| 核心 | 掩码 |
|------|------|
| 1 | 0x01 |
| 2 | 0x02 |
| 3 | 0x04 |
| 4 | 0x08 |
| 5 | 0x10 |
| 6 | 0x20 |
| 7 | 0x40 |
| 8 | 0x80 |
| 全部 | 0xff |

## 结果提取

从输出中提取关键指标：

- LLM：`TTFT`、`TPOT`、`Prefill`、`TPS`、`TotalTime`、`ModelMemory`、`LoadModelCost`
- CNN：`AvgFPS`、`AvgCost`、`ModelMemory`

结果汇总时优先给出：

1. 测试设备
2. 模型名称
3. NPU 频率
4. 关键性能指标
5. 是否存在环境限制或异常

## 最终执行要求

触发此技能后，必须按以下顺序工作：

1. 先检查环境
2. 环境不满足时，停止测试并给出明确提示
3. 环境满足时，继续执行模型性能测试
4. 输出简洁的性能结果总结

不要跳过环境检查，也不要只讲流程而不实际执行命令。