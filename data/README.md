# Data

`data/raw/` holds the source statutory texts. Raw data is **immutable**: files are never edited in
place, and each is pinned with a SHA-256 hash once acquired. Derived data (chunks, embeddings) is
rebuilt from raw by scripts and is not stored here.

The source-document inventory (official title, source, edition or amendment date, acquisition date,
hash, known gaps) will be written in Phase 1, before any document is chunked or embedded.

No documents have been acquired yet.
