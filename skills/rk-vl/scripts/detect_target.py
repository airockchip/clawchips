#!/usr/bin/env python3
import argparse
import base64
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_MODEL = "qwen3-vl-2b"
DEFAULT_OPENCLAW_BIN = "openclaw"
DEFAULT_POLL_INTERVAL_SEC = 2.0
DEFAULT_HIT_CONFIRMATIONS = 1
DEFAULT_MISS_CLEAR_COUNT = 2
DEFAULT_REMIND_COOLDOWN_SEC = 120
DEFAULT_REMIND_MAX_SILENCE_SEC = 600
DEFAULT_CAPTURE_WIDTH = 1280
DEFAULT_CAPTURE_HEIGHT = 720
DEFAULT_MIN_SCORE = 0.95
DEFAULT_STATE_DIR = Path.home() / ".openclaw" / "rk-vl-monitor"
DEFAULT_KNOWN_USERS_JSON = Path.home() / ".openclaw" / "qqbot" / "data" / "known-users.json"
DEBUG_LOG_ENABLED = True
DEBUG_LOG_PATH = Path("~/rkvl.log").expanduser()


def debug_log(message: str) -> None:
    if not DEBUG_LOG_ENABLED:
        return
    try:
        DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


@dataclass
class MonitorConfig:
    query: str
    min_score: float = DEFAULT_MIN_SCORE
    poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC
    hit_confirmations: int = DEFAULT_HIT_CONFIRMATIONS
    miss_clear_count: int = DEFAULT_MISS_CLEAR_COUNT
    remind_cooldown_sec: int = DEFAULT_REMIND_COOLDOWN_SEC
    remind_max_silence_sec: int = DEFAULT_REMIND_MAX_SILENCE_SEC
    openclaw_bin: str = DEFAULT_OPENCLAW_BIN
    known_users_json: Path = DEFAULT_KNOWN_USERS_JSON
    capture_dir: Path = DEFAULT_STATE_DIR / "captures"
    state_file: Path = DEFAULT_STATE_DIR / "state.json"
    camera_device: str | None = None
    camera_index: int | None = None
    capture_width: int = DEFAULT_CAPTURE_WIDTH
    capture_height: int = DEFAULT_CAPTURE_HEIGHT
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: float = 60.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use a Qwen VL OpenAI-compatible endpoint to detect described targets in an image."
    )
    parser.add_argument("--image", "-i", help="Input image path.")
    parser.add_argument("--query", "-q", required=True, help="Target description to search for.")
    parser.add_argument("--watch", action="store_true", help="Run continuous camera monitoring loop.")
    parser.add_argument(
        "--min-score",
        type=float,
        default=float(os.environ.get("RK_VL_MIN_SCORE", str(DEFAULT_MIN_SCORE))),
        help=f"Minimum score threshold for keeping matches. Default: {DEFAULT_MIN_SCORE}",
    )
    parser.add_argument("--camera-device", help="Camera device path, e.g. /dev/video0.")
    parser.add_argument("--camera-index", type=int, help="Camera index for OpenCV, e.g. 0.")
    parser.add_argument(
        "--capture-width",
        type=int,
        default=int(os.environ.get("RK_VL_CAPTURE_WIDTH", str(DEFAULT_CAPTURE_WIDTH))),
        help=f"Capture width for camera mode. Default: {DEFAULT_CAPTURE_WIDTH}",
    )
    parser.add_argument(
        "--capture-height",
        type=int,
        default=int(os.environ.get("RK_VL_CAPTURE_HEIGHT", str(DEFAULT_CAPTURE_HEIGHT))),
        help=f"Capture height for camera mode. Default: {DEFAULT_CAPTURE_HEIGHT}",
    )
    parser.add_argument("--capture-dir", help="Directory to store captured images in camera/watch mode.")
    parser.add_argument("--state-file", help="State file for watch mode.")
    parser.add_argument(
        "--openclaw-bin",
        default=os.environ.get("RK_VL_OPENCLAW_BIN", DEFAULT_OPENCLAW_BIN),
        help=f"OpenClaw executable. Default: {DEFAULT_OPENCLAW_BIN}",
    )
    parser.add_argument(
        "--known-users-json",
        default=os.environ.get("RK_VL_KNOWN_USERS_JSON", str(DEFAULT_KNOWN_USERS_JSON)),
        help="Path to qqbot known-users.json.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.environ.get("RK_VL_POLL_INTERVAL_SEC", str(DEFAULT_POLL_INTERVAL_SEC))),
        help=f"Watch polling interval in seconds. Default: {DEFAULT_POLL_INTERVAL_SEC}",
    )
    parser.add_argument(
        "--hit-confirmations",
        type=int,
        default=int(os.environ.get("RK_VL_HIT_CONFIRMATIONS", str(DEFAULT_HIT_CONFIRMATIONS))),
        help=f"Consecutive hits required before remind. Default: {DEFAULT_HIT_CONFIRMATIONS}",
    )
    parser.add_argument(
        "--miss-clear-count",
        type=int,
        default=int(os.environ.get("RK_VL_MISS_CLEAR_COUNT", str(DEFAULT_MISS_CLEAR_COUNT))),
        help=f"Consecutive misses to clear present state. Default: {DEFAULT_MISS_CLEAR_COUNT}",
    )
    parser.add_argument(
        "--remind-cooldown-sec",
        type=int,
        default=int(os.environ.get("RK_VL_REMIND_COOLDOWN_SEC", str(DEFAULT_REMIND_COOLDOWN_SEC))),
        help=f"Cooldown seconds for repeated reminders. Default: {DEFAULT_REMIND_COOLDOWN_SEC}",
    )
    parser.add_argument(
        "--remind-max-silence-sec",
        type=int,
        default=int(os.environ.get("RK_VL_REMIND_MAX_SILENCE_SEC", str(DEFAULT_REMIND_MAX_SILENCE_SEC))),
        help=f"Max silence seconds before re-reminding persistent targets. Default: {DEFAULT_REMIND_MAX_SILENCE_SEC}",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL),
        help=f"OpenAI-compatible service base URL. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
        help=f"Model name. Default: {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP timeout in seconds. Default: 60",
    )
    args = parser.parse_args(argv)
    if args.image and (args.camera_device is not None or args.camera_index is not None):
        parser.error("--image cannot be combined with camera options")
    if args.watch and args.image:
        parser.error("--watch cannot be combined with --image")
    if args.watch and not args.query.strip():
        parser.error("--watch requires a non-empty --query")
    debug_log(
        "parse_args "
        f"watch={args.watch} query={args.query!r} image={args.image!r} "
        f"camera_device={args.camera_device!r} camera_index={args.camera_index!r} "
        f"min_score={args.min_score!r}"
    )
    return args


def guess_mime_type(image_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(str(image_path))
    return mime_type or "application/octet-stream"


def image_to_data_url(image_path: Path) -> str:
    mime_type = guess_mime_type(image_path)
    payload = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{payload}"


def build_prompt(query: str, image_size: tuple[int, int] | None) -> str:
    size_hint = ""
    if image_size is not None:
        size_hint = f"图像尺寸为 {image_size[0]}x{image_size[1]} 像素。"
    return (
        "你是一个严格输出 JSON 的视觉目标定位器。"
        f"请在图像中查找与这段描述匹配的目标：{query!r}。"
        f"{size_hint}"
        "如果存在一个或多个匹配目标，只能返回一个 JSON 对象，格式为："
        '{"matches":[{"description":"原始描述或更具体的短描述","bbox":{"x1":0,"y1":0,"x2":0,"y2":0},"score":0.0}]}.'
        "bbox 必须是相对于原图的像素坐标，x2>x1，y2>y1。score 为 0 到 1 的浮点数。"
        "如果没有匹配目标，必须只返回 {}。"
        "不要输出 Markdown，不要输出解释，不要输出额外文字。"
    )


def read_image_size(image_path: Path) -> tuple[int, int] | None:
    try:
        with image_path.open("rb") as handle:
            header = handle.read(32)
            if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
                width = int.from_bytes(header[16:20], "big")
                height = int.from_bytes(header[20:24], "big")
                return width, height
            if header.startswith((b"GIF87a", b"GIF89a")) and len(header) >= 10:
                width = int.from_bytes(header[6:8], "little")
                height = int.from_bytes(header[8:10], "little")
                return width, height
    except OSError:
        return None

    try:
        from PIL import Image  # type: ignore

        with Image.open(image_path) as image:
            return image.size
    except Exception:
        return None


def build_request_payload(image_path: Path, query: str, model: str) -> dict[str, Any]:
    image_size = read_image_size(image_path)
    return {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": build_prompt(query, image_size)},
                    {"type": "image_url", "image_url": {"url": image_to_data_url(image_path)}},
                ],
            }
        ],
    }


def completion_url(base_url: str) -> str:
    stripped = base_url.rstrip("/")
    if stripped.endswith("/chat/completions"):
        return stripped
    if stripped.endswith("/v1"):
        return f"{stripped}/chat/completions"
    return f"{stripped}/v1/chat/completions"


def extract_message_text(response_json: dict[str, Any]) -> str:
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def request_detection(base_url: str, payload: dict[str, Any], timeout: float) -> str:
    debug_log(
        "request_detection "
        f"url={completion_url(base_url)!r} model={payload.get('model')!r} timeout={timeout}"
    )
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(completion_url(base_url), data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response_body = response.read().decode("utf-8")
    debug_log(f"request_detection response_body={response_body!r}")
    parsed = json.loads(response_body)
    return extract_message_text(parsed)


def extract_first_json_object(raw_text: str) -> dict[str, Any] | None:
    if not raw_text:
        return None

    trimmed = raw_text.strip()
    candidates = [trimmed]

    fenced = re.findall(r"```(?:json)?\s*(.*?)```", raw_text, flags=re.IGNORECASE | re.DOTALL)
    candidates.extend(chunk.strip() for chunk in fenced if chunk.strip())

    for candidate in candidates:
        parsed = try_parse_json_object(candidate)
        if parsed is not None:
            return parsed

    start = raw_text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(raw_text)):
            char = raw_text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    parsed = try_parse_json_object(raw_text[start : index + 1])
                    if parsed is not None:
                        return parsed
                    break
        start = raw_text.find("{", start + 1)
    return None


def try_parse_json_object(candidate: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def normalize_bbox(value: Any) -> dict[str, int] | None:
    coords: dict[str, int] = {}
    if isinstance(value, dict):
        raw_values = {
            "x1": value.get("x1"),
            "y1": value.get("y1"),
            "x2": value.get("x2"),
            "y2": value.get("y2"),
        }
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        raw_values = {
            "x1": value[0],
            "y1": value[1],
            "x2": value[2],
            "y2": value[3],
        }
    else:
        return None

    for key in ("x1", "y1", "x2", "y2"):
        raw = raw_values.get(key)
        if not isinstance(raw, (int, float)) or not math.isfinite(raw):
            return None
        coords[key] = int(round(raw))
    if coords["x1"] < 0 or coords["y1"] < 0:
        return None
    if coords["x2"] <= coords["x1"] or coords["y2"] <= coords["y1"]:
        return None
    return coords


def normalize_match(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    description = value.get("description")
    if not isinstance(description, str) or not description.strip():
        return None
    bbox = normalize_bbox(value.get("bbox"))
    if bbox is None:
        return None

    score = value.get("score")
    if not isinstance(score, (int, float)) or not math.isfinite(score):
        return None
    return {"description": description.strip(), "bbox": bbox, "score": float(score)}


def normalize_result(value: Any, min_score: float = DEFAULT_MIN_SCORE) -> dict[str, Any]:
    if value == {}:
        return {}
    matches_source: list[Any]
    if isinstance(value, dict) and isinstance(value.get("matches"), list):
        matches_source = value["matches"]
    elif isinstance(value, dict):
        single = normalize_match(value)
        return {"matches": [single]} if single is not None else {}
    else:
        return {}

    matches = []
    for item in matches_source:
        normalized = normalize_match(item)
        if normalized is not None and float(normalized["score"]) >= min_score:
            matches.append(normalized)
    return {"matches": matches} if matches else {}


def detect_image_by_urllib(image_path: Path, query: str, base_url: str, model: str, timeout: float, min_score: float) -> dict[str, Any]:
    debug_log(f"detect_image image_path={str(image_path)!r} query={query!r} min_score={min_score!r}")
    payload = build_request_payload(image_path=image_path, query=query, model=model)
    try:
        raw_output = request_detection(base_url, payload, timeout)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP error {exc.code}: {body}", file=sys.stderr)
        raise RuntimeError(f"HTTP error {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from API: {exc}") from exc
    parsed = extract_first_json_object(raw_output)
    normalized = normalize_result(parsed if parsed is not None else {}, min_score=min_score)
    debug_log(f"detect_image raw_output={raw_output!r}")
    debug_log(f"detect_image normalized={json.dumps(normalized, ensure_ascii=False)!r}")
    return normalized


def detect_image(image_path: Path, query: str, base_url: str, model: str, timeout: float, min_score: float) -> dict[str, Any]:
    debug_log(f"detect_image_by_modelhub image_path={str(image_path)!r} query={query!r} min_score={min_score!r}")
    payload = build_request_payload(image_path=image_path, query=query, model=model)
    try:
        from model_hub_py.client import ModelHubPyClient
        client = ModelHubPyClient(base_url, timeout=timeout)
        result = client.run(model, method="POST", path="/v1/chat/completions", json_body=payload, timeout=timeout)
        upstream_body = result.get("upstream_body")
        if not isinstance(upstream_body, dict):
            raise RuntimeError(f"invalid model_hub result payload: {json.dumps(result, ensure_ascii=False)}")
        raw_output = extract_message_text(upstream_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from API: {exc}") from exc
    parsed = extract_first_json_object(raw_output)
    normalized = normalize_result(parsed if parsed is not None else {}, min_score=min_score)
    debug_log(f"detect_image_by_modelhub raw_output={raw_output!r}")
    debug_log(f"detect_image_by_modelhub normalized={json.dumps(normalized, ensure_ascii=False)!r}")
    return normalized


def list_candidate_video_devices() -> list[Path]:
    devices = [path for path in Path("/dev").glob("video*") if re.fullmatch(r"video\d+", path.name)]
    devices.sort(key=lambda path: path.name)
    return devices


def has_v4l2_ctl() -> bool:
    return shutil.which("v4l2-ctl") is not None


def is_capture_device(dev: Path) -> bool:
    if not has_v4l2_ctl():
        return False
    try:
        result = subprocess.run(
            ["v4l2-ctl", "-d", str(dev), "--all"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    info = result.stdout + result.stderr
    return "Video Capture" in info or "Video Capture Multiplanar" in info


def is_usb_camera(dev: Path) -> bool:
    sys_path = os.path.realpath(f"/sys/class/video4linux/{dev.name}/device")
    return "/usb" in sys_path


def is_rkisp_selfpath_camera(dev: Path) -> bool:
    if not has_v4l2_ctl():
        return False
    try:
        result = subprocess.run(
            ["v4l2-ctl", "-d", str(dev), "-D"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    info = result.stdout + result.stderr
    return "rkisp_selfpath" in info


def pick_usb_camera() -> str | None:
    for dev in list_candidate_video_devices():
        if not os.access(dev, os.R_OK):
            continue
        if not is_usb_camera(dev):
            continue
        if is_capture_device(dev):
            return str(dev)
    return None


def pick_rkisp_selfpath_camera() -> str | None:
    for dev in list_candidate_video_devices():
        if not os.access(dev, os.R_OK):
            continue
        if not is_rkisp_selfpath_camera(dev):
            continue
        if is_capture_device(dev):
            return str(dev)
    return None


def pick_default_camera_source() -> str | int:
    usb_camera = pick_usb_camera()
    if usb_camera:
        debug_log(f"pick_default_camera_source selected_usb={usb_camera!r}")
        return usb_camera

    rkisp_camera = pick_rkisp_selfpath_camera()
    if rkisp_camera:
        debug_log(f"pick_default_camera_source selected_rkisp={rkisp_camera!r}")
        return rkisp_camera

    candidates = [str(path) for path in list_candidate_video_devices()]
    debug_log(f"pick_default_camera_source no_preferred_camera candidates={candidates!r}")
    if not has_v4l2_ctl() and candidates:
        debug_log(f"pick_default_camera_source fallback_first_candidate={candidates[0]!r}")
        return candidates[0]
    raise RuntimeError("no usable USB camera or rkisp_selfpath device found")


def capture_image_from_camera(
    output_path: Path,
    camera_device: str | None = None,
    camera_index: int | None = None,
    width: int = DEFAULT_CAPTURE_WIDTH,
    height: int = DEFAULT_CAPTURE_HEIGHT,
) -> Path:
    debug_log(
        "capture_image_from_camera "
        f"output_path={str(output_path)!r} camera_device={camera_device!r} "
        f"camera_index={camera_index!r} width={width} height={height}"
    )
    try:
        import cv2  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "OpenCV (cv2) is required for camera capture. "
            "Install python3-opencv for the same interpreter that runs this script."
        ) from exc

    source: str | int
    if camera_device is not None:
        source = camera_device
    elif camera_index is not None:
        source = camera_index
    else:
        source = pick_default_camera_source()

    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        debug_log(f"capture_image_from_camera failed_open source={source!r}")
        raise RuntimeError(f"failed to open camera source: {source}")

    try:
        if width > 0:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        if height > 0:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        frame = None
        for _ in range(5):
            ok, current = capture.read()
            if ok:
                frame = current
        if frame is None:
            debug_log(f"capture_image_from_camera failed_read source={source!r}")
            raise RuntimeError(f"failed to read frame from camera source: {source}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(output_path), frame):
            raise RuntimeError(f"failed to write captured frame: {output_path}")
        debug_log(f"capture_image_from_camera saved={str(output_path)!r}")
        return output_path
    finally:
        capture.release()


def make_initial_monitor_state(query: str) -> dict[str, Any]:
    return {
        "query": query,
        "target": {
            "present": False,
            "last_remind_at": 0,
            "misses": 0,
            "hit_streak": 0,
            "last_image_path": "",
            "last_description": "",
            "last_score": 0.0,
        },
    }


def load_monitor_state(state_file: Path, query: str) -> dict[str, Any]:
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = make_initial_monitor_state(query)
            debug_log(
                "load_monitor_state replaced_non_object "
                f"state_file={str(state_file)!r} query={query!r}"
            )
            return state
        stored_query = state.get("query")
        if stored_query != query:
            debug_log(
                "load_monitor_state query_changed "
                f"state_file={str(state_file)!r} "
                f"stored_query={stored_query!r} new_query={query!r} "
                "resetting target state"
            )
            return make_initial_monitor_state(query)
        target = state.get("target", {}) if isinstance(state, dict) else {}
        debug_log(
            "load_monitor_state existing "
            f"state_file={str(state_file)!r} query={query!r} "
            f"present={target.get('present', False)!r} "
            f"last_remind_at={target.get('last_remind_at', 0)!r} "
            f"hit_streak={target.get('hit_streak', 0)!r} "
            f"misses={target.get('misses', 0)!r}"
        )
        return state
    state = make_initial_monitor_state(query)
    debug_log(f"load_monitor_state new state_file={str(state_file)!r} query={query!r}")
    return state


def save_monitor_state(state_file: Path, state: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def update_monitor_state(
    state: dict[str, Any],
    result: dict[str, Any],
    image_path: Path,
    now: int,
    config: MonitorConfig,
) -> list[dict[str, Any]]:
    target = state.setdefault("target", {})
    target.setdefault("present", False)
    target.setdefault("last_remind_at", 0)
    target.setdefault("misses", 0)
    target.setdefault("hit_streak", 0)
    target.setdefault("last_image_path", "")
    target.setdefault("last_description", "")
    target.setdefault("last_score", 0.0)

    matches = result.get("matches") if isinstance(result, dict) else None
    events: list[dict[str, Any]] = []
    debug_log(
        "update_monitor_state state_before "
        f"query={config.query!r} image_path={str(image_path)!r} "
        f"matches_count={len(matches) if isinstance(matches, list) else 0} "
        f"present={target.get('present', False)!r} "
        f"last_remind_at={target.get('last_remind_at', 0)!r} "
        f"hit_streak={target.get('hit_streak', 0)!r} "
        f"misses={target.get('misses', 0)!r}"
    )
    if isinstance(matches, list) and matches:
        first = matches[0] if isinstance(matches[0], dict) else {}
        description = str(first.get("description") or config.query)
        score = float(first.get("score") or 0.0)
        target["misses"] = 0
        target["hit_streak"] = int(target.get("hit_streak", 0)) + 1

        remind = False
        elapsed: int | None = None
        if target["hit_streak"] >= config.hit_confirmations:
            if not target.get("present", False):
                remind = True
            else:
                elapsed = now - int(target.get("last_remind_at", 0))
                if elapsed >= config.remind_max_silence_sec:
                    remind = True

        cooldown_block = target.get("present", False) and (
            now - int(target.get("last_remind_at", 0)) < config.remind_cooldown_sec
        )
        debug_log(
            "update_monitor_state decision "
            f"description={description!r} score={score!r} "
            f"present={target.get('present', False)!r} "
            f"hit_streak={target.get('hit_streak', 0)!r} "
            f"elapsed={elapsed!r} "
            f"remind={remind!r} "
            f"cooldown_block={cooldown_block!r}"
        )
        if remind and not cooldown_block:
            events.append(
                {
                    "type": "remind",
                    "query": config.query,
                    "image_path": str(image_path),
                    "description": description,
                    "score": score,
                }
            )
            target["last_remind_at"] = now
            target["last_image_path"] = str(image_path)
            target["last_description"] = description
            target["last_score"] = score
            debug_log(
                "update_monitor_state queued_reminder "
                f"query={config.query!r} image_path={str(image_path)!r} score={score!r}"
            )
        target["present"] = True
    else:
        target["misses"] = int(target.get("misses", 0)) + 1
        target["hit_streak"] = 0
        if target["misses"] >= config.miss_clear_count:
            target["present"] = False
            target["last_remind_at"] = 0
            target["last_image_path"] = ""
            target["last_description"] = ""
            target["last_score"] = 0.0
            debug_log(
                "update_monitor_state cleared_target_state "
                f"query={config.query!r} misses={target.get('misses', 0)!r}"
            )

    debug_log(
        "update_monitor_state state_after "
        f"present={target.get('present', False)!r} "
        f"last_remind_at={target.get('last_remind_at', 0)!r} "
        f"hit_streak={target.get('hit_streak', 0)!r} "
        f"misses={target.get('misses', 0)!r} "
        f"events={json.dumps(events, ensure_ascii=False)!r}"
    )
    return events


def pick_latest_target(known_users_json: Path) -> str:
    if not known_users_json.exists():
        raise RuntimeError(f"known-users.json not found: {known_users_json}")
    rows = json.loads(known_users_json.read_text(encoding="utf-8"))
    rows = [row for row in rows if isinstance(row, dict) and str(row.get("openid", "")).strip()]
    if not rows:
        raise RuntimeError("no valid qqbot target")
    rows.sort(key=lambda row: int(row.get("lastSeenAt", 0) or 0), reverse=True)
    row = rows[0]
    kind = "group" if row.get("type") == "group" else "c2c"
    target = f"qqbot:{kind}:{str(row['openid']).strip()}"
    debug_log(f"pick_latest_target selected={target!r} from={str(known_users_json)!r}")
    return target


def resolve_openclaw_executable(bin_arg: str) -> str:
    """Return a path usable as subprocess argv0. Bare names are resolved with PATH."""
    name = (bin_arg or "").strip()
    if not name:
        raise RuntimeError("openclaw executable path is empty")
    candidate = Path(name).expanduser()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate.resolve())
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(
        f"openclaw executable not found: {bin_arg!r}. "
        "Set RK_VL_OPENCLAW_BIN or --openclaw-bin to a full path, or ensure the tool is on PATH "
        "(nohup/systemd often start with a minimal PATH)."
    )


def send_remind(openclaw_bin: str, target: str, query: str, image_path: Path) -> None:
    debug_log(
        f"send_remind openclaw_bin={openclaw_bin!r} target={target!r} "
        f"query={query!r} image_path={str(image_path)!r}"
    )
    try:
        result = subprocess.run(
            [
                openclaw_bin,
                "message",
                "send",
                "--channel",
                "qqbot",
                "--target",
                target,
                "--message",
                f"检测到目标“{query}”，图像如下：<qqfile>{image_path}</qqfile>",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        debug_log(f"send_remind FileNotFoundError={exc!r} openclaw_bin={openclaw_bin!r}")
        raise RuntimeError(
            f"failed to run openclaw ({openclaw_bin!r}): {exc}. "
            "If this is a Node-based CLI, ensure `node` is on PATH when the monitor starts, "
            "or set RK_VL_OPENCLAW_BIN to a full path (e.g. the real openclaw script or "
            "`node /path/to/openclaw/cli.js`)."
        ) from exc
    debug_log(
        "send_remind result "
        f"returncode={result.returncode!r} stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    if result.returncode != 0:
        raise RuntimeError(f"openclaw message send failed: {result.stderr.strip() or result.stdout.strip()}")


def build_monitor_config(args: argparse.Namespace) -> MonitorConfig:
    capture_dir = Path(args.capture_dir).expanduser().resolve() if args.capture_dir else DEFAULT_STATE_DIR / "captures"
    state_file = Path(args.state_file).expanduser().resolve() if args.state_file else capture_dir.parent / "state.json"
    return MonitorConfig(
        query=args.query,
        poll_interval_sec=args.poll_interval,
        hit_confirmations=args.hit_confirmations,
        miss_clear_count=args.miss_clear_count,
        remind_cooldown_sec=args.remind_cooldown_sec,
        remind_max_silence_sec=args.remind_max_silence_sec,
        min_score=args.min_score,
        openclaw_bin=args.openclaw_bin,
        known_users_json=Path(args.known_users_json).expanduser().resolve(),
        capture_dir=capture_dir,
        state_file=state_file,
        camera_device=args.camera_device,
        camera_index=args.camera_index,
        capture_width=args.capture_width,
        capture_height=args.capture_height,
        base_url=args.base_url,
        model=args.model,
        timeout=args.timeout,
    )


def detect_once_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.image:
        image_path = Path(args.image).expanduser().resolve()
        if not image_path.is_file():
            raise RuntimeError(f"image not found: {image_path}")
        return detect_image(
            image_path=image_path,
            query=args.query,
            base_url=args.base_url,
            model=args.model,
            timeout=args.timeout,
            min_score=args.min_score,
        )

    with tempfile.TemporaryDirectory(prefix="rk-vl-capture-") as temp_dir:
        image_path = Path(temp_dir) / "capture.jpg"
        capture_image_from_camera(
            output_path=image_path,
            camera_device=args.camera_device,
            camera_index=args.camera_index,
            width=args.capture_width,
            height=args.capture_height,
        )
        return detect_image(
            image_path=image_path,
            query=args.query,
            base_url=args.base_url,
            model=args.model,
            timeout=args.timeout,
            min_score=args.min_score,
        )


def run_watch_mode(config: MonitorConfig) -> int:
    debug_log(
        "run_watch_mode "
        f"query={config.query!r} capture_dir={str(config.capture_dir)!r} state_file={str(config.state_file)!r}"
    )
    try:
        resolved_oc = resolve_openclaw_executable(config.openclaw_bin)
    except RuntimeError as exc:
        debug_log(f"run_watch_mode openclaw_resolve_failed={exc!r}")
        print(f"[rk-vl-watch] {exc}", file=sys.stderr)
        return 1
    if resolved_oc != config.openclaw_bin:
        debug_log(f"run_watch_mode resolved_openclaw_bin={resolved_oc!r}")
    config = replace(config, openclaw_bin=resolved_oc)
    config.capture_dir.mkdir(parents=True, exist_ok=True)
    state = load_monitor_state(config.state_file, config.query)
    try:
        while True:
            stamp = int(time.time())
            image_path = config.capture_dir / f"capture-{stamp}.jpg"
            try:
                capture_image_from_camera(
                    output_path=image_path,
                    camera_device=config.camera_device,
                    camera_index=config.camera_index,
                    width=config.capture_width,
                    height=config.capture_height,
                )
                result = detect_image(
                    image_path=image_path,
                    query=config.query,
                    base_url=config.base_url,
                    model=config.model,
                    timeout=config.timeout,
                    min_score=config.min_score,
                )
                events = update_monitor_state(state=state, result=result, image_path=image_path, now=int(time.time()), config=config)
                save_monitor_state(config.state_file, state)
                debug_log(f"run_watch_mode events_count={len(events)}")
                for event in events:
                    if event.get("type") != "remind":
                        continue
                    debug_log(f"run_watch_mode processing_event={json.dumps(event, ensure_ascii=False)!r}")
                    target = pick_latest_target(config.known_users_json)
                    debug_log(f"run_watch_mode picked_target={target!r}")
                    send_remind(
                        openclaw_bin=config.openclaw_bin,
                        target=target,
                        query=str(event["query"]),
                        image_path=Path(str(event["image_path"])),
                    )
                    debug_log("run_watch_mode send_remind success")
            except Exception as exc:
                debug_log(f"run_watch_mode error={exc!r}")
                print(f"[rk-vl-watch] {exc}", file=sys.stderr)
            time.sleep(config.poll_interval_sec)
    except KeyboardInterrupt:
        return 0


def main() -> int:
    args = parse_args()
    try:
        if args.watch:
            return run_watch_mode(build_monitor_config(args))
        result = detect_once_from_args(args)
    except RuntimeError as exc:
        debug_log(f"main runtime_error={exc!r}")
        print(str(exc), file=sys.stderr)
        return 1

    debug_log(f"main result={json.dumps(result, ensure_ascii=False)!r}")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
