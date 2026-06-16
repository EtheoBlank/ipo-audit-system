"""文本向量化 (Embedder).

提供 3 种 provider：
  - ``tfidf``    本地 TF-IDF 字符 n-gram (无外部依赖；冷启动可用，效果最弱)
  - ``minimax``  调用 MiniMax embedding API
  - ``deepseek`` 调用 DeepSeek embedding API (兼容 OpenAI 协议)

向量统一以 JSON 字符串保存到 ``KnowledgeChunk.embedding``。检索时一次性
读出全表 → numpy 计算 cosine — 万级 chunk 在 sub-second 内可完成。

为什么 TF-IDF 作为默认：
  - 用户不一定配了远端 API
  - 中文实务案例检索里，关键词 + n-gram 已经能给出可用结果
  - 配 API 后可以一键重建向量，不影响数据模型
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from typing import Iterable, List, Sequence

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# TF-IDF (本地)
# ----------------------------------------------------------------------


_TFIDF_NGRAM = (2, 3)  # 中文 2-3gram 字符
_TFIDF_TOP_TERMS = 4096  # 词表上限
_TFIDF_VERSION = "tfidf-char-v1"


def _tokenize(text: str) -> List[str]:
    """字符级 n-gram 分词 — 对中文无需分词器。"""
    text = re.sub(r"\s+", "", text)
    tokens: List[str] = []
    for n in range(_TFIDF_NGRAM[0], _TFIDF_NGRAM[1] + 1):
        if len(text) < n:
            continue
        tokens.extend(text[i : i + n] for i in range(len(text) - n + 1))
    # 英文/数字单词也保留 (作为额外特征)
    tokens.extend(re.findall(r"[A-Za-z0-9]{2,}", text))
    return tokens


class TfidfEmbedder:
    """词表 + idf 的轻量实现，无 sklearn 依赖。

    使用方法：
      embedder = TfidfEmbedder()
      embedder.fit(corpus)
      vecs = embedder.transform(["xxx"])
    """

    model_name = _TFIDF_VERSION

    def __init__(self) -> None:
        self.vocab: dict[str, int] = {}
        self.idf: List[float] = []
        self.dim: int = 0

    # — fit/transform —

    # P0 安全修复: 跨书共享词表, 防止 cosine=0
    def fit(self, corpus: Sequence[str]) -> None:
        df: Counter[str] = Counter()
        n_doc = len(corpus) or 1
        for doc in corpus:
            terms = set(_tokenize(doc))
            for t in terms:
                df[t] += 1
        # 取最高频前 N 当词表
        top = [t for t, _ in df.most_common(_TFIDF_TOP_TERMS)]
        self.vocab = {t: i for i, t in enumerate(top)}
        self.idf = [math.log((1 + n_doc) / (1 + df[t])) + 1.0 for t in top]
        self.dim = len(self.vocab)
        logger.info("TF-IDF 拟合完成：词表 %d，文档 %d", self.dim, n_doc)

    def transform(self, texts: Iterable[str]) -> List[List[float]]:
        if not self.vocab:
            raise RuntimeError("Embedder 未拟合，请先 fit() 或 load() 词表")
        out: List[List[float]] = []
        for t in texts:
            tf = Counter(_tokenize(t))
            vec = [0.0] * self.dim
            length = sum(tf.values()) or 1
            for term, c in tf.items():
                idx = self.vocab.get(term)
                if idx is None:
                    continue
                vec[idx] = (c / length) * self.idf[idx]
            # L2 normalize 便于直接点积当 cosine
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out

    # — 序列化 —

    def to_state(self) -> dict:
        return {"vocab": self.vocab, "idf": self.idf, "model": self.model_name}

    @classmethod
    def from_state(cls, state: dict) -> "TfidfEmbedder":
        emb = cls()
        emb.vocab = state.get("vocab", {})
        emb.idf = state.get("idf", [])
        emb.dim = len(emb.vocab)
        return emb


# ----------------------------------------------------------------------
# 远端 API embedder
# ----------------------------------------------------------------------


class MinimaxEmbedder:
    """MiniMax embedding API 封装。

    MiniMax 的 embedding 接口为 ``POST /embeddings``；接口对一次请求的文本数有
    上限 (~32 条)，所以这里自动分批。
    """

    def __init__(self) -> None:
        self.api_key = settings.MINIMAX_API_KEY
        self.api_base = settings.MINIMAX_API_BASE
        self.model = settings.KB_EMBEDDING_MODEL
        self.dim = settings.KB_EMBEDDING_DIM
        if not self.api_key:
            raise RuntimeError("MINIMAX_API_KEY 未配置，无法使用 MiniMax embedding")

    @property
    def model_name(self) -> str:
        return f"minimax:{self.model}"

    async def aembed(self, texts: Sequence[str]) -> List[List[float]]:
        out: List[List[float]] = []
        async with httpx.AsyncClient(timeout=60) as client:
            for i in range(0, len(texts), 16):
                batch = list(texts[i : i + 16])
                resp = await client.post(
                    f"{self.api_base.rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "texts": batch, "type": "db"},
                )
                resp.raise_for_status()
                data = resp.json()
                vectors = data.get("vectors") or [d["embedding"] for d in data.get("data", [])]
                out.extend(vectors)
        return out


class DeepSeekEmbedder:
    def __init__(self) -> None:
        self.api_key = settings.DEEPSEEK_API_KEY
        self.api_base = settings.DEEPSEEK_API_BASE
        self.model = settings.KB_EMBEDDING_MODEL
        self.dim = settings.KB_EMBEDDING_DIM
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY 未配置")

    @property
    def model_name(self) -> str:
        return f"deepseek:{self.model}"

    async def aembed(self, texts: Sequence[str]) -> List[List[float]]:
        out: List[List[float]] = []
        async with httpx.AsyncClient(timeout=60) as client:
            for i in range(0, len(texts), 16):
                batch = list(texts[i : i + 16])
                resp = await client.post(
                    f"{self.api_base.rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"input": batch, "model": self.model},
                )
                resp.raise_for_status()
                data = resp.json()
                out.extend(d["embedding"] for d in data.get("data", []))
        return out


# ----------------------------------------------------------------------
# 工厂
# ----------------------------------------------------------------------


def vec_to_json(vec: List[float]) -> str:
    return json.dumps([round(v, 6) for v in vec], ensure_ascii=False)


def json_to_vec(s: str | None) -> List[float]:
    if not s:
        return []
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return []


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)
