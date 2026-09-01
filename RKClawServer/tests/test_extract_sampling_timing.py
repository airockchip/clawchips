from __future__ import annotations

from tools.extract_sampling_timing import (
    COLUMNS,
    DEFAULT_ADB_SERIAL,
    parse_log,
    row_for,
    write_csv,
)


def test_default_adb_serial_targets_the_test_board() -> None:
    assert DEFAULT_ADB_SERIAL == "bfa1f67e4c22b457"


def test_sampling_timing_log_is_grouped_with_xgrammar_state() -> None:
    lines = [
        "2026-07-10 14:00:00,000 I XGrammar create_state: enabled=True tools=2 "
        "tool_choice=auto reasoning=True stop=[]\n",
        "2026-07-10 14:00:01,000 I RKNN3 session_run start: prompt_tokens=100 "
        "max_new_tokens=50 enable_thinking=True\n",
        "2026-07-10 14:00:02,000 I XGrammar toolcall structure activated: generated=10\n",
        "2026-07-10 14:00:03,000 I XGrammar toolcall structure completed\n",
        "2026-07-10 14:00:04,000 I RKNN3 session_run end: ret=0 terminal_state=2 "
        "tokens=20 decoded_length=80 stopped_by_word=False\n",
        "2026-07-10 14:00:04,001 I RKNN3 sampling timing: callbacks=20 "
        "callback_total_ms=100.000 callback_max_ms=8.000 sample_total_ms=80.000 "
        "xgrammar_mask_total_ms=10.000 xgrammar_mask_count=4 token_select_total_ms=60.000 "
        "native_sampler_total_ms=3.000\n",
    ]

    sessions = parse_log(lines)
    assert len(sessions) == 1

    row = row_for(sessions[0])
    assert row["tools"] == "2"
    assert row["xgrammar_activated"] == "True"
    assert row["xgrammar_completed"] == "True"
    assert row["callback_avg_ms"] == "5.000"
    assert row["sample_avg_ms"] == "4.000"
    assert row["xgrammar_mask_avg_ms"] == "0.500"
    assert row["xgrammar_mask_count"] == "4"
    assert row["xgrammar_mask_active_avg_ms"] == "2.500"
    assert row["native_sampler_avg_ms"] == "0.150"


def test_old_log_without_xgrammar_mask_count_remains_compatible() -> None:
    lines = [
        "2026-07-10 14:00:01,000 I RKNN3 session_run start: prompt_tokens=1 "
        "max_new_tokens=1 enable_thinking=True\n",
        "2026-07-10 14:00:02,000 I RKNN3 sampling timing: callbacks=1 "
        "callback_total_ms=1.000 xgrammar_mask_total_ms=0.500\n",
    ]

    row = row_for(parse_log(lines)[0])

    assert row["xgrammar_mask_count"] == ""
    assert row["xgrammar_mask_active_avg_ms"] == ""


def test_new_log_without_session_start_is_parsed_from_timing_line() -> None:
    lines = [
        "2026-07-20 17:31:36,902 I [gateway.xgrammar] XGrammar create_state: "
        "enabled=True tools=1 tool_choice=required reasoning=False stop=[]\n",
        "2026-07-20 17:31:37,702 I [gateway.xgrammar] "
        "XGrammar toolcall structure activated: status=pending sample_count=1\n",
        "2026-07-20 17:31:38,309 I [gateway.xgrammar] "
        "XGrammar toolcall structure completed\n",
        "2026-07-20 17:31:38,354 I [gateway.rknn3] RKNN3 sampling timing: "
        "callbacks=26 callback_total_ms=17.139 callback_max_ms=1.467 "
        "sample_total_ms=16.796 xgrammar_mask_total_ms=1.116 "
        "xgrammar_mask_count=24 token_select_total_ms=12.098 "
        "matcher_accept_total_ms=0.479 native_sampler_total_ms=12.098 "
        "sample_max_ms=1.452\n",
    ]

    sessions = parse_log(lines)

    assert len(sessions) == 1
    assert sessions[0].start_ts == "2026-07-20 17:31:36,902"
    row = row_for(sessions[0])
    assert row["tools"] == "1"
    assert row["xgrammar_activated"] == "True"
    assert row["xgrammar_completed"] == "True"
    assert row["callback_avg_ms"] == "0.659"
    assert row["sample_avg_ms"] == "0.646"
    assert row["xgrammar_mask_active_avg_ms"] == "0.047"
    assert row["token_select_avg_ms"] == "0.465"
    assert row["matcher_accept_avg_ms"] == "0.018"


def test_timing_only_log_still_produces_a_row() -> None:
    lines = [
        "2026-07-20 17:31:38,354 I RKNN3 sampling timing: callbacks=2 "
        "callback_total_ms=2.000 sample_total_ms=1.800 "
        "token_select_total_ms=1.400\n",
    ]

    sessions = parse_log(lines)

    assert len(sessions) == 1
    assert row_for(sessions[0])["callback_avg_ms"] == "1.000"


def test_write_csv_uses_the_configured_column_order(tmp_path) -> None:
    path = tmp_path / "timing.csv"
    with path.open("w", encoding="utf-8", newline="") as output:
        write_csv([{"callbacks": "1"}], output)

    assert path.read_text(encoding="utf-8").splitlines()[0].split(",") == [title for _, title in COLUMNS]
