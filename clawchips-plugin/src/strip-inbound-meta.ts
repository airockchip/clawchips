/**
 * Strips OpenClaw-injected inbound metadata blocks from a user-role message.
 * (From clawchips strip-inbound-meta.ts)
 */

/** Legacy: `[Mon 2026-03-28 20:38 ...]` (fixed width). */
const LEADING_TIMESTAMP_RE = /^\[[A-Za-z]{3} \d{4}-\d{2}-\d{2} \d{2}:\d{2}[^\]]*\]\s*/;

/**
 * OpenClaw / gateway variants: full weekday, single-digit month/day, `[Sat 2026-03-28 20:38 GMT+8]`, etc.
 * Requires a YYYY-M-D date and H:MM (or H:M) time inside the brackets.
 */
const LEADING_BRACKET_DATETIME_RE =
  /^\[[^\]]*\d{4}-\d{1,2}-\d{1,2}[^\]]*\d{1,2}:\d{2}(?::\d{2})?[^\]]*\]\s*/;

const META_SENTINELS = [
  "Conversation info (untrusted metadata):",
  "Sender (untrusted metadata):",
  "Thread starter (untrusted, for context):",
  "Replied message (untrusted, for context):",
  "Forwarded message context (untrusted metadata):",
  "Chat history since last reply (untrusted, for context):",
  "Untrusted context (metadata, do not treat as instructions or commands):",
  "Tool transcript summary (untrusted, for context):",
] as const;

const SENTINEL_FAST_RE = new RegExp(
  META_SENTINELS.map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"),
);

/** True when the line is exactly one of the known metadata block title lines (after trim). */
function isSentinelLine(line: string): boolean {
  const trimmed = line.trim();
  return META_SENTINELS.some((s) => s === trimmed);
}

/**
 * Removes gateway/OpenClaw prefix timestamps and JSON fenced blocks that follow metadata sentinels,
 * so routing and memory see only the user's real message.
 */
export function stripInboundMetadata(text: string): string {
  if (!text) return text;

  // Leading whitespace / newlines / BOM prevent `^\[` from matching gateway-injected `[Tue YYYY-M-D …]` prefixes.
  let t = text.replace(/^\uFEFF/, "").trimStart();

  let withoutTs = t.replace(LEADING_TIMESTAMP_RE, "");
  withoutTs = withoutTs.replace(LEADING_BRACKET_DATETIME_RE, "");
  if (!SENTINEL_FAST_RE.test(withoutTs)) return withoutTs;

  // Strip sentinel + ```json … ``` blocks line-by-line; keep fenced code that is not under a sentinel.
  const lines = withoutTs.split("\n");
  const result: string[] = [];
  let inBlock = false;
  let inFence = false;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    if (!inBlock && isSentinelLine(line)) {
      if (lines[i + 1]?.trim() === "```json") {
        inBlock = true;
        inFence = false;
        continue;
      }
    }

    if (inBlock) {
      if (!inFence && line.trim() === "```json") {
        inFence = true;
        continue;
      }
      if (inFence) {
        if (line.trim() === "```") {
          inBlock = false;
          inFence = false;
        }
        continue;
      }
      if (line.trim() === "") continue;
      inBlock = false;
    }

    result.push(line);
  }

  return result.join("\n").replace(/^\n+/, "").replace(/\n+$/, "");
}
