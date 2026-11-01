"""Policy retrieval: section-level chunking + BM25, no external services.

The corpus is small and highly structured (numbered policy sections), so the
chunk unit is one section and every hit carries a stable `section_id` that the
resolver must cite. Section ids double as grounding anchors: the policy engine
rejects citations that do not exist, and evals check that the cited sections
are the ones a controller would expect.

BM25 is deliberate. For a few dozen sections with domain vocabulary it is
exact, explainable and dependency-free; `PolicyCorpus.search` is the seam where
a hybrid (BM25 + embeddings + reranker) retriever would slot in for a larger
client corpus.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

POLICY_DIR = Path(__file__).resolve().parent.parent / "domain" / "policies"
HEADER = re.compile(r"^##\s+(AP-POL-\d+\.\d+)\s+[—-]+\s+(.+?)\s*$", re.MULTILINE)
STOPWORDS = frozenset(
    "a an and are as at be by for from has have if in is it its of on or that the this to was were will with "
    "not no do does any all must may can than then when which who whose same each every per".split()
)


def tokenize(text: str) -> list[str]:
    out = []
    for tok in re.findall(r"[a-z0-9]+(?:[._][a-z0-9]+)*", text.lower()):
        if tok in STOPWORDS or len(tok) < 2:
            continue
        if len(tok) > 4 and tok.endswith("ies"):
            tok = tok[:-3] + "y"
        elif len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        out.append(tok)
    return out


@dataclass(frozen=True)
class Chunk:
    section_id: str
    title: str
    document: str
    text: str


@dataclass(frozen=True)
class Hit:
    section_id: str
    title: str
    score: float
    text: str


class PolicyCorpus:
    def __init__(self, chunks: list[Chunk], k1: float = 1.4, b: float = 0.7):
        self.chunks = chunks
        self.by_id = {c.section_id: c for c in chunks}
        self.k1, self.b = k1, b
        self._docs = [tokenize(f"{c.title} {c.title} {c.text}") for c in chunks]
        self._tf = [Counter(d) for d in self._docs]
        self._avgdl = sum(len(d) for d in self._docs) / max(1, len(self._docs))
        df: Counter[str] = Counter()
        for d in self._docs:
            df.update(set(d))
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    @classmethod
    def load(cls, directory: Path = POLICY_DIR) -> PolicyCorpus:
        chunks: list[Chunk] = []
        for path in sorted(directory.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            matches = list(HEADER.finditer(text))
            for i, m in enumerate(matches):
                end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                body = text[m.end():end].strip()
                chunks.append(Chunk(section_id=m.group(1), title=m.group(2), document=path.stem, text=body))
        if not chunks:
            raise RuntimeError(f"no policy sections found in {directory}")
        return cls(chunks)

    def search(self, query: str, k: int = 5) -> list[Hit]:
        q = tokenize(query)
        explicit = set(re.findall(r"AP-POL-\d+\.\d+", query, flags=re.IGNORECASE))
        scored = []
        for i, chunk in enumerate(self.chunks):
            tf, dl = self._tf[i], len(self._docs[i])
            score = 0.0
            for term in q:
                f = tf.get(term)
                if f:
                    score += self._idf.get(term, 0.0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self._avgdl))
            if chunk.section_id.upper() in {e.upper() for e in explicit}:
                score += 100.0
            if score > 0:
                scored.append((score, i))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [
            Hit(self.chunks[i].section_id, self.chunks[i].title, round(s, 3), self.chunks[i].text) for s, i in scored[:k]
        ]

    def get(self, section_id: str) -> Chunk | None:
        return self.by_id.get(section_id.strip().upper())

    def exists(self, section_id: str) -> bool:
        return section_id.strip().upper() in self.by_id
