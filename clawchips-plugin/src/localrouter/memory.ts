/**
 * Routing Memory Bank — SQLite-backed vector routing memory (parity with localrouter/memory.py).
 * Embeddings via embedding-engine.ts (OpenAI-compatible /v1/embeddings).
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import * as sqliteVec from "sqlite-vec";

import { computeEmbedding } from "./embedding-engine.js";
import type { MemoryConfig, RoutingConfig } from "./types.js";

/** ISO time string for `memory_items.created_at`. */
function utcNowIso(): string {
  return new Date().toISOString();
}

/** Unit-length vector for cosine distance in sqlite-vec. */
function l2Normalize(vec: number[]): number[] {
  let s = 0;
  for (const x of vec) s += x * x;
  const d = Math.sqrt(s) + 1e-12;
  return vec.map((x) => x / d);
}

/** Expands `${VAR}`, `$VAR`, `~`, and `~/` in YAML paths. */
function expandUserEnv(p: string): string {
  const withEnv = p.replace(/\$\{([^}]+)\}/g, (_, k: string) => process.env[k] ?? "").replace(
    /\$(\w+)/g,
    (_, k: string) => process.env[k] ?? "",
  );
  if (withEnv === "~") return os.homedir();
  if (withEnv.startsWith("~/") || withEnv.startsWith("~\\")) {
    return path.join(os.homedir(), withEnv.slice(2));
  }
  return withEnv;
}

/** float32 bytes for sqlite-vec `embedding MATCH` bindings. */
function vectorToSqlBlob(vec: number[]): Uint8Array {
  const float32 = Float32Array.from(vec);
  return new Uint8Array(float32.buffer.slice(0));
}

/** Converts vec0 cosine distance to a similarity score in [0, 1]. */
function distanceToScore(distance: number): number {
  return 1 - distance;
}

/** Set `CLAWCHIPS_DEBUG_MEMORY_SEARCH=1` (or `true`) to log raw/normalized query and KNN top1 in `search` / `retrieve`. */
function memorySearchDebugEnabled(): boolean {
  const v = process.env.CLAWCHIPS_DEBUG_MEMORY_SEARCH;
  return v === "1" || v === "true";
}

/**
 * sqlite-vec `vec0` requires rowid / INTEGER PK binds to be SQLITE_INTEGER.
 * `node:sqlite` may bind JS `number` as REAL, which triggers:
 * "Only integers are allowed for primary key values on memory_vectors"
 * @see https://github.com/asg017/sqlite-vec/blob/main/sqlite-vec.c (vec0Update_InsertRowidStep)
 */
function bindSqlInteger(id: number | bigint): bigint {
  if (typeof id === "bigint") return id;
  return BigInt(Math.trunc(id));
}

function requireLastInsertRowid(info: { lastInsertRowid?: number | bigint }): bigint {
  const v = info.lastInsertRowid;
  if (v === undefined) {
    throw new Error("[Memory] memory_items INSERT did not return lastInsertRowid");
  }
  return bindSqlInteger(v);
}

/** vec0 `INTEGER` metadata columns (e.g. `tier`) — node:sqlite may bind JS `number` as REAL; sqlite-vec rejects that. */
function bindVec0Tier(tier: number): bigint {
  return tier === 1 ? 1n : 0n;
}

/** Same rules as `MemoryBank` insert/search text — must match `computeEmbedding` input in hooks for comparable vectors. */
export function normalizeMemoryQueryText(cfg: MemoryConfig, query: string): string {
  let q = (query ?? "").trim();
  const maxChars = cfg.max_query_chars ?? 500;
  if (q && maxChars > 0) q = q.slice(0, maxChars);
  return q;
}

/** Collapses whitespace and truncates for logs and SQL debug. */
function previewText(text: string | null | undefined, limit = 120): string {
  const value = String(text ?? "")
    .split(/\s+/)
    .join(" ")
    .trim();
  if (value.length <= limit) return value;
  return value.slice(0, limit) + "...";
}

/** Maps stored 0/1 tier to dashboard labels. */
function tierToLabel(tier: unknown): "CLOUD" | "LOCAL" {
  return Number(tier) === 1 ? "CLOUD" : "LOCAL";
}

type SearchRow = {
  id: number;
  tier: number;
  model: string;
  query: string | null;
  strategy: string | null;
  user: string | null;
  score: number;
  ts: string;
};

/** Compact log line for KNN result sets. */
function formatResultPreview(results: SearchRow[], limit = 3): string {
  if (!results.length) return "[]";
  const parts = results.slice(0, limit).map(
    (item) =>
      `{id=${item.id}, model=${item.model}, tier=${item.tier}, score=${item.score.toFixed(4)}, user=${item.user ?? "-"}, query='${previewText(item.query)}'}`,
  );
  return "[" + parts.join(", ") + "]";
}

export type MemoryItemRow = {
  id: number;
  prompt_preview: string;
  prompt_text: string;
  query: string;
  model: string;
  tier: "CLOUD" | "LOCAL";
  feedback_tier: "CLOUD" | "LOCAL";
  has_feedback: boolean;
  strategy: string;
  user: string;
  created_at: string;
};

export type ListItemsResult = {
  items: MemoryItemRow[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
};

/**
 * SQLite-backed routing memory and feedback embedding store.
 */
export class MemoryBank {
  private readonly cfg: MemoryConfig;
  private readonly routingConfig: RoutingConfig | undefined;
  private readonly requireVector: boolean;
  private readonly db: DatabaseSync;
  private queue: Promise<unknown> = Promise.resolve();
  /** Nested calls (e.g. recordFeedback → search/add/updateItem) must not re-queue on the same mutex or we deadlock. */
  private serialDepth = 0;

  /**
   * Opens the SQLite file, loads sqlite-vec, ensures `memory_items` + optional `memory_vectors` vec0 table.
   * @param options.requireVector When true, empty embeddings throw instead of returning [].
   */
  constructor(
    cfg: MemoryConfig,
    configDir?: string | null,
    options?: { requireVector?: boolean; routingConfig?: RoutingConfig },
  ) {
    this.cfg = cfg;
    this.routingConfig = options?.routingConfig;
    this.requireVector = Boolean(options?.requireVector);

    let dbPath = (cfg.path ?? "").trim();
    if (!dbPath) {
      dbPath = path.join(os.homedir(), ".clawchips", "openclaw_memory.db");
    } else {
      dbPath = expandUserEnv(dbPath);
    }

    let p = dbPath;
    if (!path.isAbsolute(p) && configDir) {
      // Resolve relative paths against the config file location so YAML remains portable.
      p = path.join(path.resolve(configDir), dbPath);
    }
    p = path.resolve(p);
    if (p.toLowerCase().endsWith(".jsonl")) {
      // Keep backward compatibility with older JSONL-style config values.
      p = p.replace(/\.jsonl$/i, ".db");
    }

    fs.mkdirSync(path.dirname(p), { recursive: true });
    this.db = new DatabaseSync(p, { allowExtension: true });
    sqliteVec.load(this.db);
    // Only allow extension loading during sqlite-vec bootstrap.
    this.db.enableLoadExtension(false);
    this.db.exec("PRAGMA journal_mode = WAL");
    this.initSchema();
  }

  /** Base tables for items and metadata key/values (vector dim). */
  private initSchema(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS memory_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tier INTEGER NOT NULL CHECK (tier IN (0, 1)),
        model TEXT NOT NULL,
        query TEXT,
        strategy TEXT,
        user TEXT,
        created_at TEXT NOT NULL
      )
    `);
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS memory_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
      )
    `);
    this.ensureMetadataColumns();
  }

  /** Migrates older `memory_items` rows to add optional text columns. */
  private ensureMetadataColumns(): void {
    const rows = this.db.prepare("PRAGMA table_info(memory_items)").all() as { name: string }[];
    const columns = new Set(rows.map((r) => r.name));
    const optional: Record<string, string> = {
      query: "TEXT",
      strategy: "TEXT",
      user: "TEXT",
    };
    for (const [name, ddl] of Object.entries(optional)) {
      if (!columns.has(name)) {
        this.db.exec(`ALTER TABLE memory_items ADD COLUMN ${name} ${ddl}`);
      }
    }
  }

  /** Embedding width recorded at first vec0 create (must match live model output). */
  private getVectorDim(): number | undefined {
    const row = this.db.prepare("SELECT value FROM memory_meta WHERE key = 'vector_dim'").get() as
      | { value: string }
      | undefined;
    return row ? parseInt(row.value, 10) : undefined;
  }

  /** API row shape for dashboard and list views. */
  private memoryRowToDict(row: {
    id: number;
    tier: number;
    model: string;
    query: string | null;
    strategy: string | null;
    user: string | null;
    created_at: string;
  }): MemoryItemRow {
    const promptText = row.query ?? "";
    const maxChars = this.cfg.max_prompt_chars ?? 200;
    const promptPreview = previewText(promptText, maxChars);
    const tierLabel = tierToLabel(row.tier);
    return {
      id: row.id,
      prompt_preview: promptPreview,
      prompt_text: promptText,
      query: promptText,
      model: row.model ?? "",
      tier: tierLabel,
      feedback_tier: tierLabel,
      has_feedback: true,
      strategy: row.strategy ?? "",
      user: row.user ?? "",
      created_at: row.created_at ?? "",
    };
  }

  /** True once the vec0 virtual table has been created. */
  private vectorTableExists(): boolean {
    const row = this.db
      .prepare(
        `SELECT sql FROM sqlite_master WHERE name = 'memory_vectors'`,
      )
      .get() as { sql: string | null } | undefined;
    return row !== undefined;
  }

  /** Raw `CREATE VIRTUAL TABLE` DDL for schema compatibility checks. */
  private getVectorTableSql(): string {
    const row = this.db
      .prepare(
        `SELECT sql FROM sqlite_master WHERE name = 'memory_vectors'`,
      )
      .get() as { sql: string | null } | undefined;
    return row?.sql ?? "";
  }

  /** Drops vec0 + items when embedding model or schema must change. */
  private resetVectorStore(): void {
    this.db.exec("DROP TABLE IF EXISTS memory_vectors");
    this.db.exec("DELETE FROM memory_items");
    this.db.prepare("DELETE FROM memory_meta WHERE key = 'vector_dim'").run();
  }

  /**
   * sqlite-vec 0.1.x has broken DELETE behavior when vec0 TEXT metadata values exceed ~12 chars.
   * Keep long strings in auxiliary columns instead of metadata columns.
   */
  private usesLegacyTextMetadataVectorSchema(sql: string): boolean {
    return (
      /(^|[\s,(])model\s+text\b/im.test(sql) ||
      /(^|[\s,(])strategy\s+text\b/im.test(sql) ||
      /(^|[\s,(])user\s+text\b/im.test(sql)
    );
  }

  /** Avoid vec0 DELETE on legacy schemas that mishandle long metadata TEXT. */
  private shouldSkipVectorDeletesForLegacySchema(): boolean {
    const vectorSql = this.getVectorTableSql();
    return Boolean(vectorSql) && this.usesLegacyTextMetadataVectorSchema(vectorSql);
  }

  /** Creates or validates `memory_vectors` for `dim`, resetting store on incompatible legacy DDL. */
  private ensureVectorTable(dim: number): void {
    const vectorSql = this.getVectorTableSql();
    if (vectorSql && !/using\s+vec0/i.test(vectorSql)) {
      console.warn("[Memory] Legacy vector table detected; resetting routing memory for sqlite-vec.");
      this.resetVectorStore();
    }
    if (vectorSql && this.usesLegacyTextMetadataVectorSchema(vectorSql)) {
      console.warn("[Memory] sqlite-vec table uses TEXT metadata columns; resetting routing memory to avoid vec0 delete bugs.");
      this.resetVectorStore();
    }

    const existing = this.getVectorDim();
    if (vectorSql && existing === undefined) {
      console.warn("[Memory] sqlite-vec table exists without vector_dim metadata; resetting routing memory.");
      this.resetVectorStore();
    }
    if (existing !== undefined) {
      if (existing !== dim) {
        // A single store cannot safely mix embeddings produced by different dimensions.
        throw new Error(`embedding dimension mismatch: expected ${existing}, got ${dim}`);
      }
      return;
    }

    if (!this.vectorTableExists()) {
      this.db.exec(`
        CREATE VIRTUAL TABLE memory_vectors USING vec0(
          embedding float[${dim}] distance_metric=cosine,
          tier INTEGER,
          +model TEXT,
          +strategy TEXT,
          +user TEXT,
          +query TEXT
        )
      `);
    }

    this.db
      .prepare(
        `
      INSERT INTO memory_meta(key, value)
      VALUES('vector_dim', ?)
      ON CONFLICT(key) DO UPDATE SET value = excluded.value
    `,
      )
      .run(String(dim));
  }

  /** Embedding API + L2 normalize; empty vector if misconfigured and `requireVector` is false. */
  private async embedText(query: string): Promise<number[]> {
    const raw = await computeEmbedding(query, this.routingConfig);
    if (!raw.length) {
      if (this.requireVector) {
        throw new Error("embedding is empty; configure routing.embedding.model and endpoint");
      }
      return [];
    }
    return l2Normalize(raw);
  }

  /** Applies `max_query_chars` from config. */
  private normalizeQueryText(query: string): string {
    return normalizeMemoryQueryText(this.cfg, query);
  }

  /** Serializes async DB access on the shared sync connection; re-enters for nested calls. */
  private runSerialized<T>(fn: () => Promise<T>): Promise<T> {
    if (this.serialDepth > 0) {
      // Nested calls already hold the logical lock, so run inline to avoid self-deadlock.
      return fn();
    }
    // Keep sqlite writes/searches in order because this class shares one sync connection.
    const next = this.queue.then(async () => {
      this.serialDepth++;
      try {
        return await fn();
      } finally {
        this.serialDepth--;
      }
    });
    this.queue = next.then(
      () => undefined,
      () => undefined,
    );
    return next;
  }

  /** Synchronous BEGIN/COMMIT wrapper for paired item + vector writes. */
  private runInTransaction<T>(fn: () => T): T {
    this.db.exec("BEGIN");
    try {
      const result = fn();
      this.db.exec("COMMIT");
      return result;
    } catch (error) {
      try {
        this.db.exec("ROLLBACK");
      } catch {
        // Ignore rollback failures so the original sqlite error is preserved.
      }
      throw error;
    }
  }

  /**
   * Finds similar rows for the feedback prompt, then updates or inserts a memory row with the target tier/model.
   */
  async recordFeedback(params: {
    query: string;
    model: string;
    feedback_tier: string;
    strategy?: string;
    user?: string | null;
  }): Promise<number | null> {
    const targetTier = (params.feedback_tier ?? "").trim().toUpperCase();
    if (targetTier !== "LOCAL" && targetTier !== "CLOUD") {
      throw new Error("feedback_tier must be LOCAL or CLOUD");
    }
    const q = this.normalizeQueryText(params.query);
    const m = (params.model ?? "").trim();
    if (!q || !m) return null;

    const effectiveUser = this.cfg.per_user ? params.user ?? null : null;
    const tierValue = targetTier === "CLOUD" ? 1 : 0;
    const strategy = (params.strategy ?? "").trim();

    return this.runSerialized(async () => {
      const emb = await this.embedText(q);
      if (!emb.length) return null;

      console.log(
        `[Memory] Feedback search tier=${tierValue} strategy='${strategy}' user='${(effectiveUser ?? "").trim() || "-"}' query='${previewText(q)}'`,
      );

      const matches =
        (await this.search({
          embedding: emb,
          top_k: Math.max(this.cfg.top_k ?? 10, 10),
          strategy_filter: strategy || null,
          user: effectiveUser,
        })) ?? [];

      console.log(
        `[Memory] Feedback search results count=${matches.length} matches=${formatResultPreview(matches)}`,
      );

      // Reuse an exact prompt match when possible so feedback updates the existing memory slot.
      const existing = matches.find((item) => String(item.query ?? "").trim() === q);
      if (existing) {
        const updated = await this.updateItem(existing.id, {
          model: m,
          tier: tierValue,
          strategy,
          user: effectiveUser ?? undefined,
        });
        if (updated) {
          console.log(
            `[Memory] Feedback matched existing item id=${existing.id} -> model=${m} tier=${tierValue} strategy='${strategy}' user='${(effectiveUser ?? "").trim() || "-"}' query='${previewText(q)}'`,
          );
          return existing.id;
        }
      }

      const itemId = await this.add({
        query: q,
        model: m,
        embedding: emb,
        tier: tierValue,
        strategy,
        user: effectiveUser ?? undefined,
      });
      if (itemId !== null) {
        console.log(
          `[Memory] Feedback inserted new item id=${itemId} model=${m} tier=${tierValue} strategy='${strategy}' user='${(effectiveUser ?? "").trim() || "-"}' query='${previewText(q)}'`,
        );
      }
      return itemId;
    });
  }

  /** Inserts `memory_items` + `memory_vectors` row; embeds query when `embedding` omitted. */
  async add(params: {
    query: string;
    model: string;
    embedding?: number[];
    tier?: number;
    strategy?: string;
    user?: string | null;
  }): Promise<number | null> {
    const q = this.normalizeQueryText(params.query);
    const m = (params.model ?? "").trim();
    if (!m) return null;

    let emb: number[];
    if (params.embedding === undefined || params.embedding.length === 0) {
      if (!q) return null;
      emb = await this.embedText(q);
    } else {
      // Normalize caller-provided vectors so similarity scoring stays comparable.
      emb = l2Normalize([...params.embedding]);
    }
    if (!emb.length) return null;

    const tier = params.tier ?? 0;
    const strategy = (params.strategy ?? "").trim();
    const user = (params.user ?? "").trim() || null;

    return this.runSerialized(async () => {
      const itemId = this.runInTransaction(() => {
        this.ensureVectorTable(emb.length);
        const info = this.db
          .prepare(
            `
          INSERT INTO memory_items(tier, model, query, strategy, user, created_at)
          VALUES(?, ?, ?, ?, ?, ?)
        `,
          )
          .run(tier === 1 ? 1 : 0, m, q || null, strategy || null, user, utcNowIso());

        const rowidBind = requireLastInsertRowid(info);
        const insertedItemId = Number(rowidBind);
        this.db
          .prepare(
            `INSERT INTO memory_vectors(rowid, embedding, tier, model, strategy, user, query) VALUES(?, ?, ?, ?, ?, ?, ?)`,
          )
          .run(rowidBind, vectorToSqlBlob(emb), bindVec0Tier(tier === 1 ? 1 : 0), m, strategy || "", user ?? "", q);
        return insertedItemId;
      });

      console.log(
        `[Memory] Stored item id=${itemId} model=${m} tier=${tier === 1 ? 1 : 0} strategy='${strategy}' user='${user ?? "-"}' dim=${emb.length} query='${previewText(q)}'`,
      );
      return itemId;
    });
  }

  /** Updates item metadata and mirrors columns to `memory_vectors` when present. */
  async updateItem(
    itemId: number,
    updates: {
      model?: string | null;
      tier?: number | null;
      strategy?: string | null;
      user?: string | null;
    },
  ): Promise<boolean> {
    const normalizedModel = (updates.model ?? "").trim();
    if (updates.model !== undefined && updates.model !== null && !normalizedModel) {
      throw new Error("model cannot be empty");
    }

    const rowId = Math.floor(itemId);
    const setClauses: string[] = [];
    const values: (string | number | null)[] = [];

    if (updates.tier !== undefined && updates.tier !== null) {
      setClauses.push("tier = ?");
      values.push(updates.tier === 1 ? 1 : 0);
    }
    if (updates.model !== undefined) {
      setClauses.push("model = ?");
      values.push(normalizedModel);
    }
    if (updates.strategy !== undefined) {
      setClauses.push("strategy = ?");
      values.push((updates.strategy ?? "").trim() || null);
    }
    if (updates.user !== undefined) {
      setClauses.push("user = ?");
      values.push((updates.user ?? "").trim() || null);
    }
    if (!setClauses.length) return false;

    return this.runSerialized(async () => {
      const updated = this.runInTransaction(() => {
        const itemResult = this.db
          .prepare(`UPDATE memory_items SET ${setClauses.join(", ")} WHERE id = ?`)
          .run(...values, rowId);
        const itemUpdated = Number(itemResult.changes ?? 0) > 0;
        if (!itemUpdated) return false;

        const vectorSetClauses: string[] = [];
        const vectorValues: (string | number | bigint)[] = [];
        if (updates.tier !== undefined && updates.tier !== null) {
          vectorSetClauses.push("tier = ?");
          vectorValues.push(bindVec0Tier(updates.tier === 1 ? 1 : 0));
        }
        if (updates.model !== undefined) {
          vectorSetClauses.push("model = ?");
          vectorValues.push(normalizedModel);
        }
        if (updates.strategy !== undefined) {
          vectorSetClauses.push("strategy = ?");
          vectorValues.push((updates.strategy ?? "").trim());
        }
        if (updates.user !== undefined) {
          vectorSetClauses.push("user = ?");
          vectorValues.push((updates.user ?? "").trim());
        }
        if (vectorSetClauses.length && this.vectorTableExists()) {
          this.db
            .prepare(`UPDATE memory_vectors SET ${vectorSetClauses.join(", ")} WHERE rowid = ?`)
            .run(...vectorValues, bindSqlInteger(rowId));
        }
        return true;
      });

      if (updated) {
        console.log(
          `[Memory] Updated item id=${rowId} model='${normalizedModel || "-"}' tier=${updates.tier !== undefined && updates.tier !== null ? updates.tier : "-"} strategy='${updates.strategy !== undefined ? (updates.strategy ?? "").trim() : "-"}' user='${updates.user !== undefined ? (updates.user ?? "").trim() || "-" : "-"}'`,
        );
      }
      return updated;
    });
  }

  /** Loads one row by id (throws if missing). */
  async getItem(itemId: number): Promise<MemoryItemRow> {
    return this.runSerialized(async () => {
      const rowId = Math.floor(itemId);
      const row = this.db
        .prepare(
          `
        SELECT id, tier, model, query, strategy, user, created_at
        FROM memory_items WHERE id = ?
      `,
        )
        .get(rowId) as
        | {
            id: number;
            tier: number;
            model: string;
            query: string | null;
            strategy: string | null;
            user: string | null;
            created_at: string;
          }
        | undefined;
      if (!row) throw new Error(`memory item not found: ${rowId}`);
      return this.memoryRowToDict(row);
    });
  }

  /** Paginated listing for the dashboard memory tab. */
  async listItems(page = 1, pageSize = 20): Promise<ListItemsResult> {
    return this.runSerialized(async () => {
      const currentPage = Math.max(page || 1, 1);
      const currentSize = Math.max(pageSize || 20, 1);
      const offset = (currentPage - 1) * currentSize;

      const total = Number(
        (this.db.prepare("SELECT COUNT(*) AS count FROM memory_items").get() as { count: number }).count ?? 0,
      );
      const rows = this.db
        .prepare(
          `
        SELECT id, tier, model, query, strategy, user, created_at
        FROM memory_items
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ? OFFSET ?
      `,
        )
        .all(currentSize, offset) as {
        id: number;
        tier: number;
        model: string;
        query: string | null;
        strategy: string | null;
        user: string | null;
        created_at: string;
      }[];

      const totalPages = total ? Math.max(Math.ceil(total / currentSize), 1) : 0;
      return {
        items: rows.map((r) => this.memoryRowToDict(r)),
        page: currentPage,
        page_size: currentSize,
        total,
        total_pages: totalPages,
      };
    });
  }

  /** Deletes item and matching vec0 row when safe. */
  async delete(itemId: number): Promise<boolean> {
    return this.runSerialized(async () => {
      const id = Math.floor(itemId);
      return this.runInTransaction(() => {
        if (this.vectorTableExists() && !this.shouldSkipVectorDeletesForLegacySchema()) {
          this.db.prepare("DELETE FROM memory_vectors WHERE rowid = ?").run(bindSqlInteger(id));
        }
        const removed = Number(this.db.prepare("DELETE FROM memory_items WHERE id = ?").run(id).changes ?? 0);
        return removed > 0;
      });
    });
  }

  /** Remove all memory rows whose `model` equals the given id (YAML model id). */
  async forgetByModel(modelId: string): Promise<number> {
    return this.runSerialized(async () => {
      const m = (modelId ?? "").trim();
      if (!m) return 0;
      const rows = this.db.prepare("SELECT id FROM memory_items WHERE model = ?").all(m) as { id: number }[];
      return this.runInTransaction(() => {
        const skipVectorDelete = this.vectorTableExists() && this.shouldSkipVectorDeletesForLegacySchema();
        let n = 0;
        for (const { id } of rows) {
          if (this.vectorTableExists() && !skipVectorDelete) {
            this.db.prepare("DELETE FROM memory_vectors WHERE rowid = ?").run(bindSqlInteger(id));
          }
          this.db.prepare("DELETE FROM memory_items WHERE id = ?").run(id);
          n += 1;
        }
        return n;
      });
    });
  }

  /** Delete rows matching optional model id and/or created before ISO timestamp. */
  async deleteByRoutingFilter(filter: { beforeTimestamp?: number; modelId?: string }): Promise<number> {
    return this.runSerialized(async () => {
      const modelId = (filter.modelId ?? "").trim();
      const before = filter.beforeTimestamp;
      if (before === undefined && !modelId) return 0;
      const rows = this.db.prepare("SELECT id, created_at, model FROM memory_items").all() as {
        id: number;
        created_at: string;
        model: string;
      }[];
      return this.runInTransaction(() => {
        const skipVectorDelete = this.vectorTableExists() && this.shouldSkipVectorDeletesForLegacySchema();
        let removed = 0;
        for (const r of rows) {
          if (modelId && r.model !== modelId) continue;
          if (before !== undefined) {
            const t = Date.parse(r.created_at);
            if (!Number.isFinite(t) || t >= before) continue;
          }
          if (this.vectorTableExists() && !skipVectorDelete) {
            this.db.prepare("DELETE FROM memory_vectors WHERE rowid = ?").run(bindSqlInteger(r.id));
          }
          this.db.prepare("DELETE FROM memory_items WHERE id = ?").run(r.id);
          removed += 1;
        }
        return removed;
      });
    });
  }

  /** Wipe routing memory tables (destructive). */
  async clearAll(): Promise<void> {
    return this.runSerialized(async () => {
      if (this.vectorTableExists()) {
        // Drop the vec0 table so the next insert can recreate it with the active embedding dimension.
        this.db.exec("DROP TABLE memory_vectors");
      }
      this.db.exec("DELETE FROM memory_items");
      this.db.prepare("DELETE FROM memory_meta WHERE key = 'vector_dim'").run();
    });
  }

  /**
   * KNN over `memory_vectors` joined to `memory_items`, optional filters, then `score_threshold` cutoff.
   */
  async search(params: {
    query?: string | null;
    embedding?: number[];
    top_k?: number | null;
    tier?: number | null;
    model?: string | null;
    strategy_filter?: string | null;
    user?: string | null;
  }): Promise<SearchRow[]> {
    const k = params.top_k ?? this.cfg.top_k ?? 10;
    if (k <= 0) return [];

    const rawQueryStr = params.query == null ? "" : String(params.query);
    let normalizedForEmbedStr = "";
    let emb: number[];
    if (!params.embedding || params.embedding.length === 0) {
      const q = this.normalizeQueryText(rawQueryStr);
      normalizedForEmbedStr = q;
      if (!q) return [];
      emb = await this.embedText(q);
    } else {
      normalizedForEmbedStr = "(embedding path)";
      emb = l2Normalize([...params.embedding]);
    }
    if (!emb.length) return [];

    return this.runSerialized(async () => {
      if (!this.vectorTableExists()) return [];

      const storedDim = this.getVectorDim();
      if (storedDim === undefined || storedDim !== emb.length) {
        // Common failure: DB was created with an older embedding model / different `routing.embedding.dimensions`.
        console.warn(
          `[Memory] search skipped: query embedding dim=${emb.length} but store expects dim=${storedDim ?? "unset"}. ` +
            `Clear routing memory (dashboard or delete DB) after changing embedding model or dimensions.`,
        );
        return [];
      }

      const whereClauses = ["embedding MATCH ?", "k = ?"];
      const outerWhereClauses: string[] = [];
      const values: (string | number | Uint8Array | bigint)[] = [
        vectorToSqlBlob(emb),
        Math.max(k * (params.model != null || params.strategy_filter != null || params.user != null ? 8 : 3), k),
      ];
      if (params.tier !== undefined && params.tier !== null) {
        whereClauses.push("tier = ?");
        values.push(bindVec0Tier(params.tier === 1 ? 1 : 0));
      }
      if (params.model !== undefined && params.model !== null) {
        outerWhereClauses.push("m.model = ?");
        values.push(String(params.model));
      }
      if (params.strategy_filter !== undefined && params.strategy_filter !== null) {
        outerWhereClauses.push("m.strategy = ?");
        values.push(String(params.strategy_filter));
      }
      if (params.user !== undefined && params.user !== null) {
        outerWhereClauses.push("m.user = ?");
        values.push(String(params.user));
      }

      const rows = this.db
        .prepare(
          `
        WITH knn_matches AS (
          SELECT
            rowid,
            distance
          FROM memory_vectors
          WHERE ${whereClauses.join("\n            AND ")}
          ORDER BY distance
        )
        SELECT
          m.id,
          m.created_at,
          m.tier,
          m.model,
          m.query,
          m.strategy,
          m.user,
          knn_matches.distance
        FROM knn_matches
        INNER JOIN memory_items AS m ON m.id = knn_matches.rowid
        ${outerWhereClauses.length ? `WHERE ${outerWhereClauses.join(" AND ")}` : ""}
        ORDER BY knn_matches.distance
      `,
        )
        .all(...values) as {
        id: number;
        created_at: string;
        tier: number;
        model: string;
        query: string | null;
        strategy: string | null;
        user: string | null;
        distance: number;
      }[];

      if (memorySearchDebugEnabled()) {
        if (rows.length > 0) {
          const top = rows[0];
          const dist = Number(top.distance ?? 0);
          const topScore = distanceToScore(dist);
          console.log(
            `[Memory] search debug raw_query=${JSON.stringify(rawQueryStr)} normalized_query=${JSON.stringify(normalizedForEmbedStr)} knn_top1 id=${top.id} distance=${dist} score=${topScore.toFixed(6)} store_query_preview=${JSON.stringify(previewText(top.query))}`,
          );
        } else {
          console.log(
            `[Memory] search debug raw_query=${JSON.stringify(rawQueryStr)} normalized_query=${JSON.stringify(normalizedForEmbedStr)} knn_top1=(no rows)`,
          );
        }
      }

      const threshold = this.cfg.score_threshold ?? 0;
      const results: SearchRow[] = [];

      for (const row of rows) {
        const score = distanceToScore(Number(row.distance ?? 0));
        if (score < threshold) continue;

        results.push({
          id: row.id,
          tier: row.tier,
          model: String(row.model),
          query: row.query,
          strategy: row.strategy,
          user: row.user,
          score,
          ts: String(row.created_at),
        });
        if (results.length >= k) break;
      }

      if (rows.length > 0 && results.length === 0) {
        let best = -Infinity;
        for (const row of rows) {
          const s = distanceToScore(Number(row.distance ?? 0));
          if (s > best) best = s;
        }
        console.warn(
          `[Memory] KNN returned ${rows.length} row(s) but none passed score_threshold=${threshold} ` +
            `(best similarity score≈${best.toFixed(4)}). Lower memory.score_threshold or re-train feedback.`,
        );
      }

      return results;
    });
  }

  /** Logs retrieve intent, then delegates to `search` with per-user rules applied. */
  async retrieve(params: {
    query: string;
    top_k?: number | null;
    strategy_filter?: string | null;
    user?: string | null;
  }): Promise<SearchRow[] | null> {
    const effectiveUser = this.cfg.per_user ? params.user ?? null : null;
    const q = (params.query ?? "").trim();
    const requestedTopK = params.top_k ?? this.cfg.top_k ?? 10;
    console.log(
      `[Memory] Retrieve request top_k=${requestedTopK} strategy='${params.strategy_filter ?? ""}' user='${(effectiveUser ?? "").trim() || "-"}' query='${previewText(q)}' score_threshold=${this.cfg.score_threshold ?? 0}`,
    );
    if (memorySearchDebugEnabled()) {
      console.log(
        `[Memory] retrieve debug original_query=${JSON.stringify(params.query ?? "")} trimmed_for_search=${JSON.stringify(q)} (search will log normalized_query + knn_top1)`,
      );
    }

    const results = await this.search({
      query: q,
      top_k: params.top_k,
      strategy_filter: params.strategy_filter ?? null,
      user: effectiveUser,
    });

    console.log(
      `[Memory] Retrieve results count=${results?.length ?? 0} matches=${results?.length ? formatResultPreview(results) : "none"}`,
    );
    return results;
  }

  /** Bulk import from a JSONL export (field names configurable). */
  async importFromJsonl(
    jsonlPath: string,
    queryField = "Query",
    tierField = "iter",
    modelField = "model",
  ): Promise<number> {
    if (!fs.existsSync(jsonlPath)) {
      throw new Error(`JSONL file not found: ${jsonlPath}`);
    }
    const text = fs.readFileSync(jsonlPath, "utf8");
    let count = 0;
    for (const line of text.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      let data: Record<string, unknown>;
      try {
        data = JSON.parse(trimmed) as Record<string, unknown>;
      } catch {
        continue;
      }
      const query = data[queryField];
      const tier = data[tierField];
      const model = data[modelField];
      if (query && model) {
        const id = await this.add({
          query: String(query),
          tier: Number(tier),
          model: String(model),
        });
        if (id !== null) count += 1;
      }
    }
    return count;
  }

  /** Closes the SQLite connection. */
  close(): void {
    this.db.close();
  }
}
