"""Token 前缀匹配算法的正确性校验与性能基准。

运行：
    pytest tests/test_prefix_match_perf.py -s

性能测试会打印一张表格，对比不同 cache_size / cache_len / query_len 组合下
各算法的平均单次查询耗时。
"""

from __future__ import annotations

import random
import time

import pytest

from gateway.runtime.prefix_match import (
    MATCHERS,
    NaiveMatcher,
    NumpyMatcher,
    NumpyPaddedMatcher,
    TrieMatcher,
)

VOCAB = 128000


def _gen_tokens(n: int, seed: int, vocab: int = VOCAB) -> list[int]:
    rng = random.Random(seed)
    return [rng.randint(0, vocab - 1) for _ in range(n)]


def _build_query(
    cached: list[list[int]],
    query_len: int,
    prefix_len: int,
    seed: int,
    vocab: int = VOCAB,
) -> list[int]:
    """构造一条 query，与 cached[0] 共享长度为 prefix_len 的前缀，其后随机。"""
    rng = random.Random(seed)
    if prefix_len > len(cached[0]):
        prefix_len = len(cached[0])
    head = cached[0][:prefix_len]
    tail_len = max(0, query_len - prefix_len)
    tail = [rng.randint(0, vocab - 1) for _ in range(tail_len)]
    return head + tail


# ---------------------------------------------------------------------------
# 正确性测试
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("matcher_name", list(MATCHERS.keys()))
def test_correctness_basic(matcher_name: str) -> None:
    cls = MATCHERS[matcher_name]
    m = cls()
    m.add([1, 2, 3, 4, 5])
    m.add([1, 2, 9, 9])
    m.add([7, 8, 9])
    assert m.longest_prefix_len([1, 2, 3, 0, 0]) == 3
    assert m.longest_prefix_len([1, 2, 9, 9, 5]) == 4
    assert m.longest_prefix_len([7, 8, 9, 0]) == 3
    assert m.longest_prefix_len([99, 99]) == 0
    assert m.longest_prefix_len([]) == 0


@pytest.mark.parametrize("matcher_name", list(MATCHERS.keys()))
def test_correctness_empty_cache(matcher_name: str) -> None:
    cls = MATCHERS[matcher_name]
    m = cls()
    assert m.longest_prefix_len([1, 2, 3]) == 0


@pytest.mark.parametrize("matcher_name", list(MATCHERS.keys()))
def test_correctness_full_match(matcher_name: str) -> None:
    cls = MATCHERS[matcher_name]
    m = cls()
    m.add([10, 20, 30])
    assert m.longest_prefix_len([10, 20, 30]) == 3
    assert m.longest_prefix_len([10, 20, 30, 40]) == 3


@pytest.mark.parametrize("matcher_name", list(MATCHERS.keys()))
def test_correctness_against_naive_random(matcher_name: str) -> None:
    """与 NaiveMatcher 在大量随机用例上结果一致。"""
    if matcher_name == "naive":
        pytest.skip("naive is the reference")
    cls = MATCHERS[matcher_name]
    rng = random.Random(123)
    for _ in range(200):
        cache_size = rng.randint(1, 8)
        cache_len = rng.randint(0, 64)
        cached = [_gen_tokens(cache_len, seed=rng.randrange(1 << 30)) for _ in range(cache_size)]
        query_len = rng.randint(0, 80)
        prefix_len = rng.randint(0, cache_len)
        query = _build_query(cached, query_len, prefix_len, seed=rng.randrange(1 << 30))

        ref = NaiveMatcher()
        for s in cached:
            ref.add(s)
        expected = ref.longest_prefix_len(query)

        m = cls()
        for s in cached:
            m.add(s)
        got = m.longest_prefix_len(query)
        assert got == expected, f"{matcher_name}: got {got}, expected {expected}"


# ---------------------------------------------------------------------------
# 性能基准
# ---------------------------------------------------------------------------

# (cache_size, cache_len, query_len, expected_prefix_len)
# 覆盖短/中/长 query，小/中/大 cache 规模
PERF_CONFIGS = [
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


def _bench_one_matcher(m, query: list[int], iters: int) -> float:
    # 预热一次（触发 numpy 内部缓存）
    m.longest_prefix_len(query)
    t0 = time.perf_counter_ns()
    for _ in range(iters):
        m.longest_prefix_len(query)
    t1 = time.perf_counter_ns()
    return (t1 - t0) / iters / 1000.0  # µs / 次


def test_prefix_match_performance_benchmark(capsys: pytest.CaptureFixture[str]) -> None:
    rng = random.Random(2026)
    results: list[dict[str, object]] = []

    for cache_size, cache_len, query_len, expected_lcp in PERF_CONFIGS:
        cached = [_gen_tokens(cache_len, seed=rng.randrange(1 << 30)) for _ in range(cache_size)]
        # query 与 cached[0] 完全共享前缀，以测试最长匹配路径
        query = _build_query(cached, query_len, prefix_len=min(query_len, cache_len), seed=99)

        row: dict[str, object] = {
            "config": f"cache={cache_size}x{cache_len}, q={query_len}",
            "lcp": 0,
        }

        # 以 naive 为正确性基准
        ref = NaiveMatcher()
        for s in cached:
            ref.add(s)
        expected = ref.longest_prefix_len(query)
        row["lcp"] = expected
        assert expected == expected_lcp, (
            f"配置 {row['config']} 期望 LCP={expected_lcp}，实际 {expected}"
        )

        # 估算迭代次数：让每组总耗时大致 ~100ms
        cost_unit = cache_size * cache_len + query_len
        iters = max(20, min(2000, int(2_000_000 / max(1, cost_unit))))

        for name, cls in MATCHERS.items():
            m = cls()
            for s in cached:
                m.add(s)
            got = m.longest_prefix_len(query)
            assert got == expected, f"{name} LCP={got} != {expected}"
            avg_us = _bench_one_matcher(m, query, iters)
            row[name] = avg_us

        results.append(row)

    # 打印结果表格
    headers = ["config", "lcp", "naive(µs)", "trie(µs)", "numpy(µs)", "numpy_padded(µs)"]
    lines = ["", "== Token 前缀匹配性能基准 ==", ""]
    lines.append(" | ".join(h.rjust(16) for h in headers))
    lines.append("-+-".join("-" * 16 for _ in headers))
    for r in results:
        cells = [
            str(r["config"]).rjust(16),
            str(r["lcp"]).rjust(16),
            f"{r['naive']:.2f}".rjust(16),
            f"{r['trie']:.2f}".rjust(16),
            f"{r['numpy']:.2f}".rjust(16),
            f"{r['numpy_padded']:.2f}".rjust(16),
        ]
        lines.append(" | ".join(cells))
    lines.append("")
    # 找出每个配置下的最快算法
    fastest_lines = ["== 每个配置的最快算法 ==", ""]
    algo_names = ["naive", "trie", "numpy", "numpy_padded"]
    for r in results:
        best_algo = min(algo_names, key=lambda k: r[k])  # type: ignore[index]
        fastest_lines.append(f"  {r['config']}: {best_algo} ({r[best_algo]:.2f} µs)")  # type: ignore[index]
    fastest_lines.append("")

    out = "\n".join(lines) + "\n" + "\n".join(fastest_lines)
    print(out)
    with capsys.disabled():
        print(out)


# ---------------------------------------------------------------------------
# Token id 范围对性能的影响
# ---------------------------------------------------------------------------
#
# 理论分析：
#   - numpy:   int64 比较恒定时间，不应受 token id 大小影响。
#   - naive:   Python 对 [-5, 256] 的小 int 有缓存，== 直接比指针；大 id 是
#              堆对象，需走真正的值比较，预期更慢。
#   - trie:    dict 对小 int 的 hash 已被缓存；大 int 是堆对象，hash 需要计算，
#              预期更慢。
#
# 控制变量：固定 cache_size / cache_len / query_len，只变化 vocab。
# 同时把长度拉到 24k，模拟长上下文场景。

# (cache_size, cache_len, query_len, vocab)
# vocab 取 256 (小 int 缓存边界)、2048、128000 (Qwen 词表规模)、200000 (超过常见词表)
TOKEN_ID_CONFIGS = [
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


def test_token_id_range_impact_benchmark(capsys: pytest.CaptureFixture[str]) -> None:
    rng = random.Random(2027)
    results: list[dict[str, object]] = []

    for cache_size, cache_len, query_len, vocab in TOKEN_ID_CONFIGS:
        cached = [
            _gen_tokens(cache_len, seed=rng.randrange(1 << 30), vocab=vocab)
            for _ in range(cache_size)
        ]
        query = _build_query(
            cached,
            query_len,
            prefix_len=min(query_len, cache_len),
            seed=99,
            vocab=vocab,
        )

        # 正确性基准
        ref = NaiveMatcher()
        for s in cached:
            ref.add(s)
        expected = ref.longest_prefix_len(query)

        row: dict[str, object] = {
            "config": f"cache={cache_size}x{cache_len}, q={query_len}, vocab={vocab}",
            "lcp": expected,
            "cache_size": cache_size,
            "cache_len": cache_len,
            "query_len": query_len,
            "vocab": vocab,
        }

        cost_unit = cache_size * cache_len + query_len
        iters = max(10, min(1000, int(2_000_000 / max(1, cost_unit))))

        for name, cls in MATCHERS.items():
            m = cls()
            for s in cached:
                m.add(s)
            got = m.longest_prefix_len(query)
            assert got == expected, f"{name} LCP={got} != {expected}"
            avg_us = _bench_one_matcher(m, query, iters)
            row[name] = avg_us

        results.append(row)

    headers = [
        "config",
        "lcp",
        "naive(µs)",
        "trie(µs)",
        "numpy(µs)",
        "numpy_padded(µs)",
    ]
    lines = ["", "== Token id 范围对前缀匹配性能的影响 ==", ""]
    lines.append(" | ".join(h.rjust(22) for h in headers))
    lines.append("-+-".join("-" * 22 for _ in headers))
    for r in results:
        cells = [
            str(r["config"]).rjust(22),
            str(r["lcp"]).rjust(22),
            f"{r['naive']:.2f}".rjust(22),
            f"{r['trie']:.2f}".rjust(22),
            f"{r['numpy']:.2f}".rjust(22),
            f"{r['numpy_padded']:.2f}".rjust(22),
        ]
        lines.append(" | ".join(cells))
    lines.append("")

    # 对比同一组长度下，最大 vocab vs 最小 vocab 的耗时变化
    lines.append("== Token id 范围影响倍率 (vocab=200000 / vocab=256) ==")
    lines.append("")
    by_len: dict[tuple[int, int, int], dict[int, dict[str, float]]] = {}
    for r in results:
        key = (int(r["cache_size"]), int(r["cache_len"]), int(r["query_len"]))  # type: ignore[index]
        v = int(r["vocab"])  # type: ignore[index]
        by_len.setdefault(key, {})[v] = {
            "naive": r["naive"],  # type: ignore[index]
            "trie": r["trie"],    # type: ignore[index]
            "numpy": r["numpy"],  # type: ignore[index]
            "numpy_padded": r["numpy_padded"],  # type: ignore[index]
        }
    for key, by_v in by_len.items():
        if 256 not in by_v or 200000 not in by_v:
            continue
        small = by_v[256]
        big = by_v[200000]
        cs, cl, ql = key
        line = f"  cache={cs}x{cl}, q={ql}: "
        for algo in ["naive", "trie", "numpy", "numpy_padded"]:
            ratio = big[algo] / small[algo] if small[algo] > 0 else float("inf")
            line += f"{algo}×{ratio:.2f}  "
        lines.append(line)
    lines.append("")

    out = "\n".join(lines)
    print(out)
    with capsys.disabled():
        print(out)
