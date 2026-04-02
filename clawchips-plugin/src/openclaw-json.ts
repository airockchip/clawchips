/**
 * Read/write `~/.openclaw/openclaw.json` `models.providers` (OpenClaw gateway parity).
 */

import { existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

import type { LlmsConfig } from "./config.js";

/** Absolute path to the OpenClaw gateway config JSON (`~/.openclaw/openclaw.json`). */
export function openclawConfigPath(): string {
  return path.join(homedir(), ".openclaw", "openclaw.json");
}

type CacheEntry = { mtimeMs: number; path: string; llms: LlmsConfig };
let _mergedCache: CacheEntry | null = null;

/** Clears the merged LLM cache so the next resolve picks up edited openclaw.json. */
export function invalidateOpenClawMergeCache(): void {
  _mergedCache = null;
}

/** File mtime for cache invalidation; null if missing or unreadable. */
function statMtimeMs(p: string): number | null {
  try {
    return statSync(p).mtimeMs;
  } catch {
    return null;
  }
}

/**
 * Ensures `doc.models.providers` exists as an object; returns the `providers` map for mutation.
 */
export function ensureModelsProviders(doc: Record<string, unknown>): Record<string, unknown> {
  if (!doc.models || typeof doc.models !== "object" || Array.isArray(doc.models)) {
    doc.models = {};
  }
  const models = doc.models as Record<string, unknown>;
  if (!models.providers || typeof models.providers !== "object" || Array.isArray(models.providers)) {
    models.providers = {};
  }
  return models.providers as Record<string, unknown>;
}

/** Converts one OpenClaw provider JSON object into plugin `LlmsConfig` entry shape. */
function rawProviderToLlmEntry(raw: unknown): LlmsConfig[string] {
  if (!raw || typeof raw !== "object") {
    return { api: "openai-completions", baseUrl: "", models: [] };
  }
  const c = raw as Record<string, unknown>;
  const models: NonNullable<LlmsConfig[string]["models"]> = [];
  if (Array.isArray(c.models)) {
    for (const m of c.models) {
      if (!m || typeof m !== "object") continue;
      const o = m as Record<string, unknown>;
      const id = String(o.id ?? "").trim();
      if (!id) continue;
      const cost =
        o.cost && typeof o.cost === "object" && !Array.isArray(o.cost)
          ? (o.cost as { input?: number; output?: number; cacheRead?: number; cacheWrite?: number })
          : undefined;
      models.push({
        id,
        name: o.name != null ? String(o.name) : id,
        description: o.description != null ? String(o.description) : undefined,
        maxTokens: typeof o.maxTokens === "number" ? o.maxTokens : undefined,
        contextWindow: typeof o.contextWindow === "number" ? o.contextWindow : undefined,
        cost,
      });
    }
  }
  return {
    api: typeof c.api === "string" && c.api.trim() ? c.api : "openai-completions",
    baseUrl: typeof c.baseUrl === "string" ? c.baseUrl : "",
    apiKey: typeof c.apiKey === "string" ? c.apiKey : undefined,
    models,
  };
}

/** Read `models.providers` from an already-parsed openclaw.json root object. */
export function openClawProvidersFromRootDoc(doc: Record<string, unknown>): LlmsConfig {
  const providers = doc.models;
  if (!providers || typeof providers !== "object" || Array.isArray(providers)) return {};
  const pmap = (providers as Record<string, unknown>).providers;
  if (!pmap || typeof pmap !== "object" || Array.isArray(pmap)) return {};
  const out: LlmsConfig = {};
  for (const [id, cfg] of Object.entries(pmap)) {
    const pid = id.trim();
    if (!pid) continue;
    out[pid] = rawProviderToLlmEntry(cfg);
  }
  return out;
}

/**
 * Load `models.providers` from openclaw.json as `LlmsConfig` (provider id → config).
 */
export function readOpenClawProvidersAsLlms(): LlmsConfig {
  const p = openclawConfigPath();
  if (!existsSync(p)) return {};
  try {
    const raw = readFileSync(p, "utf-8");
    const doc = JSON.parse(raw) as unknown;
    if (!doc || typeof doc !== "object" || Array.isArray(doc)) return {};
    return openClawProvidersFromRootDoc(doc as Record<string, unknown>);
  } catch {
    return {};
  }
}

/**
 * YAML `llms` overrides entries with the same provider id.
 */
export function getMergedLlmsForResolve(yamlLlms: LlmsConfig): LlmsConfig {
  const p = openclawConfigPath();
  const mtime = statMtimeMs(p);
  if (mtime != null && _mergedCache && _mergedCache.path === p && _mergedCache.mtimeMs === mtime) {
    return { ..._mergedCache.llms, ...yamlLlms };
  }
  const oc = readOpenClawProvidersAsLlms();
  _mergedCache = { path: p, mtimeMs: mtime ?? -1, llms: oc };
  return { ...oc, ...yamlLlms };
}

export type ReadOpenClawResult =
  | { ok: true; path: string; data: Record<string, unknown> }
  | { ok: false; path: string; error: string };

/**
 * Reads openclaw.json as a mutable root object, or empty object if the file is missing.
 */
export function readOpenClawJsonMutable(): ReadOpenClawResult {
  const p = openclawConfigPath();
  if (!existsSync(p)) {
    return { ok: true, path: p, data: {} };
  }
  try {
    const raw = readFileSync(p, "utf-8");
    const data = JSON.parse(raw) as unknown;
    if (!data || typeof data !== "object" || Array.isArray(data)) {
      return { ok: false, path: p, error: "openclaw.json root must be an object" };
    }
    return { ok: true, path: p, data: data as Record<string, unknown> };
  } catch (e) {
    return { ok: false, path: p, error: String(e) };
  }
}

/** Writes JSON to a temp file in the same directory, then rename (atomic replace on POSIX). */
function writeOpenClawJsonAtomic(filePath: string, data: Record<string, unknown>): void {
  const dir = path.dirname(filePath);
  mkdirSync(dir, { recursive: true });
  const tmp = path.join(dir, `.openclaw.json.${process.pid}.${Date.now()}.tmp`);
  const text = JSON.stringify(data, null, 2) + "\n";
  writeFileSync(tmp, text, "utf-8");
  renameSync(tmp, filePath);
}

/**
 * Loads openclaw.json, runs `mutator` on the root object, saves atomically, invalidates merge cache.
 */
export function mutateOpenClawJson(mutator: (doc: Record<string, unknown>) => void): { path: string; changed: boolean } {
  const r = readOpenClawJsonMutable();
  if (!r.ok) {
    throw new Error(r.error);
  }
  const before = JSON.stringify(r.data);
  mutator(r.data);
  const after = JSON.stringify(r.data);
  if (after === before) {
    return { path: r.path, changed: false };
  }
  writeOpenClawJsonAtomic(r.path, r.data);
  invalidateOpenClawMergeCache();
  return { path: r.path, changed: true };
}

