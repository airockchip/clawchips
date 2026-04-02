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

export type LocalRouterYamlRoot = {
  serve?: ServeConfig;
  logging?: LoggingConfig;
  router?: RouterConfig;
  memory?: MemoryYaml;
  storage?: StorageConfig;
  llms?: LlmsConfig;
  embedding?: EmbeddingYaml;
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

/**
 * Parse YAML text into structured config and derived `routing` / `memoryConfig`.
 */
export function parseLocalRouterConfig(yamlText: string): ParsedLocalRouterConfig {
  const doc = parseYaml(yamlText) as unknown;
  const root = asRecord(doc) ?? {};

  const serve = asRecord(root.serve) as ServeConfig | undefined;
  const logging = asRecord(root.logging) as LoggingConfig | undefined;
  const router = asRecord(root.router) as RouterConfig | undefined;
  const memory = asRecord(root.memory) as MemoryYaml | undefined;
  const storage = asRecord(root.storage) as StorageConfig | undefined;
  const llms = asRecord(root.llms) as LlmsConfig | undefined;
  const embedding = asRecord(root.embedding) as EmbeddingYaml | undefined;

  return {
    serve: serve ?? {},
    logging: logging ?? {},
    router: router ?? {},
    memory: memory ?? {},
    storage: storage ?? {},
    llms: llms ?? {},
    ...(embedding ? { embedding } : {}),
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
