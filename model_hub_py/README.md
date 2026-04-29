# model_hub_py

`model_hub_py` is a device-aware model service scheduling gateway for RK chips.

It supports:

- Defining devices and multiple model services with YAML
- Queueing submitted tasks by device and concurrency limits
- Automatically running start, stop, and health check commands
- Forwarding HTTP requests to target model services
- Querying status and fetching results by `session_id`

## Packaging

Build distributable wheel and sdist packages from the project root that contains `pyproject.toml` (the version is defined by `[project].version`).

1. Install the build tool (skip this if you already installed development dependencies with `".[dev]"`):

```bash
python -m pip install build
```

2. Run the build (if you are at the monorepo root instead of this package directory, run `cd model_hub_py` first):

```bash
python -m build
```

3. Artifacts are written to `dist/`: `*-py3-none-any.whl` and `*.tar.gz`. Copy the wheel to the target environment and install it with `pip install` as shown in the Installation section.

## Installation

Install or update the package on the system:

```bash
pip3 install --break-system-packages --force-reinstall model_hub_py-0.1.0-py3-none-any.whl
```

## Configuration Example

`devices` is the list of compute devices or backends. Each `service` is bound to a device through `device` and configures its estimated memory usage with `device_memory_usage` (used for admission control and reclaiming idle services on the same device; see the repository spec for details). Internally, `device_memory_usage` is normalized to **bytes**. It can be written as a decimal integer in bytes, or as a string with units such as `2GB`, `2048MB`, or `512MiB` (`MiB/GiB` use the 1024-based convention, matching `KiB/MiB/GiB`).

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

Available device `type` values are defined in `model_hub_py.device_factory` (for example, `rknpu`, `rk182x`, and others; values are case-insensitive).

## Start The Service

```bash
model-hub-py serve --config ./model_hub_config.yaml
```

## Python Client Example

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
