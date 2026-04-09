---
name: model-benchmark
description: 当用户询问 RK1820/RK1828 设备上的模型性能、benchmark、推理速度、FPS、TTFT、TPS、吞吐、延迟、单核或多核性能，或要求在板子上实测 LLM、CNN 模型时使用。
---

## Rockchip NPU 模型性能测试技能

此技能调用自动化脚本完成环境检测与模型性能测试。

## 适用场景

当用户请求以下内容时触发：

- 告诉我某模型性能
- 测试某模型推理速度
- benchmark
- FPS / TTFT / TPS / 吞吐 / 延迟
- 单核 / 8核性能
- 在板子上跑一下某模型

## 执行流程

使用 `{技能目录}/scripts/run_benchmark.sh` 完成测试：

```bash
bash {技能目录}/scripts/run_benchmark.sh \\
  --device <ip[:port]|serial> \
  --model-type <llm|cnn> \
  --model-name <name> \
  [--npu-freq <400|600|700|800|900|1000>] \
  [--loop-count <num>] \
  [--ctx <num>] \
  [--ni <num>] \
  [--no <num>]
```

**参数说明：**

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `--device` | 否 | 自动选择 | 设备 IP 或序列号 |
| `--model-type` | 是 | - | `llm` 或 `cnn` |
| `--model-name` | 是 | - | 模型名称（支持模糊匹配） |
| `--npu-freq` | 否 | 1000 | NPU 频率 (MHz) |
| `--loop-count` | 否 | 3 | CNN 测试循环次数 |
| `--ctx` | 否 | 16384 | LLM 上下文长度 |
| `--ni` | 否 | 128 | LLM 输入令牌数 |
| `--no` | 否 | 128 | LLM 输出令牌数 |

**使用示例：**

```bash
# 执行 CNN 测试
bash {技能目录}/scripts/run_benchmark.sh --device 192.168.1.10 --model-type cnn --model-name yolov5s

# 执行 LLM 测试
bash {技能目录}/scripts/run_benchmark.sh --device 192.168.1.10 --model-type llm --model-name qwen2_1.5b

# 指定 NPU 频率执行测试
bash {技能目录}/scripts/run_benchmark.sh --device 192.168.1.10 --model-type cnn --model-name yolov5s --npu-freq 800
```

## 结果提取

从输出中提取关键指标：

- LLM：`TTFT`、`TPOT`、`Prefill`、`TPS`、`TotalTime`、`ModelMemory`、`LoadModelCost`、`TotalRuns`
- CNN：`AvgFPS`、`AvgCost`、`ModelMemory`、`InferCnt`、`TotalTime`

根据上述指标生成用户友好的性能报告表格，如果没有检测到上述指标，禁止输出任何性能数据，并直接回复测试失败以及可能的原因。