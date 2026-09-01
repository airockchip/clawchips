from __future__ import annotations

import ctypes

from gateway.native_tokenizer import GGUFTokenizer, VocabInfo


class Function:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


class FakeLibrary:
    def __init__(self):
        self.destroyed = False
        self.encode_calls = 0
        self.claw_tokenizer_create = Function(lambda path: 1)
        self.claw_tokenizer_destroy = Function(self._destroy)
        self.claw_tokenizer_last_error = Function(lambda: b"native error")
        self.claw_tokenizer_get_vocab_info = Function(self._vocab)
        self.claw_tokenizer_encode = Function(self._encode)
        self.claw_tokenizer_decode = Function(self._decode)
        self.claw_tokenizer_token_to_piece = Function(self._piece)
        self.claw_tokenizer_get_metadata = Function(self._metadata)

    def _destroy(self, handle):
        self.destroyed = True

    def _vocab(self, handle, pointer):
        info = ctypes.cast(pointer, ctypes.POINTER(VocabInfo)).contents
        info.vocab_size = 151936
        info.special_bos_id[0] = 151643
        info.n_special_bos_id = 1
        info.special_eos_id[0] = 151645
        info.special_eos_id[1] = 151645
        info.special_eos_id[2] = 151643
        info.n_special_eos_id = 3
        info.linefeed_id = 198
        return 0

    def _encode(self, handle, text, text_len, output, capacity):
        self.encode_calls += 1
        output[0], output[1] = 10, 11
        return 2

    def _decode(self, handle, tokens, count, output, capacity):
        return self._copy(b"hello\0", output, capacity)

    def _piece(self, handle, token, output, capacity):
        value = b"<special>\0"
        return self._copy(value, output, capacity)

    def _metadata(self, path, key, output, capacity):
        values = {
            b"tokenizer.chat_template": b"default-template\0",
            b"tokenizer.chat_template.tool_use": b"tool-template\0",
        }
        return self._copy(values.get(key, b""), output, capacity)

    @staticmethod
    def _copy(value, output, capacity):
        if not value:
            return 0
        if output is not None and capacity >= len(value):
            ctypes.memmove(output, value, len(value))
        return len(value)


def test_native_tokenizer_adapter(monkeypatch, tmp_path) -> None:
    tokenizer_file = tmp_path / "tokenizer.gguf"
    library_file = tmp_path / "librkclaw_native.so"
    tokenizer_file.touch()
    library_file.touch()
    library = FakeLibrary()
    monkeypatch.setattr(ctypes, "CDLL", lambda path: library)

    tokenizer = GGUFTokenizer(str(tokenizer_file), str(library_file), max_tokens=32)

    assert len(tokenizer) == 151936
    assert tokenizer.special_bos_ids == [151643]
    assert tokenizer.special_eos_ids == [151645, 151643]
    assert tokenizer.linefeed_id == 198
    assert tokenizer.chat_templates == {"default": "default-template", "tool_use": "tool-template"}
    assert tokenizer.encode("hello") == [10, 11]
    assert tokenizer.encode("") == []
    assert library.encode_calls == 1
    assert tokenizer.decode([10, 11]) == "hello"
    assert tokenizer.token_to_piece_bytes(10) == b"<special>"
    tokenizer.close()
    assert library.destroyed is True


def test_token_piece_bytes_preserve_non_utf8_vocabulary(monkeypatch, tmp_path) -> None:
    tokenizer_file = tmp_path / "tokenizer.gguf"
    library_file = tmp_path / "librkclaw_native.so"
    tokenizer_file.touch()
    library_file.touch()
    library = FakeLibrary()
    library.claw_tokenizer_token_to_piece = Function(
        lambda handle, token, output, capacity: library._copy(b"\xff\0", output, capacity)
    )
    monkeypatch.setattr(ctypes, "CDLL", lambda path: library)
    tokenizer = GGUFTokenizer(str(tokenizer_file), str(library_file), max_tokens=32)

    assert tokenizer.token_to_piece_bytes(10) == b"\xff"
    assert tokenizer.token_to_piece(10) == "\ufffd"
