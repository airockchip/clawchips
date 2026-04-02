/**
 * Tier 2: heuristic tiering + YAML router.rules → OpenClaw provider/model.
 */

import { classifyByHeuristicTier } from "./heuristic-tier.js";
import type { ParsedLocalRouterConfig } from "../config.js";
import type { Router, RoutingContext, RouteDecision } from "./types.js";
import {
  modelTargetForDefault,
  modelTargetForTier,
  parseTierRules,
} from "../resolve-model.js";

/** Rough token estimate for heuristic tiering (~4 chars per token). */
function estimateTokens(text: string): number {
  return Math.max(1, Math.ceil(text.length / 4));
}

/**
 * Tier-2 router: classifies the prompt with heuristics, then redirects to LOCAL/CLOUD/default
 * models from YAML `router.rules` and merged `llms`.
 */
export const rulesRouter: Router = {
  id: "rules-router",
  tier: 2,

  async evaluate(context: RoutingContext, pluginConfig: Record<string, unknown>): Promise<RouteDecision> {
    const parsed = pluginConfig._parsedLocalRouter as ParsedLocalRouterConfig | undefined;
    if (!parsed) {
      return { action: "passthrough", confidence: 0, reason: "no parsed config" };
    }

    if ((parsed.router.strategy ?? "rules") !== "rules") {
      return { action: "passthrough", confidence: 0, reason: "strategy not rules" };
    }

    const message = context.message ?? "";
    if (!message.trim()) {
      return { action: "passthrough", confidence: 0 };
    }

    // Heuristic score → tier label; YAML maps tiers and default to provider/model refs.
    const tierRules = parseTierRules(parsed.router.rules);
    const scored = classifyByHeuristicTier(message, undefined, estimateTokens(message));
    const fallback = modelTargetForDefault(tierRules, parsed.llms);

    if (scored.tier === null || scored.confidence < 0.5) {
      if (fallback) {
        return {
          action: "redirect",
          target: fallback,
          confidence: Math.max(0.4, scored.confidence),
          reason: "ambiguous heuristic → default",
        };
      }
      return { action: "passthrough", confidence: scored.confidence, reason: "no default model" };
    }

    const tierLabel = scored.tier === "LOCAL" ? "LOCAL" : "CLOUD";
    const target = modelTargetForTier(tierLabel, tierRules, parsed.llms);
    if (!target) {
      if (fallback) {
        return {
          action: "redirect",
          target: fallback,
          confidence: scored.confidence * 0.9,
          reason: "tier model missing → default",
        };
      }
      return { action: "passthrough", confidence: 0, reason: "cannot resolve tier model" };
    }

    return {
      action: "redirect",
      target,
      confidence: scored.confidence,
      reason: `heuristic ${tierLabel}`,
    };
  },
};
