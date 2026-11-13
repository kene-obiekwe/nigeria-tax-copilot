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
