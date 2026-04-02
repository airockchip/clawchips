# clawchips (OpenClaw plugin)

TypeScript plugin that routes each turn in **`before_model_resolve`** via `providerOverride` / `modelOverride` (no separate Python router process).

1. **Optional routing memory** (SQLite + embeddings) — short-circuit when similarity ≥ `memory.score_threshold`.
2. **Heuristics + YAML `router.rules`** — maps `LOCAL` / `CLOUD` to OpenClaw `provider/model` ids.

## Config

- Default: `~/.openclaw/clawchips.yaml` (seeded from bundled `clawchips_default.yaml` if missing). Override with plugin settings or `CLAWCHIPS_CONFIG`.
- In `router.rules`, `LOCAL`, `CLOUD`, and `default` must resolve to **provider/model** strings (same as the gateway / `~/.openclaw/openclaw.json`; prefer ids containing `/`).

**Optional setup wizard:** run it manually to set LOCAL / CLOUD / default and memory.

```bash
node ~/.openclaw/extensions/clawchips/scripts/setup.mjs
```

Enable memory in YAML (`memory.enabled`, `embedding` OpenAI-compatible endpoint, etc.) when you use routing memory.

## Directives (user messages)

`@model(provider/model)`, `@local` / `@edge` / `@rk` (local tier), `@cloud`; optional `session`. With memory enabled, natural-language phrases may trigger memory ops (see hooks).

## Dashboard

Open `/plugins/clawchips/dashboard/` on the gateway (plugin-authenticated). Build UI from `dashboard/`: `npm install && npm run build`; plugin serves `dashboard/dist`.

## Build & pack

```bash
npm install && npm run build
```

From repo root:

```bash
bash scripts/package_dist.sh
```

Produces `dist/clawchips.zip` (compiled plugin, `dashboard/dist`, manifests, `clawchips_default.yaml`). Flags `--skip-plugin-build`, `--skip-dashboard-build`; see script for details.
