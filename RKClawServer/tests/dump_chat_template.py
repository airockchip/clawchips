"""Print chat_template / chat_templates metadata from a GGUF tokenizer file.

Usage:
    python tests/dump_chat_template.py /path/to/tokenizer.gguf [/path/to/librkclaw_native.so]
"""

from __future__ import annotations

import json
import sys

from gateway.native_tokenizer import GGUFTokenizer


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python tests/dump_chat_template.py <tokenizer.gguf> [librkclaw_native.so]")
        raise SystemExit(1)

    tokenizer_path = sys.argv[1]
    library_path = sys.argv[2] if len(sys.argv) > 2 else "/usr/lib/librkclaw_native.so"

    tokenizer = GGUFTokenizer(tokenizer_path, library_path, max_tokens=32768)
    try:
        print("=== bos_token / eos_token ===")
        print(f"bos_token:     {tokenizer.bos_token!r}")
        print(f"eos_token:     {tokenizer.eos_token!r}")
        print(f"bos_token_id:  {tokenizer.bos_token_id}")
        print(f"eos_token_id:  {tokenizer.eos_token_id}")
        print(f"linefeed_id:   {tokenizer.linefeed_id}")

        print("\n=== chat_template ===")
        print(tokenizer.chat_template)

        print("\n=== chat_templates ===")
        print(json.dumps(tokenizer.chat_templates, indent=2, ensure_ascii=False))

        print("\n=== vocab_info ===")
        print(f"vocab_size:       {tokenizer.vocab_info.vocab_size}")
        print(f"n_special_bos_id: {tokenizer.vocab_info.n_special_bos_id}")
        print(f"special_bos_ids:  {tokenizer.special_bos_ids}")
        print(f"n_special_eos_id: {tokenizer.vocab_info.n_special_eos_id}")
        print(f"special_eos_ids:  {tokenizer.special_eos_ids}")
        print(f"linefeed_id:      {tokenizer.vocab_info.linefeed_id}")
    finally:
        tokenizer.close()


if __name__ == "__main__":
    main()
