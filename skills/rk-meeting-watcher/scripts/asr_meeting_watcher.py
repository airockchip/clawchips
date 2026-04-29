#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from datetime import datetime

import numpy as np


MAX_RESULT_BUFFER_SIZE = 1024
DEFAULT_REMINDER_SCRIPT = "./send_reminder.sh"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_ASR_SERVICE = "rk-asr"
DEFAULT_REQUEST_TIMEOUT = 300.0
# HTTP read timeout for initial lease POST while waiting for a free device slot (None = no limit).
DEFAULT_LEASE_ACQUIRE_HTTP_TIMEOUT = None
# 0 = wait until model_hub returns (no server-side cap; pair with DEFAULT_LEASE_ACQUIRE_HTTP_TIMEOUT).
DEFAULT_RESERVE_WAIT_TIMEOUT_SECONDS = 0
DEFAULT_LEASE_TTL_SECONDS = 30
DEFAULT_ASR_LOG_PATH = os.path.join(
    os.path.expanduser("~"),
    ".local",
    "state",
    "rk-meeting-watcher",
    "asr.log",
)


class KeywordWatcherState:
    def __init__(self, keyword, cooldown_seconds, reminder_script, asr_log_path):
        self.keyword = keyword
        self.cooldown_seconds = cooldown_seconds
        self.reminder_script = reminder_script
        self.asr_log_path = asr_log_path
        self.result_buffer = ""
        self.last_trigger_time = 0.0
        self.cooldown_logged = False
        self.cooldown_wait_logged = False
        self.next_stream_state = "first"
        self.result_lock = threading.Lock()
        self.log_lock = threading.Lock()

    def ensure_asr_log_dir(self):
        log_dir = os.path.dirname(self.asr_log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

    def write_asr_log(self, fmt, *args):
        with self.log_lock:
            self.ensure_asr_log_dir()
            with open(self.asr_log_path, "a", encoding="utf-8") as log_fp:
                timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S] ")
                log_fp.write(timestamp)
                log_fp.write(fmt % args if args else fmt)
                log_fp.write("\n")

    def reset_asr_log(self):
        with self.log_lock:
            self.ensure_asr_log_dir()
            with open(self.asr_log_path, "w", encoding="utf-8"):
                pass

    def is_in_cooldown_locked(self, now):
        return self.last_trigger_time != 0 and (now - self.last_trigger_time) < self.cooldown_seconds

    def buffer_contains_keyword_locked(self):
        return bool(self.keyword) and self.keyword in self.result_buffer

    def buffer_contains_keyword_with_suffix_locked(self, suffix):
        if self.buffer_contains_keyword_locked():
            return True

        if not self.keyword or not suffix:
            return False

        return self.keyword in (self.result_buffer + suffix)

    def should_drop_audio_input(self):
        with self.result_lock:
            now = time.time()
            in_cooldown = self.is_in_cooldown_locked(now)
            if in_cooldown and not self.cooldown_wait_logged:
                self.cooldown_wait_logged = True
                self.write_asr_log("[COOLDOWN] active, dropping microphone input")

            if not in_cooldown and self.cooldown_logged:
                self.cooldown_logged = False
                self.cooldown_wait_logged = False
                self.last_trigger_time = 0.0
                self.result_buffer = ""
                self.next_stream_state = "first"
                print("Cooldown ended, resume ASR input")
                self.write_asr_log("[COOLDOWN] ended, resume ASR input")

            return in_cooldown

    def should_drop_asr_result(self):
        with self.result_lock:
            in_cooldown = self.is_in_cooldown_locked(time.time())
            if in_cooldown:
                self.result_buffer = ""
            return in_cooldown

    def append_result_buffer(self, text, detect_suffix):
        if not text:
            return

        with self.result_lock:
            self.result_buffer += text
            if len(self.result_buffer) > MAX_RESULT_BUFFER_SIZE:
                self.result_buffer = self.result_buffer[-MAX_RESULT_BUFFER_SIZE:]

            now = time.time()
            in_cooldown = self.is_in_cooldown_locked(now)
            if self.buffer_contains_keyword_with_suffix_locked(detect_suffix) and not in_cooldown:
                self.last_trigger_time = now
                self.cooldown_logged = True
                self.cooldown_wait_logged = False
                self.result_buffer = ""
                self.next_stream_state = "first"
                print(f"Enter cooldown for {self.cooldown_seconds} seconds, drop microphone input")
                print(f"Keyword detected: {self.keyword}, running script: {self.reminder_script}")
                self.write_asr_log("[TRIGGER] keyword detected: %s", self.keyword)
                if detect_suffix:
                    self.write_asr_log("[TRIGGER] newResult: %s", detect_suffix)
                self.write_asr_log(
                    "[COOLDOWN] enter for %d seconds, drop microphone input",
                    self.cooldown_seconds,
                )
                try:
                    subprocess.Popen(
                        ["bash", self.reminder_script],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except OSError as exc:
                    print(f"Error: Failed to run reminder script, error={exc}")

    def consume_stream_state(self):
        with self.result_lock:
            stream_state = self.next_stream_state
            self.next_stream_state = "continue"
            return stream_state


class ModelHubServiceLease:
    def __init__(self, model_hub_url, service_name, ttl_seconds=DEFAULT_LEASE_TTL_SECONDS):
        self.model_hub_url = model_hub_url
        self.service_name = service_name
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.stop_event = threading.Event()
        self.thread = None
        self.lease_id = None

    def start(self):
        from model_hub_py.client import ModelHubPyClient

        client = ModelHubPyClient(
            self.model_hub_url,
            timeout=DEFAULT_LEASE_ACQUIRE_HTTP_TIMEOUT,
        )
        lease = client.touch_service_lease(
            self.service_name,
            ttl_seconds=self.ttl_seconds,
            reserve_device_slot=True,
            reserve_wait_timeout_seconds=DEFAULT_RESERVE_WAIT_TIMEOUT_SECONDS,
        )
        self.lease_id = lease["lease_id"]
        self.thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.thread.start()
        return self.lease_id

    def _heartbeat_loop(self):
        from model_hub_py.client import ModelHubPyClient

        interval = max(1.0, min(self.ttl_seconds / 2.0, self.ttl_seconds - 1.0))
        client = ModelHubPyClient(self.model_hub_url, timeout=DEFAULT_REQUEST_TIMEOUT)
        while not self.stop_event.wait(interval):
            try:
                client.touch_service_lease(
                    self.service_name,
                    lease_id=self.lease_id,
                    ttl_seconds=self.ttl_seconds,
                    reserve_device_slot=True,
                )
            except Exception as exc:
                print(f"warning: failed to renew model_hub service lease: {exc}", file=sys.stderr)

    def close(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        if not self.lease_id:
            return
        try:
            from model_hub_py.client import ModelHubPyClient

            client = ModelHubPyClient(self.model_hub_url, timeout=DEFAULT_REQUEST_TIMEOUT)
            client.release_service_lease(self.service_name, self.lease_id)
        except Exception as exc:
            print(f"warning: failed to release model_hub service lease: {exc}", file=sys.stderr)


def health_url(base_url):
    parsed = urllib.parse.urlparse(base_url)
    return urllib.parse.urlunparse(parsed._replace(path="/health", query="", fragment=""))


def check_health(base_url, timeout=2.0):
    request = urllib.request.Request(health_url(base_url), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def split_base_url(base_url):
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported scheme in base_url: {base_url}")
    if not parsed.hostname or parsed.port is None:
        raise ValueError(f"base_url must include explicit host and port: {base_url}")
    return parsed.hostname, parsed.port


def ensure_model_hub_service_ready(model_hub_url, service_name):
    from model_hub_py.client import ModelHubPyClient

    client = ModelHubPyClient(model_hub_url, timeout=DEFAULT_REQUEST_TIMEOUT)
    client.start_service(service_name)
    client.healthcheck_service(service_name)
    service_info = client.get_service(service_name)
    base_url = service_info.get("base_url")
    if not isinstance(base_url, str) or not base_url:
        raise RuntimeError(f"model_hub returned invalid base_url for service {service_name}")
    if not check_health(base_url, timeout=2.0):
        raise RuntimeError(f"ASR service is not healthy after model_hub startup: {base_url}")
    return base_url


def post_stream_chunk(server, port, chunk, stream_state, langex_mode=None):
    url = f"http://{server}:{port}/asr/stream"
    headers = {
        "Content-Type": "application/octet-stream",
        "X-Stream-State": stream_state,
    }
    if langex_mode:
        headers["X-Langex-Mode"] = langex_mode

    request = urllib.request.Request(url, data=chunk, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, response.read().decode("utf-8")


def amplify_audio(data, gain_db):
    gain_linear = 10 ** (gain_db / 20.0)
    audio_array = np.frombuffer(data, dtype=np.int16)
    amplified = audio_array * gain_linear
    amplified = np.clip(amplified, -32768, 32767)
    amplified = amplified.astype(np.int16)
    return amplified.tobytes()


def convert_to_mono_16k(data, sample_rate, channels):
    if sample_rate != 16000:
        raise ValueError(f"expected 16k microphone stream, got sample rate {sample_rate}")

    audio_array = np.frombuffer(data, dtype=np.int16)
    if channels <= 0:
        raise ValueError(f"invalid channel count: {channels}")

    if channels == 1:
        return audio_array.astype(np.int16).tobytes()

    if audio_array.size % channels != 0:
        raise ValueError("audio chunk is not aligned to channel count")

    audio_array = audio_array.reshape(-1, channels)
    mono = np.mean(audio_array, axis=1)
    mono = np.clip(mono, -32768, 32767).astype(np.int16)
    return mono.tobytes()


def parse_json_body(body):
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def recv_with_stop(sock, stop_event, size=4096):
    while not stop_event.is_set():
        try:
            return sock.recv(size)
        except socket.timeout:
            continue
    return b""


def handle_asr_result(state, parsed):
    if not isinstance(parsed, dict):
        print(parsed)
        return

    asr_state = parsed.get("state")
    asr_output = parsed.get("result", "")
    asr_new_output = parsed.get("newResult", "")
    print(f"asr_state: {asr_state}")
    print(f"asr_output: {asr_output}")
    print(f"asr_new_output: {asr_new_output}")
    state.write_asr_log("[ASR] result: %s", asr_output)

    if state.should_drop_asr_result():
        return

    state.append_result_buffer(asr_output, asr_new_output)


def read_chunked_stream(sock, stop_event, watcher_state):
    pending = b""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = recv_with_stop(sock, stop_event)
        if not chunk:
            return
        data += chunk

    header_bytes, pending = data.split(b"\r\n\r\n", 1)
    header_text = header_bytes.decode("utf-8", errors="replace")
    if "200 OK" not in header_text:
        raise RuntimeError(f"stream open failed: {header_text}")

    while not stop_event.is_set():
        while b"\r\n" not in pending:
            data = recv_with_stop(sock, stop_event)
            if not data:
                return
            pending += data

        header_line, pending = pending.split(b"\r\n", 1)
        chunk_size = int(header_line.decode("ascii"), 16)
        if chunk_size == 0:
            return

        while len(pending) < chunk_size + 2:
            data = recv_with_stop(sock, stop_event)
            if not data:
                return
            pending += data

        payload = pending[:chunk_size]
        pending = pending[chunk_size + 2:]
        text = payload.decode("utf-8", errors="replace").strip()
        if not text:
            continue

        parsed = parse_json_body(text)
        handle_asr_result(watcher_state, parsed)


def stream_listener_thread(server, port, stop_event, watcher_state):
    sock = socket.create_connection((server, port), timeout=30)
    sock.settimeout(0.5)
    request = (
        f"GET /asr/stream HTTP/1.1\r\n"
        f"Host: {server}:{port}\r\n"
        f"Connection: keep-alive\r\n"
        f"Accept: application/json\r\n"
        f"\r\n"
    )
    sock.sendall(request.encode("utf-8"))
    try:
        read_chunked_stream(sock, stop_event, watcher_state)
    finally:
        stop_event.set()
        sock.close()


def stream_mic(server,
               port,
               device,
               sample_rate,
               channels,
               chunk_ms,
               sleep_ms,
               final_wait_ms,
               gain_db,
               keyword,
               cooldown_seconds,
               reminder_script,
               asr_log_path,
               service_lease=None,
               debug_save=False,
               output_file=None,
               langex_mode=None):
    stop_event = threading.Event()
    watcher_state = KeywordWatcherState(keyword, cooldown_seconds, reminder_script, asr_log_path)
    listener = threading.Thread(
        target=stream_listener_thread,
        args=(server, port, stop_event, watcher_state),
        daemon=True,
    )
    listener.start()

    time.sleep(0.2)

    if debug_save and output_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"mic_amplified_{gain_db}db_{timestamp}.wav"

    cmd = [
        "arecord",
        "-D", device,
        "-f", "S16_LE",
        "-r", str(sample_rate),
        "-c", str(channels),
        "-t", "raw",
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE)

    chunk_frames = max(1, sample_rate * chunk_ms // 1000)
    chunk_size = chunk_frames * channels * 2
    chunk_index = 0
    interrupted = False

    wav_file = None
    if debug_save:
        wav_file = wave.open(output_file, "wb")
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)

    print(f"start mic stream from {device}")
    print(f"capture sample_rate={sample_rate} channels={channels} gain_db={gain_db} chunk_ms={chunk_ms}")
    print("send format: 16kHz mono s16le")
    print(f"Keyword: {watcher_state.keyword}")
    print(f"Cooldown seconds: {watcher_state.cooldown_seconds}")
    print(f"Reminder script: {watcher_state.reminder_script}")
    if debug_save:
        print(f"debug save sent audio to {output_file}")
    print("press Ctrl+C to stop")

    watcher_state.reset_asr_log()
    watcher_state.write_asr_log(
        "[STARTUP] stream started, keyword=%s, cooldown_seconds=%d, log_path=%s",
        watcher_state.keyword,
        watcher_state.cooldown_seconds,
        watcher_state.asr_log_path,
    )

    try:
        while True:
            data = process.stdout.read(chunk_size)
            if not data:
                break

            if watcher_state.should_drop_audio_input():
                continue

            chunk_index += 1
            mono_16k = convert_to_mono_16k(data, sample_rate, channels)
            amplified = amplify_audio(mono_16k, gain_db)
            if wav_file is not None:
                wav_file.writeframes(amplified)
            stream_state = watcher_state.consume_stream_state()
            status, body = post_stream_chunk(server, port, amplified, stream_state, langex_mode)
            print(f"push chunk {chunk_index} state={stream_state} http={status} {body}")

            if sleep_ms > 0:
                time.sleep(sleep_ms / 1000.0)
    except KeyboardInterrupt:
        interrupted = True
        print("\nstop microphone streaming")
        watcher_state.write_asr_log("[STOP] stop requested")
    finally:
        if service_lease is not None:
            service_lease.close()
        if wav_file is not None:
            wav_file.close()
        if process.stdout is not None:
            process.stdout.close()
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()

    if interrupted:
        stop_event.set()
        return

    if final_wait_ms > 0:
        print(f"wait {final_wait_ms} ms before final last chunk")
        time.sleep(final_wait_ms / 1000.0)

    final_silence = b"\x00\x00" * (16000 * chunk_ms // 1000)
    status, body = post_stream_chunk(server, port, final_silence, "last", langex_mode)
    print(f"push final last http={status} {body}")

    time.sleep(2.0)
    stop_event.set()
    listener.join(timeout=2.0)


def main():
    parser = argparse.ArgumentParser(
        description="ASR microphone streaming via model_hub startup (default) or direct ASR server (--direct)."
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("MODEL_HUB_URL", DEFAULT_BASE_URL),
        help=(
            "HTTP base URL: model_hub root (default) or with --direct the ASR server root, "
            f"e.g. http://host:18002. Default: {DEFAULT_BASE_URL} or MODEL_HUB_URL"
        ),
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="connect to the ASR server at --base-url directly instead of asking model_hub to start rk-asr.",
    )
    parser.add_argument(
        "--service-name",
        default=DEFAULT_ASR_SERVICE,
        help="model_hub service name used when not running with --direct",
    )
    parser.add_argument("--device", default="hw:1,0", help="ALSA device")
    parser.add_argument("--rate", type=int, default=16000, help="sample rate")
    parser.add_argument("--channels", type=int, default=2, help="microphone capture channel count")
    parser.add_argument("--chunk-ms", type=int, default=500, help="chunk duration in milliseconds")
    parser.add_argument("--sleep-ms", type=int, default=500, help="delay between chunks in milliseconds")
    parser.add_argument("--final-wait-ms", type=int, default=2000, help="wait time before sending last chunk")
    parser.add_argument("--gain", type=float, default=40, help="microphone gain in dB")
    parser.add_argument("keyword", help="keyword to detect")
    parser.add_argument("--cooldown-seconds", type=int, default=60, help="cooldown after keyword trigger")
    parser.add_argument("--reminder-script", default=DEFAULT_REMINDER_SCRIPT, help="script to run on keyword trigger")
    parser.add_argument("--asr-log-path", default=DEFAULT_ASR_LOG_PATH, help="ASR log file path")
    parser.add_argument(
        "--lease-ttl-seconds",
        type=int,
        default=DEFAULT_LEASE_TTL_SECONDS,
        help="model_hub external activity lease TTL while directly streaming ASR",
    )
    parser.add_argument("--debug-save", action="store_true", help="save sent audio to wav for debugging")
    parser.add_argument("--output", help="save amplified microphone audio to wav file")
    parser.add_argument("--langex-mode", help="optional language name, such as English or Chinese")
    args = parser.parse_args()

    if not args.keyword:
        print("Error: keyword must not be empty", file=sys.stderr)
        return 1
    if args.cooldown_seconds < 0:
        print("Error: cooldown_seconds must be a non-negative integer", file=sys.stderr)
        return 1

    reminder_script = args.reminder_script
    asr_log_path = args.asr_log_path
    if not os.path.isabs(reminder_script):
        reminder_script = os.path.join(os.getcwd(), reminder_script)
    if args.asr_log_path != DEFAULT_ASR_LOG_PATH and not os.path.isabs(asr_log_path):
        asr_log_path = os.path.join(os.getcwd(), asr_log_path)

    try:
        service_lease = None
        if args.direct:
            asr_base_url = args.base_url
        else:
            asr_base_url = ensure_model_hub_service_ready(args.base_url, args.service_name)
            service_lease = ModelHubServiceLease(
                args.base_url,
                args.service_name,
                ttl_seconds=args.lease_ttl_seconds,
            )
            lease_id = service_lease.start()
            print(f"registered model_hub service lease: service={args.service_name} lease_id={lease_id}")
        host, port = split_base_url(asr_base_url)
    except Exception as exc:
        print(f"failed to resolve ASR service endpoint: {exc}", file=sys.stderr)
        return 1

    try:
        stream_mic(
            host,
            port,
            args.device,
            args.rate,
            args.channels,
            args.chunk_ms,
            args.sleep_ms,
            args.final_wait_ms,
            args.gain,
            args.keyword,
            args.cooldown_seconds,
            reminder_script,
            asr_log_path,
            service_lease,
            args.debug_save,
            args.output,
            args.langex_mode,
        )
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {error_body}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"stream failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
