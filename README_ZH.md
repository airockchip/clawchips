<div align="center">
  <img src="res/logo.png" width="300" alt="ClawChips logo" />
  <h1>ClawChips 0.7.0</h1>

面向 Rockchip RK3588 + RK1820/RK1828 的开源端侧 Agent 方案。

**[English](./README.md) | 中文**
</div>

## 0.7.0 版本变化

ClawChips 现使用 **Nanobot RK 0.2.2** 作为 Agent Harness，使用
**RKClawServer 0.3.2** 在端侧运行 LLM。旧 OpenClaw 插件、端云智能路由、
插件 Dashboard、ModelHub API 和仓库内 Skills 已移除。

```text
浏览器 / 通讯渠道
        │
        ▼
Nanobot WebUI :8765 ── Nanobot Gateway :18790
        │  OpenAI 兼容请求
        ▼
RKClawServer :8081 ── RKNN3 Toolkit Lite ── RK1820/RK1828
```

RKClawServer 提供 OpenAI 兼容 Chat Completions、流式输出、ToolCall 错误
矫正、KV-Cache 保存加载、基于 XGrammar 的结构化生成，以及地址为
`http://<设备 IP>:8081/webui/` 的独立 WebUI。

## 仓库内容

- [`RKClawServer/`](./RKClawServer/README.md)：从 `v0.3.2-source.1` 导入的
  源码，已展开 XGrammar，默认携带 Linux aarch64/x86_64 Tokenizer 静态库。
- [`ClawChips_Quick_Start.md`](./ClawChips_Quick_Start.md)：完整 V0.7.0 中文
  安装、配置、使用和排障指南。
- [`release-manifest.yaml`](./release-manifest.yaml)：固定的上游 commit 和
  预编译库 SHA256。

Nanobot、模型、RKNN3 Toolkit Lite，以及部署到 `/userdata/skills` 的板端
算法资源由离线产品发布包提供，不包含在本 Git 源码仓库中。

## 从源码构建 RKClawServer

默认 native 构建直接使用仓库内 Tokenizer 预编译库，不访问网络；XGrammar
源码也已在本仓库展开。

```bash
cd RKClawServer

# x86_64 开发构建
NATIVE_BUILD_MODE=native ./scripts/build_native.sh

# aarch64 交叉构建
CROSS_COMPILE=/opt/toolchains/bin/aarch64-linux-gnu- \
  ./scripts/build_native.sh
```

在开发板实际运行推理仍需要 Toolkit Lite。离线发布包部署和模型配置请参考
[快速开始](./ClawChips_Quick_Start.md)。

### 从公开源码重建 Tokenizer

随仓库提供的预编译库可从
[`airockchip/rknn3-model-zoo/tokenizer`](https://github.com/airockchip/rknn3-model-zoo/tree/main/tokenizer)
固定 commit 重建：

```bash
cd RKClawServer
./scripts/build_tokenizer.sh --arch x86_64
CROSS_COMPILE=/opt/toolchains/bin/aarch64-linux-gnu- \
  ./scripts/build_tokenizer.sh --arch aarch64

# native 构建显式使用重建产物
TOKENIZER_ROOT="$PWD/build/deps/tokenizer-x86_64" \
NATIVE_BUILD_MODE=native ./scripts/build_native.sh
```

设置 `RKCLAW_OFFLINE=1` 可离线复用已经缓存的固定源码版本。维护者只有在更新
仓库内预编译库及其来源清单时才使用 `--update-bundled`。

## 组件版本

| 组件 | 版本 |
| --- | --- |
| ClawChips | 0.7.0 |
| RKClawServer | 0.3.2（`v0.3.2-source.1`） |
| Nanobot RK | 0.2.2（`rk-v0.2.2`） |
| 指南默认 Agent 模型 | AgentModel V3.1 |

精确 commit 与 SHA256 记录在
[`release-manifest.yaml`](./release-manifest.yaml)。

## 许可证

ClawChips 与 RKClawServer 使用 MIT 许可证。随仓库分发的第三方源码和库保留
各自的上游许可证，详见
[`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md)。
