# RKClawServer

RKClawServer 是一个直接调用 RKNN3 Toolkit Lite 的 OpenAI 兼容推理服务。服务在板端加载 Qwen3 RKNN 模型，通过 GGUF tokenizer 生成模型 prompt 和 token ID，并把 token ID 直接传入 Toolkit Lite 推理，再将结果转换为 OpenAI Chat Completions 响应。

当前提供以下接口：

- `POST /v1/chat/completions`：同步及 SSE 流式对话
- `GET /v1/models`：查询当前模型
- `GET /healthz`：检查 HTTP 进程是否存活
- `GET /readyz`：检查 tokenizer、模型和 RKNN Session 是否初始化完成

首版支持 Qwen3 文本多轮对话、thinking、tools、tool results 和并行 tool calls。暂不支持 `/v1/completions`、多模态、多模型及并发 RKNN Session。

源码发布包可一键生成到默认 `release/` 目录，也可指定输出目录：

```bash
./scripts/package_source.sh
./scripts/package_source.sh --output-dir /path/to/output
./scripts/package_source.sh --output-dir /path/to/output --offline
```

源码包会展开固定版本的 XGrammar，并携带 Linux aarch64/x86_64 Tokenizer 预编译库及其公开源码信息；不会包含模型、Toolkit Lite、产品 wheel/SO、旧 release 或产品指南。

## 1. 构建与板端要求

- RK1820 或 RK1828 aarch64 开发板
- Python 3.11
- 已正常运行 `rknn3.service`
- 与板端 Python ABI 匹配且支持 `session_run(tokens=...)` 的 Toolkit Lite wheel，例如：
  `rknn3_toolkit_lite-1.0.5a1-cp311-cp311-linux_aarch64.whl` 或更新版本
- PC 上安装 `cmake` 和 aarch64 Linux 交叉编译器
- RKNN、weight、embed 和 GGUF tokenizer 必须来自同一次模型导出

本文使用以下实际部署目录。`librkclaw_native.so` 已包含在 gateway wheel 中，不再需要单独安装到 `/usr/lib`：

```text
/userdata/RKClawServer/
├── gateway.toml
├── venv/
├── packages/
│   ├── rk_claw_server-0.3.2-cp311-cp311-linux_aarch64.whl
│   └── rknn3_toolkit_lite-1.0.5a1-cp311-cp311-linux_aarch64.whl
└── logs/gateway.log

/userdata/AgentModel-V3.1-4B-RKNN/
├── AgentModel-V3.1-4B.rknn
├── AgentModel-V3.1-4B.weight
├── AgentModel-V3.1-4B.embed.bin
└── AgentModel-V3.1-4B.tokenizer.gguf
```

## 2. 配置 Gateway

板端配置文件为 `/userdata/RKClawServer/gateway.toml`。部署前先在主机上编辑好工程根目录的 `gateway.toml`，再按第 3 节推送到板端。当前模型可使用：

```toml
[server]
host = "0.0.0.0"
port = 8081
queue_size = 32
enable_streaming = true
sse_heartbeat_interval_s = 20.0

[runtime]
target = "rk1820"
device_id = "0003:31:00.0"
core_mask = 255
native_library = ""

[model]
id = "AgentModel"
rknn_path = "/userdata/AgentModel-V3.1-4B-RKNN/AgentModel-V3.1-4B.rknn"
weight_path = "/userdata/AgentModel-V3.1-4B-RKNN/AgentModel-V3.1-4B.weight"
tokenizer_path = "/userdata/AgentModel-V3.1-4B-RKNN/AgentModel-V3.1-4B.tokenizer.gguf"
embed_path = "/userdata/AgentModel-V3.1-4B-RKNN/AgentModel-V3.1-4B.embed.bin"
chat_template_file = ""
max_context_tokens = 61440
max_new_tokens = 8192
enable_thinking = true

[sampling]
temperature = 0.3
top_p = 0.95
top_k = 1
repeat_penalty = 1.1
frequency_penalty = 0.0
presence_penalty = 0.0

[reasoning]
separate_output = true
fallback_delimiter = ""

[tool_call_correction]
enabled = false

[xgrammar]
enabled = false
model_structure = "qwen3"
debug = false

[logging]
debug_logs = false
# 可选明细日志目标：off、logger、file、both。
# 未显式配置时：debug_logs=true 表示四类明细日志默认写 logger；
# debug_logs=false 表示四类明细日志默认关闭。
# 可选：仅限制打印到 logger 的明细日志长度，0 表示不限制。
# 超过后中间替换为 "..."，前后各保留该配置大小的一半。
logger_detail_log_max_chars = 0
openai_request_log = "off"
llm_input_log = "off"
llm_output_log = "off"
openai_response_log = "off"
```

当任一明细日志目标设置为 `file` 或 `both` 时，同一次请求的明细会追加到同一个
`logs/requests/<req_id>-trace.log` 文件中，服务日志会打印该文件的绝对路径。

当板上存在多个 RKNN 设备时，建议显式设置 `device_id`。可按下面方式枚举设备：

```bash
adb -s "$DEVICE" shell '
  /userdata/RKClawServer/venv/bin/python - <<"PY"
from rknn3lite.api import RKNN3Lite
print(RKNN3Lite(llm_mode=True).get_devices_id())
PY
'
```

如果板上只有一个设备，可以把 `device_id` 设为空字符串。`core_mask` 必须与 RKNN 模型转换时使用的 core mask 一致。

`native_library` 默认留空。Gateway 会自动使用当前 wheel 内的 `gateway/_native/librkclaw_native.so`，因此安装 wheel 后无需复制动态库或设置系统搜索路径；需要调试时可填写绝对路径覆盖。该库同时提供 GGUF tokenizer、XGrammar 和原生 sampler 的 C ABI。`tokenizer_path` 仍是必填项。Gateway 会先在本地渲染 chat template，再通过 GGUF tokenizer bridge 编码为 token ID；RKNN3 Toolkit Lite 接收的是 `tokens=` 输入，不再通过 RKNN 的 tokenizer callback 处理 prompt 文本。`embed_path` 仍用于 embedding callback，把 token ID 映射成模型输入 embedding。

### 多卡分段模型

大模型已经转换成顺序兼容的 RKNN 分片时，可以用显式 stage 列表把一个模型部署到多张卡：

```toml
[multicard]
bucket_size = 128

[[multicard.stages]]
device_id = "0001:11:00.0"
rknn_path = "/userdata/LargeModel-RKNN/LargeModel_seg0.rknn"
weight_path = "/userdata/LargeModel-RKNN/LargeModel_seg0.weight"
output_tensor_name = "hidden_states" # 多个候选输出时配置

[[multicard.stages]]
device_id = "0002:21:00.0"
rknn_path = "/userdata/LargeModel-RKNN/LargeModel_seg1.rknn"
weight_path = "/userdata/LargeModel-RKNN/LargeModel_seg1.weight"
```

`[[multicard.stages]]` 的声明顺序就是执行顺序，不会根据文件名或 `device_id` 自动排序：

- 第一个 stage 接收 token 和 embedding。
- 中间 stage 接收前一段输出的 hidden states。
- 最后一个 stage 负责 logits、采样和 token 输出。

stage 顺序必须与模型切分/转换工具生成的顺序一致。服务启动时会打印
`stage index -> device -> model -> output tensor` 映射，并检查设备唯一性、文件、vocab、
embedding 维度、context 上限及相邻 pipeline tensor。`output_tensor_name` 可以省略；只有在
多个输出都可能是 hidden states 时才要求显式填写。

配置了两个或更多 stage 后，`[runtime].device_id` 与 `[model].rknn_path/weight_path`
不参与多卡初始化；没有 `[multicard]` 时仍走原有单卡路径，旧配置无需修改。

多卡需要 RKNN3 Toolkit Lite 1.0.5b2 或具备以下接口的后续正式版本：

- `session_run(tokens=..., embeds=..., prefill_only=..., disable_sampling=...)`
- `create_output_tensors()`

缺少能力时服务会在启动阶段报错，不会降级执行。当前多卡路径支持 Qwen 分段模型、流式输出、
native/xgrammar sampling、请求取消，以及 system/session KV cache；Gemma4 多卡暂不支持。

多卡 KV cache 位于 `kv_cache_dir/multicard/`。每组 cache 包含所有 stage 文件和统一
manifest；stage 数、模型文件或任一 cache 文件不匹配时会整组失效并清空所有 stage KV，
避免部分恢复导致上下文不一致。单卡原有 cache 文件格式不变。

KV checkpoint 由单卡和多卡共同使用的 `[checkpoint]` 段配置：

```toml
[checkpoint]
enabled = true
start_pos = 10240
interval = 1024
max_count = 18
```

启用后先设置 `NORMAL`，再为单卡 session 或每个多卡 stage 设置 `SAVE_CHECKPOINT`。
当配置值超过模型 context 边界时，运行时会将 interval、start position 和 count 收敛到合法范围，
并在启动日志中打印最终生效值。设置 `enabled = false` 时只保留 `NORMAL` 策略。

`server.enable_streaming=true` 时按请求中的 `stream` 字段返回流式 SSE；设为 `false` 时忽略请求中的 `stream=true`，统一返回非流式 JSON。`server.sse_heartbeat_interval_s` 默认为 20 秒；请求等待 NPU 或 RKNN prefill 尚未产出 token 时，服务会发送 OpenAI 兼容的空 `data:` chunk，避免客户端把正常等待误判为流停滞。

### Tool call 格式纠错

```toml
[tool_call_correction]
enabled = false
```

该功能默认关闭。开启后会修复缺少 `arguments`、对象字段名缺少引号，以及 JSON 前后多余的字面量 `\\n`。纠错采用独立的有序规则管线，便于继续添加新规则。流式响应会缓冲单个 `<tool_call>` 块，完成纠错后一次性输出；普通文本不受影响。

### XGrammar C++ Core 与原生采样

`native_sampling.enabled` 开启 C++ sampler；`xgrammar.enabled` 在此基础上约束 `<tool_call>`。`xgrammar.model_structure` 可选 `qwen3` 或 `qwen3.5`，与模型 ID 无关。Qwen3.5 使用开放 function name 和通用 Qwen XML 参数结构，不枚举 tools 或校验单工具 Schema。

- 两者关闭：使用 RKNN 原生采样。
- 只开启 native sampling：使用 C++ sampler。
- 同时开启：进入 `<tool_call>` 后由 XGrammar bitmask 过滤候选，再完成 C++ 采样。

采样顺序为 repeat penalty、top-k、top-p、temperature、dist。FP16 logits、matcher、重复窗口和 RNG 均在 `librkclaw_native.so` 中处理；运行时不依赖 Python XGrammar、PyTorch 或 llama-cpp-python。约束异常时直接报错，不回退到无约束采样。

`sampling.frequency_penalty` 按重复次数扣减 logit，`sampling.presence_penalty` 对重复窗口内出现过的 token 扣减一次；两者也可通过请求中的同名字段覆盖本次生成。

每次 session 结束会输出 `RKNN3 sampling timing`。可直接从板端日志生成 CSV：

```bash
python3 tools/extract_sampling_timing.py --adb \
  /tmp/rkclawserver_front.log --csv > sampling_time.csv
```

### Thinking 相关开关

- `model.enable_thinking`：请求没有指定时的默认 thinking 行为。
- 请求字段 `chat_template_kwargs.enable_thinking`：覆盖本次请求的 thinking 行为。
- `reasoning.separate_output=true`：把 `<think>...</think>` 内容放入响应的 `reasoning_content`。
- `reasoning.separate_output=false`：保留模型原始输出，全部放入 `content`。

### WebUI 管理控制台

RKClawServer 可在现有 HTTP 端口提供内嵌管理界面。WebUI 默认关闭，启用前必须配置管理员 Token：

\`\`\`toml
[logging]
# 开启后保存完整 OpenAI 请求和响应；默认关闭以避免记录敏感内容。
session_logs_enabled = false
session_retention_days = 30
server_log_path = "logs/server.log"
server_log_max_bytes = 10485760
server_log_backup_count = 5

[webui]
enabled = true
# 推荐通过 RKCLAW_WEBUI_TOKEN 环境变量传入；环境变量优先于该字段。
auth_token = ""
data_path = "logs/webui.sqlite3"
stats_retention_days = 90
reload_drain_timeout_s = 300
session_cookie_ttl_s = 28800
\`\`\`

使用 systemd 时可通过服务环境变量配置 Token：

\`\`\`ini
[Service]
Environment=RKCLAW_WEBUI_TOKEN=replace-with-a-strong-random-token
\`\`\`

重启 Server 后访问 \`http://<device-ip>:<server-port>/webui\`。管理控制台包含：

- Dashboard：当前模型、服务状态、活动/排队请求、请求成功率、token 和延迟趋势。
- 会话日志：按消息历史指纹聚合多轮请求；仅在 \`session_logs_enabled=true\` 时记录完整正文，并支持从详情弹窗导出当前会话的完整 JSON 日志。
- Server 日志：查看并过滤 \`server_log_path\` 及其轮转日志。
- Server 配置：结构化表单和原始 TOML 编辑、校验、原子保存及备份。
- 模型重载：停止接收新推理、等待现有请求排空，再释放旧模型并加载保存后的配置；失败时自动恢复最后一次成功配置和模型。

配置保存与模型重载是两个独立操作。修改 \`server.host\`、\`server.port\`、WebUI Token、WebUI 数据库路径等进程级设置后，需要重启进程；其他模型和运行参数可以通过“重新加载模型”应用。

管理接口位于 \`/api/webui/*\`，使用 HttpOnly 登录 Cookie 和 CSRF 校验。现有 \`/v1/*\`、\`/healthz\`、\`/readyz\` 不受 WebUI 登录影响。模型排空或重载期间，新推理请求返回 \`503\` 和错误码 \`service_reloading\`。


## 3. 部署

下面按构建、上传、安装和启动四步部署到开发板。

### 3.1 构建并上传文件

在本工程根目录构建 wheel：

```bash
./scripts/package.sh
```

脚本默认先在 PC 上交叉编译 aarch64 `librkclaw_native.so`，再将其写入平台 wheel，生成类似 `dist/rk_claw_server-0.3.2-cp311-cp311-linux_aarch64.whl` 的文件。该 wheel 可直接安装到 aarch64 开发板；Toolkit Lite wheel 仍需单独安装。

指定交叉编译器时直接传给打包脚本：

```bash
CROSS_COMPILE=/opt/toolchains/bin/aarch64-linux-gnu- \
  ./scripts/package.sh
```

已有交叉编译产物时可跳过 native 构建：

```bash
BUILD_NATIVE=0 \
NATIVE_LIBRARY=/path/to/aarch64/librkclaw_native.so \
  ./scripts/package.sh
```

脚本会用 `file` 检查 SO 架构，并生成匹配的 `linux_aarch64` 或 `linux_x86_64` wheel 标签。其他目标可显式设置 `WHEEL_PLATFORM`。

#### Cython release wheel

需要在发布包中隐藏 Python 实现源码时，可使用 Docker Buildx + QEMU 在
ARM64/CPython 3.11 环境中把 `gateway` 的实现模块编译为 Cython 扩展：

```bash
./scripts/build_cython_release.sh
```

默认输出到 `dist/cython/`，文件名类似：

```text
rk_claw_server-0.3.2-cp311-cp311-linux_aarch64.whl
```

脚本默认目标是 `linux/arm64`，复用
`dist/native-aarch64/lib/librkclaw_native.so`；该文件不存在时会先调用现有的
aarch64 交叉编译流程。Cython 和 GCC 则运行在真正的 ARM64 Python 3.11
容器中，避免生成错误的 CPU 架构或 CPython ABI。构建阶段还会在目标容器中
安装并导入 wheel，提前发现不可加载的扩展。

常用覆盖项：

```bash
# 使用已有 native library，不重新编译
BUILD_NATIVE=0 \
NATIVE_LIBRARY=/path/to/aarch64/librkclaw_native.so \
  ./scripts/build_cython_release.sh

# 构建本机 amd64 调试产物
TARGET_PLATFORM=linux/amd64 \
  ./scripts/build_cython_release.sh

# 指定 Buildx builder 或切换到 Docker Hub 官方镜像
BUILDER=my-builder \
PYTHON_IMAGE=python:3.11-slim-bookworm \
  ./scripts/build_cython_release.sh

# 保留调试符号（默认会 strip 所有发布 SO）
STRIP_RELEASE=0 ./scripts/build_cython_release.sh
```

默认基础镜像通过 DaoCloud 镜像代理引用，避免当前网络环境直连 Docker Hub
超时；有可用的内部 registry 或 Docker Hub 网络时，可通过 `PYTHON_IMAGE`
替换。需要完全固定供应链输入时，也可以传入带 `@sha256:...` 的镜像引用。

运行前 `docker buildx ls` 的 Platforms 需要包含 `linux/arm64`。如果没有，
应由管理员为 Docker 安装 QEMU/binfmt 支持或切换到支持 ARM64 的 builder。
wheel 中保留包初始化文件和 `gateway/__main__.py` 以支持
`python -m gateway`，其余实现模块不再携带 `.py`。Cython 能提高源码还原
门槛，但不等同于加密，字符串和部分符号仍可能被二进制分析。

设置 ADB 设备序列号并创建板端目录：

```bash
export DEVICE=your-adb-serial

adb -s "$DEVICE" shell 'mkdir -p \
  /userdata/RKClawServer/packages \
  /userdata/RKClawServer/logs'
```

上传 Python 包、Toolkit Lite 和配置：

```bash
adb -s "$DEVICE" push \
  dist/rk_claw_server-0.3.2-cp311-cp311-linux_aarch64.whl \
  /userdata/RKClawServer/packages/

adb -s "$DEVICE" push \
  /path/to/rknn3_toolkit_lite-1.0.5a1-cp311-cp311-linux_aarch64.whl \
  /userdata/RKClawServer/packages/

adb -s "$DEVICE" push gateway.toml /userdata/RKClawServer/gateway.toml
```

`rknn3_toolkit_lite` 请从对应产品的公开发布包获取；它不包含在源码仓库中。

如果模型还没有放到板端，再上传模型目录。模型文件较大，建议确认目标目录剩余空间后再操作：

```bash
adb -s "$DEVICE" push \
  /path/to/AgentModel-V3.1-4B-RKNN \
  /userdata/
```

### 3.2 单独构建原生库（可选）

`librkclaw_native.so` 把 GGUF tokenizer bridge、XGrammar C++ Core 和原生 sampler 合并在一个共享库中。仓库的 `native/3rdparty/tokenizer` 默认携带 Linux aarch64 和 x86_64 的预编译静态库，常规构建不会访问网络；`native/3rdparty/xgrammar` 固定为官方 v0.2.3 submodule，构建前需要初始化 submodule。

`scripts/package.sh` 已自动执行本节步骤。仅需检查或复用原生产物时，可在 x86_64 PC 上单独执行：

```bash
./scripts/build_native.sh
```

脚本在 PC 上默认启用 aarch64 交叉编译，并在 `PATH` 中依次查找 `aarch64-rockchip1240-linux-gnu-`、`aarch64-none-linux-gnu-` 和 `aarch64-linux-gnu-`。也可通过 `CROSS_COMPILE` 或 `CMAKE_TOOLCHAIN_FILE` 显式指定工具链。默认产物为：

```text
dist/native-aarch64/lib/librkclaw_native.so
```

指定其他工具链前缀时使用：

```bash
CROSS_COMPILE=/opt/toolchains/bin/aarch64-linux-gnu- \
  ./scripts/build_native.sh
```

也可以传入标准 CMake toolchain 文件；显式参数的优先级高于自动探测：

```bash
./scripts/build_native.sh \
  -DCMAKE_TOOLCHAIN_FILE=/path/to/aarch64-linux.cmake
```

交叉编译完成后可检查产物架构：

```bash
file dist/native-aarch64/lib/librkclaw_native.so
```

`scripts/deploy.sh` 和 `scripts/dev_deploy.sh` 只需上传并安装 gateway wheel，不再单独上传 SO。确需在 aarch64 板端本机编译时，构建脚本会自动切换为 native 模式，也可显式设置 `NATIVE_BUILD_MODE=native`。

#### 从公开源码重建 Tokenizer（可选）

预编译库来自 [airockchip/rknn3-model-zoo/tokenizer](https://github.com/airockchip/rknn3-model-zoo/tree/main/tokenizer)，固定到 commit `174e44c77230735b1458946debb62b3982c1ee58`。脚本直接调用 CMake，不依赖上游 `env_linux.sh` 中的本地工具链路径：

```bash
# 本机 x86_64
./scripts/build_tokenizer.sh --arch x86_64

# aarch64 交叉编译
CROSS_COMPILE=/opt/toolchains/bin/aarch64-linux-gnu- \
  ./scripts/build_tokenizer.sh --arch aarch64

# 使用已有源码 checkout
./scripts/build_tokenizer.sh \
  --arch x86_64 \
  --source-dir /path/to/rknn3-model-zoo/tokenizer
```

普通构建输出到 `build/deps/tokenizer-<arch>`，不会修改仓库文件。要让 native 构建自动从固定源码重建，设置 `RKCLAW_REBUILD_TOKENIZER=1`；要使用其他已构建目录，显式设置 `TOKENIZER_ROOT`。维护者更新随仓库分发的静态库时才使用 `--update-bundled`。设置 `RKCLAW_OFFLINE=1` 可复用已有源码缓存，缓存缺失时脚本会明确失败。

动态库的默认解析顺序是配置项 `runtime.native_library`、环境变量 `RKCLAW_NATIVE_LIB`、wheel 内置库和 `/usr/lib/librkclaw_native.so`。原生 sampler 在源码开发环境还会继续检查项目 `lib/`、`dist/native/` 和系统动态库路径。

### 3.3 创建 Python 环境并安装

板端已有 FastAPI、NumPy 等系统包时，推荐复用系统 site-packages：

```bash
adb -s "$DEVICE" shell '
  python3 -m venv --system-site-packages /userdata/RKClawServer/venv &&
  /userdata/RKClawServer/venv/bin/pip install --no-deps \
    /userdata/RKClawServer/packages/rknn3_toolkit_lite-1.0.5a1-cp311-cp311-linux_aarch64.whl &&
  /userdata/RKClawServer/venv/bin/pip install --no-deps \
    /userdata/RKClawServer/packages/rk_claw_server-0.3.2-cp311-cp311-linux_aarch64.whl
'
```

检查关键模块：

```bash
adb -s "$DEVICE" shell '
  /userdata/RKClawServer/venv/bin/python -c \
    "import gateway, rknn3lite; print(\"imports ok\")"
'
```

完成后设置 `native_sampling.enabled=true`。运行时无需安装 `llama-cpp-python`、Python `xgrammar` 或 `torch`。

### 3.4 启动服务

先确认 RKNN3 服务正常：

```bash
adb -s "$DEVICE" shell 'systemctl is-active rknn3.service'
```

**前台启动**，便于第一次部署时观察日志：

```bash
adb -s "$DEVICE" shell '
  cd /userdata/RKClawServer &&
  PYTHONUNBUFFERED=1 venv/bin/python -m gateway
'
```

默认会读取当前目录的 `gateway.toml`。也可以通过 `-c/--config` 指定其他路径，此时无需从配置文件所在目录启动：

```bash
venv/bin/python -m gateway --config /path/to/gateway.toml
```

初始化成功后会监听 `0.0.0.0:8081`。另开终端检查：

```bash
adb -s "$DEVICE" shell 'curl -sS http://127.0.0.1:8081/healthz'
adb -s "$DEVICE" shell 'curl -sS http://127.0.0.1:8081/readyz'
```

预期均返回：

```json
{"status":"ok"}
```

**使用 systemd 开机启动**（仓库提供了 `rkclaw-server.service`）：

```bash
adb -s "$DEVICE" push rkclaw-server.service /etc/systemd/system/

adb -s "$DEVICE" shell '
  systemctl daemon-reload &&
  systemctl enable --now rkclaw-server.service &&
  systemctl status rkclaw-server.service --no-pager
'
```

查看服务日志：

```bash
adb -s "$DEVICE" shell 'journalctl -u rkclaw-server.service -f'
```

## 4. OpenAI API 验证

### 查询模型

```bash
curl http://192.168.31.161:8081/v1/models
```

请把示例 IP 替换为开发板实际 IP，可用 `adb shell hostname -I` 查询。

### 同步请求

```bash
curl http://192.168.31.161:8081/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "AgentModel",
    "messages": [
      {"role": "user", "content": "只回答：你好"}
    ],
    "max_tokens": 64,
    "chat_template_kwargs": {
      "enable_thinking": false
    }
  }'
```

### SSE 流式请求

```bash
curl -N http://192.168.31.161:8081/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "AgentModel",
    "messages": [
      {"role": "user", "content": "介绍一下你自己"}
    ],
    "stream": true,
    "stream_options": {
      "include_usage": true
    },
    "max_completion_tokens": 256,
    "chat_template_kwargs": {
      "enable_thinking": true
    }
  }'
```

正常流式响应以 `data: {...}` 分块输出，并以以下内容结束：

```text
data: [DONE]
```

在排队和 prefill 阶段还可能收到 `delta` 为空且 `finish_reason=null` 的心跳 chunk；它不代表模型输出，客户端应忽略其内容并继续读取。

## 5. 运行机制和限制

- 服务只创建一个 RKNN Session，并在 Gateway 层通过公平异步锁显式串行化；只有获得执行权的请求才会提交到 RKNN backend。
- 等待执行权的请求若因客户端超时、断开或重试而取消，会直接从异步锁等待队列移除，不会在 RKNN FIFO 中形成重试副本积压。
- 请求必须携带完整 `messages` 历史；服务端不跨请求保存对话历史或 KV Cache。
- backend 的有界 FIFO 仍作为最后一道过载保护，队列满时返回 OpenAI 格式的 HTTP 429。
- 客户端断开后服务会调用 `session_stop()`，并继续处理后续请求。
- 模板优先级为：显式 `chat_template_file`、GGUF `tool_use` 模板、GGUF 默认模板、内置 ChatML fallback。
- 每次请求的 prompt 只在 Gateway 侧编码一次，推理时直接调用 Toolkit Lite `session_run(tokens=...)`。
- 当前输出解析器按 Qwen3 的 thinking 和 tool-call 标签实现。
- 旧版 Toolkit Lite 在运行期间动态修改采样参数可能阻塞。当前板端验证使用 `[sampling]` 中的默认值；确认厂商修复前，不建议在请求中传入不同的 `temperature`、`top_p`、`top_k`、`repeat_penalty`、`frequency_penalty` 或 `presence_penalty`。

## 6. 常见问题

### `/readyz` 无法连接

Gateway 在模型和 RKNN Session 初始化完成前不会开始监听端口。先查看启动日志：

```bash
adb -s "$DEVICE" shell 'tail -200 /userdata/RKClawServer/logs/gateway.log'
```

重点检查模型路径、tokenizer bridge、Toolkit Lite 安装状态、Python ABI 和 NPU `device_id`。

### `ModuleNotFoundError: rknn3lite`

确认使用的是 Gateway venv，并重新安装 Toolkit Lite：

```bash
/userdata/RKClawServer/venv/bin/pip install --no-deps --force-reinstall \
  /userdata/RKClawServer/packages/rknn3_toolkit_lite-1.0.5a1-cp311-cp311-linux_aarch64.whl
```

### `RKNN3_ERR_DEVICE_UNAVAILABLE`

先确认配置的 `device_id` 仍在枚举结果中，并检查该设备是否已被其他进程独占。开发调试时尽量不要在 native inference 正在执行时直接 `kill -9` Gateway；异常终止可能让 Toolkit Lite 留下失效 Session。

如果 proxy 或 NPU 固件已无法恢复，可在确认不会影响板上其他业务后重启开发板：

```bash
adb -s "$DEVICE" reboot
adb -s "$DEVICE" wait-for-device
```

### tokenizer、embedding 或 vocab size 不匹配

必须使用同一模型版本生成的 `.rknn`、`.weight`、`.embed.bin` 和 `.tokenizer.gguf`。服务启动时会校验 tokenizer vocab size 和 embedding 维度，不匹配会直接启动失败。

## 7. 本地测试

x86_64 环境不加载 aarch64 Toolkit Lite，单元测试使用 fake backend：

```bash
python3 -m pytest -q
```
