from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from model_hub_py.device.device import Device, MemoryInfo

_LOG = logging.getLogger(__name__)


def _parse_kv_line(line: str) -> tuple[str, str]:
    key, _, rest = line.partition(":")
    return key.strip(), rest.strip()


def _is_device_id_line(line: str) -> bool:
    if ":" not in line:
        return False
    key, _ = _parse_kv_line(line)
    return key == "Device ID"


def split_device_blocks(text: str) -> list[list[str]]:
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if _is_device_id_line(ln)]
    if not starts:
        return []
    blocks: list[list[str]] = []
    for j, start in enumerate(starts):
        end = starts[j + 1] if j + 1 < len(starts) else len(lines)
        blocks.append(lines[start:end])
    return blocks


def block_to_dict(block: list[str]) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in block:
        if ":" not in line:
            continue
        key, val = _parse_kv_line(line)
        data[key] = val
    return data


def _mb_to_bytes(mb: float) -> int:
    return round(mb * 1024 * 1024)


def _rknn_smi_captured_text(
    completed: subprocess.CompletedProcess[str],
    *,
    argv: list[str] | None = None,
) -> str:
    """rknn-smi 在部分环境把表格打印到 stdout，在另一些环境打印到 stderr。"""
    out = completed.stdout or ""
    if out.strip():
        return out
    err = completed.stderr or ""
    if err.strip() and argv is not None:
        _LOG.debug("rknn-smi: stdout empty, using stderr (argv=%s)", argv)
    return err


def _preview(text: str | None, max_len: int = 300) -> str:
    t = (text or "").replace("\n", "\\n")
    if len(t) <= max_len:
        return t
    return t[: max_len - 3] + "..."


def _log_rknn_smi_failure(
    completed: subprocess.CompletedProcess[str], argv: list[str]
) -> None:
    _LOG.debug(
        "rknn-smi call unusable: argv=%s returncode=%s stdout_len=%d stderr_len=%d "
        "stdout_preview=%r stderr_preview=%r",
        argv,
        completed.returncode,
        len(completed.stdout or ""),
        len(completed.stderr or ""),
        _preview(completed.stdout),
        _preview(completed.stderr),
    )


def parse_rk182x_memory_block(block: list[str]) -> MemoryInfo:
    data = block_to_dict(block)
    total_mb = float(data["Total DDR Capacity(MB)"])
    free_mb = float(data["Free DDR Capacity(MB)"])
    total_b = _mb_to_bytes(total_mb)
    free_b = _mb_to_bytes(free_mb)
    return MemoryInfo(
        total_bytes=total_b,
        available_bytes=free_b,
        used_bytes=max(total_b - free_b, 0),
    )


class RK182XDevice(Device):
    """RK182X 计算卡：通过 ``rknn-smi`` 查询设备信息与 DDR 容量。"""

    def __init__(
        self,
        *,
        device_index: int = 0,
        rknn_smi: str | Path = "rknn-smi",
        _runner: Callable[[list[str]], str] | None = None,
    ) -> None:
        self._device_index = device_index
        self._rknn_smi = str(rknn_smi)
        self._runner = _runner
        self._list_dict: dict[str, str] | None = None

    def _invoke(self, subargs: list[str]) -> str:
        argv = [self._rknn_smi, *subargs]
        if self._runner is not None:
            return self._runner(argv)
        _LOG.debug("rknn-smi invoke: device_index=%s argv=%s", self._device_index, argv)
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        text = _rknn_smi_captured_text(completed, argv=argv)
        _LOG.debug(
            "rknn-smi subprocess: returncode=%s text_len=%d (stdout=%d stderr=%d) "
            "has_device_id_key=%s",
            completed.returncode,
            len(text),
            len(completed.stdout or ""),
            len(completed.stderr or ""),
            "Device ID" in text,
        )
        if not text.strip():
            _log_rknn_smi_failure(completed, argv)
            raise subprocess.CalledProcessError(
                completed.returncode,
                argv,
                completed.stdout,
                completed.stderr,
            )
        if completed.returncode != 0 and "Device ID" not in text:
            # 有输出但不可解析，仍视为失败
            _LOG.debug("rknn-smi: output present but not parseable (no Device ID in text)")
            _log_rknn_smi_failure(completed, argv)
            raise subprocess.CalledProcessError(
                completed.returncode,
                argv,
                completed.stdout,
                completed.stderr,
            )
        if completed.returncode != 0:
            # 部分 rknn-smi 在能打印有效设备表时仍返回非 0 退出码
            _LOG.debug(
                "rknn-smi exited with %s; accepting text from stdout/stderr (argv=%s) "
                "stdout_preview=%r stderr_preview=%r",
                completed.returncode,
                argv,
                _preview(completed.stdout),
                _preview(completed.stderr),
            )
        return text

    def _get_list_dict(self) -> dict[str, str]:
        if self._list_dict is None:
            text = self._invoke(["info", "-l"])
            blocks = split_device_blocks(text)
            if self._device_index >= len(blocks):
                msg = (
                    f"device_index {self._device_index} out of range, "
                    f"found {len(blocks)} device(s) in rknn-smi list output"
                )
                raise IndexError(msg)
            self._list_dict = block_to_dict(blocks[self._device_index])
        return self._list_dict

    @property
    def name(self) -> str:
        return self._get_list_dict()["Product Name"]

    @property
    def device_id(self) -> str | int:
        return int(self._get_list_dict()["Device ID"])

    def get_core_count(self) -> int:
        """来自 ``rknn-smi info -l`` 中的 *Chip Count*（单卡上的芯片颗数）。"""
        return int(self._get_list_dict()["Chip Count"])

    def get_memory_info(self) -> MemoryInfo:
        text = self._invoke(["info", "-t", "memory"])
        blocks = split_device_blocks(text)
        if self._device_index >= len(blocks):
            msg = (
                f"device_index {self._device_index} out of range, "
                f"found {len(blocks)} device(s) in rknn-smi memory output"
            )
            raise IndexError(msg)
        return parse_rk182x_memory_block(blocks[self._device_index])
