/**
 * ClawChips — OpenClaw plugin: TS routing pipeline, hooks, dashboard.
 */

import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { createDashboardHandler } from "./dashboard/dashboard-handler.js";
import { DashboardStorage, parseRoutedUsageDetails, parseRoutedUsageForStats } from "./dashboard/dashboard-storage.js";
import { registerHooks } from "./hooks.js";
import { applyEmbeddingFromParsedConfig, loadLocalRouterConfig } from "./config.js";
import type { ParsedLocalRouterConfig } from "./config.js";
import type { MemoryBank } from "./localrouter/memory.js";
import { RouterPipeline, setGlobalPipeline } from "./router-pipeline.js";
import { createMemoryRouter } from "./localrouter/memory-router.js";
import { getMergedLlmsForResolve } from "./openclaw-json.js";
import { rulesRouter } from "./localrouter/rules-router.js";

/** Builds a short string describing usage object keys for debug / warning logs. */
function describeUsageForLog(u: Record<string, unknown> | undefined): string {
  if (!u || typeof u !== "object") return "usage: absent";
  const top = Object.keys(u).join(",") || "(empty)";
  const nested =
    u.usage && typeof u.usage === "object" && !Array.isArray(u.usage)
      ? ` nested.usage keys=${Object.keys(u.usage as object).join(",")}`
      : "";
  return `usage keys=${top}${nested}`;
}

/** True when CLAWCHIPS_DEBUG_TOKEN_STATS requests verbose token-stat logging. */
function envTokenStatsDebug(): boolean {
  return process.env.CLAWCHIPS_DEBUG_TOKEN_STATS === "1" || process.env.CLAWCHIPS_DEBUG_TOKEN_STATS === "true";
}

/** Logs token-stats messages via plugin logger; mirrors to console when debug env is set. */
function tokenStatsLog(api: OpenClawPluginApi, message: string): void {
  api.logger.warn(message);
  if (envTokenStatsDebug()) console.warn(message);
}

/** `cost.input` / `output` / `cacheRead` / `cacheWrite` in config are **per 1M tokens** (OpenAI-style). */
const TOKENS_PER_COST_UNIT = 1_000_000;

/**
 * Looks up per-1M-token cost rates for a `provider/model` ref from merged LLM config.
 */
function resolveModelCost(parsed: ParsedLocalRouterConfig, modelRef: string): {
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
} {
  const key = (modelRef ?? "").trim();
  if (!key) return { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 };
  const merged = getMergedLlmsForResolve(parsed.llms ?? {});
  let providerId = "";
  let modelId = "";
  if (key.includes("/")) {
    const [provider, ...rest] = key.split("/");
    providerId = provider.trim();
    modelId = rest.join("/").trim();
  }
  const models = merged[providerId]?.models ?? [];
  const match = models.find((m) => (m.id ?? "").trim() === modelId || (m.name ?? "").trim() === modelId);
  return {
    input: Number(match?.cost?.input ?? 0),
    output: Number(match?.cost?.output ?? 0),
    cacheRead: Number(match?.cost?.cacheRead ?? 0),
    cacheWrite: Number(match?.cost?.cacheWrite ?? 0),
  };
}

/**
 * Computes dollar (or configured unit) costs from routed usage and model cost table.
 */
function computeUsageCost(parsed: ParsedLocalRouterConfig, modelRef: string, usage?: Record<string, unknown>): {
  input_cost: number;
  output_cost: number;
  cache_read_cost: number;
  cache_write_cost: number;
  total_cost: number;
} {
  const prices = resolveModelCost(parsed, modelRef);
  const details = parseRoutedUsageDetails(usage);
  const input_cost = (details.prompt_tokens / TOKENS_PER_COST_UNIT) * prices.input;
  const output_cost = (details.completion_tokens / TOKENS_PER_COST_UNIT) * prices.output;
  const cache_read_cost = (details.cache_read_tokens / TOKENS_PER_COST_UNIT) * prices.cacheRead;
  const cache_write_cost = (details.cache_write_tokens / TOKENS_PER_COST_UNIT) * prices.cacheWrite;
  return {
    input_cost,
    output_cost,
    cache_read_cost,
    cache_write_cost,
    total_cost: input_cost + output_cost + cache_read_cost + cache_write_cost,
  };
}

const VERSION = "0.2.0";
const MODULE_DIR = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN_ROOT = path.resolve(MODULE_DIR, "..");
const RKLLM_PROVIDER_ID = "rkllm";

/** Shape of one runtime-registered RKLLM model entry. */
type ProviderModelConfig = {
  id: string;
  name: string;
  reasoning: boolean;
  input: string[];
  cost: { input: number; output: number; cacheRead: number; cacheWrite: number };
  contextWindow: number;
  maxTokens: number;
};

type ProviderConfig = {
  baseUrl: string;
  api: string;
  models: ProviderModelConfig[];
};

/** Static RKLLM provider payload injected into OpenClaw at plugin startup. */
function buildRkllmProviderConfig(): ProviderConfig {
  return {
    baseUrl: "http://127.0.0.1:8080/v1",
    api: "openai-completions",
    models: [
      {
        id: "Qwen3-4B",
        name: "Qwen3-4B RKLLM",
        reasoning: true,
        input: ["text"],
        cost: {
          input: 0,
          output: 0,
          cacheRead: 0,
          cacheWrite: 0,
        },
        contextWindow: 32000,
        maxTokens: 4096,
      },
    ],
  };
}

/** Detect the OpenClaw plugin-install flow so install-time defaults can be seeded. */
function isPluginInstallProcess(): boolean {
  return process.argv.includes("plugins") && process.argv.includes("install");
}

/** Reads the current non-RKLLM model selection from agent defaults, if any. */
function readNonRkllmAgentModel(config: Record<string, unknown> | undefined): string | undefined {
  const agents = config?.agents;
  if (!agents || typeof agents !== "object" || Array.isArray(agents)) return undefined;
  const defaults = (agents as Record<string, unknown>).defaults;
  if (!defaults || typeof defaults !== "object" || Array.isArray(defaults)) return undefined;
  const model = (defaults as Record<string, unknown>).model;
  if (typeof model === "string") {
    const trimmed = model.trim();
    if (trimmed && !trimmed.startsWith(`${RKLLM_PROVIDER_ID}/`)) return trimmed;
  }
  if (model && typeof model === "object" && !Array.isArray(model)) {
    const primary = (model as Record<string, unknown>).primary;
    if (typeof primary === "string") {
      const trimmed = primary.trim();
      if (trimmed && !trimmed.startsWith(`${RKLLM_PROVIDER_ID}/`)) return trimmed;
    }
  }
  return undefined;
}

/** Returns configured providers from the mutable runtime config object. */
function getConfiguredProviders(config: Record<string, unknown> | undefined): Record<string, unknown> {
  const models = config?.models;
  if (!models || typeof models !== "object" || Array.isArray(models)) return {};
  const providers = (models as Record<string, unknown>).providers;
  if (!providers || typeof providers !== "object" || Array.isArray(providers)) return {};
  return providers as Record<string, unknown>;
}

/** True when the host config already contains an RKLLM provider entry. */
function hasRkllmProvider(config: Record<string, unknown> | undefined): boolean {
  const providers = getConfiguredProviders(config);
  return Boolean(providers[RKLLM_PROVIDER_ID]);
}

/** Picks a cloud model ref to use for compaction, skipping the local RKLLM provider. */
function selectCloudModelId(config: Record<string, unknown> | undefined): string | undefined {
  const configured = readNonRkllmAgentModel(config);
  if (configured) return configured;
  const providers = getConfiguredProviders(config);
  for (const [providerId, rawProvider] of Object.entries(providers)) {
    if (providerId === RKLLM_PROVIDER_ID) continue;
    if (!rawProvider || typeof rawProvider !== "object" || Array.isArray(rawProvider)) continue;
    const models = (rawProvider as { models?: Array<{ id?: string }> }).models;
    for (const model of models ?? []) {
      const modelId = (model.id ?? "").trim();
      if (modelId) return `${providerId}/${modelId}`;
    }
  }
  return undefined;
}

/** Mirrors the RKLLM provider into the host config so install persistence can pick it up. */
function applyProviderToConfig(api: OpenClawPluginApi, provider: ProviderConfig): void {
  if (!api.config) api.config = {};
  if (!api.config.models || typeof api.config.models !== "object" || Array.isArray(api.config.models)) {
    api.config.models = { providers: {} };
  }
  const models = api.config.models as Record<string, unknown>;
  if (!models.providers || typeof models.providers !== "object" || Array.isArray(models.providers)) {
    models.providers = {};
  }
  (models.providers as Record<string, unknown>)[RKLLM_PROVIDER_ID] = provider;
}

/** Seeds supported compaction defaults without touching the agent primary model. */
function applyAgentDefaults(api: OpenClawPluginApi): void {
  if (!api.config) api.config = {};
  if (!api.config.agents || typeof api.config.agents !== "object" || Array.isArray(api.config.agents)) {
    api.config.agents = {};
  }
  const agents = api.config.agents as Record<string, unknown>;
  if (!agents.defaults || typeof agents.defaults !== "object" || Array.isArray(agents.defaults)) {
    agents.defaults = {};
  }
  const defaults = agents.defaults as Record<string, unknown>;
  if (!defaults.compaction || typeof defaults.compaction !== "object" || Array.isArray(defaults.compaction)) {
    defaults.compaction = {};
  }
  const compaction = defaults.compaction as Record<string, unknown>;
  compaction.mode = "safeguard";
  compaction.reserveTokensFloor = 2048;
  compaction.reserveTokens = 4096;
  compaction.keepRecentTokens = 4096;
  const cloudModelId = selectCloudModelId(api.config);
  if (cloudModelId) compaction.model = cloudModelId;
  if (!compaction.memoryFlush || typeof compaction.memoryFlush !== "object" || Array.isArray(compaction.memoryFlush)) {
    compaction.memoryFlush = {};
  }
  (compaction.memoryFlush as Record<string, unknown>).enabled = false;
}

/** Registers the RKLLM provider and also exposes it through the mutable host config. */
function registerRkllmProvider(api: OpenClawPluginApi): void {
  if (hasRkllmProvider(api.config)) {
    api.logger.info("[ClawChips] rkllm provider already configured; skipping registration");
    return;
  }
  const provider = buildRkllmProviderConfig();
  api.registerProvider?.({
    id: RKLLM_PROVIDER_ID,
    label: "rkllm",
    aliases: ["rkllm"],
    envVars: [],
    get models() {
      return provider;
    },
    auth: [],
  });
  applyProviderToConfig(api, provider);
}

type OpenClawPluginApi = {
  logger: { info: (m: string) => void; warn: (m: string) => void; error: (m: string) => void };
  config?: Record<string, unknown>;
  pluginConfig?: Record<string, unknown>;
  on: (event: string, handler: (...args: unknown[]) => unknown | Promise<unknown>) => void;
  registerProvider?: (provider: {
    id: string;
    label: string;
    aliases?: string[];
    envVars?: string[];
    models: ProviderConfig;
    auth?: unknown[];
  }) => void;
  registerHttpRoute?: (opts: {
    path: string;
    auth?: string;
    match?: string;
    handler: (req: import("node:http").IncomingMessage, res: import("node:http").ServerResponse) => void | Promise<void>;
  }) => void;
  /** If the gateway implements plugin unload, stash cleanup runs when the plugin is torn down. */
  onPluginUnload?: (handler: () => void) => void;
};

type ResolvedConfigPath = {
  configPath: string;
  createdFromBundled: boolean;
};

/**
 * Resolves YAML config path: explicit path / env, else `~/.openclaw/clawchips.yaml`,
 * seeding from bundled default on first run when missing.
 */
function resolveConfigPath(api: OpenClawPluginApi, env: NodeJS.ProcessEnv): ResolvedConfigPath {
  const cfg = (api.pluginConfig ?? {}) as { configPath?: string };
  const explicit = (cfg.configPath || env.CLAWCHIPS_CONFIG || "").trim();
  if (explicit) return { configPath: path.resolve(explicit), createdFromBundled: false };

  const openclawDir = path.join(homedir(), ".openclaw");
  const clawchipsYaml = path.join(openclawDir, "clawchips.yaml");
  const bundled = path.join(PLUGIN_ROOT, "clawchips_default.yaml");

  if (existsSync(clawchipsYaml)) return { configPath: clawchipsYaml, createdFromBundled: false };

  mkdirSync(openclawDir, { recursive: true });
  if (existsSync(bundled)) {
    copyFileSync(bundled, clawchipsYaml);
    api.logger.info(`[ClawChips] created ${clawchipsYaml} from clawchips_default.yaml`);
    return { configPath: clawchipsYaml, createdFromBundled: true };
  }
  return { configPath: clawchipsYaml, createdFromBundled: false };
}

/**
 * Prints a boxed hint to run the setup wizard and shows config path (first-run vs reconfigure).
 */
function logInteractiveSetupHint(api: OpenClawPluginApi, configPath: string, createdFromBundled: boolean): void {
  const setupScript = path.join(PLUGIN_ROOT, "scripts", "setup.mjs");
  const command = `node ${setupScript}`;
  const bar = "========== [ClawChips] setup ==========";

  api.logger.warn("");
  api.logger.warn(bar);
  if (createdFromBundled) {
    api.logger.warn("[ClawChips] First install: default config was created — run the wizard below to finish setup.");
  } else {
    api.logger.warn("[ClawChips] Reconfigure routing and memory anytime with the command below.");
  }
  api.logger.warn(`[ClawChips]   Command:`);
  api.logger.warn(`[ClawChips]     ${command}`);
  api.logger.warn(`[ClawChips]   Config file:`);
  api.logger.warn(`[ClawChips]     ${configPath}`);
  api.logger.warn(bar);
  api.logger.warn("");
}

let _parsed: ParsedLocalRouterConfig | null = null;
let _getMemoryBank: () => MemoryBank | null = () => null;

/** Returns the last loaded parsed config; throws if register has not loaded config yet. */
function getParsed(): ParsedLocalRouterConfig {
  if (!_parsed) throw new Error("config not loaded");
  return _parsed;
}

/** Updates the in-memory parsed config (e.g. after dashboard save / reload). */
function setParsed(p: ParsedLocalRouterConfig): void {
  _parsed = p;
}

const plugin = {
  id: "clawchips",
  name: "clawchips",
  description: "Optimize OpenClaw on Rockchip RK3588 + RK1820",
  version: VERSION,

  /**
   * OpenClaw entry: resolve config, wire routing pipeline + hooks + HTTP dashboard, log setup hints.
   */
  register(api: OpenClawPluginApi) {
    // Optional kill switch — skip all registration when explicitly disabled.
    if (process.env.CLAWCHIPS_PLUGIN_DISABLED === "true" || process.env.CLAWCHIPS_PLUGIN_DISABLED === "1") {
      api.logger.info("[ClawChips] disabled via CLAWCHIPS_PLUGIN_DISABLED");
      return;
    }

    // Resolve config file path (explicit, env, or ~/.openclaw with optional seed from bundle).
    const { configPath, createdFromBundled } = resolveConfigPath(api, process.env);
    registerRkllmProvider(api);
    if (isPluginInstallProcess() || !readNonRkllmAgentModel(api.config)) {
      applyAgentDefaults(api);
    }
    if (!existsSync(configPath)) {
      api.logger.error(`[ClawChips] config not found: ${configPath}`);
      return;
    }

    // Parse YAML, cache globally, and apply embedding-engine settings from config.
    const parsed = loadLocalRouterConfig(configPath);
    _parsed = parsed;
    applyEmbeddingFromParsedConfig(parsed);

    // Build the global router pipeline (memory tier first, then rules).
    const configDir = path.dirname(configPath);
    const pipeline = new RouterPipeline(api.logger);

    if (parsed.memory?.enabled === true) {
      const { router: memRouter, getMemoryBank } = createMemoryRouter(parsed, configDir);
      _getMemoryBank = getMemoryBank;
      pipeline.register(memRouter, { enabled: true, type: "builtin", tier: 1, weight: 90 });
    } else {
      _getMemoryBank = () => null;
    }

    pipeline.register(rulesRouter, { enabled: true, type: "builtin", tier: 2, weight: 60 });
    pipeline.configure({});
    setGlobalPipeline(pipeline);

    api.logger.info("[ClawChips] Router pipeline ready");

    // Dashboard persistence for request rows and routed usage aggregates (replaced on config reload).
    let dashboardStorage = new DashboardStorage(parsed.storage, configDir);

    // Install OpenClaw hooks (LLM traffic) with dashboard record/complete callbacks.
    const disposeHooks = registerHooks(api, {
      getParsed,
      configPath,
      getMemoryBank: () => _getMemoryBank(),
      dashboard: {
        recordRequest: (row) => {
          dashboardStorage.recordRequest(
            row as {
              request_id: string;
              model: string;
              prompt_text?: string;
              requested_model?: string;
              tier?: string;
              strategy?: string;
              user?: string;
              endpoint?: string;
              is_stream?: boolean;
              status?: string;
              response_id?: string;
              usage?: Record<string, unknown>;
              error_text?: string;
            },
          );
        },
        completeRoutedTurn: (requestId, model, opts) => {
          try {
            const u =
              opts.usage && typeof opts.usage === "object"
                ? (opts.usage as Record<string, unknown>)
                : undefined;
            const cost = computeUsageCost(getParsed(), model, u);
            const parsed = parseRoutedUsageForStats(u);
            dashboardStorage.completeRequest(requestId, {
              model,
              usage: opts.usage,
              cost,
              status: opts.status ?? "completed",
              error_text: opts.error_text,
            });
            dashboardStorage.recordRoutedUsage(model, u);
            if (envTokenStatsDebug()) {
              tokenStatsLog(
                api,
                `[ClawChips] routed token stats write: model=${model} requestId=${requestId.slice(0, 10)}… status=${opts.status ?? "completed"} ` +
                  `pt=${parsed.prompt_tokens} ct=${parsed.completion_tokens} tt=${parsed.total_tokens} cost=${cost.total_cost} (${describeUsageForLog(u)})`,
              );
            } else if (parsed.prompt_tokens === 0 && parsed.completion_tokens === 0) {
              tokenStatsLog(
                api,
                `[ClawChips] routed token stats: model=${model} requestId=${requestId.slice(0, 10)}… +pt=0 +ct=0 (${describeUsageForLog(u)}). completions +1. If dashboard shows 0 tokens, host llm_output likely omits usage or uses unsupported field names.`,
              );
            }
          } catch (e) {
            api.logger.warn(`[ClawChips] dashboard completeRoutedTurn: ${String(e)}`);
          }
        },
      },
    });

    const disposePluginResources = (): void => {
      disposeHooks();
      dashboardStorage.close();
      _getMemoryBank()?.close();
      _getMemoryBank = () => null;
      setGlobalPipeline(null);
    };
    if (typeof api.onPluginUnload === "function") {
      api.onPluginUnload(() => disposePluginResources());
    }

    // HTTP handler for dashboard UI + API; reload path rebuilds pipeline like startup.
    const dashHandler = createDashboardHandler({
      pluginRoot: PLUGIN_ROOT,
      configPath,
      getParsed,
      setParsed: (p) => {
        _parsed = p;
      },
      getMemoryBank: () => _getMemoryBank(),
      getDashboardStorage: () => dashboardStorage,
      onConfigReload: () => {
        const prevBank = _getMemoryBank();
        prevBank?.close();
        dashboardStorage.close();

        const p = loadLocalRouterConfig(configPath);
        _parsed = p;
        applyEmbeddingFromParsedConfig(p);
        setGlobalPipeline(null);
        const pl = new RouterPipeline(api.logger);
        if (p.memory?.enabled === true) {
          const { router: memRouter, getMemoryBank } = createMemoryRouter(p, configDir);
          _getMemoryBank = getMemoryBank;
          pl.register(memRouter, { enabled: true, type: "builtin", tier: 1, weight: 90 });
        } else {
          _getMemoryBank = () => null;
        }
        pl.register(rulesRouter, { enabled: true, type: "builtin", tier: 2, weight: 60 });
        setGlobalPipeline(pl);
        dashboardStorage = new DashboardStorage(p.storage, configDir);
      },
    });

    // Mount plugin route; unhandled paths return 404 plain text.
    api.registerHttpRoute?.({
      path: "/plugins/clawchips",
      auth: "plugin",
      match: "prefix",
      handler: async (req, res) => {
        const handled = await dashHandler(req, res);
        if (!handled) {
          res.writeHead(404, { "Content-Type": "text/plain" });
          res.end("Not Found");
        }
      },
    });

    // Log dashboard URL and print optional first-run setup wizard instructions.
    api.logger.info(`[ClawChips] Dashboard at /plugins/clawchips/dashboard/ (config: ${configPath})`);
    logInteractiveSetupHint(api, configPath, createdFromBundled);
  },
};

export default plugin;
