"""Token 前缀匹配算法集合，用于 KV cache 复用时的最长公共前缀查找。

提供四种实现用于性能对比：
- NaiveMatcher:    纯 Python 逐元素比较，O(N*M)
- TrieMatcher:     字典树，单次查询 O(L_query)，与缓存数量无关
- NumpyMatcher:    逐序列 numpy 向量化比较，SIMD 加速
- NumpyPaddedMatcher: 2D 填充后一次性向量化比较，单次 numpy 调用
"""

from __future__ import annotations

import numpy as np


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
    """字典树。查询复杂度仅与 query 长度相关，适合缓存序列较长的场景。"""

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
    """逐序列 numpy 向量化比较。每次比较走 SIMD，但每条缓存一次 numpy 调用。"""

    def __init__(self) -> None:
        self._seqs: list[np.ndarray] = []

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
    """将所有缓存序列填充到等长后，一次性 2D 向量化比较。

    适合缓存序列长度相近、数量适中的场景，单次 numpy 调用完成所有比较。
    """

    def __init__(self) -> None:
        self._seqs: list[np.ndarray] = []
        self._pad: np.ndarray = np.empty((0, 0), dtype=np.int64)
        self._lens: np.ndarray = np.empty((0,), dtype=np.int64)
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

        cmp = self._pad[:, :n] == q[:n]            # [N, n] 位置是否相等
        # 超过序列实际长度的位置视为不匹配
        mask = np.arange(n)[None, :] < self._lens[:, None]
        eff = cmp & mask
        # 末尾补一列 False，使 argmin 一定命中一个 False
        aug = np.empty((eff.shape[0], n + 1), dtype=bool)
        aug[:, :n] = eff
        aug[:, n] = False
        first_false = np.argmin(aug, axis=1)       # [N] 第一个 False 位置
        lcp = np.minimum(first_false, self._lens)
        return int(lcp.max())


MATCHERS = {
    "naive": NaiveMatcher,
    "trie": TrieMatcher,
    "numpy": NumpyMatcher,
    "numpy_padded": NumpyPaddedMatcher,
}
