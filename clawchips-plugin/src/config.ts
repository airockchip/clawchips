/**
 * Unified YAML config parsing for ClawChips: converts files in the
 * `clawchips_default.yaml` style into typed structures and generates the
 * `RoutingConfig` used by embedding-engine / MemoryBank.
 */

import fs from "node:fs";

import { parse as parseYaml } from "yaml";

import { configureEmbedding } from "./localrouter/embedding-engine.js";
import type { MemoryConfig, RoutingConfig } from "./localrouter/types.js";

// ── Raw YAML shapes (snake_case in file; camelCase also accepted where noted) ──

export type ServeConfig = {
  host?: string;
  port?: number;
  show_model_prefix?: boolean;
};

export type LoggingConfig = {
  enabled?: boolean;
  path?: string;
  max_bytes?: number;
  backup_count?: number;
};

export type RouterRulesEntry = Record<string, string>;

export type RouterConfig = {
  enable?: boolean;
  strategy?: string;
  rules?: RouterRulesEntry[];
};

export type MemoryYaml = {
  enabled?: boolean;
  /** SQLite path; relative paths resolve against config file dir when loading. */
  path?: string;
  top_k?: number;
  score_threshold?: number;
  device?: string;
  rknn_tokenizer_path?: string;
  rknn_model_path?: string;
  prompt?: string;
  max_length?: number;
  max_query_chars?: number;
  max_prompt_chars?: number;
  per_user?: boolean;
};

export type StorageConfig = {
  max_prompt_chars?: number;
  /** Dashboard SQLite path; default ~/.clawchips/openclaw_storage.db */
  path?: string;
};

/** Prices are per **1,000,000 tokens** (same unit as common provider lists). */
export type LlmModelCost = {
  input?: number;
  output?: number;
  cacheRead?: number;
  cacheWrite?: number;
};

export type LlmModelEntry = {
  id?: string;
  name?: string;
  description?: string;
  maxTokens?: number;
  contextWindow?: number;
  cost?: LlmModelCost;
};

export type LlmProviderConfig = {
  api?: string;
  baseUrl?: string;
  apiKey?: string;
  models?: LlmModelEntry[];
};

export type LlmsConfig = Record<string, LlmProviderConfig>;

/** Top-level `embedding` block (aligned with clawchips `routing.embedding`). */
export type EmbeddingYaml = {
  type?: string;
  provider?: string;
  model?: string;
  endpoint?: string;
  dimensions?: number;
  /** Inline API key (prefer env in production). */
  api_key?: string;
  /** Env var name for Bearer token (default OPENAI_API_KEY). */
  api_key_env?: string;
  /** camelCase aliases */
  apiKey?: string;
  apiKeyEnv?: string;
};

export type QqToolNotifyConfig = {
  enabled: boolean;
  /** QQ user openid (c2c) or group openid (group). */
  openid: string;
  type: "c2c" | "group";
  accountId: string;
  /** Default `false`; set `onBefore` or `notifyBefore` to `true` to enable. */
  notifyBefore: boolean;
  notifyAfter: boolean;
  includeToolNames?: string[];
  excludeToolNames?: string[];
  maxParamChars: number;
  maxResultChars: number;
};

export type LocalRouterYamlRoot = {
  serve?: ServeConfig;
  logging?: LoggingConfig;
  router?: RouterConfig;
  memory?: MemoryYaml;
  storage?: StorageConfig;
  llms?: LlmsConfig;
  embedding?: EmbeddingYaml;
  qq_tool_notify?: Record<string, unknown>;
  qqToolNotify?: Record<string, unknown>;
};

/** Fully parsed config with `routing` ready for `computeEmbedding(..., routing)`. */
export type ParsedLocalRouterConfig = {
  serve: ServeConfig;
  logging: LoggingConfig;
  router: RouterConfig;
  memory: MemoryYaml;
  storage: StorageConfig;
  llms: LlmsConfig;
  embedding?: EmbeddingYaml;
  qqToolNotify: QqToolNotifyConfig | null;
  /** Subset for embedding-engine + MemoryBank constructor. */
  routing: RoutingConfig;
  /** Normalized memory options for `MemoryBank`. */
  memoryConfig: MemoryConfig;
};

/** Narrow `unknown` YAML nodes to plain objects (not arrays). */
function asRecord(v: unknown): Record<string, unknown> | undefined {
  return v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : undefined;
}

/** Maps the top-level `embedding` block into `RoutingConfig.embedding` for the embedding engine. */
function embeddingYamlToRouting(e: EmbeddingYaml | undefined): RoutingConfig {
  if (!e) return {};
  const apiKey = e.api_key ?? e.apiKey;
  const apiKeyEnv = e.api_key_env ?? e.apiKeyEnv;
  const typeOk = e.type === undefined || e.type === "openai-compatible";
  return {
    embedding: {
      ...(typeOk ? { type: "openai-compatible" as const } : {}),
      provider: e.provider,
      model: e.model,
      endpoint: e.endpoint,
      dimensions: e.dimensions,
      ...(apiKey !== undefined && apiKey !== "" ? { apiKey: String(apiKey) } : {}),
      ...(apiKeyEnv !== undefined && apiKeyEnv !== "" ? { apiKeyEnv: String(apiKeyEnv) } : {}),
    },
  };
}

/** Subset of memory YAML fields passed to `MemoryBank` / search limits. */
function memoryYamlToConfig(m: MemoryYaml | undefined): MemoryConfig {
  if (!m) return {};
  return {
    enabled: m.enabled,
    path: m.path,
    top_k: m.top_k,
    max_query_chars: m.max_query_chars,
    max_prompt_chars: m.max_prompt_chars,
    per_user: m.per_user,
    score_threshold: m.score_threshold,
  };
}

function parseStringList(v: unknown): string[] | undefined {
  if (!Array.isArray(v)) return undefined;
  const out = v.filter((x): x is string => typeof x === "string" && x.trim().length > 0).map((s) => s.trim());
  return out.length ? out : undefined;
}

function clampInt(v: unknown, min: number, max: number, fallback: number): number {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, Math.floor(n)));
}

function pickFirst(obj: Record<string, unknown>, keys: string[]): unknown {
  for (const key of keys) {
    if (key in obj) return obj[key];
  }
  return undefined;
}

function parseQqToolNotify(raw: Record<string, unknown> | undefined): QqToolNotifyConfig | null {
  if (!raw) return null;
  const openid = typeof raw.openid === "string" ? raw.openid.trim() : "";
  const type = raw.type === "group" ? "group" : "c2c";
  const rawAccountId = pickFirst(raw, ["accountId", "account_id"]);
  const accountId = typeof rawAccountId === "string" && rawAccountId.trim() ? rawAccountId.trim() : "default";
  const notifyBefore =
    pickFirst(raw, ["onBefore", "on_before"]) === true || pickFirst(raw, ["notifyBefore", "notify_before"]) === true;
  const notifyAfter =
    pickFirst(raw, ["onAfter", "on_after"]) !== false && pickFirst(raw, ["notifyAfter", "notify_after"]) !== false;
  const includeToolNames = parseStringList(
    pickFirst(raw, ["includeToolNames", "include_tool_names", "onlyTools", "only_tools"]),
  );
  const excludeToolNames = parseStringList(
    pickFirst(raw, ["excludeToolNames", "exclude_tool_names", "skipTools", "skip_tools"]),
  );
  const maxParamChars = clampInt(pickFirst(raw, ["maxParamChars", "max_param_chars"]), 80, 8000, 500);
  const maxResultChars = clampInt(pickFirst(raw, ["maxResultChars", "max_result_chars"]), 80, 8000, 400);
  return {
    enabled: raw.enabled === true,
    openid,
    type,
    accountId,
    notifyBefore,
    notifyAfter,
    ...(includeToolNames?.length ? { includeToolNames } : {}),
    ...(excludeToolNames?.length ? { excludeToolNames } : {}),
    maxParamChars,
    maxResultChars,
  };
}

/** Router defaults keep routing enabled unless explicitly disabled in YAML. */
function normalizeRouterConfig(router: RouterConfig | undefined): RouterConfig {
  return {
    enable: router?.enable !== false,
    strategy: router?.strategy,
    rules: router?.rules,
  };
}

/**
 * Parse YAML text into structured config and derived `routing` / `memoryConfig`.
 */
export function parseLocalRouterConfig(yamlText: string): ParsedLocalRouterConfig {
  const doc = parseYaml(yamlText) as unknown;
  const root = asRecord(doc) ?? {};

  const serve = asRecord(root.serve) as ServeConfig | undefined;
  const logging = asRecord(root.logging) as LoggingConfig | undefined;
  const router = normalizeRouterConfig(asRecord(root.router) as RouterConfig | undefined);
  const memory = asRecord(root.memory) as MemoryYaml | undefined;
  const storage = asRecord(root.storage) as StorageConfig | undefined;
  const llms = asRecord(root.llms) as LlmsConfig | undefined;
  const embedding = asRecord(root.embedding) as EmbeddingYaml | undefined;
  const qqToolNotify = parseQqToolNotify(asRecord(root.qq_tool_notify ?? root.qqToolNotify));

  return {
    serve: serve ?? {},
    logging: logging ?? {},
    router,
    memory: memory ?? {},
    storage: storage ?? {},
    llms: llms ?? {},
    ...(embedding ? { embedding } : {}),
    qqToolNotify,
    routing: embeddingYamlToRouting(embedding),
    memoryConfig: memoryYamlToConfig(memory),
  };
}

/**
 * Read a YAML file and parse (UTF-8).
 */
export function loadLocalRouterConfig(filePath: string): ParsedLocalRouterConfig {
  const yamlText = fs.readFileSync(filePath, "utf8");
  return parseLocalRouterConfig(yamlText);
}

/**
 * Push parsed `routing.embedding` into embedding-engine global cache (optional convenience before `computeEmbedding` without passing `routing` each time).
 */
export function applyEmbeddingFromParsedConfig(parsed: ParsedLocalRouterConfig): void {
  const e = parsed.routing.embedding;
  if (e?.model) configureEmbedding(e);
}
