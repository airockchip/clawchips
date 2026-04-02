#!/usr/bin/env node
/**
 * ClawChips install-time router.rules + memory setup (interactive by default).
 *
 * - Interactive: prompts for LOCAL / CLOUD / default + memory.enabled every run
 *   (including when clawchips.yaml exists).
 * - Skip: set CLAWCHIPS_SETUP_SKIP=1 to copy bundled clawchips_default.yaml to the target path (no prompts).
 * - When stdin is not a TTY (e.g. under a parent process), try /dev/tty for prompts; if still unavailable, copy the bundled default (CI).
 *
 * Env:
 *   CLAWCHIPS_CONFIG   — target yaml path (default ~/.openclaw/clawchips.yaml)
 *   CLAWCHIPS_SETUP_SKIP=1 — non-interactive: use plugin's clawchips_default.yaml only
 *
 * Usage:
 *   node scripts/setup.mjs
 *   CLAWCHIPS_SETUP_SKIP=1 node scripts/setup.mjs
 */

import { copyFileSync, createReadStream, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createInterface } from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";

import { parse as parseYaml, stringify as stringifyYaml } from "yaml";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN_ROOT = path.resolve(__dirname, "..");
const DEFAULT_BUNDLED = path.join(PLUGIN_ROOT, "clawchips_default.yaml");
const DEFAULT_LOCAL_MODEL_REF = "rkllm/Qwen3-4B";

function logDiag(...args) {
  console.error("[clawchips setup]", ...args);
}

function expandHome(p) {
  if (!p || typeof p !== "string") return p;
  if (p.startsWith("~/")) return path.join(homedir(), p.slice(2));
  return p;
}

function openclawConfigPath() {
  return path.join(homedir(), ".openclaw", "openclaw.json");
}

function defaultClawchipsPath() {
  const env = (process.env.CLAWCHIPS_CONFIG || "").trim();
  if (env) return path.resolve(expandHome(env));
  return path.join(homedir(), ".openclaw", "clawchips.yaml");
}

function envSkipInteractive() {
  const v = (process.env.CLAWCHIPS_SETUP_SKIP || "").trim().toLowerCase();
  return v === "1" || v === "true" || v === "yes";
}

/**
 * When this script runs under a parent process, stdin is often not a TTY.
 * Opening /dev/tty allows readline to talk to the user's terminal anyway (Unix).
 * @returns {import('node:fs').ReadStream | null}
 */
function tryOpenDevTtyRead() {
  if (process.platform === "win32") return null;
  try {
    if (!existsSync("/dev/tty")) return null;
    return createReadStream("/dev/tty", { flags: "r" });
  } catch {
    return null;
  }
}

/**
 * @returns {{ stream: import('node:process').stdin | import('node:fs').ReadStream; closeStream: boolean } | null}
 */
function resolveInteractiveInput() {
  if (input.isTTY) return { stream: input, closeStream: false };
  const tty = tryOpenDevTtyRead();
  if (tty) return { stream: tty, closeStream: true };
  return null;
}

/**
 * @param {unknown} doc
 * @returns {string[]}
 */
function collectModelRefsFromOpenClaw(doc) {
  const out = [];
  if (!doc || typeof doc !== "object" || !("models" in doc)) return out;
  const providers = /** @type {{ models?: { providers?: Record<string, unknown> } }} */ (doc).models?.providers;
  if (!providers || typeof providers !== "object") return out;

  for (const [providerId, p] of Object.entries(providers)) {
    const models = p && typeof p === "object" && "models" in p ? p.models : undefined;
    if (!Array.isArray(models)) continue;
    for (const m of models) {
      if (m && typeof m === "object" && "id" in m && typeof m.id === "string" && m.id.trim()) {
        out.push(`${providerId}/${m.id.trim()}`);
      }
    }
  }
  return [...new Set(out)].sort();
}

/**
 * @param {unknown} doc
 * @returns {string | undefined}
 */
function suggestedPrimaryFromOpenClaw(doc) {
  if (!doc || typeof doc !== "object" || !("agents" in doc)) return undefined;
  const agents = /** @type {{ agents?: { defaults?: { model?: { primary?: string } } } }} */ (doc).agents;
  const model = agents?.defaults?.model;
  if (typeof model === "string" && model.includes("/")) return model.trim();
  const primary = model && typeof model === "object" ? model.primary : undefined;
  return typeof primary === "string" && primary.includes("/") ? primary.trim() : undefined;
}

/**
 * @param {string[]} refs
 * @returns {string | undefined}
 */
function suggestedLocalModel(refs) {
  return refs.includes(DEFAULT_LOCAL_MODEL_REF) ? DEFAULT_LOCAL_MODEL_REF : refs[0];
}

/**
 * @param {Record<string, unknown>} root
 * @returns {{ local?: string; cloud?: string; def?: string }}
 */
function parseExistingTierRules(root) {
  const out = /** @type {{ local?: string; cloud?: string; def?: string }} */ ({});
  const router = root.router;
  if (!router || typeof router !== "object" || !("rules" in router)) return out;
  const rules = /** @type {{ rules?: unknown }} */ (router).rules;
  if (!Array.isArray(rules)) return out;
  for (const row of rules) {
    if (!row || typeof row !== "object") continue;
    const r = /** @type {Record<string, unknown>} */ (row);
    if (r.LOCAL !== undefined) out.local = String(r.LOCAL).trim();
    if (r.CLOUD !== undefined) out.cloud = String(r.CLOUD).trim();
    if (r.default !== undefined) out.def = String(r.default).trim();
  }
  return out;
}

/**
 * @param {Record<string, unknown>} root
 * @returns {boolean | undefined}
 */
function parseExistingMemoryEnabled(root) {
  const memory = root.memory;
  if (!memory || typeof memory !== "object") return undefined;
  if (!("enabled" in memory)) return undefined;
  return Boolean(/** @type {Record<string, unknown>} */ (memory).enabled);
}

/**
 * @param {string} yamlPath
 * @returns {Record<string, unknown>}
 */
function loadOrSeedYaml(yamlPath) {
  if (existsSync(yamlPath)) {
    const text = readFileSync(yamlPath, "utf8");
    const doc = parseYaml(text);
    return doc && typeof doc === "object" && !Array.isArray(doc) ? /** @type {Record<string, unknown>} */ (doc) : {};
  }
  mkdirSync(path.dirname(yamlPath), { recursive: true });
  if (existsSync(DEFAULT_BUNDLED)) {
    const text = readFileSync(DEFAULT_BUNDLED, "utf8");
    const doc = parseYaml(text);
    return doc && typeof doc === "object" && !Array.isArray(doc) ? /** @type {Record<string, unknown>} */ (doc) : {};
  }
  return {
    serve: { host: "0.0.0.0", port: 8910, show_model_prefix: false },
    router: { strategy: "rules", rules: [] },
    memory: { enabled: false },
    storage: { max_prompt_chars: 200 },
  };
}

/**
 * @param {string} label
 * @param {string[]} refs
 * @param {string | undefined} suggested
 * @param {import('node:readline/promises').Interface} rl
 */
async function askModel(label, refs, suggested, rl) {
  output.write(`\n${label}\n`);
  if (refs.length) {
    refs.slice(0, 40).forEach((r, i) => output.write(`  [${i + 1}] ${r}\n`));
    if (refs.length > 40) output.write(`  ... ${refs.length} total; you may type a provider/model not listed\n`);
  } else {
    output.write("  (no models.providers in openclaw.json; type provider/model)\n");
  }
  const hint = suggested ? `Enter for default: ${suggested}` : "type provider/model";
  const line = (await rl.question(`Pick a number or enter full id (${hint})\n> `)).trim();

  if (!line && suggested) return suggested;

  const n = Number.parseInt(line, 10);
  if (Number.isFinite(n) && n >= 1 && n <= refs.length) return refs[n - 1];

  if (line.includes("/")) return line;

  output.write("  Expected provider/model (must include /). Try again for this field.\n");
  return askModel(label, refs, suggested, rl);
}

/**
 * @param {string} label
 * @param {boolean | undefined} suggested
 * @param {import('node:readline/promises').Interface} rl
 * @returns {Promise<boolean>}
 */
async function askYesNo(label, suggested, rl) {
  const hint =
    suggested === undefined ? "y/n, default n" : suggested ? "y/n, Enter for default: y" : "y/n, Enter for default: n";
  const line = (await rl.question(`${label} (${hint})\n> `)).trim().toLowerCase();

  if (!line) return suggested ?? false;
  if (["y", "yes", "1", "true"].includes(line)) return true;
  if (["n", "no", "0", "false"].includes(line)) return false;

  output.write("  Please enter y or n.\n");
  return askYesNo(label, suggested, rl);
}

function copyBundledDefault(yamlPath) {
  if (!existsSync(DEFAULT_BUNDLED)) {
    console.error(`[clawchips setup] missing bundled default: ${DEFAULT_BUNDLED}`);
    process.exit(1);
  }
  mkdirSync(path.dirname(yamlPath), { recursive: true });
  copyFileSync(DEFAULT_BUNDLED, yamlPath);
  console.log(`[clawchips setup] wrote ${yamlPath} from clawchips_default.yaml (non-interactive)`);
}

/**
 * @param {string} yamlPath
 * @param {{ inputStream: import('node:process').stdin | import('node:fs').ReadStream; closeInputStream: boolean }} io
 */
async function runInteractive(yamlPath, io) {
  const { inputStream, closeInputStream } = io;
  const ocPath = openclawConfigPath();
  let refs = [];
  let primary;

  if (existsSync(ocPath)) {
    try {
      const oc = JSON.parse(readFileSync(ocPath, "utf8"));
      refs = collectModelRefsFromOpenClaw(oc);
      primary = suggestedPrimaryFromOpenClaw(oc);
    } catch (e) {
      console.warn(`[clawchips setup] failed to parse ${ocPath}: ${e}`);
    }
  } else {
    console.warn(`[clawchips setup] not found: ${ocPath} — only manual provider/model input is supported.`);
  }

  const existingRoot = loadOrSeedYaml(yamlPath);
  const existing = parseExistingTierRules(existingRoot);
  const existingMemoryEnabled = parseExistingMemoryEnabled(existingRoot);

  const rl = createInterface({ input: inputStream, output });

  console.log(`\nClawChips — configure router.rules / memory (target: ${yamlPath})`);
  if (existsSync(yamlPath)) {
    console.log("clawchips.yaml already exists; confirm or change the below (prompted on each install).\n");
  }
  console.log("Each entry must be a provider/model already configured in OpenClaw (same as openclaw.json / gateway).\n");

  const preferredLocal = suggestedLocalModel(refs);
  const sugLocal = existing.local || preferredLocal || primary || refs[0];
  const sugCloud = existing.cloud || refs.find((r) => r !== sugLocal) || primary || refs[0];
  const sugDef = existing.def || existing.local || preferredLocal || sugLocal;

  try {
    const local = await askModel("LOCAL (local / edge routing target)", refs, sugLocal, rl);
    const cloud = await askModel("CLOUD (cloud routing target)", refs, sugCloud, rl);
    const def = await askModel("default (fallback / ambiguous-tier model)", refs, sugDef, rl);
    const memoryEnabled = await askYesNo("Enable memory routing (memory.enabled)?", existingMemoryEnabled, rl);

    const root = loadOrSeedYaml(yamlPath);
    const router = root.router && typeof root.router === "object" ? /** @type {Record<string, unknown>} */ (root.router) : {};
    router.strategy = router.strategy ?? "rules";
    router.rules = [
      { LOCAL: local },
      { CLOUD: cloud },
      { default: def },
    ];
    root.router = router;

    const memory = root.memory && typeof root.memory === "object" ? /** @type {Record<string, unknown>} */ (root.memory) : {};
    memory.enabled = memoryEnabled;
    root.memory = memory;

    const out = stringifyYaml(root, { lineWidth: 120 });

    writeFileSync(yamlPath, out, "utf8");
    console.log(`\n[clawchips setup] wrote ${yamlPath}`);
    console.log(`  LOCAL=${local}`);
    console.log(`  CLOUD=${cloud}`);
    console.log(`  default=${def}`);
    console.log(`  memory.enabled=${memoryEnabled}`);
  } finally {
    try {
      await rl.close();
    } catch {
      /* ignore */
    }
    if (closeInputStream && inputStream && typeof inputStream.destroy === "function") {
      inputStream.destroy();
    }
  }
}

async function main() {
  const yamlPath = defaultClawchipsPath();
  const ocPath = openclawConfigPath();

  logDiag("start", {
    pid: process.pid,
    ppid: process.ppid,
    cwd: process.cwd(),
    CLAWCHIPS_POSTINSTALL_CHILD: process.env.CLAWCHIPS_POSTINSTALL_CHILD ?? "(unset)",
    CLAWCHIPS_SETUP_SKIP: process.env.CLAWCHIPS_SETUP_SKIP ?? "(unset)",
    CLAWCHIPS_CONFIG: process.env.CLAWCHIPS_CONFIG ?? "(unset)",
    stdinIsTTY: process.stdin.isTTY,
    stdoutIsTTY: process.stdout.isTTY,
    yamlOut: yamlPath,
    openclawJson: ocPath,
    bundledDefault: DEFAULT_BUNDLED,
  });

  if (envSkipInteractive()) {
    logDiag("branch: CLAWCHIPS_SETUP_SKIP — copy bundled default only");
    copyBundledDefault(yamlPath);
    logDiag("finished (non-interactive skip)");
    return;
  }

  const resolved = resolveInteractiveInput();
  const inputMode = resolved
    ? resolved.closeStream
      ? "ReadStream(/dev/tty) in this process"
      : "process.stdin is TTY (e.g. child spawned with /dev/tty as fd0)"
    : "none";

  logDiag("interactive input", {
    mode: inputMode,
    hasResolvedStream: Boolean(resolved),
  });

  if (!resolved) {
    console.log(
      "[clawchips setup] no TTY (stdin not a TTY and /dev/tty unavailable); wrote clawchips_default.yaml. For CI, set CLAWCHIPS_SETUP_SKIP=1.",
    );
    logDiag("branch: no TTY — copy bundled default");
    copyBundledDefault(yamlPath);
    logDiag("finished (fallback copy)");
    return;
  }

  if (existsSync(ocPath)) {
    try {
      const oc = JSON.parse(readFileSync(ocPath, "utf8"));
      const n = collectModelRefsFromOpenClaw(oc).length;
      logDiag("openclaw.json model refs count", n);
    } catch (e) {
      logDiag("openclaw.json parse error (non-fatal)", String(e));
    }
  } else {
    logDiag("openclaw.json missing — manual provider/model only");
  }

  logDiag("branch: interactive prompts for LOCAL / CLOUD / default / memory.enabled");
  await runInteractive(yamlPath, {
    inputStream: resolved.stream,
    closeInputStream: resolved.closeStream,
  });
  logDiag("finished (interactive)");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
