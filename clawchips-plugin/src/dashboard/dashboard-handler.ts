/**
 * HTTP handler for ClawChips dashboard + API (parity with localrouter/server.py routes).
 */

import { createReadStream, existsSync, readFileSync, statSync } from "node:fs";
import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";

import { parse as parseYaml, stringify as stringifyYaml } from "yaml";

import type { ParsedLocalRouterConfig } from "../config.js";
import type { LlmsConfig, LlmModelEntry, QqToolNotifyConfig } from "../config.js";
import type { MemoryBank } from "../localrouter/memory.js";
import { applyEmbeddingFromParsedConfig, loadLocalRouterConfig } from "../config.js";
import {
  ensureModelsProviders,
  getMergedLlmsForResolve,
  mutateOpenClawJson,
  openClawProvidersFromRootDoc,
  readOpenClawJsonMutable,
} from "../openclaw-json.js";
import { resolveModelRef } from "../resolve-model.js";
import { DashboardStorage } from "./dashboard-storage.js";

/** OpenClaw passes full URL path; strip mount prefix so routes match /health, /dashboard/..., etc. */
const PLUGIN_HTTP_PREFIX = "/plugins/clawchips";

/** Maps `/plugins/clawchips/...` to internal `/...` route keys. */
function stripPluginHttpPrefix(pathname: string): string {
  if (pathname === PLUGIN_HTTP_PREFIX || pathname.startsWith(`${PLUGIN_HTTP_PREFIX}/`)) {
    const rest = pathname.slice(PLUGIN_HTTP_PREFIX.length);
    return rest === "" ? "/" : rest;
  }
  return pathname;
}

export type DashboardHandlerDeps = {
  pluginRoot: string;
  configPath: string;
  getParsed: () => ParsedLocalRouterConfig;
  setParsed: (p: ParsedLocalRouterConfig) => void;
  getMemoryBank: () => MemoryBank | null;
  /** Current storage instance (getter so config reload can swap DB without recreating the HTTP handler). */
  getDashboardStorage: () => DashboardStorage;
  onConfigReload?: () => void;
};

/** Aggregates HTTP request body as UTF-8 string. */
function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf-8")));
    req.on("error", reject);
  });
}

/** JSON response with UTF-8 charset. */
function json(res: ServerResponse, data: unknown, status = 200): void {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(data));
}

/** Flattens YAML `router.rules` rows into LOCAL/CLOUD/default string ids. */
function _rulesToMapping(rules: ParsedLocalRouterConfig["router"]["rules"]): Record<string, string> {
  const m: Record<string, string> = {};
  for (const row of rules ?? []) {
    if (row.LOCAL !== undefined) m.LOCAL = String(row.LOCAL);
    if (row.CLOUD !== undefined) m.CLOUD = String(row.CLOUD);
    if (row.default !== undefined) m.default = String(row.default);
  }
  return m;
}

/** Three-row rules list written back to YAML on routing save. */
function buildRulesYaml(localModel: string, cloudModel: string, defaultModel: string) {
  return [
    { LOCAL: localModel },
    { CLOUD: cloudModel },
    { default: defaultModel },
  ];
}

/**
 * Dropdown options: full `provider/model` ids from `llms.*.models`, plus any ids already
 * referenced in `router.rules`. `Object.keys(llms)` alone is only provider names and is
 * empty when users rely on OpenClaw-global models without an `llms:` block.
 */
function listAvailableModelRefs(parsed: ParsedLocalRouterConfig): string[] {
  const ids = new Set<string>();
  const merged = getMergedLlmsForResolve(parsed.llms ?? {});
  for (const [providerId, cfg] of Object.entries(merged)) {
    for (const m of cfg.models ?? []) {
      const mid = (m.id ?? "").trim();
      if (mid) ids.add(`${providerId}/${mid}`);
    }
  }
  const mapping = _rulesToMapping(parsed.router.rules);
  for (const v of [mapping.LOCAL, mapping.CLOUD, mapping.default]) {
    const s = (v ?? "").trim();
    if (s) ids.add(s);
  }
  return [...ids].sort((a, b) => a.localeCompare(b));
}

function routingQqToolNotifyPayload(parsed: ParsedLocalRouterConfig) {
  const cfg = parsed.qqToolNotify;
  return {
    enabled: cfg?.enabled === true,
    openid: cfg?.openid ?? "",
    type: cfg?.type ?? "c2c",
    accountId: cfg?.accountId ?? "default",
    notifyBefore: cfg?.notifyBefore === true,
    notifyAfter: cfg?.notifyAfter !== false,
    includeToolNames: cfg?.includeToolNames ?? [],
    excludeToolNames: cfg?.excludeToolNames ?? [],
    maxParamChars: cfg?.maxParamChars ?? 500,
    maxResultChars: cfg?.maxResultChars ?? 400,
  };
}

function routingConfigPayload(parsed: ParsedLocalRouterConfig, memoryAvailable: boolean) {
  const mapping = _rulesToMapping(parsed.router.rules);
  return {
    routerEnabled: parsed.router.enable !== false,
    strategy: parsed.router.strategy ?? "rules",
    readOnly: false,
    memoryEnabled: Boolean(parsed.memory?.enabled),
    memoryAvailable,
    localModel: mapping.LOCAL ?? "",
    cloudModel: mapping.CLOUD ?? "",
    defaultModel: mapping.default ?? "",
    availableModels: listAvailableModelRefs(parsed),
    qqToolNotify: routingQqToolNotifyPayload(parsed),
  };
}

function cleanStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item ?? "").trim()).filter(Boolean);
}

function sanitizeQqToolNotifyPayload(raw: unknown): QqToolNotifyConfig {
  const payload = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  const openid = typeof payload.openid === "string" ? payload.openid.trim() : "";
  const type = payload.type === "group" ? "group" : "c2c";
  const accountId =
    typeof payload.accountId === "string" && payload.accountId.trim() ? payload.accountId.trim() : "default";
  const includeToolNames = cleanStringArray(payload.includeToolNames);
  const excludeToolNames = cleanStringArray(payload.excludeToolNames);
  const maxParamChars = Number(payload.maxParamChars);
  const maxResultChars = Number(payload.maxResultChars);
  return {
    enabled: payload.enabled === true,
    openid,
    type,
    accountId,
    notifyBefore: payload.notifyBefore === true,
    notifyAfter: payload.notifyAfter !== false,
    ...(includeToolNames.length ? { includeToolNames } : {}),
    ...(excludeToolNames.length ? { excludeToolNames } : {}),
    maxParamChars: Number.isFinite(maxParamChars) ? Math.max(80, Math.min(8000, Math.floor(maxParamChars))) : 500,
    maxResultChars: Number.isFinite(maxResultChars) ? Math.max(80, Math.min(8000, Math.floor(maxResultChars))) : 400,
  };
}

function qqToolNotifyToYaml(cfg: QqToolNotifyConfig): Record<string, unknown> {
  return {
    enabled: cfg.enabled,
    ...(cfg.openid ? { openid: cfg.openid } : {}),
    type: cfg.type,
    account_id: cfg.accountId,
    on_before: cfg.notifyBefore,
    on_after: cfg.notifyAfter,
    ...(cfg.includeToolNames?.length ? { include_tool_names: cfg.includeToolNames } : {}),
    ...(cfg.excludeToolNames?.length ? { exclude_tool_names: cfg.excludeToolNames } : {}),
    max_param_chars: cfg.maxParamChars,
    max_result_chars: cfg.maxResultChars,
  };
}

/** UI rows for the providers table (counts, prices, api kind). */
function llmsToProviderApiRows(llms: LlmsConfig) {
  return Object.entries(llms).map(([id, cfg]) => ({
    id,
    api: cfg.api ?? "openai-completions",
    baseUrl: cfg.baseUrl ?? "",
    modelCount: Array.isArray(cfg.models) ? cfg.models.length : 0,
    hasApiKey: Boolean(cfg.apiKey && String(cfg.apiKey) !== "none"),
    models: (cfg.models ?? []).map((m) => ({
      id: m.id ?? "",
      name: m.name ?? m.id,
      description: m.description,
      maxTokens: m.maxTokens,
      contextWindow: m.contextWindow,
      inputPrice: m.cost?.input,
      outputPrice: m.cost?.output,
      cacheReadPrice: m.cost?.cacheRead,
      cacheWritePrice: m.cost?.cacheWrite,
    })),
  }));
}

/** Safe provider id for YAML/OpenClaw keys (no slashes or newlines). */
function validProviderId(id: string): boolean {
  const s = id.trim();
  if (!s || s.includes("/") || s.includes("\n")) return false;
  return /^[\w.-]+$/.test(s);
}

/** Serializes one model entry into `openclaw.json` `models.providers` shape. */
function modelEntryToOpenClawJson(m: LlmModelEntry): Record<string, unknown> {
  const id = (m.id ?? "").trim();
  const out: Record<string, unknown> = { id };
  if (m.name != null) out.name = m.name;
  if (m.description != null) out.description = m.description;
  if (m.maxTokens != null) out.maxTokens = m.maxTokens;
  if (m.contextWindow != null) out.contextWindow = m.contextWindow;
  if (m.cost && (m.cost.input != null || m.cost.output != null || m.cost.cacheRead != null || m.cost.cacheWrite != null)) {
    out.cost = {
      ...(m.cost.input != null ? { input: m.cost.input } : {}),
      ...(m.cost.output != null ? { output: m.cost.output } : {}),
      ...(m.cost.cacheRead != null ? { cacheRead: m.cost.cacheRead } : {}),
      ...(m.cost.cacheWrite != null ? { cacheWrite: m.cost.cacheWrite } : {}),
    };
  }
  return out;
}

/** Parses dashboard POST body into `LlmModelEntry` (ids, optional cost). */
function bodyToModelEntry(body: Record<string, unknown>): LlmModelEntry {
  const id = String(body.id ?? "").trim();
  const entry: LlmModelEntry = { id };
  if (body.name != null && String(body.name).trim()) entry.name = String(body.name).trim();
  if (body.description != null && String(body.description).trim()) entry.description = String(body.description).trim();
  if (body.maxTokens != null && body.maxTokens !== "") {
    const n = Number(body.maxTokens);
    if (Number.isFinite(n)) entry.maxTokens = Math.floor(n);
  }
  if (body.contextWindow != null && body.contextWindow !== "") {
    const n = Number(body.contextWindow);
    if (Number.isFinite(n)) entry.contextWindow = Math.floor(n);
  }
  const inp = body.inputPrice;
  const outp = body.outputPrice;
  const cacheRead = body.cacheReadPrice;
  const cacheWrite = body.cacheWritePrice;
  if ((inp != null && inp !== "") || (outp != null && outp !== "") || (cacheRead != null && cacheRead !== "") || (cacheWrite != null && cacheWrite !== "")) {
    entry.cost = {};
    if (inp != null && inp !== "") {
      const n = Number(inp);
      if (Number.isFinite(n)) entry.cost.input = n;
    }
    if (outp != null && outp !== "") {
      const n = Number(outp);
      if (Number.isFinite(n)) entry.cost.output = n;
    }
    if (cacheRead != null && cacheRead !== "") {
      const n = Number(cacheRead);
      if (Number.isFinite(n)) entry.cost.cacheRead = n;
    }
    if (cacheWrite != null && cacheWrite !== "") {
      const n = Number(cacheWrite);
      if (Number.isFinite(n)) entry.cost.cacheWrite = n;
    }
  }
  return entry;
}

/** GET `/dashboard/api/providers` payload: merged OpenClaw providers + bridge metadata. */
function providersApiPayload(deps: DashboardHandlerDeps) {
  const oc = readOpenClawJsonMutable();
  const yamlLlmsCount = Object.keys(deps.getParsed().llms ?? {}).length;
  if (!oc.ok) {
    return {
      providerCount: 0,
      providers: [] as ReturnType<typeof llmsToProviderApiRows>,
      bridge: { pendingCount: 0, pending: [], recent: [] },
      openclawConfigPath: oc.path,
      openclawError: oc.error,
      yamlLlmsCount,
    };
  }
  const llms = openClawProvidersFromRootDoc(oc.data);
  const providers = llmsToProviderApiRows(llms);
  return {
    providerCount: providers.length,
    providers,
    bridge: { pendingCount: 0, pending: [], recent: [] },
    openclawConfigPath: oc.path,
    openclawError: null as string | null,
    yamlLlmsCount,
  };
}

/**
 * Returns an HTTP handler for health, JSON APIs, static SPA, and config reload callbacks.
 */
export function createDashboardHandler(deps: DashboardHandlerDeps) {
  const staticRoot = path.join(deps.pluginRoot, "dashboard", "dist");

  return async function dashboardHttpHandler(req: IncomingMessage, res: ServerResponse): Promise<boolean> {
    const rawUrl = req.url ?? "/";
    const u = new URL(rawUrl, "http://127.0.0.1");
    const pathname = stripPluginHttpPrefix(u.pathname);
    const method = (req.method ?? "GET").toUpperCase();

    try {
      // Liveness + build info for operators.
      if (pathname === "/health" && method === "GET") {
        const parsed = deps.getParsed();
        json(res, {
          status: "ok",
          routerEnabled: parsed.router.enable !== false,
          strategy: parsed.router.strategy ?? "rules",
          llms: Object.keys(parsed.llms ?? {}),
          dashboard: {
            path: "/plugins/clawchips/dashboard/",
            built: existsSync(path.join(staticRoot, "index.html")),
            pending_provider_commands: 0,
          },
        });
        return true;
      }

      // Overview: aggregated token stats.
      if (pathname === "/statics" && method === "GET") {
        json(res, await Promise.resolve(deps.getDashboardStorage().getRoutedUsageStats()));
        return true;
      }

      // Recent request rows for widgets.
      if (pathname === "/dashboard/api/history" && method === "GET") {
        const limit = Math.min(100, Math.max(1, Number(u.searchParams.get("limit") ?? "5")));
        json(res, { items: deps.getDashboardStorage().recentRequests(limit) });
        return true;
      }

      // Paginated history with feedback columns.
      if (pathname === "/dashboard/api/feedback/history" && method === "GET") {
        const page = Math.max(1, Number(u.searchParams.get("page") ?? "1"));
        const pageSize = Math.min(100, Math.max(1, Number(u.searchParams.get("page_size") ?? "10")));
        json(res, deps.getDashboardStorage().listRequests(page, pageSize));
        return true;
      }

      // Routing memory table (MemoryBank listItems).
      if (pathname === "/dashboard/api/memory" && method === "GET") {
        const bank = deps.getMemoryBank();
        const parsed = deps.getParsed();
        const page = Math.max(1, Number(u.searchParams.get("page") ?? "1"));
        const pageSize = Math.min(100, Math.max(1, Number(u.searchParams.get("page_size") ?? "10")));
        if (!bank) {
          json(res, {
            items: [],
            page,
            page_size: pageSize,
            total: 0,
            total_pages: 0,
            enabled: Boolean(parsed.memory?.enabled),
            available: false,
          });
          return true;
        }
        const data = await bank.listItems(page, pageSize);
        json(res, {
          ...data,
          enabled: Boolean(parsed.memory?.enabled),
          available: true,
        });
        return true;
      }

      // YAML tier models + strategy (read).
      if (pathname === "/dashboard/api/routing-config" && method === "GET") {
        const parsed = deps.getParsed();
        json(res, routingConfigPayload(parsed, deps.getMemoryBank() !== null));
        return true;
      }

      // YAML tier models + strategy (write) → reload pipeline.
      if (pathname === "/dashboard/api/routing-config" && method === "PUT") {
        const body = await readBody(req);
        const payload = JSON.parse(body) as {
          routerEnabled?: boolean;
          localModel?: string;
          cloudModel?: string;
          defaultModel?: string;
          strategy?: string;
          memoryEnabled?: boolean;
          qqToolNotify?: unknown;
        };
        const routerEnabled =
          payload.routerEnabled !== undefined ? Boolean(payload.routerEnabled) : deps.getParsed().router.enable !== false;
        const localModel = (payload.localModel ?? "").trim();
        const cloudModel = (payload.cloudModel ?? "").trim();
        const defaultModel = (payload.defaultModel ?? (localModel || cloudModel)).trim();
        const strategy = (payload.strategy ?? "rules").trim() || "rules";
        const memoryEnabled =
          payload.memoryEnabled !== undefined ? Boolean(payload.memoryEnabled) : Boolean(deps.getParsed().memory?.enabled);
        const qqToolNotify = sanitizeQqToolNotifyPayload(payload.qqToolNotify);

        const parsed = deps.getParsed();
        for (const name of [localModel, cloudModel, defaultModel]) {
          if (name && !resolveModelRef(parsed.llms ?? {}, name)) {
            json(res, { error: `Unknown model id: ${name}` }, 400);
            return true;
          }
        }

        const text = readFileSync(deps.configPath, "utf-8");
        const data = parseYaml(text) as Record<string, unknown>;
        const router = (data.router as Record<string, unknown>) ?? {};
        router.enable = routerEnabled;
        router.strategy = strategy;
        router.rules = buildRulesYaml(localModel, cloudModel, defaultModel);
        data.router = router;
        const memory = (data.memory as Record<string, unknown>) ?? {};
        memory.enabled = memoryEnabled;
        data.memory = memory;
        data.qq_tool_notify = qqToolNotifyToYaml(qqToolNotify);
        const { writeFileSync } = await import("node:fs");
        writeFileSync(deps.configPath, stringifyYaml(data), "utf-8");

        const next = loadLocalRouterConfig(deps.configPath);
        deps.setParsed(next);
        applyEmbeddingFromParsedConfig(next);
        deps.onConfigReload?.();

        json(res, routingConfigPayload(next, deps.getMemoryBank() !== null));
        return true;
      }

      // OpenClaw `models.providers` snapshot for the Providers tab.
      if (pathname === "/dashboard/api/providers" && method === "GET") {
        json(res, providersApiPayload(deps));
        return true;
      }

      // User feedback on a request → optional async `recordFeedback` into MemoryBank.
      if (pathname === "/dashboard/api/feedback" && method === "POST") {
        const body = await readBody(req);
        const payload = JSON.parse(body) as { requestId?: string; feedbackTier?: string };
        const requestId = (payload.requestId ?? "").trim();
        const feedbackTier = (payload.feedbackTier ?? "").trim().toUpperCase();
        if (!requestId || !["LOCAL", "CLOUD"].includes(feedbackTier)) {
          json(res, { error: "requestId and feedbackTier (LOCAL|CLOUD) required" }, 400);
          return true;
        }
        try {
          const item = deps.getDashboardStorage().setFeedback(requestId, feedbackTier, "pending");
          const bank = deps.getMemoryBank();
          const parsed = deps.getParsed();
          const promptText = String(item.prompt_text ?? "").trim();

          let embeddingStatus: string;
          if (!promptText) {
            embeddingStatus = "skipped";
          } else if (!bank || !parsed.memory?.enabled) {
            embeddingStatus = "disabled";
          } else {
            const mapping = _rulesToMapping(parsed.router.rules);
            const targetModel =
              feedbackTier === "CLOUD" ? mapping.CLOUD ?? "" : mapping.LOCAL ?? "";
            if (!targetModel) {
              embeddingStatus = "skipped";
            } else {
              // recordFeedback runs embedding HTTP + vector search; do not block the HTTP response
              // or the dashboard UI stays on "busy" until the call completes.
              json(res, { ok: true, item, embeddingAsync: true });
              void (async () => {
                try {
                  await bank.recordFeedback({
                    query: promptText,
                    model: targetModel,
                    feedback_tier: feedbackTier,
                    strategy: String(item.strategy ?? ""),
                    user: (item.user as string) || null,
                  });
                  deps.getDashboardStorage().updateFeedbackEmbeddingStatus(requestId, "ready");
                } catch (err) {
                  deps.getDashboardStorage().updateFeedbackEmbeddingStatus(requestId, "failed");
                  console.error(`[ClawChips] feedback → memory failed: ${String(err)}`);
                }
              })();
              return true;
            }
          }

          const updated = deps.getDashboardStorage().updateFeedbackEmbeddingStatus(requestId, embeddingStatus);
          json(res, { ok: true, item: updated });
        } catch (e) {
          const msg = String(e);
          if (msg.includes("not found")) json(res, { error: msg }, 404);
          else json(res, { error: msg }, 400);
        }
        return true;
      }

      // Direct tier label on a memory row.
      const memLabelMatch = /^\/dashboard\/api\/memory\/(\d+)\/label$/.exec(pathname);
      if (memLabelMatch && method === "POST") {
        const itemId = Number(memLabelMatch[1]);
        const body = await readBody(req);
        const payload = JSON.parse(body) as { feedbackTier?: string };
        const ft = (payload.feedbackTier ?? "").trim().toUpperCase();
        if (ft !== "LOCAL" && ft !== "CLOUD") {
          json(res, { error: "feedbackTier must be LOCAL or CLOUD" }, 400);
          return true;
        }
        const bank = deps.getMemoryBank();
        if (!bank) {
          json(res, { error: "Memory unavailable" }, 503);
          return true;
        }
        const updated = await bank.updateItem(itemId, { tier: ft === "CLOUD" ? 1 : 0 });
        if (!updated) {
          json(res, { error: "not found" }, 404);
          return true;
        }
        const row = await bank.getItem(itemId);
        json(res, { ok: true, item: row });
        return true;
      }

      const memDelMatch = /^\/dashboard\/api\/memory\/(\d+)$/.exec(pathname);
      if (memDelMatch && method === "DELETE") {
        const itemId = Number(memDelMatch[1]);
        const bank = deps.getMemoryBank();
        if (!bank) {
          json(res, { error: "Memory unavailable" }, 503);
          return true;
        }
        const removed = await bank.delete(itemId);
        if (!removed) {
          json(res, { error: "not found" }, 404);
          return true;
        }
        json(res, { ok: true, item_id: itemId });
        return true;
      }

      // CRUD on `openclaw.json` providers (mutateOpenClawJson + ensureModelsProviders).
      if (pathname === "/dashboard/api/providers" && method === "POST") {
        try {
          const body = JSON.parse(await readBody(req)) as Record<string, unknown>;
          const name = String(body.name ?? "").trim();
          if (!validProviderId(name)) {
            json(res, { error: "Invalid provider id (use letters, digits, ._- only, no /)" }, 400);
            return true;
          }
          const rawModels = body.models;
          const modelIds: string[] = Array.isArray(rawModels)
            ? rawModels.map((x) => String(x).trim()).filter(Boolean)
            : typeof rawModels === "string"
              ? rawModels
                  .split(",")
                  .map((x) => x.trim())
                  .filter(Boolean)
              : [];
          mutateOpenClawJson((doc) => {
            const providers = ensureModelsProviders(doc);
            if (providers[name]) {
              throw new Error(`Provider already exists: ${name}`);
            }
            providers[name] = {
              baseUrl: String(body.baseUrl ?? "").trim(),
              api: String(body.api ?? "openai-completions").trim() || "openai-completions",
              apiKey: String(body.apiKey ?? ""),
              models: modelIds.map((mid) => ({ id: mid, name: mid })),
            };
          });
          json(res, providersApiPayload(deps));
        } catch (e) {
          json(res, { error: String(e) }, 400);
        }
        return true;
      }

      const modelPutDel = /^\/dashboard\/api\/providers\/([^/]+)\/models\/([^/]+)$/.exec(pathname);
      if (modelPutDel && (method === "PUT" || method === "DELETE")) {
        const providerId = decodeURIComponent(modelPutDel[1]);
        const modelId = decodeURIComponent(modelPutDel[2]);
        try {
          if (method === "DELETE") {
            mutateOpenClawJson((doc) => {
              const providers = ensureModelsProviders(doc);
              const raw = providers[providerId];
              if (!raw || typeof raw !== "object") {
                throw new Error(`Provider not found: ${providerId}`);
              }
              const pr = raw as Record<string, unknown>;
              const arr = Array.isArray(pr.models) ? pr.models : [];
              const next = arr.filter(
                (m) => !(m && typeof m === "object" && String((m as Record<string, unknown>).id) === modelId),
              );
              if (next.length === arr.length) {
                throw new Error(`Model not found: ${modelId}`);
              }
              pr.models = next;
            });
            json(res, providersApiPayload(deps));
            return true;
          }
          const body = JSON.parse(await readBody(req)) as Record<string, unknown>;
          mutateOpenClawJson((doc) => {
            const providers = ensureModelsProviders(doc);
            const raw = providers[providerId];
            if (!raw || typeof raw !== "object") {
              throw new Error(`Provider not found: ${providerId}`);
            }
            const pr = raw as Record<string, unknown>;
            const arr = Array.isArray(pr.models) ? [...pr.models] : [];
            const idx = arr.findIndex(
              (m) => m && typeof m === "object" && String((m as Record<string, unknown>).id) === modelId,
            );
            if (idx < 0) {
              throw new Error(`Model not found: ${modelId}`);
            }
            const entry = bodyToModelEntry({ ...body, id: modelId });
            arr[idx] = modelEntryToOpenClawJson(entry);
            pr.models = arr;
          });
          json(res, providersApiPayload(deps));
        } catch (e) {
          json(res, { error: String(e) }, 400);
        }
        return true;
      }

      const modelsPost = /^\/dashboard\/api\/providers\/([^/]+)\/models$/.exec(pathname);
      if (modelsPost && method === "POST") {
        const providerId = decodeURIComponent(modelsPost[1]);
        try {
          const body = JSON.parse(await readBody(req)) as Record<string, unknown>;
          const entry = bodyToModelEntry(body);
          if (!(entry.id ?? "").trim()) {
            json(res, { error: "Model id required" }, 400);
            return true;
          }
          mutateOpenClawJson((doc) => {
            const providers = ensureModelsProviders(doc);
            const raw = providers[providerId];
            if (!raw || typeof raw !== "object") {
              throw new Error(`Provider not found: ${providerId}`);
            }
            const pr = raw as Record<string, unknown>;
            const arr = Array.isArray(pr.models) ? [...pr.models] : [];
            const id = String(entry.id).trim();
            if (arr.some((m) => m && typeof m === "object" && String((m as Record<string, unknown>).id) === id)) {
              throw new Error(`Model already exists: ${id}`);
            }
            arr.push(modelEntryToOpenClawJson(entry));
            pr.models = arr;
          });
          json(res, providersApiPayload(deps));
        } catch (e) {
          json(res, { error: String(e) }, 400);
        }
        return true;
      }

      const providerPutDel = /^\/dashboard\/api\/providers\/([^/]+)$/.exec(pathname);
      if (providerPutDel && providerPutDel[1] && (method === "PUT" || method === "DELETE")) {
        const providerId = decodeURIComponent(providerPutDel[1]);
        try {
          if (method === "DELETE") {
            mutateOpenClawJson((doc) => {
              const providers = ensureModelsProviders(doc);
              if (!providers[providerId]) {
                throw new Error(`Provider not found: ${providerId}`);
              }
              delete providers[providerId];
            });
            json(res, providersApiPayload(deps));
            return true;
          }
          const body = JSON.parse(await readBody(req)) as Record<string, unknown>;
          mutateOpenClawJson((doc) => {
            const providers = ensureModelsProviders(doc);
            const raw = providers[providerId];
            if (!raw || typeof raw !== "object") {
              throw new Error(`Provider not found: ${providerId}`);
            }
            const pr = raw as Record<string, unknown>;
            if (body.baseUrl !== undefined) pr.baseUrl = String(body.baseUrl).trim();
            if (body.api !== undefined) {
              pr.api = String(body.api).trim() || "openai-completions";
            }
            if (body.apiKey !== undefined) {
              const k = String(body.apiKey);
              if (k !== "") pr.apiKey = k;
            }
          });
          json(res, providersApiPayload(deps));
        } catch (e) {
          json(res, { error: String(e) }, 400);
        }
        return true;
      }

      if (pathname.startsWith("/dashboard/api/providers")) {
        json(res, { error: "Not found" }, 404);
        return true;
      }

      // SPA assets under /dashboard/* (fallback to index.html for client routing).
      if (
        pathname.startsWith("/dashboard") &&
        method === "GET" &&
        !pathname.startsWith("/dashboard/api")
      ) {
        const rest = pathname === "/dashboard" || pathname === "/dashboard/" ? "/" : pathname.slice("/dashboard".length) || "/";
        let filePath = path.join(staticRoot, rest === "/" ? "index.html" : rest);
        if (rest.endsWith("/")) {
          filePath = path.join(staticRoot, "index.html");
        }
        if (!existsSync(filePath) || !statSync(filePath).isFile()) {
          filePath = path.join(staticRoot, "index.html");
        }
        const ext = path.extname(filePath);
        const types: Record<string, string> = {
          ".html": "text/html; charset=utf-8",
          ".js": "application/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8",
          ".svg": "image/svg+xml",
          ".png": "image/png",
          ".ico": "image/x-icon",
          ".json": "application/json",
        };
        const ct = types[ext] ?? "application/octet-stream";
        res.writeHead(200, { "Content-Type": ct });
        createReadStream(filePath).pipe(res);
        return true;
      }

      return false;
    } catch (e) {
      json(res, { error: String(e) }, 500);
      return true;
    }
  };
}
