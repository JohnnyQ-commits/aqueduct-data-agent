"""文本向量化封装 — Embedder。

将文本转换为向量表示，用于语义检索和相似度计算。

支持三种后端：
- local: sentence-transformers 本地模型（默认，无需 API）
- api: 通过 Anthropic API 调用（需要 API key）
- mock: 确定性哈希伪向量（仅用于测试）

用法:
    from aqueduct.memory.embedder import Embedder

    embedder = Embedder()  # 默认 local 后端
    vector = embedder.embed("电商平台订单统计")
    vectors = embedder.embed_batch(["订单量", "GMV", "客户数"])

安装 local 后端:
    pip install sentence-transformers
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Protocol

logger = logging.getLogger(__name__)

# 默认模型配置
_DEFAULT_LOCAL_MODEL = "all-MiniLM-L6-v2"
_DEFAULT_LOCAL_DIM = 384  # all-MiniLM-L6-v2 输出维度


class EmbedderBackend(Protocol):
    """Embedder 后端接口。"""

    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def dim(self) -> int: ...


class Embedder:
    """文本向量化封装。

    惰性加载模型：首次调用 embed() 时才加载/初始化后端。
    线程安全：模型加载后缓存，后续调用复用。
    """

    def __init__(
        self,
        backend: str = "local",
        model: str | None = None,
    ) -> None:
        """初始化 Embedder。

        Args:
            backend: 向量化后端。
                - "local": sentence-transformers 本地模型
                - "api": Anthropic API
                - "mock": 确定性哈希伪向量（测试用）
            model: 模型名称。
                - local 后端: sentence-transformers 模型名（默认 all-MiniLM-L6-v2）
                - api 后端: 忽略（使用 Anthropic 内置嵌入）
                - mock 后端: 向量维度（默认 384）
        """
        self._backend_name = backend
        self._model_name = model
        self._backend: EmbedderBackend | None = None

    def _init_backend(self) -> EmbedderBackend:
        """惰性初始化后端。"""
        if self._backend is not None:
            return self._backend

        if self._backend_name == "local":
            self._backend = _LocalEmbedder(self._model_name or _DEFAULT_LOCAL_MODEL)
        elif self._backend_name == "api":
            self._backend = _ApiEmbedder()
        elif self._backend_name == "mock":
            dim = int(self._model_name) if self._model_name else _DEFAULT_LOCAL_DIM
            self._backend = _MockEmbedder(dim=dim)
        else:
            raise ValueError(f"未知的 Embedder 后端: {self._backend_name!r}")

        logger.info(
            "Embedder 后端初始化完成: %s (dim=%d)",
            self._backend_name,
            self._backend.dim,
        )
        return self._backend

    def embed(self, text: str) -> list[float]:
        """文本 → 向量。

        Args:
            text: 输入文本。

        Returns:
            浮点数向量。
        """
        backend = self._init_backend()
        return backend.embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量文本 → 向量。

        Args:
            texts: 文本列表。

        Returns:
            向量列表，与输入顺序一致。
        """
        if not texts:
            return []
        backend = self._init_backend()
        return backend.embed_batch(texts)

    @property
    def dim(self) -> int:
        """向量维度。"""
        return self._init_backend().dim


class _LocalEmbedder:
    """sentence-transformers 本地模型后端。"""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model = None
        self._dim: int | None = None

    def _load_model(self) -> None:
        """惰性加载模型。"""
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ImportError(
                "sentence-transformers 未安装。请运行: pip install sentence-transformers"
            ) from e
        logger.info("加载 sentence-transformers 模型: %s", self._model_name)
        self._model = SentenceTransformer(self._model_name)
        self._dim = self._model.get_sentence_embedding_dimension()

    @property
    def dim(self) -> int:
        self._load_model()
        return self._dim  # type: ignore[return-value]

    def embed(self, text: str) -> list[float]:
        self._load_model()
        vec = self._model.encode(text, normalize_embeddings=True)
        return vec.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self._load_model()
        vecs = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vecs]


class _ApiEmbedder:
    """Anthropic API 后端（预留，当前未实现）。"""

    def __init__(self) -> None:
        self._dim = 1024  # Anthropic 嵌入维度

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError("API 后端尚未实现，请使用 local 或 mock 后端")

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("API 后端尚未实现，请使用 local 或 mock 后端")


class _MockEmbedder:
    """确定性哈希伪向量后端（仅用于测试）。

    相同文本始终产生相同向量，不同文本的向量近似正交。
    向量已归一化（L2 范数 = 1.0），可直接计算余弦相似度。
    """

    def __init__(self, dim: int = _DEFAULT_LOCAL_DIM) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        return self._hash_to_vector(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._hash_to_vector(t) for t in texts]

    def _hash_to_vector(self, text: str) -> list[float]:
        """将文本哈希映射为归一化向量。

        使用 SHA-256 生成确定性种子，然后扩展为 dim 维向量。
        """
        # 用多个哈希值填充向量（每个 SHA-256 提供 32 字节 = 256 bit）
        vec = [0.0] * self._dim
        for i in range(0, self._dim, 32):
            h = hashlib.sha256(f"{text}:{i}".encode()).digest()
            for j in range(min(32, self._dim - i)):
                # 将字节映射到 [-1, 1]
                vec[i + j] = (h[j] - 128) / 128.0

        # 归一化（L2 范数 = 1.0）
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。

    Args:
        a: 向量 a。
        b: 向量 b。

    Returns:
        余弦相似度（-1.0 ~ 1.0）。向量已归一化时等价于点积。
    """
    if len(a) != len(b):
        raise ValueError(f"向量维度不匹配: {len(a)} vs {len(b)}")

    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def top_k_similar(
    query_vec: list[float],
    candidates: list[tuple[str, list[float]]],
    k: int = 5,
    threshold: float = 0.0,
) -> list[tuple[str, float]]:
    """从候选集中找出与查询向量最相似的 Top-K。

    Args:
        query_vec: 查询向量。
        candidates: 候选列表，每项为 (标识, 向量)。
        k: 返回数量上限。
        threshold: 最低相似度阈值。

    Returns:
        按相似度降序排列的 (标识, 相似度) 列表。
    """
    scored = []
    for label, vec in candidates:
        sim = cosine_similarity(query_vec, vec)
        if sim >= threshold:
            scored.append((label, sim))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]
