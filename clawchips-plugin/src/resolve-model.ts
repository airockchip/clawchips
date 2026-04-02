/**
 * Map YAML `llms` + `router.rules` to OpenClaw provider/model.
 */

import type { LlmsConfig, RouterRulesEntry } from "./config.js";
import type { ModelTarget, Tier } from "./localrouter/types.js";
import { getMergedLlmsForResolve } from "./openclaw-json.js";

export type TierRules = {
  LOCAL?: string;
  CLOUD?: string;
  default?: string;
};

/** Merges YAML `router.rules` rows into a single map of tier/default model id strings. */
export function parseTierRules(rules: RouterRulesEntry[] | undefined): TierRules {
  const out: TierRules = {};
  for (const row of rules ?? []) {
    if (row.LOCAL !== undefined) out.LOCAL = String(row.LOCAL).trim();
    if (row.CLOUD !== undefined) out.CLOUD = String(row.CLOUD).trim();
    if (row.default !== undefined) out.default = String(row.default).trim();
  }
  return out;
}

/**
 * Resolve a model id (as in YAML rules / memory rows) to OpenClaw `provider/model`.
 */
export function resolveModelRef(llms: LlmsConfig, modelIdOrName: string): ModelTarget | null {
  const key = (modelIdOrName ?? "").trim();
  if (!key) return null;

  const merged = getMergedLlmsForResolve(llms ?? {});

  if (key.includes("/")) {
    const [p, ...rest] = key.split("/");
    if (p && rest.length) {
      return { provider: p.trim(), model: rest.join("/").trim() };
    }
  }

  for (const [providerId, cfg] of Object.entries(merged)) {
    for (const m of cfg.models ?? []) {
      const id = (m.id ?? "").trim();
      const name = (m.name ?? "").trim();
      if (id && (id === key || name === key)) {
        return { provider: providerId, model: id };
      }
    }
  }

  return null;
}

/**
 * Resolve `@model(...)` after `resolveModelTarget`: bare id/name matches YAML `llms` (any provider),
 * then `provider/model` / gateway-style path. Falls back to the original target if unknown.
 */
export function resolvePinModelFromLlms(llms: LlmsConfig, target: ModelTarget): ModelTarget {
  const m = (target.model ?? "").trim();
  const p = (target.provider ?? "").trim();
  if (m) {
    const byBare = resolveModelRef(llms, m);
    if (byBare) return byBare;
  }
  if (p && m) {
    const byLabel = resolveModelRef(llms, `${p}/${m}`);
    if (byLabel) return byLabel;
  }
  return target;
}

/** LOCAL/CLOUD tier label as in heuristic-tier. */
export function modelTargetForTier(
  tierLabel: Tier,
  tierRules: TierRules,
  llms: LlmsConfig,
): ModelTarget | null {
  const modelId = tierLabel === "LOCAL" ? tierRules.LOCAL : tierRules.CLOUD;
  if (!modelId) return null;
  return resolveModelRef(llms, modelId);
}

/** Resolves `default`, else first of LOCAL/CLOUD, via merged LLM catalog. */
export function modelTargetForDefault(tierRules: TierRules, llms: LlmsConfig): ModelTarget | null {
  const id = tierRules.default ?? tierRules.LOCAL ?? tierRules.CLOUD;
  if (!id) return null;
  return resolveModelRef(llms, id);
}
