"""The local log viewer (interface design, Section 5; D-158): what was retrieved and how the final answer
was verified, for any past query.

    .venv\\Scripts\\streamlit run app\\review.py

A separate Streamlit app, run on the reviewer's own machine and never deployed (it is not in the image and
the public app has no log page). It reads the audit rows with the read-only connection string in
`REVIEW_DATABASE_URL` (the `taxy_review` role). Without that variable it falls back to the project
connection locally and says so on screen; it only ever reads. The report itself is built by
`python/audit/report.py`, the same one `python/audit/show_query.py` prints.

Find a query by reference (`Q-` plus a prefix, refused if it matches more than one row), query id or record
id; or list queries filtered by day, status, refusal reason, tier, `ui_fallback`, unfinished rows and feedback
rating. One query is shown in ten sections: header, question, pre-check and floor, retrieval, generator
output, verification (evidence quotes highlighted inside the cited chunk text), tools, notes and
disclosures (hidden ones marked "not shown to the user", D-156), what the user saw, and local-only links.
"""
from __future__ import annotations

import html
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT / "python"), str(ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app import copy as ui  # noqa: E402
from audit.migrate import DEFAULT_SCHEMA  # noqa: E402
from audit.report import (ReferenceError_, build_report, connect_review, fetch_query, list_queries,  # noqa: E402
                          resolve_reference)

LOG = logging.getLogger("taxy.review")
STATUSES = ("answered", "not_covered", "refused", "error", "app_error")
REASONS = ("invalid_question", "no_such_section", "section_outside_v1", "foreign_law", "low_relevance",
           "unsupported_answer", "verification_unavailable")
TIERS = ("local", "anonymous", "signed_in", "reviewer")


@dataclass
class ReviewContext:
    connect: Callable = connect_review      # () -> (connection, read_only)
    schema: str = DEFAULT_SCHEMA


def segments_html(segments) -> str:
    """The cited chunk text with the evidence quotes marked, HTML-escaped."""
    return "".join(f"<mark>{html.escape(text)}</mark>" if hit else html.escape(text) for text, hit in segments)


def show_json(value) -> None:
    st.json(value if value is not None else {}, expanded=False)


def render_report(report: dict) -> None:
    h = report["header"]
    S = ui.REVIEW_SECTIONS
    if not h["config"]["pinned"]:
        st.error(ui.REVIEW_CONFIG_WARNING)

    st.subheader(S["header"])
    cost = h["cost_usd"]
    st.markdown(
        f"**{h['reference']}** · {h['received_at']} · state {h['state']} · status **{h['status']}** · reason "
        f"{h['refusal_reason']} · stage {h['refusal_stage']} · tier {h['tier']}\n\n"
        f"query id `{h['query_id']}` · record id `{h['record_id']}` · flags {', '.join(h['flags']) or 'none'} · "
        f"latency {h['latency_ms']} ms · cost total {cost['total']} (generation {cost['generation']}, verification "
        f"{cost['verification']}, tagger {cost['tagger']}; reserved {cost['reserved']})")
    cfg, s, m, p = h["config"], h["app_start"], h["models"], h["prompts"]
    st.markdown(
        f"config `{cfg['file']}` sha256 `{cfg['sha256']}` (from the {cfg['from']})\n\n"
        f"app start {s['start_id']} · {s['started_at']} · {s['deployment']} · commit `{s['git_commit']}` · outcome "
        f"{s['outcome']}\n\n"
        f"models: generator {m['generator']}, verifier {m['verifier']}, tagger {m['tagger']} · prompts: generation "
        f"{p['generation']}, schema {p['schema']}, tools {p['tool_defs']}, verifier {p['verifier']}, gap {p['gap']}")
    st.caption(f"corpus load: {h['corpus']}")
    if h["error"] or h["app_error"]:
        st.warning(ui.REVIEW_ERROR_LINE.format(error=h["error"], app_error=h["app_error"]))

    st.subheader(S["question"])
    st.text(report["question"])

    st.subheader(S["checks"])
    checks = report["checks"]
    st.markdown(f"floor: {checks['floor_line']}")
    st.caption(f"refusal: {checks['refusal']}")
    with st.expander("pre-check"):
        show_json(checks["pre_check"])

    st.subheader(S["retrieval"])
    if not report["corpus_text_available"]:
        st.warning(ui.REVIEW_NO_CORPUS_TEXT)
    if not report["passages"]:
        st.caption(ui.REVIEW_NO_PASSAGES)
    for passage in report["passages"]:
        marks = [name for name, on in (("disputed", passage["disputed"]), ("cited", passage["cited"]),
                                       ("supporting", passage["supporting"])) if on]
        with st.expander(f"{passage['label']} · rank {passage['rank']} · {passage['citation']}"):
            st.markdown(f"chunk `{passage['chunk_id']}` · origin {passage['origin']} · score {passage['score']} · "
                        f"distance {passage['distance']} · {', '.join(marks) or 'not cited'}")
            st.caption("reasons: " + "; ".join(passage["reasons"]))
            if passage["text_changed"]:
                st.warning(ui.REVIEW_TEXT_CHANGED)
            if passage["text"] is not None:
                st.text(passage["text"])

    st.subheader(S["generator"])
    parse = report["generator"]["parse"] or {}
    st.caption(f"parser: status {parse.get('status')} · rejected citations {parse.get('rejected_citations')} · "
               f"flags {parse.get('flags')} · listed calculations {parse.get('calculations')}")
    st.code(report["generator"]["output"] or "", language="json")

    st.subheader(S["verification"])
    claims = report["verification"]["claims"]
    if not claims:
        st.caption(ui.REVIEW_NO_CLAIMS)
    for c in claims:
        with st.expander(f"claim {c['claim_no']} · {c['verdict']} → {c['action']} · "
                         f"{'shown' if c['shown'] else 'not shown'}"):
            st.markdown(c["text"])
            rule = c["rule"] or {}
            st.caption(f"rule check: tokens {rule.get('tokens')} · missing {rule.get('missing')} · reason "
                       f"{rule.get('reason')} · hint {rule.get('hint')}")
            st.caption(f"model verdict {c['llm_verdict']} · second call {c['second']} · final {c['verdict']} / "
                       f"{c['reason']} / {c['sub_reason']} · unsupported part {c['unsupported_part']}")
            st.caption(f"cited {', '.join(c['cited_labels'])} · kept citations {', '.join(c['kept_chunk_ids']) or 'none'}")
            for ev in c["evidence"]:
                found = {True: ui.REVIEW_EVIDENCE_FOUND, False: ui.REVIEW_EVIDENCE_MISSING,
                         None: ui.REVIEW_EVIDENCE_UNKNOWN}[ev["found"]]
                st.markdown(f"evidence ({ev['label']}, {found}): {ev['quote']}")
                if ev["segments"]:
                    st.html(f"<pre style='white-space:pre-wrap'>{segments_html(ev['segments'])}</pre>")
    for g in report["verification"]["gaps"]:
        st.markdown(f"gap {g['gap_no']}: {g['text']}")
        st.caption(f"class {g['class']} · kept {g['kept']} · shown {g['shown']} · origin {g['origin']} · "
                   f"reason {g['reason']}")
    with st.expander("action-rule summary"):
        show_json(report["verification"]["summary"])

    st.subheader(S["tools"])
    for use in report["tools"]["tool_uses"] or []:
        show_json(use)
    for comp in report["tools"]["computations"]:
        with st.expander(f"{comp['calc_id']} · {comp['scenario']} · {comp['status']} · "
                         f"{'shown' if comp['shown'] else 'withheld'}"):
            st.caption(f"withheld reason {comp['withheld_reason']} · listed {comp['listed']}")
            st.markdown(f"checks K1 to K6: `{comp['checks']}`")
            st.markdown(f"result: {comp['result_text']} · sources {', '.join(comp['sources']) or 'none'}")

    st.subheader(S["notes"])
    for n in report["notes"]["notes"]:
        st.markdown(f"note: {n.get('text')}")
    for d in report["notes"]["disclosures"]:
        mark = f" ({d['mark']})" if d["mark"] else ""
        st.markdown(f"disclosure {d.get('kind')} {d.get('ref')}{mark}: {d.get('text')}")

    st.subheader(S["saw"])
    saw = report["user_saw"]
    st.caption(f"rendered sha256 {saw['rendered_sha256']}")
    for d in saw["deliveries"]:
        verdict = {True: ui.REVIEW_SHA_EQUAL, False: ui.REVIEW_SHA_DIFFERENT, None: ui.REVIEW_SHA_NONE}[d["matches_rendered"]]
        st.markdown(f"delivery **{d['outcome']}** · {d['at']} · shown sha256 `{d['shown_sha256']}` ({verdict})")
    for f in saw["feedback"]:
        st.markdown(f"feedback {f['at']}: rating {f['rating']} · reason {f['reason']}")
    st.text(saw["rendered"] or "")

    st.subheader(S["local"])
    st.markdown(f"JSONL record: `{report['local']['gen_log_file']}` · record id `{report['local']['record_id']}`")


def sidebar(ctx: ReviewContext, conn) -> None:
    with st.sidebar:
        st.subheader(ui.REVIEW_FIND)
        ref = st.text_input(ui.REVIEW_REF_LABEL, key="review_ref")
        if st.button(ui.REVIEW_OPEN, key="review_open") and ref.strip():
            try:
                st.session_state["review_query"] = resolve_reference(conn, ref, ctx.schema)
                st.session_state.pop("review_error", None)
            except ReferenceError_ as exc:
                st.session_state["review_error"] = str(exc)
        if st.session_state.get("review_error"):
            st.error(st.session_state["review_error"])
        st.subheader(ui.REVIEW_FILTERS)
        day = st.date_input(ui.REVIEW_FILTER_DAY, value=None, key="review_day")
        status = st.selectbox(ui.REVIEW_FILTER_STATUS, [None, *STATUSES], key="review_status",
                              format_func=lambda v: ui.REVIEW_FILTER_ANY if v is None else v)
        reason = st.selectbox(ui.REVIEW_FILTER_REASON, [None, *REASONS], key="review_reason",
                              format_func=lambda v: ui.REVIEW_FILTER_ANY if v is None else v)
        tier = st.selectbox(ui.REVIEW_FILTER_TIER, [None, *TIERS], key="review_tier",
                            format_func=lambda v: ui.REVIEW_FILTER_ANY if v is None else v)
        fallback = st.checkbox(ui.REVIEW_FILTER_FALLBACK, key="review_fallback")
        unfinished = st.checkbox(ui.REVIEW_FILTER_UNFINISHED, key="review_unfinished")
        rating = st.selectbox(ui.REVIEW_FILTER_RATING, list(ui.REVIEW_RATINGS), key="review_rating")
        rows = list_queries(conn, ctx.schema, day=day, status=status, reason=reason, tier=tier, ui_fallback=fallback,
                            unfinished=unfinished, rating=ui.REVIEW_RATINGS[rating])
        if not rows:
            st.caption(ui.REVIEW_LIST_EMPTY)
        for row in rows:
            label = ui.REVIEW_ROW.format(reference=row["reference"], when=f"{row['received_at']:%Y-%m-%d %H:%M}",
                                         status=row["status"] or row["state"], question=row["question"][:50])
            if st.button(label, key=f"review_pick_{row['query_id']}", help=ui.REVIEW_PICK):
                st.session_state["review_query"] = row["query_id"]
                st.session_state.pop("review_error", None)


def main() -> None:
    st.set_page_config(page_title=ui.REVIEW_TITLE, layout="wide")
    st.title(ui.REVIEW_TITLE)
    st.caption(ui.REVIEW_INTRO)
    ctx = st.session_state.get("review") or ReviewContext()
    try:
        conn, read_only = ctx.connect()
    except Exception as exc:  # noqa: BLE001 - never show a connection string
        LOG.error("the audit log could not be opened (%s)", type(exc).__name__)
        st.error(ui.REVIEW_UNAVAILABLE)
        return
    try:
        if not read_only:
            st.warning(ui.REVIEW_NOT_READ_ONLY)
        sidebar(ctx, conn)
        query_id = st.session_state.get("review_query")
        if query_id:
            try:
                report = build_report(fetch_query(conn, query_id, ctx.schema))
            except Exception as exc:  # noqa: BLE001
                LOG.error("the query could not be read (%s)", type(exc).__name__)
                st.error(ui.REVIEW_UNAVAILABLE)
                return
            render_report(report)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
