#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO


DEFAULT_ADB_SERIAL = "bfa1f67e4c22b457"


SESSION_START_RE = re.compile(
    r"^(?P<ts>\S+ \S+) .*RKNN3 session_run start: "
    r"prompt_tokens=(?P<prompt_tokens>\d+) max_new_tokens=(?P<max_new_tokens>\d+) "
    r"enable_thinking=(?P<enable_thinking>\S+)"
)
SESSION_END_RE = re.compile(
    r"^(?P<ts>\S+ \S+) .*RKNN3 session_run end: "
    r"ret=(?P<ret>\S+) terminal_state=(?P<terminal_state>\S+) "
    r"tokens=(?P<tokens>\d+) decoded_length=(?P<decoded_length>\d+) "
    r"stopped_by_word=(?P<stopped_by_word>\S+)"
)
CREATE_STATE_RE = re.compile(
    r"^(?P<ts>\S+ \S+) .*XGrammar create_state: "
    r"enabled=(?P<enabled>\S+) tools=(?P<tools>\d+) "
    r"tool_choice=(?P<tool_choice>\S+) reasoning=(?P<reasoning>\S+) stop=(?P<stop>.*)"
)
ACTIVATED_RE = re.compile(r"^(?P<ts>\S+ \S+) .*XGrammar toolcall structure activated:")
COMPLETED_RE = re.compile(r"^(?P<ts>\S+ \S+) .*XGrammar toolcall structure completed")
TIMING_RE = re.compile(r"^(?P<ts>\S+ \S+) .*RKNN3 sampling timing: (?P<body>.*)")
KV_RE = re.compile(r"([a-zA-Z_]+)=([^\s]+)")


@dataclass
class Session:
    index: int
    start_ts: str = ""
    end_ts: str = ""
    timing_ts: str = ""
    prompt_tokens: str = ""
    max_new_tokens: str = ""
    enable_thinking: str = ""
    ret: str = ""
    terminal_state: str = ""
    tokens: str = ""
    decoded_length: str = ""
    stopped_by_word: str = ""
    xgrammar_enabled: str = ""
    tools: str = ""
    tool_choice: str = ""
    reasoning: str = ""
    xgrammar_activated: bool = False
    xgrammar_completed: bool = False
    timing: dict[str, str] = field(default_factory=dict)


def read_lines(
    path: str,
    use_adb: bool,
    adb_serial: str = DEFAULT_ADB_SERIAL,
) -> list[str]:
    if not use_adb:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.readlines()
    command = ["adb"]
    if adb_serial:
        command.extend(["-s", adb_serial])
    command.extend(["shell", "cat", path])
    result = subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.splitlines(True)


def parse_log(lines: list[str]) -> list[Session]:
    sessions: list[Session] = []
    current: Session | None = None
    last_create_state: dict[str, str] | None = None
    pending_xgrammar_activated = False
    pending_xgrammar_completed = False

    def attach_create_state(session: Session) -> None:
        if last_create_state is None:
            return
        session.xgrammar_enabled = last_create_state["enabled"]
        session.tools = last_create_state["tools"]
        session.tool_choice = last_create_state["tool_choice"]
        session.reasoning = last_create_state["reasoning"]

    for line in lines:
        if match := CREATE_STATE_RE.search(line):
            last_create_state = match.groupdict()
            pending_xgrammar_activated = False
            pending_xgrammar_completed = False
            continue

        if match := SESSION_START_RE.search(line):
            current = Session(index=len(sessions) + 1)
            current.start_ts = match.group("ts")
            current.prompt_tokens = match.group("prompt_tokens")
            current.max_new_tokens = match.group("max_new_tokens")
            current.enable_thinking = match.group("enable_thinking")
            attach_create_state(current)
            sessions.append(current)
            continue

        if ACTIVATED_RE.search(line):
            if current is not None:
                current.xgrammar_activated = True
            else:
                pending_xgrammar_activated = True
            continue

        if COMPLETED_RE.search(line):
            if current is not None:
                current.xgrammar_completed = True
            else:
                pending_xgrammar_completed = True
            continue

        if current is not None and (match := SESSION_END_RE.search(line)):
            current.end_ts = match.group("ts")
            current.ret = match.group("ret")
            current.terminal_state = match.group("terminal_state")
            current.tokens = match.group("tokens")
            current.decoded_length = match.group("decoded_length")
            current.stopped_by_word = match.group("stopped_by_word")
            continue

        if match := TIMING_RE.search(line):
            # Newer gateway versions no longer print the session_run start/end
            # records.  Build a minimal session directly from the timing line
            # instead of silently discarding an otherwise complete sample.
            if current is None:
                current = Session(index=len(sessions) + 1)
                if last_create_state is not None:
                    current.start_ts = last_create_state["ts"]
                attach_create_state(current)
                current.xgrammar_activated = pending_xgrammar_activated
                current.xgrammar_completed = pending_xgrammar_completed
                sessions.append(current)
            current.timing_ts = match.group("ts")
            current.timing = dict(KV_RE.findall(match.group("body")))
            current = None
            last_create_state = None
            pending_xgrammar_activated = False
            pending_xgrammar_completed = False

    return sessions


def fnum(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def row_for(session: Session) -> dict[str, str]:
    timing = session.timing
    callbacks = fnum(timing.get("callbacks", "0"))
    callback_total = fnum(timing.get("callback_total_ms", "0"))
    token_select_total = fnum(timing.get("token_select_total_ms", "0"))
    xgrammar_mask_total = fnum(timing.get("xgrammar_mask_total_ms", "0"))
    xgrammar_mask_count = fnum(timing.get("xgrammar_mask_count", "0"))
    sample_total = fnum(timing.get("sample_total_ms", "0"))
    native_sampler_total = fnum(timing.get("native_sampler_total_ms", "0"))
    matcher_accept_total = fnum(timing.get("matcher_accept_total_ms", "0"))

    return {
        "idx": str(session.index),
        "start": session.start_ts,
        "end": session.end_ts,
        "tools": session.tools,
        "tokens": session.tokens,
        "callbacks": timing.get("callbacks", ""),
        "callback_total_ms": timing.get("callback_total_ms", ""),
        "callback_avg_ms": f"{callback_total / callbacks:.3f}" if callbacks else "",
        "callback_max_ms": timing.get("callback_max_ms", ""),
        "sample_total_ms": timing.get("sample_total_ms", ""),
        "sample_avg_ms": f"{sample_total / callbacks:.3f}" if callbacks else "",
        "sample_max_ms": timing.get("sample_max_ms", ""),
        "xgrammar_mask_total_ms": timing.get("xgrammar_mask_total_ms", ""),
        "xgrammar_mask_count": timing.get("xgrammar_mask_count", ""),
        "xgrammar_mask_avg_ms": f"{xgrammar_mask_total / callbacks:.3f}" if callbacks else "",
        "xgrammar_mask_active_avg_ms": (
            f"{xgrammar_mask_total / xgrammar_mask_count:.3f}"
            if xgrammar_mask_count
            else ""
        ),
        "token_select_total_ms": timing.get("token_select_total_ms", ""),
        "token_select_avg_ms": f"{token_select_total / callbacks:.3f}" if callbacks else "",
        "matcher_accept_total_ms": timing.get("matcher_accept_total_ms", ""),
        "matcher_accept_avg_ms": (
            f"{matcher_accept_total / callbacks:.3f}" if callbacks else ""
        ),
        "native_sampler_total_ms": timing.get("native_sampler_total_ms", ""),
        "native_sampler_avg_ms": f"{native_sampler_total / callbacks:.3f}" if callbacks else "",
        "xgrammar_activated": str(session.xgrammar_activated),
        "xgrammar_completed": str(session.xgrammar_completed),
        "terminal_state": session.terminal_state,
        "stopped_by_word": session.stopped_by_word,
    }


COLUMNS: list[tuple[str, str]] = [
    ("idx", "序号(idx)"),
    ("start", "开始时间(start)"),
    ("end", "结束时间(end)"),
    ("tools", "工具数(tools)"),
    ("tokens", "生成token数(tokens)"),
    ("callbacks", "回调次数(callbacks)"),
    ("callback_total_ms", "回调总耗时ms(callback_total_ms)"),
    ("callback_max_ms", "回调单次最大ms(callback_max_ms)"),
    ("sample_total_ms", "采样总耗时ms(sample_total_ms)"),
    ("sample_max_ms", "采样单次最大ms(sample_max_ms)"),
    ("xgrammar_mask_total_ms", "XGrammar约束总耗时ms(xgrammar_mask_total_ms)"),
    ("xgrammar_mask_count", "XGrammar激活回调数(xgrammar_mask_count)"),
    ("token_select_total_ms", "选token总耗时ms(token_select_total_ms)"),
    ("matcher_accept_total_ms", "Matcher推进总耗时ms(matcher_accept_total_ms)"),
    ("native_sampler_total_ms", "原生采样器总耗时ms(native_sampler_total_ms)"),
    ("xgrammar_activated", "进入XGrammar约束(xgrammar_activated)"),
    ("xgrammar_completed", "XGrammar约束完成(xgrammar_completed)"),
    ("terminal_state", "结束状态(terminal_state)"),
    ("stopped_by_word", "stop词停止(stopped_by_word)"),
    ("callback_avg_ms", "回调平均ms(callback_avg_ms)"),
    ("sample_avg_ms", "采样平均ms(sample_avg_ms)"),
    ("xgrammar_mask_avg_ms", "XGrammar约束平均ms(xgrammar_mask_avg_ms)"),
    (
        "xgrammar_mask_active_avg_ms",
        "XGrammar激活平均ms(xgrammar_mask_active_avg_ms)",
    ),
    ("token_select_avg_ms", "选token平均ms(token_select_avg_ms)"),
    ("matcher_accept_avg_ms", "Matcher推进平均ms(matcher_accept_avg_ms)"),
    ("native_sampler_avg_ms", "原生采样器平均ms(native_sampler_avg_ms)"),
]


def print_table(rows: list[dict[str, str]], output: TextIO) -> None:
    columns = COLUMNS
    widths = {
        title: max(len(title), *(len(row.get(key, "")) for row in rows))
        for key, title in columns
    }
    print("  ".join(title.ljust(widths[title]) for _, title in columns), file=output)
    print("  ".join("-" * widths[title] for _, title in columns), file=output)
    for row in rows:
        print("  ".join(row.get(key, "").ljust(widths[title]) for key, title in columns), file=output)


def write_csv(rows: list[dict[str, str]], output: TextIO) -> None:
    fieldnames = [title for _, title in COLUMNS]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({title: row.get(key, "") for key, title in COLUMNS})


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract RKNN3 sampling timing summaries from RKClawServer runtime logs."
    )
    parser.add_argument("log", nargs="?", default="/tmp/rkclawserver_front.log")
    parser.add_argument("--adb", action="store_true", help="Read the log from adb shell instead of local filesystem.")
    parser.add_argument(
        "--adb-serial",
        default=None,
        help=f"ADB device serial to use with --adb (default: {DEFAULT_ADB_SERIAL}).",
    )
    parser.add_argument("--csv", action="store_true", help="Print CSV instead of a human-readable table.")
    parser.add_argument("--output", type=Path, help="Write the table or CSV to this local file instead of stdout.")
    args = parser.parse_args()
    if args.adb_serial and not args.adb:
        parser.error("--adb-serial requires --adb")

    lines = read_lines(args.log, args.adb, args.adb_serial or DEFAULT_ADB_SERIAL)
    rows = [row_for(session) for session in parse_log(lines) if session.timing]
    if not rows:
        print("No RKNN3 sampling timing entries found.", file=sys.stderr)
        return 1

    output: TextIO = sys.stdout
    handle: TextIO | None = None
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        handle = args.output.open("w", encoding="utf-8", newline="")
        output = handle
    try:
        if args.csv:
            write_csv(rows, output)
        else:
            print_table(rows, output)
    finally:
        if handle is not None:
            handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
