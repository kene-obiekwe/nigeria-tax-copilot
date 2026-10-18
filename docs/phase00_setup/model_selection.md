# LLM and Embedding Model Selection (D-03)

Status: **recommendation for Kene to approve.** The embedding choice is provisional until the
Phase 4 hit-rate results.

All prices are USD per 1M tokens, taken from the providers' official pricing pages on
**2026-10-18** (access date). Standard tier, no batch or cache discounts. Prices change; re-check
before relying on them.

## 1. Hosted LLMs

| Model | Input | Output | Source |
|---|---|---|---|
| Claude Sonnet 5.5 (`claude-sonnet-5-5`) | $2.00 | $10.00 | <https://platform.claude.com/docs/en/about-claude/pricing> |
| Claude Haiku 5.5 (`claude-haiku-5-5`, prompts up to 100k tokens) | $0.10 | $0.50 | same page |
| GPT-6 Luna (`gpt-6-luna`) | $0.10 | $0.50 | <https://developers.openai.com/api/docs/pricing> |
| Gemini 3.5 Flash-Lite | $0.30 | $2.50 | <https://ai.google.dev/gemini-api/docs/pricing> |

Notes:

- Google's page says some Gemini prices (3.6 Flash, 3.8 Flash, 4 Argon) rise on 1 December 2026 or
  1 January 2027. The Flash-Lite price shown is the amount currently listed.
- `openai.com/api/pricing` returned HTTP 403, so the OpenAI prices come from
  `developers.openai.com`. The page was read through a summarising fetch tool, not a browser. Check
  the figures against the page by eye before committing budget to OpenAI.
- Tool calling: all three vendors offer a tool/function-calling API. I did not re-verify the
  current tool-calling documentation in this task; confirm it in Phase 7 before relying on it.
- **Quality has not been measured.** The prices above say nothing about answer quality on Nigerian
  statutory text. Quality is decided by the Phase 5 to 8 results, which is why the LLM is
  swappable behind one small interface.

## 2. Embedding options

| Model | Price (input) | Dimension | Max input | Notes |
|---|---|---|---|---|
| `text-embedding-3-small` (OpenAI) | $0.02 | 1536 | 8192 tokens | API. Price: <https://developers.openai.com/api/docs/pricing>. Dimension and limit: <https://developers.openai.com/api/docs/guides/embeddings>. |
| `gemini-embedding-2` (Google) | $0.20 | 3072 default (128 to 3072 allowed) | 8192 tokens | API. Price: <https://ai.google.dev/gemini-api/docs/pricing>. Dimensions: <https://ai.google.dev/gemini-api/docs/embeddings>. |
| `BAAI/bge-small-en-v1.5` (local, sentence-transformers) | $0 (runs on CPU) | 384 | 512 tokens | MIT licence, about 33M parameters. Source: <https://huggingface.co/BAAI/bge-small-en-v1.5>. Needs `sentence-transformers` and PyTorch, which makes the Docker image much larger. |

A developer-forum thread reported an inconsistency between OpenAI's pricing page and model page
for the *batch* embedding price. This plan uses the standard price only.

The vector column width depends on the model (1536, 3072 or 384), so switching the embedding model
after Phase 3 means re-creating the table and re-embedding. The re-embedding procedure is a Phase 3
deliverable.

## 3. Cost per query

Assumptions (all estimates, to be replaced by measured values in Phase 8):

- Generation call: 5 chunks x ~400 tokens = 2,000 tokens of context, plus ~300 tokens of
  instructions and question, so **2,300 input**, **300 output**.
- Verification call (second LLM call checking claims against cited text): answer 300 + cited
  chunks 2,000 + instructions ~200, so **2,500 input**, **150 output**.
- Total per query: **4,800 input, 450 output** tokens. Query embedding (~30 tokens) is negligible.

| Setup | Cost per query | Per 1,000 queries |
|---|---|---|
| Sonnet 5.5 for both calls | $0.0141 | $14.10 |
| Sonnet 5.5 generation + Haiku 5.5 verification | $0.0079 | $7.90 |
| Haiku 5.5 for both calls | $0.0007 | $0.71 |
| GPT-6 Luna for both calls | $0.0007 | $0.71 |
| Gemini 3.5 Flash-Lite for both calls | $0.0026 | $2.57 |

Workings: Sonnet both = 4,800 x $2/M + 450 x $10/M = $0.0096 + $0.0045. Mixed = generation
(2,300 x $2/M + 300 x $10/M = $0.0076) + verification on Haiku (2,500 x $0.10/M + 150 x $0.50/M =
$0.0003). Haiku and Luna have identical prices, so identical cost.

## 4. One-off corpus embedding cost

Assumption: the v1 corpus (Nigeria Tax Act 2025, company income tax and VAT parts) is roughly
**200,000 tokens** after chunking. The real size is unknown until Phase 1 and 2. Assume the corpus
is embedded about **20 times** during development (chunking changes, model comparisons).

| Model | One embedding of the corpus | 20 embeddings |
|---|---|---|
| `text-embedding-3-small` | $0.004 | $0.08 |
| `gemini-embedding-2` | $0.040 | $0.80 |
| `bge-small-en-v1.5` (local) | $0 | $0 |

Embedding cost is negligible at this scale for every option. Choose on retrieval quality and
deployment simplicity, not on price.

## 5. Recommendation

1. **LLM: Claude Sonnet 5.5 for generation, Claude Haiku 5.5 for the verification pass.**
   About $0.008 per query on the stated assumptions. One vendor and one SDK keep the hand-rolled
   loop simple, and a stronger model on the step where hedged, citation-faithful wording matters
   most is the cheapest place to spend. The cheap tier is enough to try for verification; Phase 6
   decides whether it is. Fall back to Sonnet for both calls ($0.014) if verification quality is
   weak. This is a judgement, not a measured result. Phase 8 may change it.
2. **Embeddings (provisional until Phase 4): `text-embedding-3-small`**, 1536 dimensions. It is
   effectively free at this scale, needs no PyTorch in the deployed image, and has a long input
   limit (8192 tokens) so no legal section gets truncated. Phase 4 should also run
   `bge-small-en-v1.5` as the free local baseline and keep whichever gives the better hit-rate@k.
   Its 512-token limit may truncate long sections.

Not yet done: no SDK is installed and no API key is needed until you approve these choices.
