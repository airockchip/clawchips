/**
 * Optional QQ notifications on tool calls via qqbot `sendProactive`.
 * Dynamic import only — no compile-time dependency.
 *
 * Supports:
 * - Upstream `@openclaw/qqbot` (api re-exports proactive)
 * - Tencent `@tencent-connect/openclaw-qqbot` (plugin id `openclaw-qqbot`): `sendProactive` lives in
 *   `dist/src/proactive.js` and is NOT on the package main export — load that file explicitly.
 */

import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

import type { ParsedLocalRouterConfig, QqToolNotifyConfig } from "./config.js";

/** qqbot plugin stores known peers under state dir, e.g. ~/.openclaw/qqbot/data/known-users.json */
function knownUsersJsonPaths(): string[] {
  const out: string[] = [];
  for (const root of openclawStateRoots()) {
    out.push(join(root, "qqbot", "data", "known-users.json"));
  }
  return [...new Set(out)];
}

type KnownUserRow = {
  openid?: string;
  type?: string;
  accountId?: string;
  lastSeenAt?: number;
};

function loadBestKnownUser(): { openid: string; type: "c2c" | "group"; accountId: string } | null {
  for (const p of knownUsersJsonPaths()) {
    if (!existsSync(p)) continue;
    try {
      const data = JSON.parse(readFileSync(p, "utf8")) as unknown;
      if (!Array.isArray(data) || data.length === 0) continue;
      const rows = data.filter((x): x is KnownUserRow => {
        if (!x || typeof x !== "object" || Array.isArray(x)) return false;
        const id = (x as KnownUserRow).openid;
        return typeof id === "string" && id.trim().length > 0;
      });
      if (rows.length === 0) continue;
      rows.sort((a, b) => (b.lastSeenAt ?? 0) - (a.lastSeenAt ?? 0));
      const u = rows[0]!;
      const openid = u.openid!.trim();
      const type = u.type === "group" ? "group" : "c2c";
      const accountId =
        typeof u.accountId === "string" && u.accountId.trim() ? u.accountId.trim() : "default";
      return { openid, type, accountId };
    } catch {
      /* try next path */
    }
  }
  return null;
}

/** README example placeholders — treated like unset `openid`; use known-users.json fallback. */
const OPENID_README_PLACEHOLDERS = new Set(["<user-or-group-openid>", "<用户或群-openid>"]);

/**
 * Read `clawchips.yaml` top-level `qq_tool_notify` (alias: `qqToolNotify`).
 * Opt-in: `enabled` must be true; `openid` set, or resolvable from known-users.json.
 */
export function parseQqToolNotifyFromParsedConfig(parsed: ParsedLocalRouterConfig): QqToolNotifyConfig | null {
  const o = parsed.qqToolNotify;
  if (!o) return null;
  if (o.enabled !== true) return null;

  let explicitOpenid = (typeof o.openid === "string" ? o.openid : "").trim();
  if (OPENID_README_PLACEHOLDERS.has(explicitOpenid)) {
    explicitOpenid = "";
  }

  const fromKnown = explicitOpenid ? null : loadBestKnownUser();
  const openid = explicitOpenid || fromKnown?.openid || "";
  if (!openid) return null;

  const typeExplicit = o.type === "group" ? "group" : o.type === "c2c" ? "c2c" : null;
  const type: "c2c" | "group" =
    typeExplicit ?? (fromKnown && !explicitOpenid ? fromKnown.type : "c2c");

  return {
    ...o,
    enabled: true,
    openid,
    type,
    accountId: fromKnown && !explicitOpenid ? fromKnown.accountId : o.accountId,
  };
}

export function shouldNotifyToolName(toolName: string, cfg: QqToolNotifyConfig): boolean {
  const n = (toolName || "").trim();
  if (!n) return false;
  if (cfg.excludeToolNames?.includes(n)) return false;
  if (cfg.includeToolNames?.length && !cfg.includeToolNames.includes(n)) return false;
  return true;
}

function stringifyLimited(val: unknown, max: number): string {
  try {
    const s = typeof val === "string" ? val : JSON.stringify(val);
    if (s.length <= max) return s;
    return `${s.slice(0, max)}…`;
  } catch {
    return "(unserializable)";
  }
}

export type ToolNotifyPhase = "before" | "after";

export function buildQqToolNotifyText(args: {
  phase: ToolNotifyPhase;
  toolName: string;
  params: Record<string, unknown>;
  sessionKey: string;
  runId?: string;
  toolCallId?: string;
  cfg: QqToolNotifyConfig;
  result?: unknown;
  error?: string;
  durationMs?: number;
}): string {
  const { phase, toolName, params, sessionKey, runId, toolCallId, cfg, result, error, durationMs } = args;
  const lines = [
    `[ClawChips] 工具调用 ${phase === "before" ? "开始" : "完成"}`,
    `工具: ${toolName}`,
  ];
  // if (sessionKey) lines.push(`会话: ${sessionKey.length > 120 ? `${sessionKey.slice(0, 120)}…` : sessionKey}`);
  lines.push(`参数: ${stringifyLimited(params, cfg.maxParamChars)}`);
  if (phase === "after") {
    // if (error) lines.push(`Error: ${error}`);
    // else if (result !== undefined) lines.push(`Success`);
    if (durationMs != null && Number.isFinite(durationMs)) lines.push(`耗时: ${durationMs}ms`);
  }
  return lines.join("\n");
}

type SendProactive = (
  options: { to: string; text: string; type?: "c2c" | "group"; accountId?: string },
  cfg: Record<string, unknown>,
) => Promise<{ success: boolean; error?: string }>;

let sendProactiveLoad: Promise<SendProactive | null> | null = null;

function pickSendProactive(mod: unknown): SendProactive | null {
  const fn = (mod as { sendProactive?: SendProactive }).sendProactive;
  return typeof fn === "function" ? fn : null;
}

/** State dir(s): OPENCLAW_STATE_DIR or ~/.openclaw */
function openclawStateRoots(): string[] {
  const env = process.env.OPENCLAW_STATE_DIR?.trim();
  const roots = [env, join(homedir(), ".openclaw")].filter((s): s is string => Boolean(s && s.length));
  return [...new Set(roots)];
}

/** Global extension installs: ~/.openclaw/extensions/openclaw-qqbot (Tencent) or …/qqbot (upstream). */
function globalQqbotProactivePaths(): string[] {
  const names = ["openclaw-qqbot", "qqbot"];
  const out: string[] = [];
  for (const root of openclawStateRoots()) {
    for (const name of names) {
      out.push(join(root, "extensions", name, "dist", "src", "proactive.js"));
    }
  }
  return out;
}

function qqbotPackageJsonForRequire(): string[] {
  const names = ["openclaw-qqbot", "qqbot"];
  const out: string[] = [];
  for (const root of openclawStateRoots()) {
    for (const name of names) {
      out.push(join(root, "extensions", name, "package.json"));
    }
  }
  return out;
}

async function loadSendProactive(): Promise<SendProactive | null> {
  if (!sendProactiveLoad) {
    sendProactiveLoad = (async () => {
      const tryImport = async (label: string, fn: () => Promise<unknown>): Promise<SendProactive | null> => {
        try {
          const mod = await fn();
          return pickSendProactive(mod);
        } catch (e) {
          if (process.env.CLAWCHIPS_QQ_NOTIFY_DEBUG === "1" || process.env.CLAWCHIPS_QQ_NOTIFY_DEBUG === "true") {
            console.warn(`[ClawChips] qqToolNotify load try "${label}" failed: ${String(e)}`);
          }
          return null;
        }
      };

      let fn: SendProactive | null;

      // --- Tencent @tencent-connect/openclaw-qqbot (npm / global extension; proactive not re-exported from main) ---
      fn = await tryImport("@tencent-connect/openclaw-qqbot/dist/src/proactive.js", () =>
        import("@tencent-connect/openclaw-qqbot/dist/src/proactive.js"),
      );
      if (fn) return fn;

      fn = await tryImport("@tencent-connect/openclaw-qqbot/dist/src/proactive", () =>
        import("@tencent-connect/openclaw-qqbot/dist/src/proactive"),
      );
      if (fn) return fn;

      try {
        const req = createRequire(import.meta.url);
        const resolved = req.resolve("@tencent-connect/openclaw-qqbot/dist/src/proactive.js");
        fn = await tryImport("tencent-proactive-via-require", () => import(pathToFileURL(resolved).href));
        if (fn) return fn;
      } catch {
        /* optional */
      }

      for (const pkgJson of qqbotPackageJsonForRequire()) {
        if (!existsSync(pkgJson)) continue;
        try {
          const req = createRequire(pkgJson);
          const resolved = req.resolve("./dist/src/proactive.js");
          fn = await tryImport(`resolve:${pkgJson}`, () => import(pathToFileURL(resolved).href));
          if (fn) return fn;
        } catch {
          /* next */
        }
      }

      for (const abs of globalQqbotProactivePaths()) {
        if (!existsSync(abs)) continue;
        fn = await tryImport(`path:${abs}`, () => import(pathToFileURL(abs).href));
        if (fn) return fn;
      }

      const cwdProactive = join(process.cwd(), "node_modules/@tencent-connect/openclaw-qqbot/dist/src/proactive.js");
      if (existsSync(cwdProactive)) {
        fn = await tryImport("cwd:tencent", () => import(pathToFileURL(cwdProactive).href));
        if (fn) return fn;
      }

      // --- Upstream @openclaw/qqbot (api re-exports proactive) ---
      fn = await tryImport("@openclaw/qqbot/api.js", () => import("@openclaw/qqbot/api.js"));
      if (fn) return fn;

      fn = await tryImport("@openclaw/qqbot/api", () => import("@openclaw/qqbot/api"));
      if (fn) return fn;

      try {
        const req = createRequire(import.meta.url);
        const resolved = req.resolve("@openclaw/qqbot/api.js");
        fn = await tryImport("openclaw-qqbot-api", () => import(pathToFileURL(resolved).href));
        if (fn) return fn;
      } catch {
        /* optional */
      }

      // Monorepo sibling extensions
      const relativeAttempts = [
        "../../qqbot/api.js",
        "../../qqbot/src/proactive.js",
        "../../../qqbot/api.js",
        "../../../qqbot/src/proactive.js",
      ];
      for (const rel of relativeAttempts) {
        fn = await tryImport(rel, () => import(new URL(rel, import.meta.url).href));
        if (fn) return fn;
      }

      return null;
    })();
  }
  return sendProactiveLoad;
}

/**
 * Send proactive QQ message; logs failures. Resolves when the send finishes (success or failure).
 * Callers can await this to preserve order (e.g. after_tool_call after before_tool_call).
 */
export function scheduleQqToolNotify(args: {
  cfg: QqToolNotifyConfig;
  phase: ToolNotifyPhase;
  text: string;
  hostConfig: Record<string, unknown> | undefined;
  log: { warn: (m: string) => void };
}): Promise<void> {
  const { cfg, text, hostConfig, log } = args;
  if (!hostConfig || typeof hostConfig !== "object") {
    log.warn("[ClawChips] qqToolNotify: host api.config missing; cannot send QQ message");
    return Promise.resolve();
  }
  return (async () => {
    const sendProactive = await loadSendProactive();
    if (!sendProactive) {
      log.warn(
        "[ClawChips] qqToolNotify: could not load sendProactive. Install QQ plugin: @tencent-connect/openclaw-qqbot (global: ~/.openclaw/extensions/openclaw-qqbot) or @openclaw/qqbot. Set CLAWCHIPS_QQ_NOTIFY_DEBUG=1 for import errors.",
      );
      return;
    }
    try {
      const result = await sendProactive(
        { to: cfg.openid, text, type: cfg.type, accountId: cfg.accountId },
        hostConfig,
      );
      if (!result.success) {
        log.warn(`[ClawChips] qqToolNotify: send failed: ${result.error ?? "unknown"}`);
      }
    } catch (e) {
      log.warn(`[ClawChips] qqToolNotify: ${String(e)}`);
    }
  })();
}

/**
 * Stable id for pairing before/after tool-call QQ lines when `toolCallId` is missing.
 */
export function qqToolNotifyPairKey(args: {
  toolCallId?: string;
  runId?: string;
  toolName: string;
  sessionKey: string;
}): string {
  const tid = (args.toolCallId ?? "").trim();
  if (tid) return tid;
  const rid = (args.runId ?? "").trim();
  return `${rid}::${args.toolName}::${args.sessionKey}`;
}
