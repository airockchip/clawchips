/**
 * Tier 1: routing memory — semantic search in MemoryBank.
 */

import type { ParsedLocalRouterConfig } from "../config.js";
import { MemoryBank } from "./memory.js";
import type { Router, RoutingContext, RouteDecision } from "./types.js";
import { modelTargetForTier, parseTierRules, resolveModelRef } from "../resolve-model.js";

/** Tier-1 memory router plus accessor for the same `MemoryBank` instance (dashboard / hooks). */
export type MemoryRouterHandle = {
  router: Router;
  getMemoryBank: () => MemoryBank | null;
};

/**
 * Builds a tier-1 router that redirects using semantic hits from `MemoryBank`, or a no-op when memory is off.
 */
export function createMemoryRouter(
  parsed: ParsedLocalRouterConfig,
  configFileDir: string,
): MemoryRouterHandle {
  if (parsed.memory?.enabled !== true) {
    const noop: Router = {
      id: "memory-router",
      tier: 1,
      async evaluate(): Promise<RouteDecision> {
        return { action: "passthrough", confidence: 0, reason: "memory disabled" };
      },
    };
    return { router: noop, getMemoryBank: () => null };
  }

  const mem = parsed.memoryConfig;
  const bank = new MemoryBank(mem, configFileDir, { routingConfig: parsed.routing });

  const router: Router = {
    id: "memory-router",
    tier: 1,

    async evaluate(context: RoutingContext, _pluginConfig: Record<string, unknown>): Promise<RouteDecision> {
      const message = (context.message ?? "").trim();
      if (!message) {
        return { action: "passthrough", confidence: 0, reason: "empty message" };
      }

      // KNN search expects a precomputed embedding from the pipeline / hooks.
      let embedding = context.embedding;
      if (!embedding?.length) {
        return { action: "passthrough", confidence: 0, reason: "no embedding" };
      }

      const rows = await bank.search({
        embedding,
        query: message,
        top_k: mem.top_k ?? 10,
      });

      if (!rows.length) {
        // Empty can mean: no KNN hits, JOIN mismatch, dim mismatch, or all candidates filtered inside MemoryBank.search (score_threshold).
        return { action: "passthrough", confidence: 0, reason: "no memory matches" };
      }

      const best = rows[0];
      // `MemoryBank.search` already applies `memory.score_threshold`; survivors are above threshold here.

      // Prefer feedback tier (0=LOCAL, 1=CLOUD) over stored `model` string. Rows can have
      // tier=1 while `model` still points at the LOCAL id (stale row); routing must follow tier.
      const tierRules = parseTierRules(parsed.router.rules);
      const tierLabel = best.tier === 1 ? "CLOUD" : "LOCAL";
      let target = modelTargetForTier(tierLabel, tierRules, parsed.llms);
      if (!target) {
        target = resolveModelRef(parsed.llms, best.model);
      }
      if (!target) {
        return { action: "passthrough", confidence: 0, reason: "unknown model in memory" };
      }

      return {
        action: "redirect",
        target,
        confidence: best.score,
        reason: `memory (score=${best.score.toFixed(4)})`,
      };
    },
  };

  return {
    router,
    getMemoryBank: () => bank,
  };
}
