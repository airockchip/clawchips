/**
 * ClawChips — OpenClaw hooks (before_model_resolve, before_prompt_build, llm_output, session_end).
 */

import { randomUUID } from "node:crypto";

import { parseDirectives, stripDirectives } from "./directive-parser.js";
import type { ParsedLocalRouterConfig } from "./config.js";
import { computeEmbedding, embeddingInputDebugEnabled } from "./localrouter/embedding-engine.js";
import { normalizeMemoryQueryText, type MemoryBank } from "./localrouter/memory.js";
import {
  modelTargetForTier,
  parseTierRules,
  resolveModelRef,
  resolvePinModelFromLlms,
} from "./resolve-model.js";
import { getGlobalPipeline } from "./router-pipeline.js";
import type { ClawChipsDirectivesConfig, ModelTarget, RouteDecision, Tier } from "./localrouter/types.js";
import { stripInboundMetadata } from "./strip-inbound-meta.js";
import {
  buildQqToolNotifyText,
  parseQqToolNotifyFromParsedConfig,
  qqToolNotifyPairKey,
  scheduleQqToolNotify,
  shouldNotifyToolName,
} from "./qq-tool-notify.js";

export type OpenClawPluginApiLike = {
  logger: { info: (m: string) => void; warn: (m: string) => void; error: (m: string) => void };
  /** Host OpenClaw config; required for qqToolNotify → `sendProactive`. */
  config?: Record<string, unknown>;
  pluginConfig?: Record<string, unknown>;
  on: (event: string, handler: (...args: unknown[]) => unknown | Promise<unknown>) => void;
  /** When the host supports plugin unload, register cleanup (e.g. clear stash eviction timer). */
  onPluginUnload?: (handler: () => void) => void;
};

type SessionStash = {
  decision?: RouteDecision;
  startTime?: number;
  pinnedModel?: ModelTarget;
  preferredTier?: Tier;
  tokenUsage?: { input: number; output: number };
  hadError?: boolean;
  /** Dashboard SQLite: current turn */
  dashboardRequestId?: string;
  dashboardRoutedModel?: string;
  dashboardStrategy?: string;
  dashboardTier?: string;
  dashboardFinalized?: boolean;
};

const sessionStash = new Map<string, SessionStash>();
const STASH_MAX_AGE_MS = 30 * 60 * 1000;
/** Throttle "no stash" spam when llm_output session key does not match any open turn. */
let lastLlmOutputNoStashLogMs = 0;

/** True when `CLAWCHIPS_DEBUG_TOKEN_STATS` enables verbose token / dashboard logging. */
function envTokenStatsDebug(): boolean {
  return process.env.CLAWCHIPS_DEBUG_TOKEN_STATS === "1" || process.env.CLAWCHIPS_DEBUG_TOKEN_STATS === "true";
}

/** Routed token diagnostics: use warn because some hosts only surface warn+. */
function tokenStatsLog(api: OpenClawPluginApiLike, message: string): void {
  api.logger.warn(message);
  if (envTokenStatsDebug()) console.warn(message);
}

/**
 * Hosts may expose the session id under different ctx keys between hooks; align with the same lookup everywhere.
 */
function sessionKeyFromContext(ctx: Record<string, unknown> | undefined): string {
  if (!ctx || typeof ctx !== "object") return "";
  for (const key of ["sessionKey", "sessionId", "session_id", "conversationId", "conversation_id"]) {
    const v = ctx[key];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return "";
}

let stashCleanupTimer: ReturnType<typeof setInterval> | null = null;

/** Periodic eviction of stale `sessionStash` entries when session_end is missed. */
function startStashCleanup(): void {
  if (stashCleanupTimer) return;
  stashCleanupTimer = setInterval(() => {
    const now = Date.now();
    for (const [key, stash] of sessionStash) {
      // Session-end hooks are not guaranteed, so age out stale routing state defensively.
      if (stash.startTime && now - stash.startTime > STASH_MAX_AGE_MS) {
        sessionStash.delete(key);
      }
    }
  }, 5 * 60 * 1000);
  if (stashCleanupTimer && typeof stashCleanupTimer === "object" && "unref" in stashCleanupTimer) {
    (stashCleanupTimer as NodeJS.Timeout).unref();
  }
}

/** Clears the stash eviction interval (call on plugin unload or in tests). */
export function stopStashCleanup(): void {
  if (stashCleanupTimer != null) {
    clearInterval(stashCleanupTimer);
    stashCleanupTimer = null;
  }
}

/** First non-empty prompt/message field from the host event, with inbound metadata stripped. */
function extractMessage(ev: Record<string, unknown>): string {
  const raw =
    (ev.prompt as string) ||
    (ev.message as string) ||
    (ev.content as string) ||
    (ev.userMessage as string) ||
    (ev.text as string) ||
    "";
  return stripInboundMetadata(raw);
}

/** First finite numeric value among `keys` on `obj` (floor); for provider usage field variance. */
function pickUsageNumber(obj: Record<string, unknown> | undefined, keys: string[]): number {
  if (!obj) return 0;
  for (const k of keys) {
    const v = obj[k];
    if (v == null) continue;
    const n = Number(v);
    if (Number.isFinite(n)) return Math.floor(n);
  }
  return 0;
}

/** Merge `usage` object + top-level token fields (OpenClaw / provider variants). */
function normalizeLlmUsage(ev: Record<string, unknown>): {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
} {
  const fromNested =
    ev.usage && typeof ev.usage === "object" && !Array.isArray(ev.usage)
      ? (ev.usage as Record<string, unknown>)
      : {};
  const merged: Record<string, unknown> = { ...fromNested };
  for (const k of [
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input",
    "output",
    "total",
    "input_tokens",
    "output_tokens",
    "promptTokens",
    "completionTokens",
    "totalTokens",
    "inputTokens",
    "outputTokens",
    "promptTokenCount",
    "completionTokenCount",
    "totalTokenCount",
  ]) {
    if (merged[k] == null && ev[k] != null) merged[k] = ev[k];
  }
  const prompt_tokens = pickUsageNumber(merged, [
    "prompt_tokens",
    "input",
    "input_tokens",
    "promptTokens",
    "inputTokens",
    "promptTokenCount",
  ]);
  const completion_tokens = pickUsageNumber(merged, [
    "completion_tokens",
    "output",
    "output_tokens",
    "completionTokens",
    "outputTokens",
    "completionTokenCount",
  ]);
  let total_tokens = pickUsageNumber(merged, ["total_tokens", "total", "totalTokens", "totalTokenCount"]);
  if (!total_tokens) total_tokens = prompt_tokens + completion_tokens;
  return { prompt_tokens, completion_tokens, total_tokens };
}

/** Shape returned to OpenClaw to override provider/model when routing redirects. */
function makeResult(provider?: string, model?: string) {
  return provider && model ? { providerOverride: provider, modelOverride: model } : undefined;
}

/** Stable `provider/model` string for dashboard rows and memory keys. */
function modelLabel(provider: string, model: string): string {
  const p = (provider ?? "").trim();
  const m = (model ?? "").trim();
  return p && m ? `${p}/${m}` : m || p || "unknown";
}

/** Returns `LOCAL` / `CLOUD` when `target` matches YAML tier rules, else empty. */
function inferTierLabel(parsed: ParsedLocalRouterConfig, target: ModelTarget | undefined): string {
  if (!target) return "";
  const tierRules = parseTierRules(parsed.router.rules);
  const localT = modelTargetForTier("LOCAL", tierRules, parsed.llms);
  const cloudT = modelTargetForTier("CLOUD", tierRules, parsed.llms);
  if (localT && localT.provider === target.provider && localT.model === target.model) return "LOCAL";
  if (cloudT && cloudT.provider === target.provider && cloudT.model === target.model) return "CLOUD";
  return "";
}

/** Best-effort model id from context or event (before routing override). */
function requestedModelFromContext(cx: Record<string, unknown>, ev: Record<string, unknown>): string {
  const v =
    (cx.requestedModel as string) ||
    (cx.modelId as string) ||
    (cx.model as string) ||
    (ev.model as string) ||
    "";
  return String(v).trim();
}

/** Inbound metadata off first, then `@model` / `@local` markers — matches cleaned user prompt for memory. */
function normalizePromptForRoutingMemory(raw: string): string {
  return stripDirectives(stripInboundMetadata(raw));
}

export type DashboardPersistenceHooks = {
  recordRequest: (row: Record<string, unknown>) => void;
  completeRoutedTurn: (
    requestId: string,
    model: string,
    opts: {
      usage?: Record<string, unknown>;
      cost?: Record<string, unknown>;
      status?: string;
      error_text?: string;
    },
  ) => void;
};

export type HooksDeps = {
  getParsed: () => ParsedLocalRouterConfig;
  configPath: string;
  getMemoryBank: () => MemoryBank | null;
  /** When set, routed turns are written to dashboard SQLite (stats + request_history). */
  dashboard?: DashboardPersistenceHooks;
};

/** Optional per-user key for memory rows when `memory.per_user` is enabled. */
function memoryUserForStore(parsed: ParsedLocalRouterConfig, cx: Record<string, unknown>): string | undefined {
  if (!parsed.memoryConfig.per_user) return undefined;
  const u = cx.user ?? cx.userId;
  if (typeof u === "string" && u.trim()) return u.trim();
  return undefined;
}

/** Persist directive-based routing to SQLite routing memory (same store as tier-1 memory router). */
async function recordDirectiveToMemory(
  api: OpenClawPluginApiLike,
  deps: HooksDeps,
  parsed: ParsedLocalRouterConfig,
  cx: Record<string, unknown>,
  args: { message: string; target: ModelTarget; strategy: string },
): Promise<void> {
  if (parsed.memory?.enabled !== true) return;
  const bank = deps.getMemoryBank();
  if (!bank) return;
  const q = normalizePromptForRoutingMemory(args.message);
  if (!q) return;
  const modelStr = modelLabel(args.target.provider, args.target.model);
  const tl = inferTierLabel(parsed, args.target);
  const tierNum = tl === "CLOUD" ? 1 : 0;
  const user = memoryUserForStore(parsed, cx);
  try {
    await bank.add({
      query: q,
      model: modelStr,
      tier: tierNum,
      strategy: args.strategy,
      ...(user !== undefined ? { user } : {}),
    });
  } catch (e) {
    api.logger.warn(`[ClawChips] directive → memory: ${String(e)}`);
  }
}

/** Completes dashboard row for an open turn (e.g. session_end) when llm_output did not finalize. */
function flushIncompleteDashboardTurn(
  api: OpenClawPluginApiLike,
  deps: HooksDeps,
  stash: SessionStash,
  sessionKey: string,
): void {
  const dash = deps.dashboard;
  if (!dash || !stash.dashboardRequestId || stash.dashboardFinalized) return;
  const model = stash.dashboardRoutedModel ?? "unknown";
  const usage =
    stash.tokenUsage && (stash.tokenUsage.input > 0 || stash.tokenUsage.output > 0)
      ? {
          prompt_tokens: stash.tokenUsage.input,
          completion_tokens: stash.tokenUsage.output,
          total_tokens: stash.tokenUsage.input + stash.tokenUsage.output,
        }
      : undefined;
  tokenStatsLog(
    api,
    `[ClawChips] dashboard flush: requestId=${stash.dashboardRequestId.slice(0, 10)}… sessionKey=${sessionKey ? `${sessionKey.slice(0, 16)}…` : "(empty)"} ` +
      `model=${model} hasUsage=${usage ? "yes" : "no"} hadError=${stash.hadError ? "yes" : "no"}`,
  );
  try {
    dash.completeRoutedTurn(stash.dashboardRequestId, model, {
      usage,
      status: stash.hadError ? "error" : "completed",
    });
  } catch (e) {
    api.logger.warn(`[ClawChips] dashboard flush failed: ${String(e)}`);
  }
  stash.dashboardFinalized = true;
  sessionStash.set(sessionKey, stash);
}

/** Opens a new dashboard request row after flushing any stale turn for the same session. */
function beginDashboardTurn(
  api: OpenClawPluginApiLike,
  deps: HooksDeps,
  sessionKey: string,
  stash: SessionStash,
  args: {
    prompt: string;
    target: ModelTarget;
    strategy: string;
    tier: string;
    requestedModel?: string;
  },
): void {
  // Finish any previously started turn for the same session before opening a new one.
  flushIncompleteDashboardTurn(api, deps, stash, sessionKey);
  const dash = deps.dashboard;
  if (!dash) return;
  const model = modelLabel(args.target.provider, args.target.model);
  const requestId = randomUUID();
  stash.dashboardRequestId = requestId;
  stash.dashboardRoutedModel = model;
  stash.dashboardStrategy = args.strategy;
  stash.dashboardTier = args.tier;
  stash.dashboardFinalized = false;
  stash.tokenUsage = undefined;
  stash.hadError = false;
  if (!sessionKey) {
    tokenStatsLog(
      api,
      "[ClawChips] beginDashboardTurn: empty sessionKey; llm_output/session_end may use a different key and token stats can be lost.",
    );
  }
  try {
    dash.recordRequest({
      request_id: requestId,
      prompt_text: stripInboundMetadata(args.prompt),
      model,
      requested_model: args.requestedModel ?? "",
      tier: args.tier,
      strategy: args.strategy,
      status: "started",
    });
  } catch (e) {
    api.logger.warn(`[ClawChips] dashboard record failed: ${String(e)}`);
    stash.dashboardRequestId = undefined;
    stash.dashboardRoutedModel = undefined;
    stash.dashboardStrategy = undefined;
    stash.dashboardTier = undefined;
    return;
  }
  tokenStatsLog(api, `[ClawChips] dashboard turn start: requestId=${requestId.slice(0, 12)}… model=${model}`);
  sessionStash.set(sessionKey, stash);
}

/** Opens a dashboard task row without rerouting, so host-selected models still appear in task history. */
function beginDashboardObservedTurn(
  api: OpenClawPluginApiLike,
  deps: HooksDeps,
  sessionKey: string,
  stash: SessionStash,
  args: {
    prompt: string;
    strategy: string;
    requestedModel?: string;
  },
): void {
  flushIncompleteDashboardTurn(api, deps, stash, sessionKey);
  const dash = deps.dashboard;
  if (!dash) return;
  const requestId = randomUUID();
  const requestedModel = (args.requestedModel ?? "").trim();
  const initialModel = requestedModel || "unknown";
  stash.dashboardRequestId = requestId;
  stash.dashboardRoutedModel = initialModel;
  stash.dashboardStrategy = args.strategy;
  stash.dashboardTier = "";
  stash.dashboardFinalized = false;
  stash.tokenUsage = undefined;
  stash.hadError = false;
  if (!sessionKey) {
    tokenStatsLog(
      api,
      "[ClawChips] beginDashboardObservedTurn: empty sessionKey; llm_output/session_end may use a different key and token stats can be lost.",
    );
  }
  try {
    dash.recordRequest({
      request_id: requestId,
      prompt_text: stripInboundMetadata(args.prompt),
      model: initialModel,
      requested_model: requestedModel,
      tier: "",
      strategy: args.strategy,
      status: "started",
    });
  } catch (e) {
    api.logger.warn(`[ClawChips] dashboard record failed: ${String(e)}`);
    stash.dashboardRequestId = undefined;
    stash.dashboardRoutedModel = undefined;
    stash.dashboardStrategy = undefined;
    stash.dashboardTier = undefined;
    return;
  }
  tokenStatsLog(api, `[ClawChips] dashboard observed turn start: requestId=${requestId.slice(0, 12)}… model=${initialModel}`);
  sessionStash.set(sessionKey, stash);
}

/**
 * Subscribes to OpenClaw: resolves routing before the model, records dashboard stats on output,
 * and flushes on session end. Passes parsed config and MemoryBank via `deps`.
 * Returns a disposer that clears the periodic stash cleanup timer (for unload/tests).
 */
export function registerHooks(api: OpenClawPluginApiLike, deps: HooksDeps): () => void {
  const { getParsed, getMemoryBank } = deps;
  startStashCleanup();

  const pluginCfg = (api.pluginConfig ?? {}) as Record<string, unknown>;
  const routing = (pluginCfg.routing ?? {}) as Record<string, unknown>;
  const directivesConfig = (routing.directives ?? {}) as ClawChipsDirectivesConfig;
  const getQqToolNotify = () => parseQqToolNotifyFromParsedConfig(getParsed());

  const pluginConfigPayload = (): Record<string, unknown> => ({
    routing: pluginCfg.routing,
    _parsedLocalRouter: getParsed(),
    _getMemoryBank: getMemoryBank,
  });

  // Routing: directives → pipeline → optional default model; may return provider/model overrides.
  api.on("before_model_resolve", async (event, ctx) => {
    const ev = event as Record<string, unknown>;
    const cx = ctx as Record<string, unknown>;
    const sessionKey = sessionKeyFromContext(cx);
    const message = extractMessage(ev);

    api.logger.info(`[ClawChips] message: ${message}`);

    // TODO: If message starts with "Pre-compaction memory flush" or "Compaction safeguard", route directly to CLOUD
    if (message.startsWith("Pre-compaction memory flush") ||
        message.startsWith("Read HEARTBEAT.md if it exists (workspace context).") ||
        message.startsWith("A new session was started via /new or /reset.") ||
        message.startsWith("Compaction safeguard")) {
      const parsed = getParsed();
      const tierRules = parseTierRules(parsed.router.rules);
      const cloudTarget = modelTargetForTier("CLOUD", tierRules, parsed.llms);
      if (cloudTarget) {
        api.logger.info(`[ClawChips] Compaction operation: routing to CLOUD ${cloudTarget.provider}/${cloudTarget.model}`);
        return makeResult(cloudTarget.provider, cloudTarget.model);
      }
    }

    const INTERNAL_PREFIXES = ["[system]", "[internal]"];
    if (INTERNAL_PREFIXES.some((p) => message.startsWith(p))) {
      return;
    }

    if (!message.trim()) {
      return;
    }

    const parsed = getParsed();
    const stash: SessionStash = sessionStash.get(sessionKey) ?? {};
    if (parsed.router.enable === false) {
      stash.startTime = Date.now();
      beginDashboardObservedTurn(api, deps, sessionKey, stash, {
        prompt: message,
        strategy: "",
        requestedModel: requestedModelFromContext(cx, ev),
      });
      sessionStash.set(sessionKey, stash);
      api.logger.info("[ClawChips] router disabled; skipping routing");
      return;
    }

    flushIncompleteDashboardTurn(api, deps, stash, sessionKey);
    stash.startTime = Date.now();
    tokenStatsLog(
      api,
      `[ClawChips] before_model_resolve: sessionKey=${sessionKey ? `${sessionKey.slice(0, 16)}…` : "(empty)"} ` +
        `existingRequestId=${stash.dashboardRequestId ? `${stash.dashboardRequestId.slice(0, 10)}…` : "(none)"} ` +
        `messageLen=${message.length}`,
    );

    const directives =
      directivesConfig.enabled !== false ? parseDirectives(message) : [];
    const cleanedMessage = directives.length > 0 ? stripDirectives(message) : message;

    let pinnedModel: ModelTarget | undefined = stash.pinnedModel;
    let preferredTier: Tier | undefined = stash.preferredTier;

    const tierRules = parseTierRules(parsed.router.rules);

    // Directive routing wins before the normal pipeline so explicit user intent stays deterministic.
    for (const d of directives) {
      switch (d.type) {
        case "pin-model":
          if (directivesConfig.allowPinModel !== false) {
            pinnedModel = d.model;
          }
          break;
        case "prefer-tier":
          if (directivesConfig.allowPreferTier !== false) {
            preferredTier = d.tier;
            if (d.scope === "session") stash.preferredTier = d.tier;
          }
          break;
        default:
          break;
      }
    }

    if (pinnedModel) {
      pinnedModel = resolvePinModelFromLlms(parsed.llms, pinnedModel);
      const allowPin = directivesConfig.allowPinModel !== false;
      // Session-scoped pin updates the stash; otherwise reuse an existing session pin if present.
      const sessionPinThisTurn = allowPin && directives.some((d) => d.type === "pin-model" && d.scope === "session");
      const anyPinThisTurn = allowPin && directives.some((d) => d.type === "pin-model");
      if (sessionPinThisTurn) {
        stash.pinnedModel = pinnedModel;
      } else if (!anyPinThisTurn && stash.pinnedModel) {
        stash.pinnedModel = pinnedModel;
      }
      stash.decision = {
        action: "redirect",
        target: pinnedModel,
        confidence: 1,
        routerTier: 0,
        reason: "pin-model directive",
      };
      beginDashboardTurn(api, deps, sessionKey, stash, {
        prompt: cleanedMessage,
        target: pinnedModel,
        strategy: "pin-model directive",
        tier: inferTierLabel(parsed, pinnedModel),
        requestedModel: requestedModelFromContext(cx, ev),
      });
      void recordDirectiveToMemory(api, deps, parsed, cx, {
        message,
        target: pinnedModel,
        strategy: "pin-model directive",
      });
      sessionStash.set(sessionKey, stash);
      return makeResult(pinnedModel.provider, pinnedModel.model);
    }

    if (preferredTier) {
      const t = modelTargetForTier(preferredTier, tierRules, parsed.llms);
      if (t) {
        stash.decision = {
          action: "redirect",
          target: t,
          confidence: 1,
          routerTier: 0,
          reason: `prefer-tier: ${preferredTier}`,
        };
        beginDashboardTurn(api, deps, sessionKey, stash, {
          prompt: cleanedMessage,
          target: t,
          strategy: `prefer-tier: ${preferredTier}`,
          tier: inferTierLabel(parsed, t),
          requestedModel: requestedModelFromContext(cx, ev),
        });
        void recordDirectiveToMemory(api, deps, parsed, cx, {
          message,
          target: t,
          strategy: `prefer-tier: ${preferredTier}`,
        });
        sessionStash.set(sessionKey, stash);
        return makeResult(t.provider, t.model);
      }
    }

    const pipeline = getGlobalPipeline();
    if (!pipeline) {
      return;
    }

    let embedding: number[] | undefined;
    try {
      // Match MemoryBank's stored query normalization (esp. max_query_chars) so vectors align with feedback rows.
      // Strip inbound metadata again: routing embedding must not include gateway `[Tue …]` prefixes (see strip-inbound-meta).
      const embeddingSource = normalizeMemoryQueryText(
        parsed.memoryConfig,
        stripInboundMetadata(cleanedMessage),
      );
      if (embeddingInputDebugEnabled()) {
        api.logger.warn(
          `[ClawChips] embedding input debug messageLen=${message.length} cleanedLen=${cleanedMessage.length} embeddingSourceLen=${embeddingSource.length} ` +
            `message_json=${JSON.stringify(message)} cleaned_json=${JSON.stringify(cleanedMessage)} embeddingSource_json=${JSON.stringify(embeddingSource)}`,
        );
      }
      embedding = await computeEmbedding(embeddingSource, parsed.routing);
    } catch {
      // Embedding-assisted routing is best-effort; fall back to non-vector signals on failure.
      api.logger.warn("[ClawChips] embedding failed, continuing without");
    }

    const routingContext = {
      hookPoint: "onUserMessage" as const,
      message: cleanedMessage,
      embedding,
      sessionKey,
      recentMessages: (cx.recentMessages as string[]) ?? [],
      directives,
      pinnedModel,
      preferredTier,
    };

    try {
      const decision = await pipeline.run("onUserMessage", routingContext, pluginConfigPayload());

      stash.decision = decision;
      sessionStash.set(sessionKey, stash);

      if (decision.action === "redirect" && decision.target) {
        const t = decision.target;
        api.logger.info(
          `[ClawChips] Routed to ${t.provider}/${t.model} (${decision.reason ?? ""})`,
        );
        beginDashboardTurn(api, deps, sessionKey, stash, {
          prompt: cleanedMessage,
          target: t,
          strategy: String(decision.reason ?? decision.action ?? "redirect"),
          tier: inferTierLabel(parsed, t),
          requestedModel: requestedModelFromContext(cx, ev),
        });
        sessionStash.set(sessionKey, stash);
        return makeResult(t.provider, t.model);
      }

      if (decision.action === "passthrough") {
        const fb = tierRules.default
          ? resolveModelRef(parsed.llms, tierRules.default)
          : null;
        if (fb) {
          beginDashboardTurn(api, deps, sessionKey, stash, {
            prompt: cleanedMessage,
            target: fb,
            strategy: `passthrough:${tierRules.default ?? "default"}`,
            tier: inferTierLabel(parsed, fb),
            requestedModel: requestedModelFromContext(cx, ev),
          });
          sessionStash.set(sessionKey, stash);
          return makeResult(fb.provider, fb.model);
        }
      }
    } catch (err) {
      api.logger.error(`[ClawChips] pipeline failed: ${String(err)}`);
    }
  });

  // Optional: notify a fixed QQ (c2c/group) target on tool calls via qqbot `sendProactive`.
  // Read dynamically from `clawchips.yaml`; after waits for the matching before send when both are enabled.
  const qqBeforeNotifyDone = new Map<string, Promise<void>>();

  api.on("before_tool_call", async (event, ctx) => {
    const qqToolNotify = getQqToolNotify();
    if (!qqToolNotify || !qqToolNotify.notifyBefore) return;
    const ev = event as Record<string, unknown>;
    const cx = ctx as Record<string, unknown>;
    const toolName = String(ev.toolName ?? "");
    if (!shouldNotifyToolName(toolName, qqToolNotify)) return;
    const sessionKey = sessionKeyFromContext(cx);
    const runId = typeof ev.runId === "string" ? ev.runId : undefined;
    const toolCallId = typeof ev.toolCallId === "string" ? ev.toolCallId : undefined;
    const params =
      ev.params && typeof ev.params === "object" && !Array.isArray(ev.params)
        ? (ev.params as Record<string, unknown>)
        : {};
    const text = buildQqToolNotifyText({
      phase: "before",
      toolName,
      params,
      sessionKey,
      runId,
      toolCallId,
      cfg: qqToolNotify,
    });
    const p = scheduleQqToolNotify({
      cfg: qqToolNotify,
      phase: "before",
      text,
      hostConfig: api.config,
      log: api.logger,
    });
    if (qqToolNotify.notifyAfter) {
      qqBeforeNotifyDone.set(qqToolNotifyPairKey({ toolCallId, runId, toolName, sessionKey }), p);
    }
  });

  api.on("after_tool_call", async (event, ctx) => {
    const qqToolNotify = getQqToolNotify();
    if (!qqToolNotify || !qqToolNotify.notifyAfter) return;
    const ev = event as Record<string, unknown>;
    const cx = ctx as Record<string, unknown>;
    const toolName = String(ev.toolName ?? "");
    const sessionKey = sessionKeyFromContext(cx);
    const runId = typeof ev.runId === "string" ? ev.runId : undefined;
    const toolCallId = typeof ev.toolCallId === "string" ? ev.toolCallId : undefined;
    const pairKey = qqToolNotifyPairKey({ toolCallId, runId, toolName, sessionKey });
    const waitBefore = qqBeforeNotifyDone.get(pairKey);
    qqBeforeNotifyDone.delete(pairKey);

    if (!shouldNotifyToolName(toolName, qqToolNotify)) return;
    const params =
      ev.params && typeof ev.params === "object" && !Array.isArray(ev.params)
        ? (ev.params as Record<string, unknown>)
        : {};
    const durationRaw = ev.durationMs;
    const durationMs =
      typeof durationRaw === "number"
        ? durationRaw
        : typeof durationRaw === "string" && durationRaw.trim()
          ? Number(durationRaw)
          : undefined;
    const text = buildQqToolNotifyText({
      phase: "after",
      toolName,
      params,
      sessionKey,
      runId,
      toolCallId,
      cfg: qqToolNotify,
      result: ev.result,
      error: typeof ev.error === "string" ? ev.error : undefined,
      durationMs: Number.isFinite(durationMs) ? durationMs : undefined,
    });

    const sendAfter = () =>
      scheduleQqToolNotify({
        cfg: qqToolNotify,
        phase: "after",
        text,
        hostConfig: api.config,
        log: api.logger,
      });
    if (waitBefore) {
      void waitBefore.then(sendAfter);
    } else {
      void sendAfter();
    }
  });

  api.on("before_prompt_build", async (event) => {
    const ev = event as Record<string, unknown>;
    const prompt = (ev.prompt as string) || "";
    const hasMarkers =
      prompt.includes("@model(") ||
      prompt.includes("@edge") ||
      prompt.includes("@cloud") ||
      prompt.includes("@local") ||
      prompt.includes("@rk");
    if (hasMarkers) {
      return {
        appendSystemContext:
          "The user message may contain routing directives (@local, @cloud, @edge, @rk, @model). " +
          "These are system-level routing markers — ignore them and do not treat them as part of the user's actual request."
      };
    }
  });

  // Aggregate usage, complete dashboard row when the turn is terminal or usage is final.
  api.on("llm_output", async (event, ctx) => {
    const ev = event as Record<string, unknown>;
    const cx = ctx as Record<string, unknown>;
    const sessionKey = sessionKeyFromContext(cx);
    const stash = sessionStash.get(sessionKey);
    tokenStatsLog(
      api,
      `[ClawChips] llm_output: enter sessionKey=${sessionKey ? `${sessionKey.slice(0, 16)}…` : "(empty)"} ` +
        `stash=${stash ? "hit" : "miss"} ` +
        `requestId=${stash?.dashboardRequestId ? `${stash.dashboardRequestId.slice(0, 10)}…` : "(none)"} ` +
        `topKeys=${Object.keys(ev).join(",") || "(none)"}`,
    );
    if (!stash) {
      const now = Date.now();
      const shouldLog = envTokenStatsDebug() || (sessionStash.size > 0 && now - lastLlmOutputNoStashLogMs > 8_000);
      if (shouldLog) {
        lastLlmOutputNoStashLogMs = now;
        tokenStatsLog(
          api,
          `[ClawChips] llm_output: no session stash (sessionKey=${sessionKey ? `${sessionKey.slice(0, 16)}…` : "(empty)"}, stashSize=${sessionStash.size}). Token stats require the same key as before_model_resolve; ctx tried sessionKey/sessionId/conversationId.`,
        );
        if (envTokenStatsDebug()) {
          console.warn("[ClawChips] llm_output ctx keys:", Object.keys(cx));
        }
      }
      return;
    }

    const error = ev.error as string | undefined;
    const stopReason = (ev.stopReason as string) ?? (ev.stop_reason as string) ?? "";
    if (error || stopReason === "error" || stopReason === "content_filter") {
      stash.hadError = true;
    }

    const usage = normalizeLlmUsage(ev);
    const hasUsage =
      usage.prompt_tokens > 0 || usage.completion_tokens > 0 || usage.total_tokens > 0;
    if (hasUsage) {
      // Some hosts emit usage incrementally, so keep aggregating until the turn is finalized.
      if (!stash.tokenUsage) stash.tokenUsage = { input: 0, output: 0 };
      stash.tokenUsage.input += usage.prompt_tokens;
      stash.tokenUsage.output += usage.completion_tokens;
    }

    const dash = deps.dashboard;
    if (!dash || !stash.dashboardRequestId || stash.dashboardFinalized) {
      if (envTokenStatsDebug() && stash.dashboardRequestId && stash.dashboardFinalized) {
        tokenStatsLog(
          api,
          `[ClawChips] llm_output: skip (turn already finalized) requestId=${stash.dashboardRequestId.slice(0, 10)}…`,
        );
      }
      sessionStash.set(sessionKey, stash);
      return;
    }

    const finishedFlag = Boolean(ev.finished) || Boolean(ev.done) || Boolean((ev as { isComplete?: boolean }).isComplete);
    const terminal = Boolean(stopReason) || Boolean(error) || finishedFlag;
    const usageLooksFinal = hasUsage;
    if (!terminal && !usageLooksFinal) {
      // Ignore non-terminal streaming chunks that carry neither final markers nor token totals yet.
      const usageObj =
        ev.usage && typeof ev.usage === "object" && !Array.isArray(ev.usage)
          ? (ev.usage as Record<string, unknown>)
          : null;
      tokenStatsLog(
        api,
        `[ClawChips] llm_output: early return requestId=${stash.dashboardRequestId ? `${stash.dashboardRequestId.slice(0, 10)}…` : "(none)"} ` +
          `terminal=${terminal ? "yes" : "no"} finished=${finishedFlag ? "yes" : "no"} stopReason=${stopReason || "(empty)"} ` +
          `error=${error ? "yes" : "no"} parsedUsage=pt:${usage.prompt_tokens},ct:${usage.completion_tokens},tt:${usage.total_tokens} ` +
          `topKeys=${Object.keys(ev).join(",") || "(none)"}` +
          (usageObj ? ` | usage.nestedKeys=${Object.keys(usageObj).join(",")}` : ""),
      );
      sessionStash.set(sessionKey, stash);
      return;
    }

    // Prefer the accumulated stash totals because providers may split usage across multiple events.
    const mergedUsage =
      stash.tokenUsage && (stash.tokenUsage.input > 0 || stash.tokenUsage.output > 0)
        ? {
            prompt_tokens: stash.tokenUsage.input,
            completion_tokens: stash.tokenUsage.output,
            total_tokens: stash.tokenUsage.input + stash.tokenUsage.output,
          }
        : hasUsage
          ? {
              prompt_tokens: usage.prompt_tokens,
              completion_tokens: usage.completion_tokens,
              total_tokens: usage.total_tokens,
            }
          : undefined;

    if (!mergedUsage) {
      const usageObj =
        ev.usage && typeof ev.usage === "object" && !Array.isArray(ev.usage)
          ? (ev.usage as Record<string, unknown>)
          : null;
      tokenStatsLog(
        api,
        `[ClawChips] dashboard tokens: no usage merged (requestId=${String(stash.dashboardRequestId ?? "").slice(0, 10)}…) ` +
          `llm_output.topKeys=${Object.keys(ev).join(",")}` +
          (usageObj ? ` | usage.nestedKeys=${Object.keys(usageObj).join(",")}` : ""),
      );
    } else if (envTokenStatsDebug()) {
      tokenStatsLog(
        api,
        `[ClawChips] dashboard tokens: merged usage requestId=${String(stash.dashboardRequestId ?? "").slice(0, 10)}… ` +
          `pt=${mergedUsage.prompt_tokens} ct=${mergedUsage.completion_tokens} tt=${mergedUsage.total_tokens}`,
      );
    }

    const provider = String(ev.provider ?? "").trim();
    const model = String(ev.model ?? "").trim();
    const finalModel =
      (provider && model ? modelLabel(provider, model) : "") ||
      model ||
      provider ||
      stash.dashboardRoutedModel ||
      "unknown";
    stash.dashboardRoutedModel = finalModel;

    try {
      dash.completeRoutedTurn(stash.dashboardRequestId, finalModel, {
        usage: mergedUsage,
        status: error || stopReason === "error" ? "error" : "completed",
        error_text: error,
      });
    } catch (e) {
      api.logger.warn(`[ClawChips] dashboard complete failed: ${String(e)}`);
    }
    stash.dashboardFinalized = true;
    sessionStash.set(sessionKey, stash);
  });

  // Flush incomplete stats and drop session-scoped stash.
  api.on("session_end", async (_event, ctx) => {
    const sessionKey = sessionKeyFromContext(ctx as Record<string, unknown>);
    const stash = sessionStash.get(sessionKey);
    tokenStatsLog(
      api,
      `[ClawChips] session_end: sessionKey=${sessionKey ? `${sessionKey.slice(0, 16)}…` : "(empty)"} ` +
        `stash=${stash ? "hit" : "miss"} stashSize=${sessionStash.size}`,
    );
    if (stash) {
      flushIncompleteDashboardTurn(api, deps, stash, sessionKey);
    }
    sessionStash.delete(sessionKey);
  });

  // Handle before_compaction hook - route to CLOUD
  api.on("before_compaction", async (event) => {
    const ev = event as Record<string, unknown>;
    const message = extractMessage(ev);
    api.logger.info(`[ClawChips] before_compaction message: ${message}`);
  });

  api.logger.info(
    "[ClawChips] Hooks registered (before_model_resolve, before_prompt_build, before_tool_call, after_tool_call, llm_output, session_end)",
  );

  return () => stopStashCleanup();
}
