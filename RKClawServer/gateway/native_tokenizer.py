from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Any


class NativeTokenizerError(RuntimeError):
    pass


class VocabInfo(ctypes.Structure):
    _fields_ = [
        ("vocab_size", ctypes.c_int32),
        ("special_bos_id", ctypes.c_int32 * 64),
        ("special_eos_id", ctypes.c_int32 * 64),
        ("n_special_bos_id", ctypes.c_int32),
        ("n_special_eos_id", ctypes.c_int32),
        ("linefeed_id", ctypes.c_int32),
    ]


class GGUFTokenizer:
    """ctypes adapter for the model-zoo llama.cpp GGUF tokenizer."""

    def __init__(self, tokenizer_path: str, library_path: str, max_tokens: int):
        if not Path(tokenizer_path).is_file():
            raise NativeTokenizerError(f"Tokenizer GGUF not found: {tokenizer_path}")
        if not Path(library_path).is_file():
            raise NativeTokenizerError(f"Native tokenizer library not found: {library_path}")
        self.path = tokenizer_path
        self.max_tokens = max_tokens
        try:
            self.lib = ctypes.CDLL(library_path)
        except OSError as exc:
            raise NativeTokenizerError(f"Failed to load native tokenizer library {library_path}: {exc}") from exc
        self._configure_api()
        self.handle = self.lib.claw_tokenizer_create(tokenizer_path.encode())
        if not self.handle:
            raise NativeTokenizerError(self._last_error("Failed to create GGUF tokenizer"))
        self.vocab_info = VocabInfo()
        if self.lib.claw_tokenizer_get_vocab_info(self.handle, ctypes.byref(self.vocab_info)) != 0:
            self.close()
            raise NativeTokenizerError(self._last_error("Failed to query GGUF vocabulary"))
        self.special_bos_ids = _deduplicate(self.vocab_info.special_bos_id[: self.vocab_info.n_special_bos_id])
        self.special_eos_ids = _deduplicate(self.vocab_info.special_eos_id[: self.vocab_info.n_special_eos_id])
        self.bos_token_id = self.special_bos_ids[0] if self.special_bos_ids else None
        self.eos_token_id = self.special_eos_ids[0] if self.special_eos_ids else None
        self.linefeed_id = int(self.vocab_info.linefeed_id)
        self.bos_token = self.token_to_piece(self.bos_token_id) if self.bos_token_id is not None else ""
        self.eos_token = self.token_to_piece(self.eos_token_id) if self.eos_token_id is not None else ""
        self.chat_template = self.get_metadata("tokenizer.chat_template")
        self.chat_templates: dict[str, str] = {}
        if self.chat_template:
            self.chat_templates["default"] = self.chat_template
        tool_template = self.get_metadata("tokenizer.chat_template.tool_use")
        if tool_template:
            self.chat_templates["tool_use"] = tool_template

    def __len__(self) -> int:
        return int(self.vocab_info.vocab_size)

    def close(self) -> None:
        handle = getattr(self, "handle", None)
        if handle:
            self.lib.claw_tokenizer_destroy(handle)
            self.handle = None

    def __del__(self):  # pragma: no cover - best effort during interpreter shutdown
        try:
            self.close()
        except Exception:
            pass

    def encode(self, text: str, add_special_tokens: bool = True) -> list[int]:
        encoded = text.encode("utf-8")
        # The native tokenizer rejects a null/zero-length input, while the
        # tokenizer API convention for an empty string is an empty token list.
        if not encoded:
            return []
        output = (ctypes.c_int32 * self.max_tokens)()
        count = self.lib.claw_tokenizer_encode(self.handle, encoded, len(encoded), output, self.max_tokens)
        if count < 0:
            raise NativeTokenizerError(self._last_error("Tokenization failed"))
        if count >= self.max_tokens:
            raise NativeTokenizerError(f"Tokenized input reached configured capacity {self.max_tokens}")
        return list(output[:count])

    def encode_into(self, text: str, tokens_ptr: Any, capacity: int) -> int:
        encoded = text.encode("utf-8")
        result = self.lib.claw_tokenizer_encode(self.handle, encoded, len(encoded), tokens_ptr, capacity)
        if result < 0:
            raise NativeTokenizerError(self._last_error("Tokenization callback failed"))
        return result

    def decode(self, tokens: list[int], **_: Any) -> str:
        if not tokens:
            return ""
        token_array = (ctypes.c_int32 * len(tokens))(*tokens)
        required = self.lib.claw_tokenizer_decode(self.handle, token_array, len(tokens), None, 0)
        if required < 0:
            raise NativeTokenizerError(self._last_error("Decode failed"))
        output = ctypes.create_string_buffer(required)
        if self.lib.claw_tokenizer_decode(self.handle, token_array, len(tokens), output, required) < 0:
            raise NativeTokenizerError(self._last_error("Decode failed"))
        # A token boundary may end inside a UTF-8 character. Accumulated decoding on the
        # next callback will include the complete byte sequence.
        return output.raw[: required - 1].decode("utf-8", errors="ignore")

    def token_to_piece(self, token: int) -> str:
        return self.token_to_piece_bytes(token).decode("utf-8", errors="replace")

    def token_to_piece_bytes(self, token: int) -> bytes:
        required = self.lib.claw_tokenizer_token_to_piece(self.handle, token, None, 0)
        if required < 0:
            raise NativeTokenizerError(self._last_error("TokenToPiece failed"))
        output = ctypes.create_string_buffer(required)
        if self.lib.claw_tokenizer_token_to_piece(self.handle, token, output, required) < 0:
            raise NativeTokenizerError(self._last_error("TokenToPiece failed"))
        return output.raw[: required - 1]

    def get_metadata(self, key: str) -> str:
        path = self.path.encode()
        encoded_key = key.encode()
        required = self.lib.claw_tokenizer_get_metadata(path, encoded_key, None, 0)
        if required < 0:
            raise NativeTokenizerError(self._last_error(f"Failed to read GGUF metadata {key}"))
        if required == 0:
            return ""
        output = ctypes.create_string_buffer(required)
        self.lib.claw_tokenizer_get_metadata(path, encoded_key, output, required)
        return output.raw[: required - 1].decode("utf-8")

    def get_special_token(self, name: str) -> str:
        """Resolve a named special token to its string piece.

        Tries GGUF metadata ``tokenizer.ggml.{name}`` (string) then
        ``tokenizer.ggml.{name}_id`` (integer resolved via
        :meth:`token_to_piece`). Returns an empty string when the
        tokenizer exposes no such token -- callers fall back to a
        hardcoded string declared in the model profile.
        """
        if name == "bos_token":
            return self.bos_token
        if name == "eos_token":
            return self.eos_token
        value = self.get_metadata(f"tokenizer.ggml.{name}")
        if value:
            return value
        id_str = self.get_metadata(f"tokenizer.ggml.{name}_id")
        if id_str:
            try:
                return self.token_to_piece(int(id_str))
            except (ValueError, TypeError):
                pass
        return ""

    def _last_error(self, fallback: str) -> str:
        value = self.lib.claw_tokenizer_last_error()
        return value.decode("utf-8", errors="replace") if value else fallback

    def _configure_api(self) -> None:
        int32_pointer = ctypes.POINTER(ctypes.c_int32)
        self.lib.claw_tokenizer_create.argtypes = [ctypes.c_char_p]
        self.lib.claw_tokenizer_create.restype = ctypes.c_void_p
        self.lib.claw_tokenizer_destroy.argtypes = [ctypes.c_void_p]
        self.lib.claw_tokenizer_last_error.argtypes = []
        self.lib.claw_tokenizer_last_error.restype = ctypes.c_char_p
        self.lib.claw_tokenizer_get_vocab_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(VocabInfo)]
        self.lib.claw_tokenizer_get_vocab_info.restype = ctypes.c_int
        self.lib.claw_tokenizer_encode.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32, int32_pointer, ctypes.c_int32,
        ]
        self.lib.claw_tokenizer_encode.restype = ctypes.c_int
        self.lib.claw_tokenizer_decode.argtypes = [
            ctypes.c_void_p, int32_pointer, ctypes.c_int32, ctypes.c_void_p, ctypes.c_int32,
        ]
        self.lib.claw_tokenizer_decode.restype = ctypes.c_int
        self.lib.claw_tokenizer_token_to_piece.argtypes = [
            ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p, ctypes.c_int32,
        ]
        self.lib.claw_tokenizer_token_to_piece.restype = ctypes.c_int
        self.lib.claw_tokenizer_get_metadata.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int32,
        ]
        self.lib.claw_tokenizer_get_metadata.restype = ctypes.c_int


def _deduplicate(values: Any) -> list[int]:
    result: list[int] = []
    for value in values:
        parsed = int(value)
        if parsed not in result:
            result.append(parsed)
    return result
