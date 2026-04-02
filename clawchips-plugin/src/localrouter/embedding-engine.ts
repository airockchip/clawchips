/**
 * ClawChips Smart Router — Embedding Engine
 *
 * Computes embeddings for History Router prompt vectorization.
 * Uses OpenAI-compatible /v1/embeddings API.
 */

import type { RoutingConfig } from "./types.js";

type EmbeddingConfig = NonNullable<RoutingConfig["embedding"]>;

let cachedConfig: EmbeddingConfig | undefined;

/** JSON POST headers including optional Bearer token from `resolveApiKey`. */
function embeddingHeaders(config: EmbeddingConfig): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  // const key = resolveApiKey(config);
  // if (key) headers.Authorization = `Bearer ${key}`;
  return headers;
}

/** Stores default embedding config for `computeEmbedding` when `routingConfig` is omitted. */
export function configureEmbedding(config: EmbeddingConfig): void {
  cachedConfig = config;
}

/** Enable logging the exact string passed to `/v1/embeddings` (matches `len=`). */
export function embeddingInputDebugEnabled(): boolean {
  return false;
}

/**
 * Single-text embedding via OpenAI-compatible `/v1/embeddings`; uses `routingConfig` or last `configureEmbedding`.
 */
export async function computeEmbedding(text: string, routingConfig?: RoutingConfig): Promise<number[]> {
  const config = routingConfig?.embedding ?? cachedConfig;
  if (!config?.model) {
    console.warn("[ClawChips:Embed] No embedding model configured, returning empty vector");
    return [];
  }
  return openaiCompatibleEmbedding(text, config);
}

/** Standard cosine similarity in [0, 1] range for equal-length non-empty vectors. */
export function cosineSimilarity(a: number[], b: number[]): number {
  if (a.length !== b.length || a.length === 0) return 0;
  let dot = 0, normA = 0, normB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }
  const denom = Math.sqrt(normA) * Math.sqrt(normB);
  return denom === 0 ? 0 : dot / denom;
}

/** Batch embedding request; order of output vectors matches `texts` when the API returns indices. */
export async function batchEmbeddings(texts: string[], routingConfig?: RoutingConfig): Promise<number[][]> {
  const config = routingConfig?.embedding ?? cachedConfig;
  if (!config?.model) {
    return texts.map(() => []);
  }
  return batchOpenaiCompatible(texts, config);
}

/** POST `/v1/embeddings` for one string; truncates input; returns `[]` on HTTP or parse errors. */
async function openaiCompatibleEmbedding(text: string, config: EmbeddingConfig): Promise<number[]> {
  // Default endpoint matches local Ollama-style servers; strip trailing slashes before appending /v1/embeddings.
  const endpoint = (config.endpoint ?? "http://localhost:11434").replace(/\/+$/, "");
  const url = `${endpoint}/v1/embeddings`;
  const model = config.model!;

  try {
    console.log(`[ClawChips:Embed] POST ${url} model=${model} len=${text.length}`);
    if (embeddingInputDebugEnabled()) {
      console.log(`[ClawChips:Embed] debug exact input (this string is what len counts) json=${JSON.stringify(text)}`);
    }

    const response = await fetch(url, {
      method: "POST",
      headers: embeddingHeaders(config),
      body: JSON.stringify({
        model,
        input: text.slice(0, 8192),
        ...(config.dimensions ? { dimensions: config.dimensions } : {}),
      }),
      signal: AbortSignal.timeout(10000),
    });

    if (!response.ok) {
      const body = await response.text().catch(() => "");
      console.error(`[ClawChips:Embed] HTTP ${response.status}: ${body.slice(0, 300)}`);
      throw new Error(`Embedding API error: ${response.status}`);
    }

    const data = (await response.json()) as {
      data?: Array<{ embedding?: number[] }>;
    };

    const embedding = data.data?.[0]?.embedding;
    if (embedding && embedding.length > 0) {
      console.log(`[ClawChips:Embed] OK dims=${embedding.length}`);
      return embedding;
    }

    console.warn("[ClawChips:Embed] Empty embedding in response");
    return [];
  } catch (err) {
    console.warn(`[ClawChips:Embed] Failed: ${String(err)}`);
    return [];
  }
}

/** Same transport as `openaiCompatibleEmbedding` with `input` as string array. */
async function batchOpenaiCompatible(texts: string[], config: EmbeddingConfig): Promise<number[][]> {
  const endpoint = (config.endpoint ?? "http://localhost:11434").replace(/\/+$/, "");
  const url = `${endpoint}/v1/embeddings`;
  const model = config.model!;

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: embeddingHeaders(config),
      body: JSON.stringify({
        model,
        input: texts.map((t) => t.slice(0, 8192)),
        ...(config.dimensions ? { dimensions: config.dimensions } : {}),
      }),
      signal: AbortSignal.timeout(30000),
    });

    if (!response.ok) {
      throw new Error(`Embedding API error: ${response.status}`);
    }

    const data = (await response.json()) as {
      data?: Array<{ embedding?: number[]; index?: number }>;
    };

    if (data.data && data.data.length === texts.length) {
      const sorted = [...data.data].sort((a, b) => (a.index ?? 0) - (b.index ?? 0));
      return sorted.map((d) => d.embedding ?? []);
    }

    return texts.map(() => []);
  } catch {
    return texts.map(() => []);
  }
}
