# Notice

This project is provided for research and demonstration purposes only.

- It is not an interface to any real tax filing system and has no connection to the Nigeria Revenue
  Service (formerly FIRS) or any other authority.
- It is not designed to process real taxpayer data. Users are asked not to enter it, and a screen blocks
  email addresses, phone numbers and long numbers before anything is sent or stored. The public demo stores
  each question, the answer and how it was checked for 90 days, as the privacy section of the
  [README](README.md#privacy) says.
- Its output is not tax, legal or financial advice.

The code and the project's own documents are licensed under the MIT licence; see [LICENSE](LICENSE).

The statute texts in `data/raw/` and their transcriptions (the chunks, OCR text and proofread text under
`data/derived/` and `data/curated/`) are **not licensed by the project**. They remain the work of their
official publishers and are redistributed with their source and hash, for verification and reproduction
only. The embedding vectors published as release assets of tag `corpus-2026-10-18` are derived from those
texts by the OpenAI embeddings API and are published for reproduction only.

Third-party file: `python/ingest/assets/bge-small-en-v1.5.tokenizer.json` is the tokenizer of
BAAI/bge-small-en-v1.5 (MIT licence), from <https://huggingface.co/BAAI/bge-small-en-v1.5>. It is
used only to count tokens for the 512-token baseline check.
