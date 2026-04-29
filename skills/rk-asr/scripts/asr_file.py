#!/usr/bin/env python3
import argparse
import base64
import io
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
import wave

TRANSCRIPT_DIR = pathlib.Path.home() / ".openclaw" / "workspace" / "asr_res"
LONG_AUDIO_MS = 30000
MAX_TRANSCRIBE_MS = 20 * 60 * 1000

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_ASR_SERVICE = "rk-asr"
DEFAULT_REQUEST_TIMEOUT = 300.0

def request_transcribe_direct(base_url: str, wav_bytes, langex_mode=None):
    if langex_mode is None:
        langex_mode = "unknown"
    url = f"{base_url.rstrip('/')}/asr/transcribe"
    headers = {"Content-Type": "audio/wav"}
    if langex_mode is not None:
        headers["X-Langex-Mode"] = langex_mode
    request = urllib.request.Request(
        url,
        data=wav_bytes,
        headers=headers,
        method="POST",
    )

    with urllib.request.urlopen(request) as response:
        body = response.read().decode("utf-8")
        return response.status, body


def request_transcribe_model_hub(
    model_hub_url: str,
    wav_bytes: bytes,
    langex_mode: str | None,
) -> tuple[int, str]:
    if langex_mode is None:
        langex_mode = "unknown"
    from model_hub_py.client import ModelHubPyClient

    headers = {"Content-Type": "audio/wav", "X-Langex-Mode": langex_mode}
    client = ModelHubPyClient(model_hub_url, timeout=DEFAULT_REQUEST_TIMEOUT)
    result = client.run(
        DEFAULT_ASR_SERVICE,
        method="POST",
        path="/asr/transcribe",
        headers=headers,
        body=wav_bytes,
        timeout=DEFAULT_REQUEST_TIMEOUT,
    )
    code = int(result.get("upstream_status_code") or 0)
    upstream_body = result.get("upstream_body")
    if isinstance(upstream_body, dict):
        body = json.dumps(upstream_body, ensure_ascii=False)
    elif result.get("body_base64"):
        raw = base64.b64decode(result["body_base64"])
        body = raw.decode("utf-8", errors="replace")
    else:
        body = "{}"
    return code, body


def request_transcribe(
    wav_bytes: bytes,
    langex_mode: str | None,
    *,
    base_url: str,
    use_direct: bool,
) -> tuple[int, str]:
    if use_direct:
        return request_transcribe_direct(base_url, wav_bytes, langex_mode)
    return request_transcribe_model_hub(base_url, wav_bytes, langex_mode)


def parse_transcribe_response(body):
    parsed = json.loads(body)
    if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
        return parsed["data"], parsed.get("warning")
    if isinstance(parsed, dict):
        return parsed, None
    raise ValueError("unexpected response format")


def get_result_text(parsed):
    result = parsed.get("result")
    if not isinstance(result, str):
        raise ValueError("missing result field in response")
    return result.strip()


def get_audio_duration_ms(file_path):
    try:
        with wave.open(file_path, "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            if frame_rate <= 0:
                return None
            return int(wav_file.getnframes() * 1000 / frame_rate)
    except (wave.Error, OSError):
        return None


def split_wav_file(file_path, chunk_ms):
    with wave.open(file_path, "rb") as wav_file:
        frame_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        total_frames = wav_file.getnframes()
        if frame_rate <= 0 or channels <= 0 or sample_width <= 0 or total_frames <= 0:
            raise ValueError("invalid wav format")

        frames_per_chunk = int(frame_rate * chunk_ms / 1000)
        if frames_per_chunk <= 0:
            raise ValueError("invalid chunk size")

        chunk_bytes_list = []
        while True:
            frames = wav_file.readframes(frames_per_chunk)
            if not frames:
                break

            chunk_buffer = io.BytesIO()
            with wave.open(chunk_buffer, "wb") as chunk_file:
                chunk_file.setnchannels(channels)
                chunk_file.setsampwidth(sample_width)
                chunk_file.setframerate(frame_rate)
                chunk_file.writeframes(frames)
            chunk_bytes_list.append(chunk_buffer.getvalue())

    if not chunk_bytes_list:
        raise ValueError("audio file is empty")

    return chunk_bytes_list


def read_wav_bytes(file_path):
    with open(file_path, "rb") as wav_file:
        return wav_file.read()


def transcribe_response_to_text(
    file_path,
    langex_mode=None,
    *,
    base_url: str,
    use_direct: bool = False,
):
    wav = read_wav_bytes(file_path)
    status, body = request_transcribe(
        wav,
        langex_mode,
        base_url=base_url,
        use_direct=use_direct,
    )
    if status != 200:
        raise RuntimeError(f"ASR HTTP {status}: {body[:2000]}")
    parsed, warning = parse_transcribe_response(body)
    return status, get_result_text(parsed), warning


def transcribe_with_chunking(
    file_path,
    langex_mode=None,
    *,
    base_url: str,
    use_direct: bool = False,
):
    duration_ms = get_audio_duration_ms(file_path)
    if duration_ms is None or duration_ms <= MAX_TRANSCRIBE_MS:
        _, result_text, warning = transcribe_response_to_text(
            file_path,
            langex_mode,
            base_url=base_url,
            use_direct=use_direct,
        )
        return result_text, warning, duration_ms

    chunk_bytes_list = split_wav_file(file_path, MAX_TRANSCRIBE_MS)
    result_parts = []
    warnings = []
    for chunk_bytes in chunk_bytes_list:
        status, body = request_transcribe(
            chunk_bytes,
            langex_mode,
            base_url=base_url,
            use_direct=use_direct,
        )
        if status != 200:
            raise RuntimeError(f"ASR HTTP {status}: {body[:2000]}")
        parsed, warning = parse_transcribe_response(body)
        chunk_result = get_result_text(parsed)
        if chunk_result:
            result_parts.append(chunk_result)
        if warning:
            warnings.append(warning)
    return "\n".join(result_parts).strip(), "\n".join(warnings), duration_ms


def save_transcript(file_path, text):
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    transcript_path = TRANSCRIPT_DIR / (pathlib.Path(file_path).stem + ".txt")
    transcript_path.write_text(text, encoding="utf-8")
    return transcript_path


def main():
    parser = argparse.ArgumentParser(
        description="ASR transcribe via model_hub gateway (default) or direct HTTP server (--direct)."
    )
    parser.add_argument("file_path", help="wav file path on server machine")
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
        help="POST wav to {base_url}/asr/transcribe directly instead of via model_hub.",
    )
    parser.add_argument("--langex-mode", help="optional language name, such as English or Chinese")
    parser.add_argument(
        "--force-file",
        action="store_true",
        help="always save transcript to file and send the absolute path",
    )
    args = parser.parse_args()
    args.file_path = os.path.abspath(args.file_path)

    try:
        result_text, warning, duration_ms = transcribe_with_chunking(
            args.file_path,
            args.langex_mode,
            base_url=args.base_url,
            use_direct=args.direct,
        )
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {error_body}", file=sys.stderr)
        return 1
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"invalid response: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1

    send_as_file = args.force_file or (
        duration_ms is not None and duration_ms > LONG_AUDIO_MS
    )

    if send_as_file:
        transcript_path = save_transcript(args.file_path, result_text)
        print("**直接使用下面的绝对路径，直接将文件发送给用户，严禁读文件！**")
        print(f"FILE (absolute path): {transcript_path}")
    else:
        result_text = result_text or "ASR result is empty"
        print("**直接将识别的 ASR 文本返回给用户**")
        print(f"text res:\n{result_text}")

    if warning:
        print(f"warning: {warning}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
