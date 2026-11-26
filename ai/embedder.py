"""embedder：把文字轉成向量（embedding），新聞 RAG 用。本機跑 EMBED_MODEL（bge-m3），不呼叫外部 API、不花錢。

  embed(texts)：一批文字 → 一批向量（長度都是 1，所以 cosine 相似度＝內積）
  to_pgvector(vector)、from_pgvector(text)：向量 ↔ pgvector 的寫法 "[0.1,0.2,…]"

模型第一次用時從 Hugging Face 下載（約 2.3 GB），存在 HF_HOME（容器裡是 /app/data/hf，重建 image 不用再下載）。
一個行程只載入一次（get_model 有快取）；只有 ai worker（celery-ai）載入模型，其他 worker 不會多佔一份記憶體。
"""
import functools
import json

from common.config import EMBED_MODEL


@functools.cache
def get_model():
    """載入模型（大、慢，只載一次）。sentence_transformers 在這裡才 import：它會載入 torch，只有轉向量的任務需要。"""
    import sentence_transformers
    return sentence_transformers.SentenceTransformer(EMBED_MODEL, device="cpu")


def embed(texts: list[str]) -> list[list[float]]:
    """一批文字 → 一批向量（normalize：長度 1）。"""
    if not texts:
        return []
    return get_model().encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False).tolist()


def to_pgvector(vector: list[float]) -> str:
    """
    >>> to_pgvector([0.5, -0.25])
    '[0.5,-0.25]'
    """
    return "[" + ",".join(str(float(x)) for x in vector) + "]"


def from_pgvector(text: str) -> list[float]:
    """
    >>> from_pgvector("[0.5,-0.25]")
    [0.5, -0.25]
    """
    return json.loads(text)
