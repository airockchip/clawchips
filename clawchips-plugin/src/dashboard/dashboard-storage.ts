/**
 * SQLite-backed request history + routed token stats (Python storage.py parity).
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { DatabaseSync } from "node:sqlite";

import type { StorageConfig } from "../config.js";

/** ISO timestamp for SQLite `created_at` / `updated_at` columns. */
function utcNowIso(): string {
  return new Date().toISOString();
}

/** `node:sqlite` Statement.run() does not accept `undefined` as a bound value. */
function sqlFiniteNumberOrNull(v: number | undefined | null): number | null {
  if (v == null) return null;
  return Number.isFinite(v) ? v : null;
}

/** Reads token counts from flat or nested `usage` objects (provider field name variance). */
function usageInt(usage: Record<string, unknown> | undefined, ...keys: string[]): number {
  if (!usage || typeof usage !== "object") return 0;
  const tryKeys = (obj: Record<string, unknown>) => {
    for (const key of keys) {
      const v = obj[key];
      if (v == null) continue;
      const n = Number(v);
      if (Number.isFinite(n)) return Math.floor(n);
    }
    return 0;
  };
  let n = tryKeys(usage);
  if (n !== 0) return n;
  const nested = usage.usage;
  if (nested && typeof nested === "object" && !Array.isArray(nested)) {
    n = tryKeys(nested as Record<string, unknown>);
  }
  return n;
}

/** Same parsing as `recordRoutedUsage` — for logging / diagnostics. */
export function parseRoutedUsageDetails(usage?: Record<string, unknown>): {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
} {
  const pt = usageInt(
    usage,
    "prompt_tokens",
    "input",
    "input_tokens",
    "promptTokens",
    "inputTokens",
    "promptTokenCount",
  );
  const ct = usageInt(
    usage,
    "completion_tokens",
    "output",
    "output_tokens",
    "completionTokens",
    "outputTokens",
    "completionTokenCount",
  );
  let tt = usageInt(usage, "total_tokens", "total", "totalTokens", "totalTokenCount");
  if (!tt) tt = pt + ct;
  const cache_read_tokens = usageInt(usage, "cache_read_tokens", "cacheRead", "cacheReadTokens");
  const cache_write_tokens = usageInt(usage, "cache_write_tokens", "cacheWrite", "cacheWriteTokens");
  return {
    prompt_tokens: pt,
    completion_tokens: ct,
    total_tokens: tt,
    cache_read_tokens,
    cache_write_tokens,
  };
}

/** Prompt/completion/total only (no cache columns) — for aggregate stats rows. */
export function parseRoutedUsageForStats(usage?: Record<string, unknown>): {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
} {
  const { prompt_tokens, completion_tokens, total_tokens } = parseRoutedUsageDetails(usage);
  return { prompt_tokens, completion_tokens, total_tokens };
}

type UsageStatsEntry = {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  completions: number;
  requests: number;
  input_tokens: number;
  output_tokens: number;
};

function toUsageStatsEntry(row?: {
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
  requests?: number | null;
  completions?: number | null;
}): UsageStatsEntry {
  const promptTokens = Number(row?.prompt_tokens ?? 0);
  const completionTokens = Number(row?.completion_tokens ?? 0);
  const totalTokens = Number(row?.total_tokens ?? (promptTokens + completionTokens));
  const requests = Number(row?.requests ?? row?.completions ?? 0);
  return {
    prompt_tokens: promptTokens,
    completion_tokens: completionTokens,
    total_tokens: totalTokens,
    completions: requests,
    requests,
    input_tokens: promptTokens,
    output_tokens: completionTokens,
  };
}

/**
 * Persists per-request history and per-model token aggregates in SQLite (`request_history`, `routed_token_stats`).
 */
export class DashboardStorage {
  private readonly db: DatabaseSync;

  /**
   * Opens or creates the DB at `cfg.path` (default `~/.clawchips/openclaw_storage.db`), WAL mode, applies schema.
   */
  constructor(cfg: StorageConfig, configDir?: string | null) {
    let p = (cfg.path ?? "").trim();
    if (!p) {
      p = path.join(os.homedir(), ".clawchips", "openclaw_storage.db");
    } else {
      p = p.replace(/^~(?=$|[\\/])/, os.homedir());
      p = path.resolve(p);
    }
    if (!path.isAbsolute(p) && configDir) {
      p = path.join(path.resolve(configDir), p);
    }
    p = path.resolve(p);
    if (p.toLowerCase().endsWith(".jsonl")) {
      p = p.replace(/\.jsonl$/i, ".db");
    }
    fs.mkdirSync(path.dirname(p), { recursive: true });
    this.db = new DatabaseSync(p);
    this.db.exec("PRAGMA journal_mode = WAL");
    this.initSchema();
  }

  /** Creates tables and migrates missing columns on existing DBs. */
  private initSchema(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS request_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL UNIQUE,
        prompt_preview TEXT,
        prompt_text TEXT,
        model TEXT NOT NULL,
        requested_model TEXT,
        tier TEXT,
        strategy TEXT,
        endpoint TEXT,
        user TEXT,
        is_stream INTEGER NOT NULL DEFAULT 0,
        status TEXT,
        response_id TEXT,
        prompt_tokens INTEGER,
        completion_tokens INTEGER,
        total_tokens INTEGER,
        cache_read_tokens INTEGER,
        cache_write_tokens INTEGER,
        input_cost REAL,
        output_cost REAL,
        cache_read_cost REAL,
        cache_write_cost REAL,
        total_cost REAL,
        error_text TEXT,
        created_at TEXT NOT NULL,
        completed_at TEXT,
        feedback_tier TEXT,
        feedback_at TEXT,
        feedback_embedding_status TEXT
      )
    `);
    this.ensureRequestHistoryColumns();
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS routed_token_stats (
        model TEXT PRIMARY KEY,
        prompt_tokens INTEGER NOT NULL DEFAULT 0,
        completion_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens INTEGER NOT NULL DEFAULT 0,
        completions INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
      )
    `);
  }

  /** Adds cost/cache columns when upgrading from older schemas. */
  private ensureRequestHistoryColumns(): void {
    const rows = this.db.prepare(`PRAGMA table_info(request_history)`).all() as Array<{ name: string }>;
    const existing = new Set(rows.map((r) => String(r.name ?? "")));
    const missing = [
      ["cache_read_tokens", "INTEGER"],
      ["cache_write_tokens", "INTEGER"],
      ["input_cost", "REAL"],
      ["output_cost", "REAL"],
      ["cache_read_cost", "REAL"],
      ["cache_write_cost", "REAL"],
      ["total_cost", "REAL"],
    ].filter(([name]) => !existing.has(name));
    for (const [name, type] of missing) {
      this.db.exec(`ALTER TABLE request_history ADD COLUMN ${name} ${type}`);
    }
  }

  /** Upserts `routed_token_stats` for one completion (increments completions even when usage is empty). */
  recordRoutedUsage(model: string, usage?: Record<string, unknown>): Record<string, number> {
    const modelName = (model ?? "").trim();
    if (!modelName) throw new Error("model is required");
    const { prompt_tokens: pt, completion_tokens: ct, total_tokens: tt } = parseRoutedUsageForStats(usage);
    // Always persist: many providers omit usage on llm_output; we still count completions for Overview.
    this.db
      .prepare(
        `
      INSERT INTO routed_token_stats(model, prompt_tokens, completion_tokens, total_tokens, completions, updated_at)
      VALUES(?, ?, ?, ?, 1, ?)
      ON CONFLICT(model) DO UPDATE SET
        prompt_tokens = prompt_tokens + excluded.prompt_tokens,
        completion_tokens = completion_tokens + excluded.completion_tokens,
        total_tokens = total_tokens + excluded.total_tokens,
        completions = completions + 1,
        updated_at = excluded.updated_at
    `,
      )
      .run(modelName, pt, ct, tt, utcNowIso());
    const row = this.db
      .prepare(
        `SELECT prompt_tokens, completion_tokens, total_tokens, completions FROM routed_token_stats WHERE model = ?`,
      )
      .get(modelName) as
      | { prompt_tokens: number; completion_tokens: number; total_tokens: number; completions: number }
      | undefined;
    return {
      prompt_tokens: row?.prompt_tokens ?? 0,
      completion_tokens: row?.completion_tokens ?? 0,
      total_tokens: row?.total_tokens ?? 0,
      completions: row?.completions ?? 0,
    };
  }

  /** All per-model aggregates plus rolled-up totals for the Overview chart. */
  getRoutedUsageStats(): {
    by_model: Record<string, Record<string, number>>;
    totals: Record<string, number>;
    breakdown: {
      total: Record<string, number>;
      local: Record<string, number>;
      cloud: Record<string, number>;
    };
  } {
    const modelRows = this.db
      .prepare(
        `
        SELECT
          model,
          COUNT(*) AS requests,
          SUM(COALESCE(prompt_tokens, 0)) AS prompt_tokens,
          SUM(COALESCE(completion_tokens, 0)) AS completion_tokens,
          SUM(COALESCE(total_tokens, 0)) AS total_tokens
        FROM request_history
        WHERE TRIM(COALESCE(model, '')) <> ''
        GROUP BY model
        ORDER BY model ASC
        `,
      )
      .all() as Array<{
      model: string;
      prompt_tokens: number;
      completion_tokens: number;
      total_tokens: number;
      requests: number;
    }>;
    const totalRow = this.db
      .prepare(
        `
        SELECT
          COUNT(*) AS requests,
          SUM(COALESCE(prompt_tokens, 0)) AS prompt_tokens,
          SUM(COALESCE(completion_tokens, 0)) AS completion_tokens,
          SUM(COALESCE(total_tokens, 0)) AS total_tokens
        FROM request_history
        `,
      )
      .get() as {
      prompt_tokens: number | null;
      completion_tokens: number | null;
      total_tokens: number | null;
      requests: number | null;
    };
    const tierRows = this.db
      .prepare(
        `
        SELECT
          UPPER(TRIM(COALESCE(tier, ''))) AS tier,
          COUNT(*) AS requests,
          SUM(COALESCE(prompt_tokens, 0)) AS prompt_tokens,
          SUM(COALESCE(completion_tokens, 0)) AS completion_tokens,
          SUM(COALESCE(total_tokens, 0)) AS total_tokens
        FROM request_history
        GROUP BY UPPER(TRIM(COALESCE(tier, '')))
        `,
      )
      .all() as Array<{
      tier: string;
      prompt_tokens: number | null;
      completion_tokens: number | null;
      total_tokens: number | null;
      requests: number | null;
    }>;

    const by_model: Record<string, Record<string, number>> = {};
    for (const r of modelRows) {
      const m = (r.model ?? "").trim();
      if (!m) continue;
      by_model[m] = toUsageStatsEntry(r);
    }

    const breakdown = {
      total: toUsageStatsEntry(totalRow),
      local: toUsageStatsEntry(),
      cloud: toUsageStatsEntry(),
    };
    for (const row of tierRows) {
      if (row.tier === "LOCAL") breakdown.local = toUsageStatsEntry(row);
      if (row.tier === "CLOUD") breakdown.cloud = toUsageStatsEntry(row);
    }

    return {
      by_model,
      totals: breakdown.total,
      breakdown,
    };
  }

  /** Inserts or updates a `request_history` row (start of turn or upsert by `request_id`). */
  recordRequest(row: {
    request_id: string;
    prompt_text?: string;
    model: string;
    requested_model?: string;
    tier?: string;
    strategy?: string;
    user?: string;
    endpoint?: string;
    is_stream?: boolean;
    status?: string;
    response_id?: string;
    usage?: Record<string, unknown>;
    cost?: {
      input_cost?: number;
      output_cost?: number;
      cache_read_cost?: number;
      cache_write_cost?: number;
      total_cost?: number;
    };
    error_text?: string;
  }): Record<string, unknown> {
    const rid = (row.request_id ?? "").trim();
    if (!rid) throw new Error("request_id is required");
    const prompt = (row.prompt_text ?? "").trim().slice(0, 2000);
    const preview = prompt.slice(0, 160);
    const usage = row.usage;
    const parsedUsage = parseRoutedUsageDetails(usage);
    const pt = parsedUsage.prompt_tokens;
    const ct = parsedUsage.completion_tokens;
    const tt = parsedUsage.total_tokens;
    const crt = parsedUsage.cache_read_tokens;
    const cwt = parsedUsage.cache_write_tokens;
    const cost = row.cost ?? {};

    this.db
      .prepare(
        `
      INSERT INTO request_history(
        request_id, prompt_preview, prompt_text, model, requested_model, tier, strategy, endpoint, user,
        is_stream, status, response_id, prompt_tokens, completion_tokens, total_tokens,
        cache_read_tokens, cache_write_tokens, input_cost, output_cost, cache_read_cost, cache_write_cost, total_cost,
        error_text, created_at, completed_at
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(request_id) DO UPDATE SET
        prompt_preview = excluded.prompt_preview,
        prompt_text = excluded.prompt_text,
        model = excluded.model,
        requested_model = excluded.requested_model,
        tier = excluded.tier,
        strategy = excluded.strategy,
        endpoint = excluded.endpoint,
        user = excluded.user,
        is_stream = excluded.is_stream,
        status = excluded.status,
        response_id = COALESCE(excluded.response_id, request_id),
        prompt_tokens = COALESCE(excluded.prompt_tokens, prompt_tokens),
        completion_tokens = COALESCE(excluded.completion_tokens, completion_tokens),
        total_tokens = COALESCE(excluded.total_tokens, total_tokens),
        cache_read_tokens = COALESCE(excluded.cache_read_tokens, cache_read_tokens),
        cache_write_tokens = COALESCE(excluded.cache_write_tokens, cache_write_tokens),
        input_cost = COALESCE(excluded.input_cost, input_cost),
        output_cost = COALESCE(excluded.output_cost, output_cost),
        cache_read_cost = COALESCE(excluded.cache_read_cost, cache_read_cost),
        cache_write_cost = COALESCE(excluded.cache_write_cost, cache_write_cost),
        total_cost = COALESCE(excluded.total_cost, total_cost),
        error_text = excluded.error_text,
        completed_at = COALESCE(excluded.completed_at, completed_at)
    `,
      )
      .run(
        rid,
        preview || null,
        prompt || null,
        (row.model ?? "").trim(),
        row.requested_model?.trim() || null,
        row.tier?.trim() || null,
        row.strategy?.trim() || null,
        row.endpoint?.trim() || null,
        row.user?.trim() || null,
        row.is_stream ? 1 : 0,
        (row.status ?? "started").trim() || "started",
        row.response_id?.trim() || null,
        pt,
        ct,
        tt,
        crt,
        cwt,
        sqlFiniteNumberOrNull(cost.input_cost),
        sqlFiniteNumberOrNull(cost.output_cost),
        sqlFiniteNumberOrNull(cost.cache_read_cost),
        sqlFiniteNumberOrNull(cost.cache_write_cost),
        sqlFiniteNumberOrNull(cost.total_cost),
        row.error_text?.trim() || null,
        utcNowIso(),
        (row.status ?? "") !== "started" ? utcNowIso() : null,
      );
    return this.getRequest(rid);
  }

  /** Finalizes a row with usage, cost, and status when the LLM turn completes. */
  completeRequest(
    requestId: string,
    opts: {
      response_id?: string;
      usage?: Record<string, unknown>;
      cost?: {
        input_cost?: number;
        output_cost?: number;
        cache_read_cost?: number;
        cache_write_cost?: number;
        total_cost?: number;
      };
      status?: string;
      error_text?: string;
      model?: string;
      tier?: string;
    } = {},
  ): Record<string, unknown> {
    const rid = requestId.trim();
    if (!rid) throw new Error("request_id is required");
    const usage = opts.usage;
    const parsedUsage = parseRoutedUsageDetails(usage);
    const pt = parsedUsage.prompt_tokens;
    const ct = parsedUsage.completion_tokens;
    const tt = parsedUsage.total_tokens;
    const crt = parsedUsage.cache_read_tokens;
    const cwt = parsedUsage.cache_write_tokens;
    const cost = opts.cost ?? {};
    const row = this.db.prepare(`SELECT request_id FROM request_history WHERE request_id = ?`).get(rid);
    if (!row) throw new Error("not found");
    this.db
      .prepare(
        `
      UPDATE request_history SET
        model = COALESCE(?, model),
        tier = COALESCE(?, tier),
        response_id = COALESCE(?, response_id),
        prompt_tokens = COALESCE(?, prompt_tokens),
        completion_tokens = COALESCE(?, completion_tokens),
        total_tokens = COALESCE(?, total_tokens),
        cache_read_tokens = COALESCE(?, cache_read_tokens),
        cache_write_tokens = COALESCE(?, cache_write_tokens),
        input_cost = COALESCE(?, input_cost),
        output_cost = COALESCE(?, output_cost),
        cache_read_cost = COALESCE(?, cache_read_cost),
        cache_write_cost = COALESCE(?, cache_write_cost),
        total_cost = COALESCE(?, total_cost),
        status = ?,
        error_text = ?,
        completed_at = ?
      WHERE request_id = ?
    `,
      )
      .run(
        opts.model?.trim() || null,
        opts.tier?.trim() || null,
        opts.response_id?.trim() || null,
        pt,
        ct,
        tt,
        crt,
        cwt,
        sqlFiniteNumberOrNull(cost.input_cost),
        sqlFiniteNumberOrNull(cost.output_cost),
        sqlFiniteNumberOrNull(cost.cache_read_cost),
        sqlFiniteNumberOrNull(cost.cache_write_cost),
        sqlFiniteNumberOrNull(cost.total_cost),
        (opts.status ?? "completed").trim() || "completed",
        opts.error_text?.trim() || null,
        utcNowIso(),
        rid,
      );
    return this.getRequest(rid);
  }

  /** Single request row as API-friendly dict (throws if missing). */
  getRequest(requestId: string): Record<string, unknown> {
    const rid = requestId.trim();
    const row = this.db
      .prepare(
        `
      SELECT request_id, prompt_preview, prompt_text, model, requested_model, tier, strategy, endpoint, user,
             is_stream, status, response_id, prompt_tokens, completion_tokens, total_tokens,
             cache_read_tokens, cache_write_tokens, input_cost, output_cost, cache_read_cost, cache_write_cost, total_cost, error_text,
             created_at, completed_at, feedback_tier, feedback_at, feedback_embedding_status
      FROM request_history WHERE request_id = ?
    `,
      )
      .get(rid) as Record<string, unknown> | undefined;
    if (!row) throw new Error("not found");
    return this.rowToDict(row);
  }

  /** Stores user LOCAL/CLOUD feedback on a request (embedding pipeline may update status later). */
  setFeedback(requestId: string, feedbackTier: string, embeddingStatus = "pending"): Record<string, unknown> {
    const rid = requestId.trim();
    const targetTier = (feedbackTier ?? "").trim().toUpperCase();
    if (!["LOCAL", "CLOUD"].includes(targetTier)) throw new Error("feedback_tier must be LOCAL or CLOUD");
    const r = this.db.prepare(`SELECT request_id FROM request_history WHERE request_id = ?`).get(rid);
    if (!r) throw new Error("not found");
    this.db
      .prepare(
        `UPDATE request_history SET feedback_tier = ?, feedback_at = ?, feedback_embedding_status = ? WHERE request_id = ?`,
      )
      .run(targetTier, utcNowIso(), embeddingStatus, rid);
    return this.getRequest(rid);
  }

  /** Updates async embedding outcome after `recordFeedback` (ready / failed / skipped). */
  updateFeedbackEmbeddingStatus(requestId: string, status: string): Record<string, unknown> {
    const rid = requestId.trim();
    this.db
      .prepare(`UPDATE request_history SET feedback_embedding_status = ? WHERE request_id = ?`)
      .run(status, rid);
    return this.getRequest(rid);
  }

  /** Newest N rows for dashboard widgets. */
  recentRequests(limit = 5): Record<string, unknown>[] {
    const lim = Math.max(1, Math.min(100, limit));
    const rows = this.db
      .prepare(
        `
      SELECT request_id, prompt_preview, prompt_text, model, requested_model, tier, strategy, endpoint, user,
             is_stream, status, response_id, prompt_tokens, completion_tokens, total_tokens,
             cache_read_tokens, cache_write_tokens, input_cost, output_cost, cache_read_cost, cache_write_cost, total_cost, error_text,
             created_at, completed_at, feedback_tier, feedback_at, feedback_embedding_status
      FROM request_history ORDER BY datetime(created_at) DESC, id DESC LIMIT ?
    `,
      )
      .all(lim) as Record<string, unknown>[];
    return rows.map((r) => this.rowToDict(r));
  }

  /** Paginated full history for the feedback table. */
  listRequests(page = 1, pageSize = 20): {
    items: Record<string, unknown>[];
    page: number;
    page_size: number;
    total: number;
    total_pages: number;
  } {
    const currentPage = Math.max(1, page);
    const currentSize = Math.max(1, Math.min(100, pageSize));
    const offset = (currentPage - 1) * currentSize;
    const total = Number((this.db.prepare(`SELECT COUNT(*) AS c FROM request_history`).get() as { c: number }).c);
    const rows = this.db
      .prepare(
        `
      SELECT request_id, prompt_preview, prompt_text, model, requested_model, tier, strategy, endpoint, user,
             is_stream, status, response_id, prompt_tokens, completion_tokens, total_tokens,
             cache_read_tokens, cache_write_tokens, input_cost, output_cost, cache_read_cost, cache_write_cost, total_cost, error_text,
             created_at, completed_at, feedback_tier, feedback_at, feedback_embedding_status
      FROM request_history ORDER BY datetime(created_at) DESC, id DESC LIMIT ? OFFSET ?
    `,
      )
      .all(currentSize, offset) as Record<string, unknown>[];
    const totalPages = total ? Math.max(Math.ceil(total / currentSize), 1) : 0;
    return {
      items: rows.map((r) => this.rowToDict(r)),
      page: currentPage,
      page_size: currentSize,
      total,
      total_pages: totalPages,
    };
  }

  /** Normalizes SQLite row types for JSON responses. */
  private rowToDict(row: Record<string, unknown>): Record<string, unknown> {
    return {
      request_id: String(row.request_id ?? ""),
      prompt_preview: row.prompt_preview ?? "",
      prompt_text: row.prompt_text ?? "",
      model: row.model ?? "",
      requested_model: row.requested_model ?? "",
      tier: row.tier ?? "",
      strategy: row.strategy ?? "",
      endpoint: row.endpoint ?? "",
      user: row.user ?? "",
      is_stream: Boolean(row.is_stream),
      status: row.status ?? "",
      response_id: row.response_id ?? "",
      prompt_tokens: Number(row.prompt_tokens ?? 0),
      completion_tokens: Number(row.completion_tokens ?? 0),
      total_tokens: Number(row.total_tokens ?? 0),
      input_tokens: Number(row.prompt_tokens ?? 0),
      output_tokens: Number(row.completion_tokens ?? 0),
      cache_read_tokens: Number(row.cache_read_tokens ?? 0),
      cache_write_tokens: Number(row.cache_write_tokens ?? 0),
      input_cost: Number(row.input_cost ?? 0),
      output_cost: Number(row.output_cost ?? 0),
      cache_read_cost: Number(row.cache_read_cost ?? 0),
      cache_write_cost: Number(row.cache_write_cost ?? 0),
      total_cost: Number(row.total_cost ?? 0),
      error_text: row.error_text ?? "",
      created_at: row.created_at ?? "",
      completed_at: row.completed_at ?? "",
      feedback_tier: row.feedback_tier ?? "",
      feedback_at: row.feedback_at ?? "",
      feedback_embedding_status: row.feedback_embedding_status ?? "",
      has_feedback: Boolean(row.feedback_tier),
    };
  }

  /** Releases the database handle. */
  close(): void {
    this.db.close();
  }
}
