# Rockchip NPU 模型性能测试

测试 LLM、CNN 模型在 RK1820/RK1828 设备上的推理性能。

## 使用方法

示例：帮我在12.16.10.15:5555这个adb设备上测试qwen3 0.6b模型在rk1820上面的性能

注意：
1、需要指定用于测试的设备（IP:端口或序列号）
2、需要指定模型名称（会根据名称进行模糊匹配）
3、可以选择性指定RK182X NPU频率、测试循环次数、LLM的输入输出令牌数


## 文件结构

```
rk-model-benchmark/
├── SKILL.md              # Skill 定义文档
├── README.md             # 本文件
└── scripts/
    └── run_benchmark.sh  # 自动化测试脚本
```