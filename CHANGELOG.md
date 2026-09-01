# Changelog

## 0.7.0 - 2026-09-01

### Added

- RKClawServer 0.3.2 public source for on-device RKNN3 LLM inference.
- Expanded, pinned XGrammar source for structured generation.
- Reproducible Tokenizer builds from public source, with bundled Linux
  aarch64 and x86_64 static libraries by default.
- A complete ClawChips V0.7.0 Chinese Quick Start and version manifest.

### Changed

- Replaced OpenClaw with Nanobot RK 0.2.2 as the agent harness.
- Updated the default model and deployment examples to AgentModel V3.1.
- Updated the architecture to Nanobot WebUI `8765`, Nanobot Gateway `18790`,
  and RKClawServer `8081`.

### Removed

- `clawchips-plugin/` and its local edge/cloud router and Dashboard.
- `model_hub_py/` and the ModelHub API.
- Repository-level `skills/`; board-side Skills are delivered in the offline
  package under `/userdata/skills`.
- Root Node package metadata and obsolete plugin release scripts.

## 0.6.0-alpha

- Previous OpenClaw plugin technical preview.
