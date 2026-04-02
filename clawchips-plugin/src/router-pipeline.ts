/**
 * ClawChips — cascade pipeline (from clawchips, log prefix adapted).
 */

import type {
  HookPoint,
  Router,
  RouterRegistration,
  PipelineConfig,
  RoutingContext,
  RouteDecision,
  RouteAction,
} from "./localrouter/types.js";

type Logger = { info(m: string): void; warn(m: string): void; error(m: string): void };

type RegisteredRouter = {
  router: Router;
  registration: RouterRegistration;
};

const ACTION_PRIORITY: Record<RouteAction, number> = {
  block: 4,
  redirect: 3,
  transform: 2,
  passthrough: 1,
};

/**
 * Runs registered routers by ascending tier; may stop early when a tier’s merged decision is confident enough.
 */
export class RouterPipeline {
  private routers = new Map<string, RegisteredRouter>();
  private pipelineConfig: PipelineConfig = {};
  private logger: Logger;

  /** Minimum confidence to short-circuit after a tier (tier 1 defaults stricter than tier 2). */
  private confidenceThresholds: Record<number, number> = {
    1: 0.85,
    2: 0,
  };

  constructor(logger?: Logger) {
    this.logger = logger ?? {
      info: (m) => console.log(m),
      warn: (m) => console.warn(m),
      error: (m) => console.error(m),
    };
  }

  /** Adds or replaces a router id in the map; optional registration overrides tier/weight/enable flags. */
  register(router: Router, registration?: RouterRegistration): void {
    this.routers.set(router.id, {
      router,
      registration: registration ?? { enabled: true, type: "builtin", tier: router.tier },
    });
    this.logger.info(`[RouterPipeline] Registered router: ${router.id} (tier ${router.tier})`);
  }

  /** Merges per-router registration tweaks and/or replaces pipeline hook-point ordering. */
  configure(config: { routers?: Record<string, RouterRegistration>; pipeline?: PipelineConfig }): void {
    if (config.routers) {
      for (const [id, reg] of Object.entries(config.routers)) {
        const existing = this.routers.get(id);
        if (existing) {
          existing.registration = { ...existing.registration, ...reg };
        }
      }
    }
    if (config.pipeline) {
      this.pipelineConfig = config.pipeline;
    }
  }

  /** Dynamic-imports `registration.module` for each custom router and swaps in the loaded `Router`. */
  async loadCustomRouters(): Promise<void> {
    for (const [id, entry] of this.routers) {
      if (entry.registration.type === "custom" && entry.registration.module) {
        try {
          const mod = await import(entry.registration.module);
          const router: Router = mod.default ?? mod;
          if (typeof router.evaluate !== "function") {
            this.logger.error(`[RouterPipeline] Custom router "${id}" missing evaluate()`);
            continue;
          }
          router.id = id;
          entry.router = router;
        } catch (err) {
          this.logger.error(`[RouterPipeline] Failed to load "${id}": ${String(err)}`);
        }
      }
    }
  }

  /**
   * Evaluates routers registered for `hookPoint`, grouped by tier, returning the last merged decision
   * or the first tier that clears the confidence short-circuit threshold.
   */
  async run(
    hookPoint: HookPoint,
    context: RoutingContext,
    pluginConfig: Record<string, unknown>,
  ): Promise<RouteDecision> {
    const routerIds = this.getRoutersForHookPoint(hookPoint);
    this.logger.info(`[ClawChips:Pipeline] run(${hookPoint}) routers=[${routerIds.join(", ")}]`);
    if (routerIds.length === 0) {
      return { action: "passthrough", confidence: 0, reason: "No routers configured" };
    }

    // Resolve id list to enabled registrations; group by tier for ordered cascade.
    const entries = routerIds
      .map((id) => this.routers.get(id))
      .filter((e): e is RegisteredRouter => !!e && e.registration.enabled !== false);

    if (entries.length === 0) {
      return { action: "passthrough", confidence: 0, reason: "No enabled routers" };
    }

    const byTier = new Map<number, RegisteredRouter[]>();
    for (const entry of entries) {
      const tier = entry.registration.tier ?? entry.router.tier;
      const group = byTier.get(tier) ?? [];
      group.push(entry);
      byTier.set(tier, group);
    }

    const tiers = [...byTier.keys()].sort((a, b) => a - b);
    let lastDecision: RouteDecision = { action: "passthrough", confidence: 0 };

    for (const tier of tiers) {
      const group = byTier.get(tier)!;
      const decisions = await this.runTierGroup(group, context, pluginConfig);

      if (decisions.length === 0) continue;

      // Within a tier, parallel routers are merged by weight and action priority.
      const merged = this.mergeTierDecisions(decisions);
      this.logger.info(
        `[RouterPipeline] Tier ${tier}: action=${merged.action} confidence=${merged.confidence.toFixed(2)}` +
          (merged.target ? ` target=${merged.target.provider}/${merged.target.model}` : "") +
          (merged.reason ? ` reason="${merged.reason}"` : ""),
      );

      lastDecision = merged;

      // Stop if this tier produced a non-passthrough decision above the tier threshold (e.g. memory hit).
      const threshold = this.confidenceThresholds[tier] ?? 0;
      if (merged.action !== "passthrough" && merged.confidence >= threshold) {
        this.logger.info(
          `[RouterPipeline] Tier ${tier} short-circuit (conf ${merged.confidence.toFixed(2)} ≥ ${threshold})`,
        );
        break;
      }
    }

    return lastDecision;
  }

  /** Runs all routers in one tier in parallel; failed evaluations are logged and skipped. */
  private async runTierGroup(
    group: RegisteredRouter[],
    context: RoutingContext,
    pluginConfig: Record<string, unknown>,
  ): Promise<Array<{ decision: RouteDecision; weight: number }>> {
    const tasks = group.map(({ router, registration }) => ({
      id: router.id,
      weight: registration.weight ?? 50,
      promise: router.evaluate(context, pluginConfig).then((d) => {
        d.routerId = router.id;
        d.routerTier = router.tier;
        return d;
      }),
    }));

    const settled = await Promise.allSettled(tasks.map((t) => t.promise));
    const results: Array<{ decision: RouteDecision; weight: number }> = [];

    for (let i = 0; i < settled.length; i++) {
      const result = settled[i];
      const { id, weight } = tasks[i];
      if (result.status === "fulfilled") {
        results.push({ decision: result.value, weight });
      } else {
        this.logger.error(`[RouterPipeline] Router "${id}" failed: ${String(result.reason)}`);
      }
    }

    return results;
  }

  /** Picks highest-weight / highest-action-priority decision; blends confidence by weight across the tier. */
  private mergeTierDecisions(
    items: Array<{ decision: RouteDecision; weight: number }>,
  ): RouteDecision {
    if (items.length === 0) {
      return { action: "passthrough", confidence: 0, reason: "No decisions" };
    }
    if (items.length === 1) return items[0].decision;

    items.sort((a, b) => {
      if (b.weight !== a.weight) return b.weight - a.weight;
      return (ACTION_PRIORITY[b.decision.action] ?? 0) - (ACTION_PRIORITY[a.decision.action] ?? 0);
    });

    let winner = items[0].decision;

    if (winner.action === "passthrough") {
      const redirectCandidate = items.find((i) => i.decision.action === "redirect" && i.decision.target);
      if (redirectCandidate) {
        winner = redirectCandidate.decision;
      }
    }

    const totalWeight = items.reduce((s, i) => s + i.weight, 0);
    const weightedConfidence =
      totalWeight > 0
        ? items.reduce((s, i) => s + i.decision.confidence * i.weight, 0) / totalWeight
        : 0;

    return {
      ...winner,
      confidence: weightedConfidence,
    };
  }

  /** Uses `pipelineConfig[hookPoint]` order when set; otherwise all registered router ids. */
  private getRoutersForHookPoint(hookPoint: HookPoint): string[] {
    const configured = this.pipelineConfig[hookPoint];
    if (configured && configured.length > 0) return configured;
    return [...this.routers.keys()];
  }

  /** Registered router ids (order not guaranteed). */
  listRouters(): string[] {
    return [...this.routers.keys()];
  }

  /** True if `register` was called with this router id. */
  hasRouter(id: string): boolean {
    return this.routers.has(id);
  }
}

let globalPipeline: RouterPipeline | null = null;

/** Replaces the process-wide pipeline (e.g. on config reload). */
export function setGlobalPipeline(pipeline: RouterPipeline | null): void {
  globalPipeline = pipeline;
}

/** Current pipeline used by hooks after plugin `register` (or null before init / after teardown). */
export function getGlobalPipeline(): RouterPipeline | null {
  return globalPipeline;
}
