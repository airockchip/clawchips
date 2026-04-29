#!/usr/bin/env python3

import argparse
import base64
import json
import os
import subprocess
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TTS_SERVICE = "rk-tts"
DEFAULT_REQUEST_TIMEOUT = 300.0
DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def _direct_tts_post_url(base_url: str) -> str:
    b = base_url.rstrip("/")
    if b.endswith("/tts"):
        return b
    return f"{b}/tts"


def _health_url(base_url):
    parsed = urllib.parse.urlparse(base_url)
    return urllib.parse.urlunparse(parsed._replace(path="/health", query="", fragment=""))


def check_health(base_url, timeout=2.0):
    req = urllib.request.Request(_health_url(base_url), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (OSError, urllib.error.URLError):
        return False


def wait_until_ready(base_url, wait_timeout=15.0):
    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        if check_health(base_url, timeout=1.0):
            return True
        time.sleep(0.2)
    return False


def write_float32_wav(output_path, audio_bytes, sample_rate):
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    num_channels = 1
    bits_per_sample = 32
    block_align = num_channels * bits_per_sample // 8
    byte_rate = sample_rate * block_align
    data_size = len(audio_bytes)
    chunk_size = 36 + data_size

    with open(output_path, "wb") as file_obj:
        file_obj.write(b"RIFF")
        file_obj.write(struct.pack("<I", chunk_size))
        file_obj.write(b"WAVE")
        file_obj.write(b"fmt ")
        file_obj.write(struct.pack("<I", 16))
        file_obj.write(struct.pack("<H", 3))  # IEEE float
        file_obj.write(struct.pack("<H", num_channels))
        file_obj.write(struct.pack("<I", sample_rate))
        file_obj.write(struct.pack("<I", byte_rate))
        file_obj.write(struct.pack("<H", block_align))
        file_obj.write(struct.pack("<H", bits_per_sample))
        file_obj.write(b"data")
        file_obj.write(struct.pack("<I", data_size))
        file_obj.write(audio_bytes)


def open_aplay_wav(wav_path, device=""):
    cmd = ["aplay", "-q"]
    if device:
        cmd.extend(["-D", device])
    cmd.append(wav_path)
    return subprocess.run(cmd, check=False)


def iter_tts_stream(base_url, text, timeout=None):
    """直连 tts_server：base_url 为服务根（如 http://host:8088）或已带 /tts 的完整 URL。"""
    payload = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        _direct_tts_post_url(base_url),
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw_line in resp:
            line = raw_line.strip()
            if not line:
                continue
            yield json.loads(line.decode("utf-8"))


def iter_tts_stream_model_hub(base_url: str, text: str, timeout=None):
    """通过 model_hub_py 转发到 rk-tts；base_url 为 Model Hub 根地址，解析上游 NDJSON 流（与 iter_tts_stream 相同 yield 结构）。"""
    from model_hub_py.client import ModelHubPyClient

    effective = timeout if timeout is not None else DEFAULT_REQUEST_TIMEOUT
    headers = {"Content-Type": "application/json; charset=utf-8"}
    client = ModelHubPyClient(base_url, timeout=effective)
    result = client.run(
        DEFAULT_TTS_SERVICE,
        method="POST",
        path="/tts",
        headers=headers,
        json_body={"text": text},
        timeout=effective,
    )
    code = int(result.get("upstream_status_code") or 0)
    if not (200 <= code < 300):
        raise RuntimeError(f"TTS upstream error: HTTP {code}")

    upstream_body = result.get("upstream_body")
    text_body: str | None = None
    if isinstance(upstream_body, str):
        text_body = upstream_body
    elif isinstance(upstream_body, dict):
        yield upstream_body
        return
    elif result.get("body_base64"):
        raw = base64.b64decode(result["body_base64"])
        text_body = raw.decode("utf-8", errors="replace")

    if not text_body:
        return

    for line in text_body.splitlines():
        line = line.strip()
        if not line:
            continue
        yield json.loads(line)


def _request_tts_from_stream(stream_iter, output_path):
    audio_bytes = bytearray()
    sample_rate = 24000
    got_last_frame = False

    for item in stream_iter:
        sample_rate = int(item.get("samplerate", sample_rate))
        audio_base64 = item.get("audio_base64", "")
        if audio_base64:
            audio_bytes.extend(base64.b64decode(audio_base64))

        if item.get("is_last_frame", False):
            got_last_frame = True
            break

    if not got_last_frame:
        raise RuntimeError("TTS stream ended before is_last_frame=true")
    if not audio_bytes:
        raise RuntimeError("TTS stream returned empty audio")

    write_float32_wav(output_path, audio_bytes, sample_rate)
    return output_path


def request_tts(base_url, text, output_path, timeout=None):
    return _request_tts_from_stream(
        iter_tts_stream(base_url, text, timeout=timeout),
        output_path,
    )


def request_tts_model_hub(base_url, text, output_path, timeout=None):
    return _request_tts_from_stream(
        iter_tts_stream_model_hub(base_url, text, timeout=timeout),
        output_path,
    )


def play_tts(base_url, text, device="", timeout=None, *, use_direct=False):
    tmp_wav = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="rk_tts_play_",
            suffix=".wav",
            delete=False,
        ) as tmp_file:
            tmp_wav = tmp_file.name

        if use_direct:
            request_tts(base_url, text, tmp_wav, timeout=timeout)
        else:
            request_tts_model_hub(base_url, text, tmp_wav, timeout=timeout)
        result = open_aplay_wav(tmp_wav, device)
        if result.returncode != 0:
            raise RuntimeError(f"aplay failed with code {result.returncode}")
    finally:
        if tmp_wav and os.path.exists(tmp_wav):
            os.unlink(tmp_wav)


def main():
    parser = argparse.ArgumentParser(
        description="RK TTS via model_hub gateway (default) or direct tts_server (--direct)."
    )
    parser.add_argument(
        "--server-url",
        default=os.environ.get("MODEL_HUB_URL", DEFAULT_BASE_URL),
        help=(
            "HTTP URL: model_hub root (default) or with --direct the tts_server root "
            f"(e.g. http://host:8088), POST body goes to .../tts. Default: {DEFAULT_BASE_URL} or MODEL_HUB_URL"
        ),
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="POST JSON to {server-url}/tts directly instead of via model_hub.",
    )
    parser.add_argument("--text", default="")
    parser.add_argument("--output", default="/userdata/output.wav")
    parser.add_argument("--play", action="store_true")
    parser.add_argument("--device", default="")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--wait-timeout", type=float, default=15.0)
    args = parser.parse_args()

    if args.health:
        if not args.direct:
            print(
                "--health is intended for direct tts_server; use with --direct",
                file=sys.stderr,
            )
            return 2
        return 0 if check_health(args.server_url) else 1

    if args.wait:
        if not args.direct:
            print(
                "--wait is intended for direct tts_server; use with --direct",
                file=sys.stderr,
            )
            return 2
        if wait_until_ready(args.server_url, args.wait_timeout):
            return 0
        print("tts_server is not ready", file=sys.stderr)
        return 1

    if not args.text:
        print("--text is required unless --health/--wait is used", file=sys.stderr)
        return 1

    if args.play:
        play_tts(args.server_url, args.text, args.device, use_direct=args.direct)
    else:
        if args.direct:
            output_path = request_tts(args.server_url, args.text, args.output)
        else:
            output_path = request_tts_model_hub(args.server_url, args.text, args.output)
        print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
