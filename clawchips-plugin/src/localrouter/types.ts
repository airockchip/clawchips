/**
 * Local router + pipeline types (YAML, embedding, memory; clawchips-aligned routing).
 * Tier labels are **LOCAL** and **CLOUD** only.
 */

export type Tier = "LOCAL" | "CLOUD";

export type TaskComplexity = "SIMPLE" | "MEDIUM" | "COMPLEX" | "REASONING";

export type TaskCategory =
  | "coding"
  | "research"
  | "writing"
  | "analysis"
  | "productivity"
  | "tool-usage"
  | "memory"
  | "reasoning";

export type ModelTarget = {
  provider: string;
  model: string;
};

export type HookPoint = "onUserMessage" | "onToolCallProposed" | "onToolCallExecuted";

export type RoutingContext = {
  hookPoint: HookPoint;
  message?: string;
  embedding?: number[];
  sessionKey?: string;
  recentMessages?: string[];
  directives?: UserDirective[];
  pinnedModel?: ModelTarget;
  preferredTier?: Tier;
};

export type RouteAction = "passthrough" | "redirect" | "transform" | "block";

/** Output of one router `evaluate`; `confidence` merges with weight in `RouterPipeline`. */
export type RouteDecision = {
  action: RouteAction;
  target?: ModelTarget;
  complexity?: TaskComplexity;
  category?: TaskCategory;
  transformedContent?: string;
  reason?: string;
  confidence: number;
  routerId?: string;
  routerTier?: 0 | 1 | 2 | 3;
};

export type HistoryFilter = {
  before?: number;
  category?: TaskCategory;
  complexity?: TaskComplexity;
  model?: ModelTarget;
};

/** Marker or NL-derived commands applied before the pipeline runs. */
export type UserDirective =
  | { type: "pin-model"; model: ModelTarget; scope: "turn" | "session" }
  | { type: "prefer-tier"; tier: Tier; scope: "turn" | "session" }
  | { type: "forget-model"; model: ModelTarget }
  | { type: "delete-history"; filter: HistoryFilter }
  | { type: "reset-history" };

export interface Router {
  id: string;
  tier: 1 | 2 | 3;
  evaluate(
    context: RoutingContext,
    pluginConfig: Record<string, unknown>,
  ): Promise<RouteDecision>;
}

export type RouterRegistration = {
  enabled?: boolean;
  type?: "builtin" | "custom";
  module?: string;
  options?: Record<string, unknown>;
  weight?: number;
  tier?: 1 | 2 | 3;
};

/** Per-hook ordered list of router ids; omit to use registration order of all routers. */
export type PipelineConfig = {
  onUserMessage?: string[];
  onToolCallProposed?: string[];
  onToolCallExecuted?: string[];
};

export type ClawChipsDirectivesConfig = {
  enabled?: boolean;
  allowPinModel?: boolean;
  allowPreferTier?: boolean;
  allowForgetModel?: boolean;
  allowDeleteHistory?: boolean;
  allowResetHistory?: boolean;
};

export type ClawChipsPluginConfig = {
  configPath?: string;
  routing?: {
    directives?: ClawChipsDirectivesConfig;
  };
};

export type ScoringResult = {
  score: number;
  tier: Tier | null;
  confidence: number;
  signals: string[];
  agenticScore?: number;
};

/** OpenAI-compatible embeddings (used by embedding-engine + memory bank). */
export type RoutingConfig = {
  embedding?: {
    type?: "openai-compatible";
    provider?: string;
    model?: string;
    /** Base URL without trailing slash, e.g. https://api.openai.com/v1 or http://localhost:11434 */
    endpoint?: string;
    dimensions?: number;
    /** If set, sent as Authorization: Bearer … */
    apiKey?: string;
    /** Env var name for API key (default OPENAI_API_KEY) when apiKey is unset */
    apiKeyEnv?: string;
  };
};

/** SQLite-backed routing memory (parity with localrouter/memory.py MemoryConfig). */
export type MemoryConfig = {
  enabled?: boolean;
  path?: string;
  top_k?: number;
  max_query_chars?: number;
  max_prompt_chars?: number;
  per_user?: boolean;
  score_threshold?: number;
};
