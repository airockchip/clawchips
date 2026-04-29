# model_hub_py

`model_hub_py` 是一个RK芯片的设备模型服务调度网关。

它支持：

- 用 YAML 定义设备与多个模型服务
- 按设备与并发度排队提交任务
- 自动执行启动、停止和健康检查命令
- 将 HTTP 请求转发到目标模型服务
- 用 `session_id` 查询状态和获取结果

## 打包

在与 `pyproject.toml` 同级的项目根目录构建可分发的 wheel 与 sdist（版本号见 `[project].version`）。

1. 安装构建工具（若已用 `".[dev]"` 安装过开发依赖，可跳过）：

```bash
python -m pip install build
```

2. 执行构建（若当前在单仓根目录而非本包目录，请先 `cd model_hub_py`）：

```bash
python -m build
```

3. 产物位于 `dist/`：`*-py3-none-any.whl` 与 `*.tar.gz`。将 wheel 拷贝到目标环境后，按上文「安装」一节用 `pip install` 安装即可。

## 安装

安装/更新到系统中

```bash
pip3 install --break-system-packages --force-reinstall model_hub_py-0.1.0-py3-none-any.whl
```

## 配置示例

`devices` 为计算设备/后端列表；每个 `service` 通过 `device` 绑定到某台设备，并配置估计内存占用 `device_memory_usage`（用于准入与同设备上空闲服务回收，详见仓库内 spec）。`device_memory_usage` 内部统一为**字节**：可写十进制整数（字节），或带单位的字符串如 `2GB`、`2048MB`、`512MiB`（1024 进制，与 `KiB/MiB/GiB` 一致）。

```yaml
devices:
  - name: rk3588
    type: rknpu
    max_concurrency: 3
    scheduling_timeout_seconds: 45
    resume_window_seconds: 300

services:
  - name: qwen-local
    device: rk3588
    startup_mode: always
    device_memory_usage: "2048MB"
    base_url: http://127.0.0.1:8001
    start_command: python -m qwen_server --port 8001
    stop_command: pkill -f "qwen_server --port 8001"
    healthcheck_command: curl -fsS http://127.0.0.1:8001/health
    startup_timeout_seconds: 120
    result_ttl_seconds: 60
```

可选设备类型 `type` 见 `model_hub_py.device_factory`（如 `rknpu`、`rk182x` 等，大小写不敏感）。

## 启动服务

```bash
model-hub-py serve --config ./model_hub_config.yaml
```

## Python 客户端示例

```python
from model_hub_py.client import ModelHubPyClient

client = ModelHubPyClient("http://127.0.0.1:8000")

result = client.run(
    "qwen-local",
    method="POST",
    path="/v1/chat/completions",
    json_body={
        "model": "qwen",
        "messages": [{"role": "user", "content": "hello"}],
    },
    timeout=60,
)

print(result)
```
