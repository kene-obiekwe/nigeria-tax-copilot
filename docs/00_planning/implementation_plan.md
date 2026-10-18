# Implementation Plan: Nigerian SME Tax Advisory Copilot

A Grounded Retrieval-Augmented Generation (RAG) System Over Nigerian Tax Statutes

> **Conversion note.** This is a Markdown conversion of the original planning document
> (`Tax_Advisory_Copilot_Implementation_Plan.docx`, sha256
> `665433b938d8e3f8411bcff51bf8f84457dafc96959514cb20ae9115572b7abb`). Technical content is
> unchanged. Non-technical framing was removed or reworded neutrally.
>
> **Scope and stack have since changed.** The statutory scope below (CITA, VAT Act, Finance Acts)
> is kept as originally written. It is superseded by decision **D-01** (v1 is based on the Nigeria
> Tax Act 2025; correction **C-01**), and the "Docker is not used locally" line is superseded by
> decision **D-02** (correction **C-02**). See [decision_log.md](decision_log.md). Where this plan
> and the decision log disagree, the decision log wins.

| | |
|---|---|
| Project | Nigerian SME Tax Advisory Copilot |
| Document role | Implementation Plan: phased, gated execution plan for a grounded RAG + agentic system |
| Core technique | Retrieval-Augmented Generation (RAG) with citation verification and refusal guardrails |
| Status | Ready to execute. Generation (Phase 5) is gated behind validated retrieval (Phase 4); the project is not considered complete until the evaluation harness (Phase 8) meets its stated threshold and the live deployment (Phase 11) reproduces those results. |

## 1. Purpose and Position of This Document

This plan sequences the Nigerian SME Tax Advisory Copilot from an empty repository to a documented, evaluated, publicly packaged system. It is a standalone project that does not extend or depend on any other project. Its specific purpose is to build a grounded, guardrailed GenAI system, not just call a chat API.

The plan is organized as thirteen phases (Phase 0 through Phase 12), each with a stated objective, primary tools, key deliverable, an explicit exit gate, and a task checklist. Four phases carry hard dependencies that should not be skipped: generation (Phase 5) should not be built before retrieval accuracy is measured (Phase 4); the project should not be presented as complete before the guardrail logic survives an adversarial test (Phase 6), the full pipeline passes a measured evaluation threshold (Phase 8), and the live deployment (Phase 11) is confirmed to reproduce those same results before final sign-off (Phase 12).

## 2. What Makes This a RAG Project, Not a Chatbot Wrapper

The distinction that this entire plan protects: a basic integration would send a user's question straight to an LLM and return whatever it generates. This project instead retrieves the specific, real statutory passages relevant to the question first, generates an answer strictly from those passages, verifies the generated claims against the retrieved text, and refuses to answer when nothing relevant was found. Every phase below exists to build and prove one part of that chain (retrieval accuracy, grounding, citation verification, and refusal) rather than to make the chat interface more polished.

## 3. Scope Definition

### 3.1 In Scope

- Acquisition and documentation of a defined set of Nigerian tax statutes and official guidance (starting scope: Companies Income Tax Act, VAT Act, relevant Finance Act amendments). *See the C-01 note in the conversion note above: superseded by D-01.*
- Structure-aware parsing and chunking that preserves citable section/subsection references.
- Embedding and vector storage of the corpus in PostgreSQL + pgvector.
- A hybrid retrieval layer (semantic search plus exact section/category matching) with measured accuracy.
- Grounded generation that cites its sources, with a post-generation verification step.
- Explicit refusal behavior when a question falls outside the corpus's coverage.
- At least one deterministic, code-executed calculation tool invoked via agentic tool-calling, never LLM-generated arithmetic.
- An automated evaluation harness measuring retrieval accuracy, groundedness, citation accuracy, and refusal correctness.
- A simple interface with visible citations, a visible corpus snapshot date, and a permanent disclaimer.
- A full audit log of every query, its retrieved evidence, and its verification outcome.
- GitHub documentation sufficient for independent reproduction of both the system and its evaluation results.

### 3.2 Out of Scope

- Giving real tax advice or being used for an actual filing or compliance decision.
- Coverage of Personal Income Tax Act, state-level taxes, or any jurisdiction outside the documented v1 corpus (these are reasonable future extensions, not v1 commitments).
- Real-time tracking of legislative changes: the corpus is a dated snapshot, refreshed manually when a new Finance Act is reviewed.
- Fine-tuning a custom language model: the project uses a hosted LLM via API with retrieval and prompting, not model training.
- Integration with FIRS systems, e-filing, or any real taxpayer data. All test scenarios use synthetic figures.
- Production-scale, authenticated, multi-tenant deployment: v1 ships as a single public demo instance behind a query rate limit, not a multi-user product with accounts or a paid posture.
- Assuming any legal liability or professional responsibility for the system's output.

> **DISCLAIMER DISCIPLINE.** Every deliverable in this project (README, UI, evaluation report) should state plainly that this is a research and reference tool, not a substitute for a licensed tax professional. This framing is not a legal formality; it is part of what makes the project read as credible rather than naive.

## 4. System Architecture

Nine logical layers, each built and validated in roughly the order listed:

| Layer | Responsibility | Primary tools |
|---|---|---|
| 1. Source documents | Raw statutory text, preserved as the reference point for all downstream processing | Official FIRS/legislative publications |
| 2. Ingestion & chunking | Parse raw text into section-addressable, citable chunks with metadata | Python, structure-aware parsing |
| 3. Embedding & vector store | Convert chunks to vectors and store them for similarity search | PostgreSQL + pgvector, an embedding model |
| 4. Retrieval | Find the passages relevant to a given question (semantic + exact-match) | Python, pgvector queries |
| 5. Orchestration / agent layer | Coordinate retrieval, tool calls, and generation for a given question | Python, LLM tool-calling API |
| 6. Generation | Produce a cited answer strictly from retrieved passages | LLM API |
| 7. Guardrails | Verify citations, enforce refusal when evidence is insufficient | Python verification logic |
| 8. Evaluation | Measure retrieval accuracy, groundedness, citation accuracy, refusal correctness | Python, labeled test sets |
| 9. Interface & audit | Present cited answers to a user and log every interaction | Streamlit, PostgreSQL logging |

## 5. Technology Stack

| Component | Choice | Why |
|---|---|---|
| Vector store | PostgreSQL + pgvector | Applies relational-database discipline and avoids introducing a separate vector-database product for a project of this scale |
| Orchestration | Python, hand-rolled retrieval/generation logic | Writing the RAG loop directly (rather than a high-level framework) is a deliberate choice: it keeps the mechanics visible and testable, which is what the evaluation measures |
| LLM | One hosted LLM API with tool-calling support | Avoids the cost and complexity of model training; the engineering value is in retrieval and grounding, not in the base model |
| Embeddings | An API-based or open-source sentence embedding model | Document and justify the choice based on retrieval accuracy in Phase 4, not on availability alone |
| Interface | Streamlit | Lightweight, fast to build, sufficient to demonstrate the citation UI without a frontend build step |
| Testing | pytest, custom evaluation scripts | Automated, repeatable scoring rather than one-off manual review |
| Version control | Git + GitHub | Reproducibility and public presentation |
| Containerization | Docker | Earns its place specifically because the project is deployed (Phase 11), not used during local development, where it would add setup overhead without a matching benefit. *Superseded by D-02 / C-02: local pgvector runs in Docker.* |
| Deployment platform | Render (containerized web service, auto-deploy from GitHub) | Deploys the Dockerfile directly, gives a stable public URL and a free tier suitable for a low-traffic demo |
| Hosted database | Supabase (managed PostgreSQL + pgvector) | A real managed database with pgvector supported out of the box, rather than a self-managed instance that could silently lose data |

## 6. Implementation Phases

Phase 0 to 1 can start immediately. Phases 2 to 3 build the corpus and vector store. Phase 4 is the first hard gate: do not proceed to generation until retrieval accuracy is measured. Phase 6 is the second hard gate: the guardrail logic must survive adversarial testing before the evaluation harness is finalized in Phase 8. Phase 11 moves the finished, evaluated system onto a public URL; Phase 12 confirms that live deployment actually reproduces what was measured locally before the project is called complete.

### Phase 0: Environment & Repository Setup

| | |
|---|---|
| Objective | Stand up a reproducible local environment and the repository before any legal text or AI component is touched. |
| Primary tools | VS Code, Git/GitHub, PostgreSQL (with pgvector extension), Python virtual environment |
| Key deliverable(s) | Initialized GitHub repository with the proposed folder structure; local PostgreSQL instance with pgvector enabled; Python environment with core dependencies installed |
| Exit gate | pgvector extension loads successfully in a local PostgreSQL database, and the repository skeleton is committed. |

- Create the GitHub repository (e.g. ng-tax-advisory-copilot) with the folder structure in Section 7 of this plan.
- Install PostgreSQL locally and enable the pgvector extension; confirm a test vector column can be created and queried.
- Create a Python virtual environment; install core dependencies (an LLM client SDK, an embedding client or sentence-transformers, psycopg2/SQLAlchemy, pytest, Streamlit).
- Decide and document the LLM provider and embedding model to be used, including expected per-query cost, so budget is understood before heavy experimentation.
- Commit the empty folder skeleton and a README stub stating the project's scope and disclaimer up front.

### Phase 1: Corpus Acquisition & Source Documentation

| | |
|---|---|
| Objective | Acquire the actual Nigerian tax statutes and official guidance that will ground every answer, and document their provenance before any processing. |
| Primary tools | Official FIRS publications; statutory texts (CITA, PITA, VAT Act, relevant Finance Act amendments) |
| Key deliverable(s) | A documented, version-dated source-document inventory; raw statutory PDFs/text stored under data/raw/ |
| Exit gate | Every source document has a recorded title, official source, edition/amendment date, and acquisition date, before any document is chunked or embedded. |

- Identify the specific statutes and guidance documents the corpus will cover for v1 (recommended starting scope: Companies Income Tax Act, Value Added Tax Act, and relevant Finance Act amendments; Personal Income Tax Act can follow in a later iteration). *Scope superseded by D-01 / C-01.*
- Acquire the official text of each document from a primary source (FIRS or the National Assembly's published Acts) rather than a secondary summary site.
- Record source documentation: official title, publication/amendment date, acquisition date, and a link or citation to the primary source.
- Flag which amendments are current as of the corpus snapshot date. Tax law changes yearly via Finance Acts, so this date must be visible everywhere the system presents an answer.
- Store raw documents under data/raw/ with a data/README.md explaining scope, version, and known gaps (e.g., "does not include Personal Income Tax Act v1").

### Phase 2: Document Parsing & Structure-Aware Chunking

| | |
|---|---|
| Objective | Convert raw statutory text into clean, citable chunks that preserve the legal document's actual section/subsection structure. This is the single most important data-quality decision in the project. |
| Primary tools | Python, PDF/text parsing libraries, regex-based structure detection |
| Key deliverable(s) | A chunked, structured representation of the corpus where every chunk carries its section number, parent act, and amendment date as metadata |
| Exit gate | A manual spot-check of 20 random chunks confirms each one maps to a real, correctly numbered section of the source act. A wrong section number here corrupts every citation downstream. |

- Extract text from the source PDFs while preserving section/subsection numbering rather than treating the document as an undifferentiated block of text.
- Chunk by legal unit (section or subsection) rather than by a fixed token window. A citation has to point to "Section 24(1)(b)", not an arbitrary 500-token slice that straddles two unrelated provisions.
- Attach metadata to every chunk: parent act, section number, amendment/version date, and source document ID.
- Handle cross-references between sections (e.g., "as defined in Section 8") by preserving the reference text so retrieval can be pointed toward related sections when relevant.
- Write the chunking logic as a reproducible script (python/ingest/) rather than a manual one-off edit, so the corpus can be rebuilt when the tax law is amended.

### Phase 3: Embedding & Vector Store Implementation

| | |
|---|---|
| Objective | Convert the structured chunks into vector embeddings and load them into a queryable store. |
| Primary tools | PostgreSQL + pgvector, an embedding model (API-based or local sentence-transformers) |
| Key deliverable(s) | A populated vector table with one row per chunk, including its embedding and full metadata; a reusable embedding/load script |
| Exit gate | A known-relevant test query (e.g., a VAT exemption question) returns its correct source section inside the top-5 nearest neighbors. |

- Choose and document the embedding model, including its dimensionality and any cost per call.
- Create the pgvector-backed schema: chunk text, embedding vector, section metadata, act name, amendment date.
- Write the embedding/load script and run it against the full Phase 2 chunk set.
- Spot-check retrieval quality with a handful of known questions before moving to the full retrieval-evaluation phase.
- Document the re-embedding procedure for when the corpus is updated after a new Finance Act.

### Phase 4: Retrieval Layer & Retrieval-Quality Evaluation

| | |
|---|---|
| Objective | Build the search logic that finds the right passages for a given question, and prove its accuracy with a measured test set before any generation is wired on top of it. |
| Primary tools | PostgreSQL + pgvector, Python retrieval scripts, a labeled evaluation question set |
| Key deliverable(s) | A retrieval function (semantic search, optionally combined with exact section-number/keyword matching); a retrieval-accuracy report against a labeled test set |
| Exit gate | Retrieval hit-rate (correct section appears in the top-k results) is measured and meets a stated threshold. Generation should not be built on top of unvalidated retrieval. |

- Implement semantic (vector) search over the embedded chunks, with a configurable top-k.
- Add hybrid retrieval: exact-match lookup when a user references a specific section number, plus metadata filtering by tax category (VAT vs. CIT vs. PIT) to narrow the search space.
- Build a labeled test set of 30 to 50 questions, each with its known correct source section(s), covering the statutes in scope.
- Run retrieval against the test set and compute hit-rate at top-k; investigate and fix systematic misses (e.g., chunking boundary problems) before proceeding.
- Document the final retrieval configuration (top-k, filtering rules) and the measured accuracy.

> **GATE.** Phase 5 (generation) does not start until retrieval hit-rate is measured against the labeled test set. Building a citation-generating model on top of unvalidated retrieval just produces confident citations to the wrong section, which is arguably worse than no citation at all.

### Phase 5: Grounded Generation & Prompt Engineering

| | |
|---|---|
| Objective | Wire the validated retrieval layer to an LLM that generates answers strictly from retrieved passages, with citations. |
| Primary tools | LLM API, Python orchestration layer |
| Key deliverable(s) | A generation function that takes a question, retrieves relevant chunks, and produces a cited answer |
| Exit gate | On a sample of 15 questions, every factual claim in the generated answer traces to a specific retrieved chunk, verified by manual read-through. |

- Design the prompt template so the model is instructed to answer only from the supplied passages and to cite the specific section for every claim.
- Pass retrieved chunks (with their section metadata) into the prompt as structured context, not as an unlabeled wall of text.
- Require the model to output citations in a consistent, parseable format (e.g., inline section references) so the UI can render them as clickable or highlighted references.
- Test the generation step against both in-scope questions (should answer with citations) and out-of-corpus questions (should decline; formal testing comes in Phase 6).
- Log the full input/output pair for every test call to build the basis of the audit trail implemented in Phase 9.

### Phase 6: Guardrails: Citation Verification & Refusal Logic

| | |
|---|---|
| Objective | Build the layer that makes this a trustworthy system rather than a basic RAG demo: automatic verification that generated claims actually match their cited source, and a reliable refusal path when the corpus doesn't support an answer. |
| Primary tools | Python, a verification pass (either a second LLM call acting as a checker, or rule-based citation-matching logic) |
| Key deliverable(s) | A post-generation verification step that flags or blocks ungrounded claims; a tested refusal path for out-of-scope or ambiguous questions |
| Exit gate | Against an adversarial test set (trick questions, out-of-corpus questions, questions baiting a fabricated citation), the system correctly refuses or flags every case. This is the gate that separates a trustworthy system from a basic chatbot wrapper. |

- Implement a verification pass that checks each cited section actually contains the claim attributed to it, rejecting or flagging mismatches before the answer is returned.
- Implement explicit refusal behavior when retrieval returns no sufficiently relevant passages. The system should say so rather than answering from the model's general knowledge.
- Build an adversarial test set specifically designed to bait fabrication: questions about sections that don't exist, questions outside the corpus's tax-type scope, deliberately ambiguous questions.
- Run the adversarial set and record the refusal/flagging accuracy; iterate on the prompt and verification logic until the target accuracy is met.
- Document the guardrail design decisions. This section of the README is likely to be the most scrutinized part of the project by a technical reviewer.

> **GATE.** The evaluation harness in Phase 8 should not be treated as final until the guardrail logic here survives a dedicated adversarial test set. This is the gate that most directly determines whether the finished system is trustworthy or just plausible-looking.

### Phase 7: Agentic Tool Use

| | |
|---|---|
| Objective | Extend the system from pure question-answering into light agentic behavior, giving it callable tools rather than relying on the LLM to do everything via free text. |
| Primary tools | Python function/tool definitions, LLM tool-calling API |
| Key deliverable(s) | At least two working tools the model can invoke: a section-lookup tool and a deterministic calculation helper |
| Exit gate | A multi-step question (e.g., one requiring both a rate lookup and a simple arithmetic calculation) is answered correctly end-to-end using the tools, with the calculation performed by code rather than generated by the model. |

- Implement a `lookup_section(act, section_number)` tool that retrieves a specific section directly, for when a user asks about a known provision.
- Implement a deterministic `calculate_liability(...)` helper for simple, clearly-scoped scenarios (e.g., applying a stated VAT rate to a given amount). The arithmetic is computed in code, never generated by the LLM, since language models are unreliable at precise calculation.
- Wire these as callable tools through the LLM's tool-calling interface so the model decides when to invoke them as part of answering a question.
- Test a handful of multi-step questions that require combining retrieval with a tool call.
- Document each tool's exact scope and limitations. The calculation tool in particular should state plainly which scenarios it covers and which it does not.

### Phase 8: Evaluation Harness & Hallucination Benchmarking

| | |
|---|---|
| Objective | Build a repeatable, automated evaluation suite that measures the system's reliability as a whole, not just its individual components in isolation. |
| Primary tools | Python, the labeled test set from Phase 4 extended with expected answers, optionally an LLM-as-judge scoring pass |
| Key deliverable(s) | An evaluation report covering retrieval hit-rate, groundedness/faithfulness, citation accuracy, and refusal correctness, run against the full pipeline end-to-end |
| Exit gate | Hallucination/groundedness metrics meet a stated, documented threshold before the project is presented as complete. An unmeasured RAG system is not a credible claim. |

- Extend the Phase 4 test set with expected final answers and expected citations, covering normal questions, edge cases, and the adversarial set from Phase 6.
- Implement automated scoring: retrieval hit-rate, groundedness (is every claim traceable to a retrieved chunk), citation accuracy (does the cited section actually say what's claimed), and refusal correctness.
- Run the full pipeline against the complete test set and produce a scored evaluation report.
- Track basic operational metrics alongside accuracy: average latency and approximate cost per query, since both matter for any real deployment discussion.
- Store the evaluation report and the test set itself in the repository so the claimed accuracy numbers are independently reproducible, not just asserted in the README.

> **GATE.** The project is not presented as complete (README, demo, or any public listing) until the measured hallucination/groundedness numbers from this phase are in hand and published alongside the evaluation test set.

### Phase 9: Interface & Audit Logging

| | |
|---|---|
| Objective | Give the system a usable interface and a persistent record of every interaction, so its reasoning can be inspected after the fact. |
| Primary tools | Streamlit (or a similarly lightweight Python UI framework), PostgreSQL for logging |
| Key deliverable(s) | A working chat interface showing answers with inline citations; a query log table recording every question, retrieved chunks, generated answer, and verification result |
| Exit gate | A reviewer can open the log for any past query and see exactly what was retrieved and how the final answer was verified. |

- Build a simple chat UI that displays the answer with its citations rendered clearly (e.g., hoverable or linked references to the specific section).
- Display the corpus snapshot date prominently in the UI, since tax law changes yearly and users need to know how current the grounding is.
- Implement the audit log table: timestamp, question, retrieved chunk IDs, generated answer, verification outcome, and refusal flag where applicable.
- Add the disclaimer text permanently visible in the UI, not just in the README, stating this is a research/reference tool, not professional tax advice.
- Test the full user flow end-to-end: ask a question, see a grounded cited answer, confirm the log captured it correctly.

### Phase 10: Documentation, Disclaimers & GitHub Packaging

| | |
|---|---|
| Objective | Package the project as a reproducible, professionally framed public repository, with the scope and limitations stated as clearly as the capability. |
| Primary tools | GitHub, Markdown |
| Key deliverable(s) | Complete repository with README as entry point, architecture documentation, evaluation results, and explicit scope/disclaimer sections |
| Exit gate | A third party could clone the repository, follow the README, rebuild the corpus and vector store, and reproduce the evaluation report's numbers. |

- Write the README: problem statement, why RAG (not a plain LLM) was necessary, architecture diagram, reproduction steps, evaluation results, and a prominent disclaimer section.
- Document the exact corpus scope and its snapshot date, and state explicitly what the system does not cover (jurisdictions, tax types, or questions out of scope).
- Publish the architecture diagram (ingestion → chunking → embedding → retrieval → generation → verification → audit log) under diagrams/.
- Include the evaluation report and test set in docs/ so the claimed accuracy is independently checkable, not just asserted.
- Add a licensing/usage note clarifying the project is for research and demonstration, not an interface to real tax filing systems, and does not store or process real taxpayer data.

### Phase 11: Deployment

| | |
|---|---|
| Objective | Containerize the application and publish a live, publicly reachable demo backed by a hosted database, so the project is a working link rather than a repository someone has to clone to evaluate. |
| Primary tools | Docker, Render (containerized web service), Supabase (hosted PostgreSQL + pgvector), environment-variable secrets management |
| Key deliverable(s) | A Dockerfile that reproducibly builds the app; a hosted Supabase Postgres instance populated with the Phase 3 embeddings; a live Render URL; a basic query rate limit |
| Exit gate | The deployed URL answers a known Phase 4/8 test question correctly end-to-end (retrieval, citation, and verification all working against the hosted database, not just the local one), and a rate limit is confirmed to cap LLM API cost exposure on the public link. |

- Write a Dockerfile that packages the Streamlit app and its Python dependencies into a single reproducible image.
- Provision a Supabase Postgres instance with pgvector enabled, and re-run the Phase 3 embedding/load scripts against it so the hosted database mirrors the local one.
- Create a Render web service from the Dockerfile, connected to the GitHub repository for auto-deploy on push.
- Move all secrets (LLM API key, hosted database connection string) into Render's environment-variable store; confirm none are committed to the repository or baked into the Docker image.
- Implement a simple session- or daily-based query rate limit so a public, unauthenticated link cannot run up unbounded LLM API cost.
- Document the free tier's cold-start behavior (the app may take 30 to 60 seconds to wake after a period of inactivity) in the README rather than leaving it unexplained.
- Smoke-test the live URL against a handful of the Phase 4/8 test questions to confirm the deployed system reproduces local behavior.

> **GATE.** Final QA (Phase 12) must test the live deployed URL, not just the local build. A deployment that silently diverges from local behavior (stale embeddings, a missing secret, a dependency that resolves differently inside the container) defeats the entire purpose of publishing a link: it would mean the thing a reviewer actually clicks on has not been verified.

### Phase 12: Final QA & Review

| | |
|---|---|
| Objective | Perform an end-to-end check, against the live deployment and not just the local build, before treating the project as complete. |
| Primary tools | The full evaluation suite; a fresh adversarial pass; the deployed URL |
| Key deliverable(s) | A completed QA checklist and sign-off note |
| Exit gate | All phase gates in this plan are satisfied, or consciously deferred with a documented reason. |

- Re-run the full evaluation harness end-to-end on a clean build of the vector store.
- Run a fresh, expanded adversarial test pass (new trick questions not used during development) to catch overfitting to the original test set.
- Repeat a sample of the evaluation questions against the live deployed URL, not only the local build, to catch silent drift (stale embeddings, missing secrets, a dependency that behaves differently in the container).
- Verify every claim in the README is independently reproducible from the repository's own code and data.
- Confirm the disclaimer language is visible in both the live UI and the README, and that no part of the project implies it gives real tax advice.

## 7. Repository Structure

A proposed structure that keeps source separated from processing, and processing separated from evaluation.

| Path | Contents |
|---|---|
| README.md, LICENSE/NOTICE.md | Entry point, disclaimer, licensing/usage notes |
| data/README.md, data/raw/ | Source-document inventory and raw statutory text (acquisition dates, versions) |
| python/ingest/ | Parsing and structure-aware chunking scripts |
| python/embed/ | Embedding and vector-store load scripts |
| python/retrieval/ | Retrieval logic (semantic + hybrid search) |
| python/generation/ | Prompting, generation, and citation-verification logic |
| python/tools/ | Agentic tool implementations (section lookup, deterministic calculation) |
| eval/ | Labeled test set, adversarial test set, evaluation scripts, and the latest evaluation report |
| app/ | Streamlit interface |
| docs/architecture.md, docs/methodology.md, docs/limitations.md | Architecture diagram, methodology, scope and limitations |
| diagrams/ | Architecture and data-flow diagrams |
| logs/ (excluded from Git, documented schema only) | Query/audit log: schema documented, synthetic sample data only |
| Dockerfile, .dockerignore | Reproducible container build for deployment (Phase 11) |
| .env.example (no real secrets committed) | Documents the required environment variables without exposing actual keys/credentials |

## 8. Evaluation Methodology

The evaluation harness is what separates a claimed capability from a demonstrated one. It should answer four questions, each with its own metric:

| Question | Metric | How it's measured |
|---|---|---|
| Does retrieval find the right section? | Retrieval hit-rate @ k | Labeled test set of 30 to 50 questions with known correct section(s); check whether the correct section appears in the top-k retrieved results |
| Is every claim in the answer actually supported? | Groundedness / faithfulness | Manual or LLM-assisted review checking each sentence of the generated answer against its cited source passage |
| Is the cited section the right one, and does it say what's claimed? | Citation accuracy | Automated check cross-referencing the cited section ID against the retrieved chunk actually used |
| Does the system correctly decline when it should? | Refusal correctness | Adversarial test set of out-of-corpus and trick questions; measure correct-refusal rate |

All four metrics, plus the test sets used to produce them, belong in the repository. A claimed accuracy number with no reproducible test set is not evidence; this project holds to an evidence-over-assertion standard.

## 9. Risk & Ethical Considerations

- **Tone calibration:** generated answers should read as informative reference material, not as confident professional advice. Prompt language should encourage appropriately hedged phrasing where the statute itself is ambiguous.
- **Version drift:** tax law changes yearly via Finance Acts; the corpus snapshot date must be visible everywhere an answer is shown, and the README must state clearly how current the corpus is.
- **No real taxpayer data:** all test and demonstration scenarios use synthetic figures; the project does not collect, store, or process real personal or business financial information.
- **Cost control:** track and cap per-query LLM API cost during development; consider caching repeated or near-duplicate queries. This becomes a live operational concern, not just a development-time one, once the link is public in Phase 11. The rate limit built in that phase is what actually bounds exposure.
- **Overfitting the evaluation:** the adversarial retest in Phase 12 uses fresh questions not seen during Phase 6 to 8 development, to avoid the evaluation numbers reflecting memorized test cases rather than general reliability.
- **Secrets hygiene:** the LLM API key and database credentials move from a local .env file to the deployment platform's secret store in Phase 11, never committed to the repository or baked into the Docker image.

## 10. Definition of Done

| Criterion | Established in |
|---|---|
| Every source document has documented provenance and a stated snapshot date | Phase 1 |
| Retrieval accuracy is measured against a labeled test set before generation is built on top of it | Phase 4 |
| Every generated claim in a sampled answer traces to a specific retrieved, correctly cited passage | Phase 5, 6 |
| Guardrail logic correctly refuses or flags every case in an adversarial test set | Phase 6 |
| At least one agentic tool performs deterministic calculation in code, never via LLM-generated arithmetic | Phase 7 |
| A full evaluation report (retrieval, groundedness, citation accuracy, refusal correctness) is published with its test set | Phase 8 |
| Every query is logged with its retrieved evidence and verification outcome | Phase 9 |
| The disclaimer is visible in both the UI and the README, and scope/limitations are stated explicitly | Phase 10 |
| A third party can clone the repository and reproduce both the system and its evaluation numbers | Phase 10, 12 |
| The system is reachable at a live public URL, backed by a hosted database, with secrets kept out of the repository | Phase 11 |
| A query rate limit is in place and confirmed to bound LLM API cost exposure on the public link | Phase 11 |
| The live deployment reproduces the Phase 8 evaluation results on a spot-check sample | Phase 12 |

## 11. Immediate Next Actions

1. Create the GitHub repository and commit the folder skeleton from Section 7.
2. Set up local PostgreSQL with pgvector enabled, and a Python virtual environment.
3. Decide the v1 statutory scope (recommended: CITA + VAT Act + relevant Finance Act amendments) and acquire the official texts. *Scope superseded by D-01 / C-01.*
4. Record the source-document inventory with acquisition dates and snapshot version, before any parsing begins.
5. Hold off on generation (Phase 5) until retrieval accuracy is actually measured in Phase 4; that boundary is this plan's most important discipline.

Credibility of this system rests entirely on the Phase 8 evaluation numbers being real and reproducible, and on the live URL from Phase 11 actually demonstrating them rather than a local build the reviewer has to take on faith.
