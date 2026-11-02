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


CLIENTS = {OpenAIEmbeddingClient.model: OpenAIEmbeddingClient}
DEFAULT_MODEL = OpenAIEmbeddingClient.model


def get_client(model: str) -> EmbeddingClient:
    if model not in CLIENTS:
        raise ValueError(f"unknown embedding model {model!r}; known: {sorted(CLIENTS)}")
    return CLIENTS[model]()
