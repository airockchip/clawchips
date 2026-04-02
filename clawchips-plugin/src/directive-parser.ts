/**
 * User directives — based on clawchips, extended for LOCAL/CLOUD markers.
 */

import type { UserDirective, ModelTarget, Tier, HistoryFilter } from "./localrouter/types.js";

const MODEL_REGEX = /@model\(([^)]+)\)(\s+session)?/gi;
const TIER_REGEX = /@(edge|local|cloud|rk)(\s+session)?/gi;

const FORGET_ZH = ["不要用", "别用", "忘掉", "以后不用"];
const FORGET_EN = ["forget", "don't use", "stop using", "never use"];
const DELETE_ZH = ["删除", "删掉", "清除"];
const DELETE_EN = ["delete", "remove", "clear"];
const RESET_ZH = ["重置", "清空所有"];
const RESET_EN = ["reset"];
const HISTORY_ZH = ["历史", "记录"];
const HISTORY_EN = ["history", "records"];

const ROUTING_MEM_ZH = ["路由记忆", "路由库", "记忆库"];
const ROUTING_MEM_EN = ["routing memory", "route memory"];

/**
 * Parses `@model` / `@tier` markers and natural-language history/memory directives from one user message.
 */
export function parseDirectives(message: string): UserDirective[] {
  const directives: UserDirective[] = [];
  parseMarkerDirectives(message, directives);
  parseNaturalLanguageDirectives(message, directives);
  return directives;
}

/** Removes marker syntax so downstream text does not repeat directive tokens. */
export function stripDirectives(message: string): string {
  let cleaned = message.replace(MODEL_REGEX, "").replace(TIER_REGEX, "");
  return cleaned.replace(/\s{2,}/g, " ").trim();
}

/**
 * Parses `provider/model` or a bare model id; bare ids default provider to `openai` for compatibility.
 */
export function resolveModelTarget(raw: string): ModelTarget {
  const parts = raw.split("/");
  if (parts.length >= 2) {
    return { provider: parts[0], model: parts.slice(1).join("/") };
  }
  return { provider: "openai", model: raw };
}

/** Last wins: returns the most recent `prefer-tier` in the directive list. */
export function resolvePreferredTier(directives: UserDirective[]): Tier | undefined {
  for (let i = directives.length - 1; i >= 0; i--) {
    const d = directives[i];
    if (d.type === "prefer-tier") return d.tier;
  }
  return undefined;
}

/** Appends pin-model and prefer-tier directives from `@model(...)` and `@edge|local|cloud|rk` patterns. */
function parseMarkerDirectives(message: string, out: UserDirective[]): void {
  let match: RegExpExecArray | null;

  MODEL_REGEX.lastIndex = 0;
  while ((match = MODEL_REGEX.exec(message)) !== null) {
    const model = resolveModelTarget(match[1].trim());
    const scope = match[2]?.trim() === "session" ? "session" : "turn";
    out.push({ type: "pin-model", model, scope });
  }

  TIER_REGEX.lastIndex = 0;
  while ((match = TIER_REGEX.exec(message)) !== null) {
    const keyword = match[1].toLowerCase();
    const tier: Tier = keyword === "cloud" ? "CLOUD" : "LOCAL";
    const scope = match[2]?.trim() === "session" ? "session" : "turn";
    out.push({ type: "prefer-tier", tier, scope });
  }
}

/** Detects reset/delete/forget phrasing (ZH/EN) and optional model names for forget-model. */
function parseNaturalLanguageDirectives(message: string, out: UserDirective[]): void {
  const lower = message.toLowerCase();

  const mentionsRoutingMemory =
    matchesAny(lower, [...ROUTING_MEM_ZH, ...ROUTING_MEM_EN]) ||
    (matchesAny(lower, ["路由", "routing"]) && matchesAny(lower, ["记忆", "memory"]));

  if (
    matchesAny(lower, [...RESET_ZH, ...RESET_EN]) &&
    matchesAny(lower, [...HISTORY_ZH, ...HISTORY_EN, "路由", "routing"]) &&
    !mentionsRoutingMemory
  ) {
    out.push({ type: "reset-history" });
    return;
  }

  if (matchesAny(lower, [...DELETE_ZH, ...DELETE_EN]) && matchesAny(lower, [...HISTORY_ZH, ...HISTORY_EN])) {
    out.push({ type: "delete-history", filter: {} as HistoryFilter });
    return;
  }

  if (matchesAny(lower, [...FORGET_ZH, ...FORGET_EN])) {
    const modelName = extractModelNameFromMessage(lower);
    if (modelName) {
      out.push({ type: "forget-model", model: resolveModelTarget(modelName) });
    }
  }
}

/** True if `text` contains any of the given substrings. */
function matchesAny(text: string, keywords: string[]): boolean {
  return keywords.some((k) => text.includes(k));
}

const KNOWN_MODEL_PATTERNS = [
  /gpt-4o(?:-mini)?/,
  /claude[- ](?:sonnet|opus|haiku)[- ]\d+(?:\.\d+)?/,
  /o[134]-(?:mini|preview)/,
  /gemini[- ]\d+/,
  /llama\d*(?:\.\d+)?(?::\d+b)?/,
  /qwen[\d.]*(?:-[\w]+)?/i,
  /minimax[- ]?m?[\d.]*/i,
  /minicpm\d*(?:\.\d+)?/,
  /deepseek[- ]\w+/,
];

/** Heuristic: first known model substring in the message for forget-model directives. */
function extractModelNameFromMessage(lower: string): string | null {
  for (const pattern of KNOWN_MODEL_PATTERNS) {
    const match = lower.match(pattern);
    if (match) return match[0];
  }
  return null;
}
