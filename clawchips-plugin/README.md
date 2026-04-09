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

### QQ tool-call notifications (`qq_tool_notify`)

Optional: send proactive QQ messages on `before_tool_call` / `after_tool_call` via the installed QQ Bot plugin (`sendProactive`). Configure this in **`clawchips.yaml`** as a top-level `qq_tool_notify` block (camelCase alias `qqToolNotify` is also accepted).

| Field | Meaning |
| --- | --- |
| `enabled` | Must be `true` to activate. |
| `openid` | Target user or group openid. |
| *(omit or placeholder)* | If `openid` is unset, or exactly `"<user-or-group-openid>"` / `"<用户或群-openid>"`, ClawChips reads `known-users.json` under `$OPENCLAW_STATE_DIR/qqbot/data/` and `~/.openclaw/qqbot/data/`, and uses the row with the greatest **`lastSeenAt`**. |
| `type` | `c2c` or `group` (default `c2c` when not using known-users fallback). |
| `accountId` | QQ bot account id (default `default`). |
| `onBefore` / `onAfter` | **Before:** default **off** — set `onBefore` or `notifyBefore` to `true` to enable. **After:** default **on** — set `onAfter` or `notifyAfter` to `false` to disable. Aliases: `notifyBefore` / `notifyAfter`. |
| `includeToolNames` / `excludeToolNames` | Allow/deny tool names. Aliases: `onlyTools` / `skipTools`. |
| `maxParamChars` / `maxResultChars` | Limits for argument/result text in the message. |

**Plugins:** Tencent `@tencent-connect/openclaw-qqbot` (global extension `openclaw-qqbot`) or `@openclaw/qqbot`. Requires QQ channel enabled in the host config.

**Ordering:** If both phases are enabled, the **after** send is chained after the **before** send completes, so messages tend to arrive in order on QQ.

**Debug:** `CLAWCHIPS_QQ_NOTIFY_DEBUG=1` logs failed dynamic imports for `sendProactive`.

Example (`~/.openclaw/clawchips.yaml`):

```yaml
qq_tool_notify:
  enabled: true
  type: c2c
  account_id: default
  on_before: false
  on_after: true
```

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
