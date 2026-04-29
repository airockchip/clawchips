#!/usr/bin/env python3
"""
Build a local chunk+embedding SQLite index for quick document search.

Pipeline:
1) Read one Markdown document
2) Split each document into overlapping chunks
3) Use ModelHubPyClient to POST /v1/embeddings on the hub (OpenAI-compatible JSON body)
4) Store chunks and embedding vectors in SQLite

Example:
  python skills/rk-rag/scripts/rk_rag.py \
    --input-file ./docs/guide.md \
    --db ./quick_search.db \
    --endpoint http://127.0.0.1:18080 \
    --model rknn-embedding
"""

from __future__ import annotations

import argparse
import datetime as dt
import yaml
import hashlib
import json
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple


MARKDOWN_EXTENSIONS = {".md", ".markdown"}
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB_ROOT = SCRIPT_DIR.parent / "database"


@dataclass
class Document:
    path: str
    text: str
    sha256: str


@dataclass
class Chunk:
    doc_path: str
    chunk_index: int
    text: str
    start_char: int
    end_char: int


@dataclass
class SectionBlock:
    text: str
    start_char: int
    end_char: int


@dataclass
class Section:
    heading_path: List[Tuple[int, str]]
    blocks: List[SectionBlock]


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def read_text_file(path: Path) -> Optional[str]:
    # Keep deterministic fallback order for common UTF-8 / Chinese encodings.
    for enc in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            data = path.read_text(encoding=enc)
            if "\x00" in data:
                return None
            return data
        except UnicodeDecodeError:
            continue
        except OSError:
            return None
    return None


def resolve_db_path(raw_db: str, db_root: str) -> Path:
    p = Path(raw_db)
    if p.is_absolute():
        return p.resolve()
    root = Path(db_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return (root / p).resolve()


def load_single_markdown(input_file: Path) -> Document:
    if not input_file.is_file():
        raise ValueError(f"input file does not exist: {input_file}")
    if input_file.suffix.lower() not in MARKDOWN_EXTENSIONS:
        raise ValueError("input file must be a markdown document (.md/.markdown)")

    text = read_text_file(input_file)
    if text is None:
        raise ValueError(f"cannot decode file as text: {input_file}")
    cleaned = text.strip()
    if not cleaned:
        raise ValueError(f"markdown file is empty: {input_file}")

    return Document(path=str(input_file.resolve()), text=cleaned, sha256=sha256_text(cleaned))


HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:-]+\|[\s|:-]*$")


def _line_offsets(lines: Sequence[str]) -> List[int]:
    offsets = [0] * (len(lines) + 1)
    cur = 0
    for i, line in enumerate(lines):
        offsets[i] = cur
        cur += len(line)
    offsets[len(lines)] = cur
    return offsets


def _is_heading(line: str) -> Optional[Tuple[int, str]]:
    m = HEADING_RE.match(line.rstrip("\n"))
    if not m:
        return None
    level = len(m.group(1))
    title = m.group(2).strip().rstrip("#").strip()
    return level, title


def _is_table_start(lines: Sequence[str], i: int) -> bool:
    if i + 1 >= len(lines):
        return False
    header = lines[i].rstrip("\n")
    sep = lines[i + 1].rstrip("\n")
    if "|" not in header:
        return False
    if not TABLE_SEP_RE.match(sep):
        return False
    return True


def parse_markdown_sections(text: str) -> List[Section]:
    lines = text.splitlines(keepends=True)
    offsets = _line_offsets(lines)

    sections: List[Section] = []
    heading_path: List[Tuple[int, str]] = []
    cur_blocks: List[SectionBlock] = []
    i = 0

    def flush_section() -> None:
        nonlocal cur_blocks
        if cur_blocks:
            sections.append(Section(heading_path=heading_path.copy(), blocks=cur_blocks))
            cur_blocks = []

    while i < len(lines):
        line = lines[i]

        h = _is_heading(line)
        if h:
            flush_section()
            level, title = h
            while heading_path and heading_path[-1][0] >= level:
                heading_path.pop()
            heading_path.append((level, title))
            i += 1
            continue

        if not line.strip():
            i += 1
            continue

        block_start_i = i

        if FENCE_RE.match(line):
            fence = FENCE_RE.match(line).group(1)  # type: ignore[union-attr]
            i += 1
            while i < len(lines):
                if lines[i].lstrip().startswith(fence):
                    i += 1
                    break
                i += 1
        elif _is_table_start(lines, i):
            i += 2
            while i < len(lines):
                s = lines[i].rstrip("\n")
                if not s.strip():
                    break
                if "|" not in s:
                    break
                i += 1
        else:
            i += 1
            while i < len(lines):
                if not lines[i].strip():
                    break
                if _is_heading(lines[i]):
                    break
                if FENCE_RE.match(lines[i]):
                    break
                if _is_table_start(lines, i):
                    break
                i += 1

        raw = "".join(lines[block_start_i:i]).strip()
        if raw:
            cur_blocks.append(
                SectionBlock(
                    text=raw,
                    start_char=offsets[block_start_i],
                    end_char=offsets[i],
                )
            )

    flush_section()
    return sections


def _heading_prefix(path: Sequence[Tuple[int, str]]) -> str:
    if not path:
        return ""
    return "\n".join([f"{'#' * level} {title}" for level, title in path if title]).strip()


def markdown_structured_chunks(text: str, soft_max_chars: int) -> List[Tuple[str, int, int]]:
    if soft_max_chars <= 0:
        raise ValueError("soft_max_chars must be > 0")

    sections = parse_markdown_sections(text)
    out: List[Tuple[str, int, int]] = []

    for sec in sections:
        if not sec.blocks:
            continue
        prefix = _heading_prefix(sec.heading_path)

        cur_text = ""
        cur_start = -1
        cur_end = -1

        for b in sec.blocks:
            candidate_body = b.text if not cur_text else f"{cur_text}\n\n{b.text}"
            candidate_full = f"{prefix}\n\n{candidate_body}".strip() if prefix else candidate_body

            if cur_text and len(candidate_full) > soft_max_chars:
                final_text = f"{prefix}\n\n{cur_text}".strip() if prefix else cur_text
                out.append((final_text, cur_start, cur_end))
                cur_text = b.text
                cur_start = b.start_char
                cur_end = b.end_char
            else:
                if not cur_text:
                    cur_text = b.text
                    cur_start = b.start_char
                    cur_end = b.end_char
                else:
                    cur_text = f"{cur_text}\n\n{b.text}"
                    cur_end = b.end_char

        if cur_text:
            final_text = f"{prefix}\n\n{cur_text}".strip() if prefix else cur_text
            out.append((final_text, cur_start, cur_end))

    return out


def build_chunks(docs: Sequence[Document], chunk_size: int) -> List[Chunk]:
    out: List[Chunk] = []
    for d in docs:
        parts = markdown_structured_chunks(d.text, soft_max_chars=chunk_size)
        for i, (chunk_text, s, e) in enumerate(parts):
            out.append(
                Chunk(
                    doc_path=d.path,
                    chunk_index=i,
                    text=chunk_text,
                    start_char=s,
                    end_char=e,
                )
            )
    return out


def to_float32_blob(vec: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *[float(x) for x in vec])


def preview_text(text: str, limit: int = 180) -> str:
    s = " ".join(text.split())
    if len(s) <= limit:
        return s
    return s[:limit] + "..."


def write_chunks_markdown(
    out_path: Path,
    source_doc: Document,
    chunks: Sequence[Chunk],
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    lines.append("# Chunk Preview")
    lines.append("")
    lines.append(f"- source: `{source_doc.path}`")
    lines.append(f"- doc_sha256: `{source_doc.sha256}`")
    lines.append(f"- chunk_count: `{len(chunks)}`")
    lines.append("")

    for c in chunks:
        lines.append("")
        lines.append(f"<!-- ==================== CHUNK {c.chunk_index:04d} START ==================== -->")
        lines.append("")
        lines.append(f"## Chunk {c.chunk_index}")
        lines.append("")
        lines.append(f"- char_range: `{c.start_char}..{c.end_char}`")
        lines.append(f"- chars: `{len(c.text)}`")
        lines.append("")
        lines.append(c.text)
        lines.append("")
        lines.append(f"<!-- ==================== CHUNK {c.chunk_index:04d} END ====================== -->")
        lines.append("")
        lines.append("---")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


class EmbeddingClient:
    def __init__(
        self,
        endpoint: str,
        model: str,
        timeout_sec: int = 30,
        dimensions: Optional[int] = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout_sec = timeout_sec
        self.dimensions = dimensions

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []

        from model_hub_py.client import ModelHubPyClient

        payload: dict[str, object] = {
            "model": self.model,
            "input": list(texts),
            "encoding_format": "float",
        }
        if self.dimensions is not None and self.dimensions > 0:
            payload["dimensions"] = self.dimensions

        client = ModelHubPyClient(self.endpoint, timeout=float(self.timeout_sec))
        result = client.run(
            self.model,
            method="POST",
            path="/v1/embeddings",
            json_body=payload,
            timeout=float(self.timeout_sec),
        )
        upstream_body = result.get("upstream_body")
        if not isinstance(upstream_body, dict):
            raise RuntimeError(
                f"invalid model_hub embedding result: {json.dumps(result, ensure_ascii=False)[:500]}"
            )
        data = upstream_body
        items = data.get("data")
        if not isinstance(items, list):
            raise RuntimeError("embedding response missing data[]")

        indexed: List[Tuple[int, List[float]]] = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise RuntimeError(f"embedding item at index {i} is not an object")
            idx = item.get("index", i)
            emb = item.get("embedding")
            if not isinstance(idx, int):
                raise RuntimeError(f"embedding item index invalid at {i}")
            if not isinstance(emb, list) or not emb:
                raise RuntimeError(f"embedding item embedding invalid at {i}")
            indexed.append((idx, [float(x) for x in emb]))

        indexed.sort(key=lambda x: x[0])
        return [v for _, v in indexed]


def pick_latest_target(known_users_json: Path | str) -> str:
    # argparse default uses "~/.openclaw/..."; Path does not expand "~" by itself.
    path = Path(known_users_json).expanduser()
    if not path.exists():
        raise RuntimeError(f"known-users.json not found: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows = [row for row in rows if isinstance(row, dict) and str(row.get("openid", "")).strip()]
    if not rows:
        raise RuntimeError("no valid qqbot target")
    rows.sort(key=lambda row: int(row.get("lastSeenAt", 0) or 0), reverse=True)
    row = rows[0]
    kind = "group" if row.get("type") == "group" else "c2c"
    return f"qqbot:{kind}:{str(row['openid']).strip()}"


def _http_get_json(url: str, timeout_sec: int) -> Optional[dict[str, Any]]:
    req = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _http_post_json(url: str, payload: dict[str, Any], timeout_sec: int) -> Optional[dict[str, Any]]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _extract_chat_content(resp: dict[str, Any]) -> str:
    choices = resp.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        msg = choices[0].get("message")
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                texts: List[str] = []
                for item in content:
                    if isinstance(item, dict):
                        t = item.get("text")
                        if isinstance(t, str) and t:
                            texts.append(t)
                if texts:
                    return "\n".join(texts)
    return json.dumps(resp, ensure_ascii=False)


def _send_text_to_qq(args: argparse.Namespace, text: str) -> bool:
    if getattr(args, "no_qq_send", False):
        return False
    payload = str(text or "").strip()
    if not payload:
        return False
    try:
        target = pick_latest_target(str(getattr(args, "qq_known_users_json")))
    except Exception as e:
        raw = getattr(args, "qq_known_users_json", "")
        resolved = str(Path(str(raw)).expanduser())
        print(
            f"[rk-rag] cannot pick qqbot target: {type(e).__name__}: {e}\n"
            f"  configured path: {raw!r}\n"
            f"  resolved path:   {resolved!r}",
            file=sys.stderr,
        )
        return False
    if shutil.which("openclaw") is None:
        print("[rk-rag] openclaw command not found, skip qqbot send", file=sys.stderr)
        return False
    proc = subprocess.run(
        [
            "openclaw",
            "message",
            "send",
            "--channel",
            "qqbot",
            "--target",
            target,
            "--message",
            payload,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        print("已完成查询")
        return True
    err = (proc.stderr or proc.stdout or "").strip()
    print(f"[rk-rag] qqbot send failed: {err}", file=sys.stderr)
    return False


def _format_recalls_text_for_server(recalls_payload: Sequence[dict[str, Any]]) -> str:
    rows = [str(r.get("chunk_text", "")).strip() for r in recalls_payload if str(r.get("chunk_text", "")).strip()]
    if not rows:
        return "(no recalls)"
    return "\n".join([f"[{i + 1}] {t}" for i, t in enumerate(rows)])


LLM_MODEL_NAME = "qwen3-4b-instruct-2507"
MODEL_HUB_CONFIG_PATH = Path("/userdata/model_hub/model_hub_config.yaml")
EMBEDDING_DIR = Path("/userdata/model_hub/qwen3-4b-instruct-2507")


def _llm_available() -> bool:
    if not EMBEDDING_DIR.is_dir():
        return False
    if not MODEL_HUB_CONFIG_PATH.is_file():
        return False
    try:
        raw = MODEL_HUB_CONFIG_PATH.read_text(encoding="utf-8")
        cfg = yaml.safe_load(raw)
    except Exception:
        return False
    if not isinstance(cfg, dict):
        return False
    services = cfg.get("services")
    if not isinstance(services, list):
        return False
    return any(
        isinstance(s, dict) and s.get("name") == LLM_MODEL_NAME
        for s in services
    )


def _forward_to_server_and_send_qq(
    args: argparse.Namespace,
    query: str,
    recalls_payload: Sequence[dict[str, Any]],
) -> None:
    if getattr(args, "no_server_forward", False):
        return

    server_endpoint = str(getattr(args, "server_endpoint", "http://127.0.0.1:8000")).rstrip("/")
    server_timeout = int(getattr(args, "server_timeout", 60))

    recalls_text_for_server = _format_recalls_text_for_server(recalls_payload)

    prompt_text = (
        "你是KK，来自RK的智能助手。作为专业的知识处理助手，请严格按以下步骤处理问题：\n"
        "1. 语言识别：根据用户问题自动切换回答语言\n"
        "2. 信息整合：\n"
        "    - 如果未找到相关知识内容，明确告知“未能从知识库中检索到与问题相关的信息”。\n"
        "    - 如果知识库中有相关信息，则基于该信息进行回答，禁止自行编造或推测。\n"
        "    - 禁止添加知识库外的解释、背景或扩展。\n"
        "3. 准确性验证：\n"
        "    - 所有回答必须严格依据知识库中的内容，不推导、不假设、不猜测。\n"
        "    - 对于复杂问题，仅使用知识库中已有的信息进行推理，直接输出结果，不引用或标注来源。\n"
        "4. 格式清晰：\n"
        "    - 回答应采用自然换行方式，确保可读性。\n"
        "    - 不使用Markdown格式，只用纯文本。\n"
        "5. 口语化表达：\n"
        "    - 知识库中给出的参考信息多为书面语言，请用口语化的方式回答问题，让非专业人士能轻松理解，\n"
        "    - 专业名词和术语请保持原文\n"
        "    - 要求语言通顺，避免使用过多的缩写\n"
        "    - 单位用全称，如\"兆赫兹\"、\"伏特\"\n"
        "\n"
        "遵守以下响应规范：\n"
        "    禁用\"根据提供内容\"等冗余前缀\n"
        "    保持专业中立立场，避免主观推测\n"
        "    不在检索内容中的信息必须声明\"根据现有信息无法确定\"\n"
        "\n"
        "相关检索内容：\n"
        "===================\n"
        f"{recalls_text_for_server}\n"
        "\n"
        f"请根据相关检索内容回答问题：{query}\n"
    )

    if not _llm_available():
        print(prompt_text)
        return

    from model_hub_py.client import ModelHubPyClient

    payload = {
        "messages": [{"role": "user", "content": prompt_text}],
        "temperature": 0.2,
    }
    client = ModelHubPyClient(server_endpoint, timeout=float(server_timeout))
    result = client.run(
        LLM_MODEL_NAME,
        method="POST",
        path="/v1/chat/completions",
        json_body=payload,
        timeout=float(server_timeout),
    )
    upstream_body = result.get("upstream_body")
    if not isinstance(upstream_body, dict):
        print("[rk-rag] server chat call failed", file=sys.stderr)
        return

    llm_answer = _extract_chat_content(upstream_body).strip()
    if not llm_answer:
        print("[rk-rag] empty server answer, skip qqbot send", file=sys.stderr)
        return

    _send_text_to_qq(args, llm_answer)


def _blob_to_float32_list(blob: bytes) -> List[float]:
    if len(blob) % 4 != 0:
        raise ValueError(f"invalid embedding blob length={len(blob)} (must be multiple of 4)")
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def search_topk(args: argparse.Namespace) -> int:
    try:
        import numpy as np  # lazy import to avoid making index mode depend on numpy
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"search mode requires numpy: {e}") from e

    db_path = resolve_db_path(args.db, args.db_root)
    if not db_path.is_file():
        raise ValueError(f"db not found: {db_path}")
    if not args.query.strip():
        raise ValueError("query must not be empty")

    client = EmbeddingClient(
        endpoint=args.endpoint,
        model=args.model,
        timeout_sec=args.timeout,
        dimensions=args.dimensions,
    )
    q_vecs = client.embed_batch([args.query])
    if len(q_vecs) != 1 or not q_vecs[0]:
        raise RuntimeError("failed to compute query embedding")
    q_vec = q_vecs[0]
    q_dim = len(q_vec)

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """
            SELECT
              c.id, c.chunk_index, c.chunk_text, c.start_char, c.end_char,
              c.embedding, c.embedding_dim, c.embedding_model, d.path
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            ORDER BY c.id ASC
            """
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("[rk-rag][search] no chunk records found", file=sys.stderr)
        return 2

    kept_meta: List[Tuple[int, int, str, int, int, str, str]] = []
    doc_vecs: List[List[float]] = []
    for row in rows:
        (
            chunk_id,
            chunk_index,
            chunk_text,
            start_char,
            end_char,
            emb_blob,
            emb_dim,
            emb_model,
            doc_path,
        ) = row
        if not isinstance(emb_blob, (bytes, bytearray, memoryview)):
            continue
        vec = _blob_to_float32_list(bytes(emb_blob))
        if len(vec) != q_dim:
            continue
        kept_meta.append(
            (
                int(chunk_id),
                int(chunk_index),
                str(chunk_text),
                int(start_char),
                int(end_char),
                str(emb_model or ""),
                str(doc_path),
            )
        )
        doc_vecs.append(vec)

    if not doc_vecs:
        print(
            f"[rk-rag][search] no chunks match query dim={q_dim}. "
            "Re-index with same embedding model/dimensions.",
            file=sys.stderr,
        )
        return 3

    embeddings_query_dense = np.asarray(q_vec, dtype=np.float32).reshape(1, -1)
    embeddings_doc_dense = np.asarray(doc_vecs, dtype=np.float32)
    dense_scores = (embeddings_query_dense @ embeddings_doc_dense.T)

    scores_1d = dense_scores[0]
    topk = max(1, int(args.topk))
    topk = min(topk, scores_1d.shape[0])

    # Stable top-k: sort by score desc, then by chunk_id asc.
    ranked_indices = sorted(
        range(scores_1d.shape[0]),
        key=lambda i: (-float(scores_1d[i]), kept_meta[i][0]),
    )[:topk]

    recalls_payload = []
    for rank, idx in enumerate(ranked_indices, start=1):
        score = float(scores_1d[idx])
        chunk_id, chunk_index, chunk_text, start_char, end_char, emb_model, doc_path = kept_meta[idx]
        recalls_payload.append(
            {
                "rank": rank,
                "score": score,
                "chunk_id": chunk_id,
                "chunk_index": chunk_index,
                "start_char": start_char,
                "end_char": end_char,
                "embedding_model": emb_model,
                "doc_path": doc_path,
                "chunk_text": chunk_text,
            }
        )

    if getattr(args, "recall_json_out", ""):
        out_path = Path(args.recall_json_out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_obj = {
            "query": args.query,
            "topk": topk,
            "query_dim": q_dim,
            "candidates": int(scores_1d.shape[0]),
            "recalls": recalls_payload,
        }
        out_path.write_text(json.dumps(out_obj, ensure_ascii=False), encoding="utf-8")

    _forward_to_server_and_send_qq(args, args.query, recalls_payload)

    return 0


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS documents (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          path TEXT NOT NULL UNIQUE,
          sha256 TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chunks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          document_id INTEGER NOT NULL,
          chunk_index INTEGER NOT NULL,
          chunk_text TEXT NOT NULL,
          start_char INTEGER NOT NULL,
          end_char INTEGER NOT NULL,
          embedding BLOB NOT NULL,
          embedding_dim INTEGER NOT NULL,
          embedding_model TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
          UNIQUE(document_id, chunk_index)
        );

        CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);
        """
    )


def upsert_document(conn: sqlite3.Connection, path: str, sha256: str) -> int:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO documents(path, sha256, updated_at)
        VALUES(?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256, updated_at=excluded.updated_at
        """,
        (path, sha256, now),
    )
    row = conn.execute("SELECT id FROM documents WHERE path = ?", (path,)).fetchone()
    if not row:
        raise RuntimeError(f"failed to load document id for {path}")
    return int(row[0])


def replace_document_chunks(
    conn: sqlite3.Connection,
    document_id: int,
    chunks: Sequence[Chunk],
    embeddings: Sequence[Sequence[float]],
    model: str,
) -> None:
    if len(chunks) != len(embeddings):
        raise RuntimeError("chunks/embeddings size mismatch")

    conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
    now = utc_now_iso()

    rows = []
    for c, e in zip(chunks, embeddings):
        rows.append(
            (
                document_id,
                c.chunk_index,
                c.text,
                c.start_char,
                c.end_char,
                to_float32_blob(e),
                len(e),
                model,
                now,
            )
        )

    conn.executemany(
        """
        INSERT INTO chunks(
          document_id, chunk_index, chunk_text, start_char, end_char,
          embedding, embedding_dim, embedding_model, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def count_all_chunks(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(1) FROM chunks").fetchone()
    return int(row[0] or 0) if row else 0


def build_index(args: argparse.Namespace) -> int:
    input_file = Path(args.input_file).resolve()
    db_path = resolve_db_path(args.db, args.db_root)

    doc = load_single_markdown(input_file)

    chunks = build_chunks([doc], chunk_size=args.chunk_size)
    if not chunks:
        print("[rk-rag] chunking produced no chunks", file=sys.stderr)
        return 3
    chunks_md_path = (
        Path(args.chunks_markdown).resolve()
        if args.chunks_markdown
        else db_path.with_suffix(".chunks.md")
    )
    write_chunks_markdown(chunks_md_path, doc, chunks)

    client = EmbeddingClient(
        endpoint=args.endpoint,
        model=args.model,
        timeout_sec=args.timeout,
        dimensions=args.dimensions,
    )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)

        # Fixed batch-size=1: send one chunk per embedding request.
        embedded: List[List[float]] = []
        for c in chunks:
            vecs = client.embed_batch([c.text])
            if len(vecs) != 1:
                raise RuntimeError(f"embedding count mismatch: expected 1, got {len(vecs)}")
            embedded.append(vecs[0])

        existed_before = count_all_chunks(conn)
        doc_id = upsert_document(conn, doc.path, doc.sha256)
        replace_document_chunks(conn, doc_id, chunks, embedded, args.model)
        existed_after = count_all_chunks(conn)

        conn.commit()
        print(
            f"[rk-rag] chunk_existing={existed_before} "
            f"chunk_imported={len(chunks)} chunk_total={existed_after}"
        )
        return 0
    finally:
        conn.close()


def list_databases(args: argparse.Namespace) -> int:
    db_root = Path(args.db_root).resolve()
    if not db_root.is_dir():
        print(f"[rk-rag] database directory not found: {db_root}", file=sys.stderr)
        return 1

    db_files = sorted(db_root.glob("*.db"))
    if not db_files:
        print("[rk-rag] no knowledge base found")
        return 0

    print(f"[rk-rag] found {len(db_files)} knowledge base(s):\n")
    for db_path in db_files:
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                doc_count = int(conn.execute("SELECT COUNT(1) FROM documents").fetchone()[0] or 0)
                chunk_count = int(conn.execute("SELECT COUNT(1) FROM chunks").fetchone()[0] or 0)
                doc_paths = [
                    str(r[0]) for r in conn.execute("SELECT path FROM documents ORDER BY id ASC").fetchall()
                ]
            finally:
                conn.close()
        except Exception:
            doc_count = 0
            chunk_count = 0
            doc_paths = []

        print(f"  - {db_path.name}  (documents: {doc_count}, chunks: {chunk_count})")
        for dp in doc_paths:
            print(f"      source: {dp}")
    return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    raw = list(argv if argv is not None else sys.argv[1:])
    if raw and raw[0] not in {"index", "search", "list"}:
        # Backward compatibility: old usage without subcommand defaults to index mode.
        raw = ["index", *raw]

    p = argparse.ArgumentParser(description="Quick search indexer + dense retrieval")
    sub = p.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="Build chunk+embedding SQLite index")
    p_index.add_argument("--input-file", type=str, required=True, help="Single markdown file path")
    p_index.add_argument("--db", type=str, required=True, help="SQLite output path")
    p_index.add_argument(
        "--db-root",
        type=str,
        default=str(DEFAULT_DB_ROOT),
        help="Root directory for relative --db paths (default: <skill>/database)",
    )
    p_index.add_argument(
        "--chunks-markdown",
        type=str,
        default="",
        help="Optional output markdown path for chunk preview (default: <db>.chunks.md)",
    )
    p_index.add_argument(
        "--chunk-size",
        type=int,
        default=1800,
        help="Soft max chars per chunk (structure-first, heading/table aware)",
    )
    p_index.add_argument("--endpoint", type=str, default="http://127.0.0.1:8000", help="Embedding service endpoint")
    p_index.add_argument("--model", type=str, default="embedding", help="Embedding model id")
    p_index.add_argument("--dimensions", type=int, default=0, help="Optional output dimensions (0 disables)")
    p_index.add_argument("--timeout", type=int, default=30, help="HTTP timeout seconds")

    p_search = sub.add_parser("search", help="Dense retrieval from existing SQLite index")
    p_search.add_argument("--db", type=str, required=True, help="SQLite index path")
    p_search.add_argument(
        "--db-root",
        type=str,
        default=str(DEFAULT_DB_ROOT),
        help="Root directory for relative --db paths (default: <skill>/database)",
    )
    p_search.add_argument("--query", type=str, required=True, help="User query text")
    p_search.add_argument("--topk", type=int, default=5, help="Top-k results")
    p_search.add_argument(
        "--recall-json-out",
        type=str,
        default="",
        help="Optional output path for full recalled chunks in JSON (for downstream use)",
    )
    p_search.add_argument("--endpoint", type=str, default="http://127.0.0.1:8000", help="Embedding service endpoint")
    p_search.add_argument("--model", type=str, default="embedding", help="Embedding model id")
    p_search.add_argument("--dimensions", type=int, default=0, help="Optional query dimensions (0 disables)")
    p_search.add_argument("--timeout", type=int, default=30, help="HTTP timeout seconds")
    p_search.add_argument("--server-endpoint", type=str, default="http://127.0.0.1:8000", help="LLM server endpoint")
    p_search.add_argument("--server-timeout", type=int, default=60, help="LLM server HTTP timeout seconds")
    p_search.add_argument(
        "--qq-known-users-json",
        type=str,
        default="~/.openclaw/qqbot/data/known-users.json",
        help="Path to qqbot known-users.json for picking latest target",
    )
    p_search.add_argument("--no-server-forward", action="store_true", help="Disable forwarding recalls to LLM server")
    p_search.add_argument("--no-qq-send", action="store_true", help="Disable sending LLM answer to qqbot")

    p_list = sub.add_parser("list", help="List all knowledge bases in the database directory")
    p_list.add_argument(
        "--db-root",
        type=str,
        default=str(DEFAULT_DB_ROOT),
        help="Root directory for .db files (default: <skill>/database)",
    )

    args = p.parse_args(raw)
    if hasattr(args, "dimensions"):
        args.dimensions = args.dimensions if args.dimensions and args.dimensions > 0 else None
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = parse_args(argv)
        if args.command == "search":
            return search_topk(args)
        if args.command == "list":
            return list_databases(args)
        return build_index(args)
    except Exception as e:
        print(f"[rk-rag] error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
