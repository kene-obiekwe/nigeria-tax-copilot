# Diagrams

`architecture.mmd` is the one source of the architecture diagram, written in Mermaid. There is no rendered
image: GitHub renders the same block inside [`docs/reference/architecture.md`](../docs/reference/architecture.md).
Outside GitHub the block appears as Mermaid source text.

To change the diagram, edit `architecture.mmd`, then paste the same text into the `mermaid` block of
`architecture.md`. `tests/test_docs.py` checks that the two are equal and that every file or folder path named
in a node exists in the repository.
