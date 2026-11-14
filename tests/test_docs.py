"""Documentation checks (Phase 10, D-177 (b)): the architecture diagram is one Mermaid source that is
repeated in docs/reference/architecture.md, every path a node names exists, and the new pages have no
broken relative link, no section sign and no attribution string. Free: reads files only."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MMD = ROOT / "diagrams" / "architecture.mmd"
PAGE = ROOT / "docs" / "reference" / "architecture.md"
NEW_DOCS = [PAGE, ROOT / "diagrams" / "README.md"]
PATH = re.compile(r"\b(?:python|data|app|eval|docs|diagrams|tests)/[\w./-]*[\w/]")
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
SECTION_SIGN = "§"
# Authorship lines only (CLAUDE.md Section 1). Naming a model as a factual reference is allowed (D-06).
AI_TOOL = r"(?:claude|gemini|opus|sonnet|haiku|an? ai\b|ai assistant|chatgpt|gpt|copilot)"
ATTRIBUTION = re.compile(rf"co-authored-by|generated with|written (?:by|with) {AI_TOOL}", re.I)


def normal(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def page_block(text: str) -> str:
    blocks = re.findall(r"```mermaid\n(.*?)\n```", normal(text), flags=re.S)
    assert len(blocks) == 1, "architecture.md must hold exactly one mermaid block"
    return blocks[0]


def node_paths(source: str) -> set[str]:
    return {p.rstrip("/") for p in PATH.findall(source)}


def test_block_in_the_page_equals_the_source_file():
    assert normal(page_block(PAGE.read_text(encoding="utf-8"))) == normal(MMD.read_text(encoding="utf-8"))


def test_the_comparison_ignores_line_endings_only():
    source = normal(MMD.read_text(encoding="utf-8"))
    crlf = "```mermaid\n" + source.replace("\n", "\r\n") + "\n```\n"
    assert normal(page_block(crlf)) == source
    assert normal(page_block("```mermaid\n" + source + "\n  extra\n```\n")) != source


def test_every_path_named_in_a_node_exists():
    paths = node_paths(MMD.read_text(encoding="utf-8"))
    assert len(paths) >= 40
    missing = sorted(p for p in paths if not (ROOT / p).exists())
    assert missing == []


def test_path_finder_reads_files_and_folders_and_skips_other_slashes():
    found = node_paths('A["python/ingest/x.py (f)<br/>data/curated/ntaa_nrs_proofread/, gen-log/3 audit-schema/1"]')
    assert found == {"python/ingest/x.py", "data/curated/ntaa_nrs_proofread"}


def test_a_missing_path_would_be_caught(tmp_path):
    assert not (tmp_path / "python/ingest/not_a_module.py").exists()
    assert "python/ingest/not_a_module.py" in node_paths('N["python/ingest/not_a_module.py"]')


def test_every_stage_one_to_fifteen_is_drawn_once():
    source = MMD.read_text(encoding="utf-8")
    ids = re.findall(r"^\s+([BQ]\d+)\[", source, flags=re.M)
    assert ids == [f"B{i}" for i in range(1, 7)] + [f"Q{i}" for i in range(7, 16)]
    for number, node in zip(range(1, 16), ids):
        assert re.search(rf'{node}\["{number} ', source)


def test_the_source_is_mermaid_text_and_no_svg_is_kept():
    assert MMD.read_text(encoding="utf-8").startswith("flowchart TD")
    assert not list((ROOT / "diagrams").glob("*.svg"))


@pytest.mark.parametrize("doc", NEW_DOCS + [MMD], ids=lambda p: p.name)
def test_new_docs_have_no_section_sign_and_no_attribution(doc):
    text = doc.read_text(encoding="utf-8")
    assert SECTION_SIGN not in text
    assert ATTRIBUTION.findall(text) == []


@pytest.mark.parametrize("doc", NEW_DOCS, ids=lambda p: p.name)
def test_new_docs_have_no_broken_relative_link(doc):
    broken = [t for t in LINK.findall(doc.read_text(encoding="utf-8"))
              if not t.startswith(("http://", "https://", "mailto:")) and not (doc.parent / t).exists()]
    assert broken == []


def test_attribution_pattern_allows_model_names_and_catches_authorship():
    assert ATTRIBUTION.findall("Generation uses Claude Sonnet 5.5 and the Claude Haiku 5.5 verifier.") == []
    assert ATTRIBUTION.findall("Co-Authored-By: Claude <noreply@example.com>")
    assert ATTRIBUTION.findall("Generated with a coding tool")
    assert ATTRIBUTION.findall("This page was written by Claude.")
    assert ATTRIBUTION.findall("Written with an AI assistant")
    assert ATTRIBUTION.findall("written with Gemini")
    assert ATTRIBUTION.findall("The answer is written by the model") == []


# Phase 10 step B3: README, reference pages, privacy, notice and the docs index --------------------------------

import sys  # noqa: E402

sys.path.insert(0, str(ROOT))
from app import copy as ui  # noqa: E402
from test_reproduce import rp  # noqa: E402

README = ROOT / "README.md"
NOTICE = ROOT / "NOTICE.md"
DATA_README = ROOT / "data" / "README.md"
DOCS_INDEX = ROOT / "docs" / "README.md"
LIMITATIONS = ROOT / "docs" / "reference" / "limitations.md"
REPRODUCE = ROOT / "docs" / "reference" / "reproduce.md"
B3_DOCS = [README, NOTICE, DATA_README, DOCS_INDEX, LIMITATIONS, REPRODUCE]
OPERATOR = "Kene Christopher Obiekwe"
MAILBOX = "taxy.copilot@gmail.com"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def privacy_section(text: str) -> str:
    start = text.index("\n## Privacy\n")
    nxt = re.search(r"^## ", text[start + 5:], flags=re.M)
    return text[start: start + 5 + nxt.start()] if nxt else text[start:]


def flat(text: str) -> str:
    """Text with line breaks, indentation and bold marks folded, for sentence comparisons."""
    return re.sub(r"\s+", " ", text.replace("**", ""))


def test_the_readme_figures_equal_the_committed_records():
    """Every G1, G2 and G3 figure of the README equals the records (design Section 1.2 rule 1; the same check
    as `reproduce.py` B6): the results section holds the G1 figures, the G2 verdict and run 4 counts and the
    four G3 figures of each run, with no count that a record does not hold."""
    assert rp.readme_problems(read(README), ROOT) == []


def test_the_figure_check_would_catch_a_drifted_readme():
    text = read(README)
    for old, new in (("30 of 30", "29 of 30"), ("489 of 489", "488 of 489"), ("0 of 80", "1 of 80"), ("603", "602")):
        assert old in text, old
        assert rp.readme_problems(text.replace(old, new, 1), ROOT), old
    lines = text.splitlines()
    row = next(i for i, line in enumerate(lines) if line.startswith("| G2"))
    lines[row] = lines[row].replace("**not met**", "**met**")                       # the G2 row reads "met"
    assert rp.readme_problems("\n".join(lines), ROOT)


def test_g2_is_stated_as_not_met_in_the_results_table_in_the_same_type_as_the_passes():
    section = rp.results_section(read(README))
    rows = {line.split("|")[1].strip(): line for line in section.splitlines() if line.startswith(("| G1", "| G2", "| G3"))}
    assert set(rows) == {"G1 retrieval", "G2 adversarial guardrails", "G3 end to end"}
    assert "**not met**" in rows["G2 adversarial guardrails"]
    assert "**passed**" in rows["G1 retrieval"] and "**passed**" in rows["G3 end to end"]
    assert "Phase 7 exit check" in section


def test_the_readme_follows_the_design_outline_and_status_rules():
    text = read(README)
    headings = re.findall(r"^## (.+)$", text, flags=re.M)
    assert re.findall(r"^# (.+)$", text, flags=re.M)[0] == "Taxy: Nigerian SME Tax Reference"   # UI revision UI-12
    assert headings == ["Status", "Results at a glance", "The problem",
                        "Why retrieval rather than a plain model", "Scope and snapshot date", "What it does not cover",
                        "Architecture", "How to run", "How to reproduce", "Evaluation results", "Known limitations",
                        "Disclaimer", "Privacy", "Licence and usage", "Documentation"]
    status = text[text.index("## Status"): text.index("## Results at a glance")]
    assert "Phases 1 to 10 are done" in status and "Phase 11 (deployment) is next" in status
    assert "no live demo yet" in status and "G2 was **not met**" in status
    assert "advisory" not in text.lower()
    top = text.split("## Status")[0]
    assert "not tax advice" in top and "Personal income tax is out of scope" in top
    assert re.search(r"portfolio|career|project \d of", text, re.I) is None


def test_the_readme_privacy_section_has_the_operator_the_mailbox_children_and_the_complaint_route():
    section = flat(privacy_section(read(README)))
    for needed in (OPERATOR, MAILBOX, "the service is not intended for children", "90 days",
                   "complain to the Nigeria Data Protection Commission", "outside Nigeria", "Anthropic", "OpenAI",
                   "Render", "Supabase", "Google", "the hosting platform keeps its own request logs",
                   "tick a box confirming that you understand this", "nothing is sent or stored until you do",
                   "The tick itself is not stored"):
        assert needed in section, needed
    assert "[operator name]" not in section and "[contact route]" not in section and "[links]" not in section


def test_the_popover_says_the_same_facts_as_the_readme_privacy_section():
    popover = flat(ui.PRIVACY_DETAILS)
    for needed in (OPERATOR, MAILBOX, "not intended for children", "90 days", "Nigeria Data Protection Commission",
                   "outside Nigeria", "Anthropic", "OpenAI", "Render", "Supabase", "Google",
                   "the hosting platform keeps its own request logs", "tick a box",
                   "nothing is sent or stored until you do", "any answer can be reviewed"):
        assert needed in popover, needed
    links = re.findall(r"\]\((https://[^)]+)\)", ui.PRIVACY_DETAILS)
    assert len(links) == 5 and all(link in read(README) for link in links)


def test_the_consent_wording_in_the_readme_is_the_apps_wording():
    section = flat(privacy_section(read(README)))
    assert ui.CONSENT_TEXT in section and ui.CONSENT_LABEL in section


D186 = {
    "withdrawal": "You can withdraw your consent at any time by writing to taxy.copilot@gmail.com; "
                  "your stored questions are then deleted.",
    "transfer": "These providers process data outside Nigeria, where the law may not protect it in the same way.",
    "automated": "The service makes no decision about you; it returns reference text from the statutes.",
}


def test_the_d186_sentences_are_in_the_readme_and_the_app_as_written():
    readme = flat(privacy_section(read(README)))
    popover = flat(ui.PRIVACY_DETAILS)
    consent = flat(ui.CONSENT_TEXT)
    assert D186["withdrawal"] in readme and D186["withdrawal"] in consent
    assert D186["transfer"] in readme and D186["transfer"] in popover
    assert D186["automated"] in readme and D186["automated"] in popover
    log = flat(read(ROOT / "docs" / "00_planning" / "decision_log.md"))
    assert "| D-186 |" in log and "s26(4) and s35(2)" in log and "s43(1)(a)" in log and "s27(1)(g)" in log


def test_the_old_taxpayer_data_sentence_is_gone_and_the_corrected_one_is_in_place():
    for doc in (README, NOTICE):
        text = flat(read(doc))
        assert "does not collect, store or process real taxpayer data" not in text, doc.name
        assert "no real taxpayer data" not in text.lower(), doc.name
        assert "not designed to process real taxpayer data" in text, doc.name
    assert "not licensed by the project" in flat(read(NOTICE)) and "MIT licence" in read(NOTICE)
    assert "not licensed by the project" in flat(read(README))


def test_the_data_readme_has_the_mirror_line():
    text = flat(read(DATA_README))
    assert "corpus-2026-10-18" in text and "--mirror" in text and "assets.json" in text


@pytest.mark.parametrize("doc", B3_DOCS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_b3_docs_have_no_section_sign_no_attribution_and_no_broken_link(doc):
    text = read(doc)
    assert SECTION_SIGN not in text
    assert ATTRIBUTION.findall(text) == []
    broken = [t for t in LINK.findall(text)
              if not t.startswith(("http://", "https://", "mailto:")) and not (doc.parent / t).exists()]
    assert broken == []


@pytest.mark.parametrize("doc", [LIMITATIONS, REPRODUCE], ids=lambda p: p.name)
def test_reference_pages_open_with_the_disclaimer_line(doc):
    assert "not tax advice" in read(doc).split("\n\n", 2)[1]


def test_the_limitations_page_puts_personal_income_tax_first_and_names_the_cases():
    text = flat(read(LIMITATIONS))
    for needed in ("Personal income tax is out of scope", "ADV4-009", "ADV4-044", "N05", "Q032", "N17", "Q009", "N11",
                   "8.8%", "does not detect names or addresses", "Each question is answered on its own",
                   "log viewer is local only", "note tagger", "Phase 11", "Interest is never computed"):
        assert needed in text, needed
    assert text.index("Personal income tax is out of scope") < text.index("ADV4-009")


def test_the_reproduce_page_names_every_check_and_the_no_regeneration_rule():
    text = read(REPRODUCE)
    for check_id in [f"A{i}" for i in range(1, 11)] + [f"B{i}" for i in range(1, 7)]:
        assert f"| {check_id} |" in text, check_id
    for needed in ("reproduce.py --profile clean", "corpus-2026-10-18", "not part of this reproduction", "D-171",
                   "Phase 12", "assets.json", "Leave `LLM_API_KEY` and `EMBEDDING_API_KEY` empty"):
        assert needed in text, needed


def test_the_docs_index_lists_the_new_and_changed_documents():
    text = read(DOCS_INDEX)
    for target in ("reference/limitations.md", "reference/reproduce.md", "reference/architecture.md",
                   "../README.md", "../NOTICE.md", "../data/README.md", "phase09_interface/ui_revision.md"):
        assert f"]({target})" in text, target
