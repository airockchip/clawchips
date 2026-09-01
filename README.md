<div align="center">
  <img src="res/logo.png" width="300" alt="ClawChips logo" />
  <h1>ClawChips 0.7.0</h1>

An open-source edge agent stack for Rockchip RK3588 + RK1820/RK1828.

**English | [中文](./README_ZH.md)**
</div>

## What changed in 0.7.0

ClawChips now uses **Nanobot RK 0.2.2** as the agent harness and
**RKClawServer 0.3.2** for on-device LLM inference. The previous OpenClaw
plugin, edge/cloud router, plugin Dashboard, ModelHub API, and repository-level
Skills have been removed.

```text
Browser / messaging channels
            │
            ▼
Nanobot WebUI :8765 ── Nanobot Gateway :18790
            │  OpenAI-compatible requests
            ▼
RKClawServer :8081 ── RKNN3 Toolkit Lite ── RK1820/RK1828
```

RKClawServer provides OpenAI-compatible Chat Completions, streaming output,
tool-call correction, KV-cache persistence, structured generation through
XGrammar, and its own WebUI at `http://<device-ip>:8081/webui/`.

## Repository contents

- [`RKClawServer/`](./RKClawServer/README.md): source imported from
  `v0.3.2-source.1`, with XGrammar expanded and Linux aarch64/x86_64
  Tokenizer static libraries bundled.
- [`ClawChips_Quick_Start.md`](./ClawChips_Quick_Start.md): complete Chinese
  V0.7.0 installation, configuration, usage, and troubleshooting guide.
- [`release-manifest.yaml`](./release-manifest.yaml): immutable upstream
  commits and bundled-library checksums.

Nanobot, models, RKNN3 Toolkit Lite, and board-side algorithm resources under
`/userdata/skills` are delivered in the offline product package. They are not
part of this Git source repository.

## Build RKClawServer from source

The default native build uses the bundled Tokenizer library and does not
access the network. XGrammar is already expanded in this repository.

```bash
cd RKClawServer

# x86_64 development build
NATIVE_BUILD_MODE=native ./scripts/build_native.sh

# aarch64 cross build
CROSS_COMPILE=/opt/toolchains/bin/aarch64-linux-gnu- \
  ./scripts/build_native.sh
```

Toolkit Lite is still required to run inference on a board. See the
[Quick Start](./ClawChips_Quick_Start.md) for offline-package deployment and
model configuration.

### Rebuild Tokenizer from public source

The bundled libraries are reproducible from
[`airockchip/rknn3-model-zoo/tokenizer`](https://github.com/airockchip/rknn3-model-zoo/tree/main/tokenizer)
at the commit recorded in the release manifest.

```bash
cd RKClawServer
./scripts/build_tokenizer.sh --arch x86_64
CROSS_COMPILE=/opt/toolchains/bin/aarch64-linux-gnu- \
  ./scripts/build_tokenizer.sh --arch aarch64

# Use a rebuilt library in the native build
TOKENIZER_ROOT="$PWD/build/deps/tokenizer-x86_64" \
NATIVE_BUILD_MODE=native ./scripts/build_native.sh
```

Set `RKCLAW_OFFLINE=1` to reuse the cached fixed source revision without
network access. Maintainers can use `--update-bundled` to refresh a tracked
library and its provenance manifest.

## Versions

| Component | Version |
| --- | --- |
| ClawChips | 0.7.0 |
| RKClawServer | 0.3.2 (`v0.3.2-source.1`) |
| Nanobot RK | 0.2.2 (`rk-v0.2.2`) |
| Agent model used by the guide | AgentModel V3.1 |

Exact commits and SHA256 values are in
[`release-manifest.yaml`](./release-manifest.yaml).

## License

ClawChips and RKClawServer are released under MIT. Bundled third-party code
and libraries retain their upstream licenses; see
[`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md).
