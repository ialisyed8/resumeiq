"""BM25 lexical retrieval — cheap coarse relevance before embeddings."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.]*")
STOPWORDS = frozenset(["a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "at", "for", "with", "by", "from", "as", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did", "will", "would", "shall", "should", "may", "might", "can", "could", "this", "that", "these", "those", "it", "its", "i", "we", "you", "they", "he", "she", "our", "your", "their", "my", "me", "us", "them"])

K1 = 1.5
B = 0.75


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall((text or "").lower()) if t not in STOPWORDS]


@dataclass(slots=True)
class BM25:
    """Small in-memory BM25. Corpus here is one resume's chunks, so this is fast."""

    documents: list[list[str]] = field(default_factory=list)
    _df: Counter = field(default_factory=Counter)
    _avg_len: float = 0.0

    @classmethod
    def build(cls, texts: list[str]) -> BM25:
        docs = [tokenize(t) for t in texts]
        index = cls(documents=docs)
        for doc in docs:
            for term in set(doc):
                index._df[term] += 1
        index._avg_len = (sum(len(d) for d in docs) / len(docs)) if docs else 0.0
        return index

    def _idf(self, term: str) -> float:
        n = len(self.documents)
        df = self._df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def score(self, query: str, doc_index: int) -> float:
        doc = self.documents[doc_index]
        if not doc:
            return 0.0
        counts = Counter(doc)
        length = len(doc)
        total = 0.0
        for term in tokenize(query):
            tf = counts.get(term, 0)
            if not tf:
                continue
            denom = tf + K1 * (1 - B + B * length / (self._avg_len or 1))
            total += self._idf(term) * (tf * (K1 + 1)) / denom
        return total

    def top_k(self, query: str, k: int = 5) -> list[tuple[int, float]]:
        scored = [(i, self.score(query, i)) for i in range(len(self.documents))]
        scored = [(i, s) for i, s in scored if s > 0]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]
