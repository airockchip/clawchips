#!/usr/bin/env python3
"""Token 前缀匹配算法 — 正确性校验与性能基准 (独立脚本，无第三方依赖)。

使用方法：
    python3 tests/test_prefix_match_standalone.py

可选环境变量：
    QUICK=1              # 只跑少量配置，快速验证
    ONLY=id_range        # 只跑 token id 范围基准 (perf | id_range | all)
    ONLY=perf            # 只跑不同长度下的性能基准
"""

from __future__ import annotations

import os
import random
import sys
import time
from typing import Callable

# ---------------------------------------------------------------------------
# 算法实现
# ---------------------------------------------------------------------------

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    np = None  # type: ignore[assignment]


VOCAB = 128000


class NaiveMatcher:
    """逐序列逐元素比较，作为正确性基准与性能下限参考。"""

    def __init__(self) -> None:
        self._seqs: list[list[int]] = []

    def add(self, tokens: list[int]) -> None:
        self._seqs.append(list(tokens))

    def longest_prefix_len(self, query: list[int]) -> int:
        best = 0
        for seq in self._seqs:
            n = min(len(seq), len(query))
            i = 0
            while i < n and seq[i] == query[i]:
                i += 1
            if i > best:
                best = i
        return best


class _TrieNode:
    __slots__ = ("children",)

    def __init__(self) -> None:
        self.children: dict[int, _TrieNode] = {}


class TrieMatcher:
    """字典树。查询复杂度仅与 query 长度相关。"""

    def __init__(self) -> None:
        self._root = _TrieNode()

    def add(self, tokens: list[int]) -> None:
        node = self._root
        for t in tokens:
            nxt = node.children.get(t)
            if nxt is None:
                nxt = _TrieNode()
                node.children[t] = nxt
            node = nxt

    def longest_prefix_len(self, query: list[int]) -> int:
        node = self._root
        depth = 0
        for t in query:
            nxt = node.children.get(t)
            if nxt is None:
                break
            node = nxt
            depth += 1
        return depth


class NumpyMatcher:
    """逐序列 numpy 向量化比较。"""

    def __init__(self) -> None:
        self._seqs: list = []

    def add(self, tokens: list[int]) -> None:
        self._seqs.append(np.asarray(tokens, dtype=np.int64))

    def longest_prefix_len(self, query: list[int]) -> int:
        q = np.asarray(query, dtype=np.int64)
        ql = len(q)
        best = 0
        for s in self._seqs:
            n = min(ql, len(s))
            if n == 0:
                continue
            cmp = q[:n] == s[:n]
            if cmp.all():
                lcp = n
            else:
                lcp = int(np.argmin(cmp))
            if lcp > best:
                best = lcp
        return best


class NumpyPaddedMatcher:
    """2D 填充后一次性向量化比较。"""

    def __init__(self) -> None:
        self._seqs: list = []
        self._pad = np.empty((0, 0), dtype=np.int64)
        self._lens = np.empty((0,), dtype=np.int64)
        self._dirty = False

    def add(self, tokens: list[int]) -> None:
        self._seqs.append(np.asarray(tokens, dtype=np.int64))
        self._dirty = True

    def _rebuild(self) -> None:
        if not self._seqs:
            self._pad = np.empty((0, 0), dtype=np.int64)
            self._lens = np.empty((0,), dtype=np.int64)
            self._dirty = False
            return
        maxlen = max(len(s) for s in self._seqs)
        n = len(self._seqs)
        pad = np.zeros((n, maxlen), dtype=np.int64)
        lens = np.empty((n,), dtype=np.int64)
        for i, s in enumerate(self._seqs):
            pad[i, : len(s)] = s
            lens[i] = len(s)
        self._pad = pad
        self._lens = lens
        self._dirty = False

    def longest_prefix_len(self, query: list[int]) -> int:
        if self._dirty:
            self._rebuild()
        if self._pad.shape[0] == 0:
            return 0
        q = np.asarray(query, dtype=np.int64)
        n = min(len(q), self._pad.shape[1])
        if n == 0:
            return 0
        cmp = self._pad[:, :n] == q[:n]
        mask = np.arange(n)[None, :] < self._lens[:, None]
        eff = cmp & mask
        aug = np.empty((eff.shape[0], n + 1), dtype=bool)
        aug[:, :n] = eff
        aug[:, n] = False
        first_false = np.argmin(aug, axis=1)
        lcp = np.minimum(first_false, self._lens)
        return int(lcp.max())


def get_matchers() -> dict[str, Callable[[], object]]:
    matchers: dict[str, Callable[[], object]] = {
        "naive": NaiveMatcher,
        "trie": TrieMatcher,
    }
    if HAS_NUMPY:
        matchers["numpy"] = NumpyMatcher
        matchers["numpy_padded"] = NumpyPaddedMatcher
    return matchers


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def gen_tokens(n: int, seed: int, vocab: int = VOCAB) -> list[int]:
    rng = random.Random(seed)
    return [rng.randint(0, vocab - 1) for _ in range(n)]


def build_query(
    cached: list[list[int]],
    query_len: int,
    prefix_len: int,
    seed: int,
    vocab: int = VOCAB,
) -> list[int]:
    """构造一条 query，与 cached[0] 共享长度为 prefix_len 的前缀。"""
    rng = random.Random(seed)
    if prefix_len > len(cached[0]):
        prefix_len = len(cached[0])
    head = cached[0][:prefix_len]
    tail_len = max(0, query_len - prefix_len)
    tail = [rng.randint(0, vocab - 1) for _ in range(tail_len)]
    return head + tail


def bench_one_matcher(m, query: list[int], iters: int) -> float:
    m.longest_prefix_len(query)  # 预热
    t0 = time.perf_counter_ns()
    for _ in range(iters):
        m.longest_prefix_len(query)
    t1 = time.perf_counter_ns()
    return (t1 - t0) / iters / 1000.0  # µs/次


def print_table(headers: list[str], rows: list[list[str]], col_width: int = 22) -> None:
    print(" | ".join(h.rjust(col_width) for h in headers))
    print("-+-".join("-" * col_width for _ in headers))
    for row in rows:
        print(" | ".join(c.rjust(col_width) for c in row))


# ---------------------------------------------------------------------------
# 正确性测试
# ---------------------------------------------------------------------------

def run_correctness(matchers: dict[str, Callable[[], object]]) -> tuple[int, int]:
    cases = [
        # (cached, query, expected)
        ([[1, 2, 3, 4, 5], [1, 2, 9, 9], [7, 8, 9]], [1, 2, 3, 0, 0], 3),
        ([[1, 2, 3, 4, 5], [1, 2, 9, 9], [7, 8, 9]], [1, 2, 9, 9, 5], 4),
        ([[1, 2, 3, 4, 5], [1, 2, 9, 9], [7, 8, 9]], [7, 8, 9, 0], 3),
        ([[1, 2, 3, 4, 5], [1, 2, 9, 9], [7, 8, 9]], [99, 99], 0),
        ([[1, 2, 3, 4, 5], [1, 2, 9, 9], [7, 8, 9]], [], 0),
        ([], [1, 2, 3], 0),
        ([[10, 20, 30]], [10, 20, 30], 3),
        ([[10, 20, 30]], [10, 20, 30, 40], 3),
    ]
    passed = 0
    failed = 0
    for name, cls in matchers.items():
        for cached, query, expected in cases:
            m = cls()
            for s in cached:
                m.add(s)
            got = m.longest_prefix_len(query)
            if got == expected:
                passed += 1
            else:
                failed += 1
                print(f"  [FAIL] {name}: cached={cached}, query={query}, "
                      f"expected={expected}, got={got}")

    # 随机正确性：与 NaiveMatcher 对比
    rng = random.Random(123)
    for name, cls in matchers.items():
        if name == "naive":
            continue
        for _ in range(200):
            cache_size = rng.randint(1, 8)
            cache_len = rng.randint(0, 64)
            cached = [gen_tokens(cache_len, seed=rng.randrange(1 << 30))
                      for _ in range(cache_size)]
            query_len = rng.randint(0, 80)
            prefix_len = rng.randint(0, cache_len)
            query = build_query(cached, query_len, prefix_len,
                                seed=rng.randrange(1 << 30))

            ref = NaiveMatcher()
            for s in cached:
                ref.add(s)
            expected = ref.longest_prefix_len(query)

            m = cls()
            for s in cached:
                m.add(s)
            got = m.longest_prefix_len(query)
            if got == expected:
                passed += 1
            else:
                failed += 1
                print(f"  [FAIL] {name} (random): got {got}, expected {expected}")

    print(f"\n正确性: {passed} passed, {failed} failed")
    return passed, failed


# ---------------------------------------------------------------------------
# 性能基准 1：不同长度
# ---------------------------------------------------------------------------

PERF_CONFIGS = [
    # (cache_size, cache_len, query_len, expected_lcp)
    (4, 1024, 64, 64),
    (4, 1024, 256, 256),
    (4, 1024, 1024, 1024),
    (8, 2048, 512, 512),
    (8, 2048, 2048, 2048),
    (16, 4096, 256, 256),
    (16, 4096, 1024, 1024),
    (32, 4096, 512, 512),
    (32, 8192, 2048, 2048),
    (64, 8192, 1024, 1024),
    (64, 8192, 4096, 4096),
]


def run_perf_benchmark(matchers: dict[str, Callable[[], object]],
                       quick: bool = False) -> None:
    print("\n" + "=" * 70)
    print("== Token 前缀匹配性能基准 (不同 cache_size / cache_len / query_len) ==")
    print("=" * 70)
    print()

    rng = random.Random(2026)
    if quick:
        configs = PERF_CONFIGS[::3]
    else:
        configs = PERF_CONFIGS

    headers = ["config", "lcp"] + [f"{n}(µs)" for n in matchers]
    rows: list[list[str]] = []
    fastest: list[str] = []

    for cache_size, cache_len, query_len, expected_lcp in configs:
        cached = [gen_tokens(cache_len, seed=rng.randrange(1 << 30))
                  for _ in range(cache_size)]
        query = build_query(cached, query_len,
                            prefix_len=min(query_len, cache_len), seed=99)

        ref = NaiveMatcher()
        for s in cached:
            ref.add(s)
        expected = ref.longest_prefix_len(query)
        assert expected == expected_lcp, (
            f"配置 cache={cache_size}x{cache_len}, q={query_len} "
            f"期望 LCP={expected_lcp}，实际 {expected}"
        )

        cost_unit = cache_size * cache_len + query_len
        iters = max(20, min(2000, int(2_000_000 / max(1, cost_unit))))
        if quick:
            iters = max(5, iters // 4)

        row = [f"cache={cache_size}x{cache_len}, q={query_len}", str(expected)]
        times: dict[str, float] = {}
        for name, cls in matchers.items():
            m = cls()
            for s in cached:
                m.add(s)
            got = m.longest_prefix_len(query)
            assert got == expected, f"{name} LCP={got} != {expected}"
            avg_us = bench_one_matcher(m, query, iters)
            times[name] = avg_us
            row.append(f"{avg_us:.2f}")
        rows.append(row)
        best_algo = min(times, key=lambda k: times[k])
        fastest.append(f"  cache={cache_size}x{cache_len}, q={query_len}: "
                       f"{best_algo} ({times[best_algo]:.2f} µs)")

    print_table(headers, rows)
    print()
    print("== 每个配置的最快算法 ==")
    for line in fastest:
        print(line)


# ---------------------------------------------------------------------------
# 性能基准 2：token id 范围影响
# ---------------------------------------------------------------------------

TOKEN_ID_CONFIGS = [
    # (cache_size, cache_len, query_len, vocab)
    (8, 4096, 2048, 256),
    (8, 4096, 2048, 2048),
    (8, 4096, 2048, 128000),
    (8, 4096, 2048, 200000),
    (8, 16384, 8192, 256),
    (8, 16384, 8192, 128000),
    (8, 16384, 8192, 200000),
    (8, 24576, 12288, 256),
    (8, 24576, 12288, 128000),
    (8, 24576, 12288, 200000),
]


def run_token_id_range_benchmark(matchers: dict[str, Callable[[], object]],
                                 quick: bool = False) -> None:
    print("\n" + "=" * 70)
    print("== Token id 范围对前缀匹配性能的影响 ==")
    print("=" * 70)
    print()

    rng = random.Random(2027)
    if quick:
        configs = [TOKEN_ID_CONFIGS[0], TOKEN_ID_CONFIGS[2],
                   TOKEN_ID_CONFIGS[7], TOKEN_ID_CONFIGS[9]]
    else:
        configs = TOKEN_ID_CONFIGS

    headers = ["config", "lcp"] + [f"{n}(µs)" for n in matchers]
    rows: list[list[str]] = []
    results: list[dict[str, object]] = []

    for cache_size, cache_len, query_len, vocab in configs:
        cached = [gen_tokens(cache_len, seed=rng.randrange(1 << 30), vocab=vocab)
                  for _ in range(cache_size)]
        query = build_query(cached, query_len,
                            prefix_len=min(query_len, cache_len),
                            seed=99, vocab=vocab)

        ref = NaiveMatcher()
        for s in cached:
            ref.add(s)
        expected = ref.longest_prefix_len(query)

        cost_unit = cache_size * cache_len + query_len
        iters = max(10, min(1000, int(2_000_000 / max(1, cost_unit))))
        if quick:
            iters = max(5, iters // 4)

        row = [f"cache={cache_size}x{cache_len}, q={query_len}, vocab={vocab}",
               str(expected)]
        times: dict[str, float] = {}
        for name, cls in matchers.items():
            m = cls()
            for s in cached:
                m.add(s)
            got = m.longest_prefix_len(query)
            assert got == expected, f"{name} LCP={got} != {expected}"
            avg_us = bench_one_matcher(m, query, iters)
            times[name] = avg_us
            row.append(f"{avg_us:.2f}")
        rows.append(row)
        results.append({
            "cache_size": cache_size, "cache_len": cache_len,
            "query_len": query_len, "vocab": vocab, "times": times,
        })

    print_table(headers, rows)
    print()

    # 倍率表
    print("== Token id 范围影响倍率 (vocab=200000 / vocab=256) ==")
    print()
    by_len: dict[tuple, dict[int, dict[str, float]]] = {}
    for r in results:
        key = (r["cache_size"], r["cache_len"], r["query_len"])
        by_len.setdefault(key, {})[r["vocab"]] = r["times"]
    for key, by_v in by_len.items():
        if 256 not in by_v or 200000 not in by_v:
            continue
        small = by_v[256]
        big = by_v[200000]
        cs, cl, ql = key
        parts = []
        for algo in matchers:
            s = small[algo]
            b = big[algo]
            ratio = b / s if s > 0 else float("inf")
            parts.append(f"{algo}×{ratio:.2f}")
        print(f"  cache={cs}x{cl}, q={ql}: " + "  ".join(parts))


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"Python: {sys.version.split()[0]}  platform: {sys.platform}")
    print(f"numpy:  {'available' if HAS_NUMPY else 'NOT available (numpy matchers skipped)'}")

    matchers = get_matchers()
    print(f"matchers: {list(matchers.keys())}")

    quick = os.environ.get("QUICK") == "1"
    only = os.environ.get("ONLY", "all").lower()

    print()
    print("#" * 70)
    print("# 正确性测试")
    print("#" * 70)
    _, failed = run_correctness(matchers)
    if failed > 0:
        print(f"\n[!] 正确性测试失败 {failed} 项，终止。")
        return 1

    if only in ("perf", "all"):
        run_perf_benchmark(matchers, quick=quick)

    if only in ("id_range", "all"):
        run_token_id_range_benchmark(matchers, quick=quick)

    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
