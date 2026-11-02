"""Embedding clients behind one small interface (vector store design, section 6).

A client has a `model` key (the value stored in chunk_embeddings.model), a width `dims`, and two
methods: `embed_documents` for chunk inputs and `embed_query` for questions. A model that needs a
query prefix (bge, Phase 4) applies it inside its own client. `tokens_used` and `requests` count
what the client has sent, from the provider's own usage figures.

The OpenAI key is read from .env (EMBEDDING_API_KEY) and passed straight to the SDK. It is never
printed or logged, and provider errors are re-raised without their message, which can quote part
of the key.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


class EmbeddingError(RuntimeError):
    """An embedding call failed. The message names the error type and status only."""


class EmbeddingClient(Protocol):
    model: str
    dims: int
    usd_per_million_tokens: float
    tokens_used: int
    requests: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class OpenAIEmbeddingClient:
    """`text-embedding-3-small` (D-03): 1536 dimensions, $0.02 per 1M input tokens (2026-10-18)."""

    model = "openai/text-embedding-3-small"
    api_model = "text-embedding-3-small"
    dims = 1536
    usd_per_million_tokens = 0.02

    def __init__(self, api_key: str | None = None, *, http_client=None, max_retries: int = 5,
                 timeout: float = 60.0):
        from openai import OpenAI

        if api_key is None:
            load_dotenv(ROOT / ".env")
            api_key = os.getenv("EMBEDDING_API_KEY")
        if not api_key:
            raise EmbeddingError("EMBEDDING_API_KEY is not set in .env")
        # The SDK retries 429, 5xx, connection errors and timeouts with exponential backoff.
        self._client = OpenAI(api_key=api_key, max_retries=max_retries, timeout=timeout,
                              http_client=http_client)
        self.tokens_used = 0
        self.requests = 0

    def __repr__(self) -> str:
        return f"OpenAIEmbeddingClient(model={self.model!r})"

    def _embed(self, texts: list[str]) -> list[list[float]]:
        import openai

        try:
            response = self._client.embeddings.create(model=self.api_model, input=texts,
                                                      encoding_format="float")
        except openai.OpenAIError as exc:
            status = getattr(exc, "status_code", None)
            raise EmbeddingError(f"{type(exc).__name__}" + (f" (HTTP {status})" if status else "")) from None
        self.requests += 1
        self.tokens_used += response.usage.prompt_tokens
        data = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in data] != list(range(len(texts))):
            raise EmbeddingError(f"expected {len(texts)} embeddings in input order, got {len(data)}")
        vectors = [list(item.embedding) for item in data]
        if any(len(vector) != self.dims for vector in vectors):
            raise EmbeddingError(f"expected {self.dims} dimensions")
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


class BgeSmallEmbeddingClient:
    """`BAAI/bge-small-en-v1.5` (D-03 local baseline): 384 dimensions, runs on this machine, free.

    Needs requirements-eval.txt (sentence-transformers and torch), which the deployed image does
    not install. The weights are downloaded once from Hugging Face at a pinned revision. Queries
    take the model card's retrieval instruction; passages are embedded as they are. Vectors are
    L2-normalised, so cosine distance is unchanged. The input limit is 512 tokens and inputs are
    never silently truncated: a longer input is an error (every chunk is within it, Phase 2b).
    """

    model = "BAAI/bge-small-en-v1.5"
    revision = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
    dims = 384
    max_tokens = 512
    query_prefix = "Represent this sentence for searching relevant passages: "
    usd_per_million_tokens = 0.0

    def __init__(self, *, device: str = "cpu", batch_size: int = 32):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.model, revision=self.revision, device=device)
        self._model.max_seq_length = self.max_tokens
        self._batch_size = batch_size
        self.tokens_used = 0
        self.requests = 0

    def __repr__(self) -> str:
        return f"BgeSmallEmbeddingClient(model={self.model!r})"

    def _embed(self, texts: list[str]) -> list[list[float]]:
        lengths = [len(ids) for ids in self._model.tokenizer(texts)["input_ids"]]
        if max(lengths) > self.max_tokens:
            raise EmbeddingError(f"input of {max(lengths)} tokens exceeds the {self.max_tokens}-token limit")
        vectors = self._model.encode(texts, batch_size=self._batch_size, normalize_embeddings=True,
                                     convert_to_numpy=True, show_progress_bar=False)
        self.requests += 1
        self.tokens_used += sum(lengths)
        if vectors.shape != (len(texts), self.dims):
            raise EmbeddingError(f"expected {len(texts)} x {self.dims} embeddings, got {vectors.shape}")
        return [[float(x) for x in vector] for vector in vectors]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self.query_prefix + text])[0]


CLIENTS = {OpenAIEmbeddingClient.model: OpenAIEmbeddingClient,
           BgeSmallEmbeddingClient.model: BgeSmallEmbeddingClient}
DEFAULT_MODEL = OpenAIEmbeddingClient.model


def get_client(model: str) -> EmbeddingClient:
    if model not in CLIENTS:
        raise ValueError(f"unknown embedding model {model!r}; known: {sorted(CLIENTS)}")
    return CLIENTS[model]()
