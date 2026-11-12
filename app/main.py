"""The Streamlit app: one page, top to bottom (interface design, Sections 3 and 9).

    .venv\\Scripts\\streamlit run app\\main.py

Title and one line, the status strip, the disclaimer, the scope box, the privacy line, example questions
(while the session has no questions), this session's questions and answers as cards, the question box, and
a sidebar. Every text is in `app/copy.py`.

What the script does with a question (Section 2): the personal-data screen, then `open_query()` (admission
and the receipt row in one call), then a worker thread runs `generate()`; the script waits for it with a
status text and shows the answer only after the audit rows are written. A rerun or a closed tab does not
stop the worker: it finishes and logs. One question at a time per session: the question box is disabled
while one is running.

Question box (deviation from Section 3.1, asked and approved at the Build B start): Streamlit 1.66
`st.chat_input` cannot be pre-filled, and the design needs a box that example clicks and "Edit question"
can fill, so the box is a text area (2,000 characters, the guardrails limit) in a form with an Ask button.

The start-up check (Section 3.8) runs once per process before the box is enabled. If it fails, the app
fails closed: it shows the unavailable text and no question box.
"""
from __future__ import annotations

import concurrent.futures
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT / "python"), str(ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app import cards  # noqa: E402
from app import copy as ui  # noqa: E402
from app.services import Services, get_services  # noqa: E402

LOG = logging.getLogger("taxy.app")
EXAMPLES_FILE = ROOT / "app" / "examples.yml"


def load_examples(path: Path = EXAMPLES_FILE) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def format_snapshot(iso: str | None) -> str | None:
    if not iso:
        return None
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%d %B %Y").lstrip("0")


def session_key() -> str:
    """A random key for this browser session: not personal, not derived from anything."""
    return st.session_state.setdefault("session_key", uuid.uuid4().hex)


# Callbacks (run before the script, so they may set the question box) ----------------------------------------


def fill_example(text: str) -> None:
    st.session_state["question_box"] = text


def edit_blocked() -> None:
    st.session_state["question_box"] = st.session_state.pop("blocked", "")


# Page pieces ------------------------------------------------------------------------------------------------------


def header(services: Services) -> None:
    st.title(ui.PAGE_TITLE)
    st.write(ui.TAGLINE)
    date = format_snapshot(services.snapshot)
    st.caption(ui.STATUS_STRIP.format(date=date) if date else ui.STATUS_STRIP_UNKNOWN)
    st.info(ui.DISCLAIMER)


def scope_box(first_question: bool) -> None:
    with st.expander(ui.SCOPE_TITLE, expanded=first_question):
        st.markdown(ui.SCOPE_TEXT)


def privacy_line() -> None:
    st.caption(ui.PRIVACY_LINE)
    with st.popover(ui.PRIVACY_LINK):
        st.markdown(ui.PRIVACY_DETAILS)


def examples_block() -> None:
    st.subheader(ui.EXAMPLES_TITLE)
    st.caption(ui.EXAMPLES_HELP)
    for i, example in enumerate(load_examples()):
        st.caption(example["topic"])
        st.button(example["question"], key=f"example-{i}", on_click=fill_example, args=(example["question"],),
                  use_container_width=True)


def sidebar(services: Services, identity, key: str) -> int | None:
    """The sidebar; returns the questions left today when a count limit applies, else None."""
    left = None
    with st.sidebar:
        date = format_snapshot(services.snapshot)
        if date:
            st.caption(ui.SIDEBAR_SNAPSHOT.format(date=date))
        st.caption(ui.SIDEBAR_DISCLAIMER)
        if identity.signed_in:
            st.caption(ui.SIDEBAR_SIGNED_IN_AS.format(display=identity.display or ""))
        elif services.deployment != "local":
            st.caption(ui.SIDEBAR_SIGN_IN)
        if services.service is not None:
            try:
                usage = services.service.usage_today(identity, key)
                limits = services.limits_for(identity, key)
                cap = None if limits is None else (limits.per_user_day if identity.signed_in else limits.per_session)
                st.markdown(f"**{ui.SIDEBAR_TODAY}**")
                st.caption(ui.SIDEBAR_QUESTIONS.format(n=usage["questions"]))
                if cap is not None:
                    left = max(cap - usage["questions"], 0)
                    st.caption(ui.SIDEBAR_LEFT.format(n=left))
                st.caption(ui.SIDEBAR_SPEND.format(amount=f"{usage['spend_usd']:.4f}"))
            except Exception as exc:  # noqa: BLE001
                LOG.error("usage figures failed (%s)", type(exc).__name__)
                st.caption(ui.SIDEBAR_USAGE_UNAVAILABLE)
    return left


def limit_text(services: Services, identity, key: str, reason: str | None) -> str:
    limits = services.limits_for(identity, key)
    if reason == "cost_cap":
        return ui.LIMIT_COST_CAP
    if reason == "anonymous_pool":
        return ui.LIMIT_ANONYMOUS_POOL
    if reason == "session_limit" and limits and limits.per_session is not None:
        return ui.LIMIT_SESSION.format(n=limits.per_session)
    if reason == "daily_limit" and limits and limits.per_user_day is not None:
        return ui.LIMIT_DAILY.format(n=limits.per_user_day)
    return ui.LIMIT_GENERIC


# Entries: this session's questions and answers ---------------------------------------------------------------


def render_entries(services: Services, key: str, reader) -> None:
    entries = st.session_state.setdefault("entries", [])
    if entries:
        st.subheader(ui.THIS_SESSION)
    for i, entry in enumerate(entries):
        with st.chat_message("user"):
            st.write(entry["question"])
        with st.chat_message("assistant"):
            if entry["kind"] == "card":
                cards.render_card(st, entry["card"], key=f"card-{i}", reader=reader,
                                  feedback=lambda card, rating, reason: save_feedback(services, key, card, rating, reason))
            else:
                st.error(entry["text"]) if entry.get("level") == "error" else st.warning(entry["text"])
                st.caption(ui.REFERENCE.format(reference=entry["reference"]))


def save_feedback(services: Services, key: str, card, rating: int, reason: str | None) -> bool:
    try:
        services.service.feedback(card.query_id, key, rating, reason)
        return True
    except Exception as exc:  # noqa: BLE001
        LOG.error("%s: feedback failed (%s)", card.reference, type(exc).__name__)
        return False


def finish_pending(services: Services, box) -> None:
    """Wait for the running question (staged status text, up to the UI wait), then show the outcome."""
    pending = st.session_state["pending"]
    sub, started = pending["submitted"], pending["started"]
    stage_1, stage_2 = services.stage_seconds
    outcome = None
    with box:
        with st.status(ui.STAGE_1, expanded=True) as status:
            while not sub.future.done():
                elapsed = time.monotonic() - started
                if elapsed >= services.ui_wait_s:
                    break
                status.update(label=ui.STAGE_1 if elapsed < stage_1 else ui.STAGE_2 if elapsed < stage_2 else ui.STAGE_3)
                concurrent.futures.wait([sub.future], timeout=services.poll_s)
            try:
                outcome = services.service.wait(sub, timeout=0)  # None after the UI wait: delivery ui_timeout
            except Exception as exc:  # noqa: BLE001 - the worker itself failed: nothing is shown
                LOG.error("%s: the worker failed (%s)", sub.reference, type(exc).__name__)
                outcome = None
            status.update(label=ui.STAGE_DONE, state="complete", expanded=False)
    entries = st.session_state.setdefault("entries", [])
    question, asked_at = pending["question"], pending["asked_at"]
    if outcome is None:
        entries.append({"kind": "message", "question": question, "reference": sub.reference, "level": "warning",
                        "text": ui.TIMEOUT_TEXT.format(reference=sub.reference)})
    elif not outcome.showable:  # withheld: logged badly, so not shown (D-153)
        entries.append({"kind": "message", "question": question, "reference": outcome.reference, "level": "error",
                        "text": ui.WITHHELD_TEXT.format(reference=outcome.reference)})
    else:
        card = cards.card_from_outcome(outcome, question, asked_at, services.chunks, services.config_sha256)
        try:
            services.service.deliver(outcome.query_id, "shown_plain" if card.ui_fallback else "shown", card.text)
        except Exception as exc:  # noqa: BLE001 - the answer is already in the log
            LOG.error("%s: the delivery could not be recorded (%s)", outcome.reference, type(exc).__name__)
        entries.append({"kind": "card", "question": question, "card": card})
    del st.session_state["pending"]
    st.rerun()


# The page ---------------------------------------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title=ui.PAGE_TITLE, layout="centered", initial_sidebar_state="expanded")
    services = get_services()
    header(services)
    if not services.ok:  # fail closed (Section 3.8): no question box
        st.error(ui.UNAVAILABLE_TEXT)
        return

    key = session_key()
    identity = services.identity()
    entries = st.session_state.setdefault("entries", [])
    pending = st.session_state.get("pending")
    left = sidebar(services, identity, key)

    scope_box(first_question=not entries)
    privacy_line()
    if not entries and pending is None and "blocked" not in st.session_state:
        examples_block()

    reader = cards.make_reader(st, services.chunks)
    render_entries(services, key, reader)
    live = st.container()

    # The question box
    if st.session_state.pop("clear_box", False):
        st.session_state["question_box"] = ""
    if "blocked" in st.session_state:  # D-160: a hard block; the only button is "Edit question"
        st.warning(f"**{ui.BLOCKED_NOTICE_TITLE}.** {ui.SCREEN_TEXT}")
        st.button(ui.EDIT_BUTTON, on_click=edit_blocked)
        return

    busy = pending is not None
    with st.form("ask", clear_on_submit=False):
        st.text_area(ui.QUESTION_LABEL, key="question_box", max_chars=ui.QUESTION_MAX_CHARS, height=120,
                     placeholder=ui.QUESTION_PLACEHOLDER, disabled=busy)
        submitted = st.form_submit_button(ui.ASK_BUTTON, disabled=busy or left == 0)
    st.caption(ui.SINGLE_TURN_LINE)

    if busy:
        finish_pending(services, live)
        return
    if not submitted:
        return
    question = (st.session_state.get("question_box") or "").strip()
    if not question:
        st.warning(ui.EMPTY_QUESTION)
        return
    sub = services.service.submit(question, identity, key)
    if sub.kind == "blocked":
        st.session_state["blocked"] = question
        st.rerun()
    elif sub.kind == "unrecorded":
        st.error(ui.UNRECORDED_TEXT)
    elif sub.kind == "limited":
        st.error(limit_text(services, identity, key, sub.reason))
    else:
        st.session_state["pending"] = {"submitted": sub, "question": question, "started": time.monotonic(),
                                       "asked_at": datetime.now(timezone.utc)}
        st.session_state["clear_box"] = True
        st.rerun()


if __name__ == "__main__":  # Streamlit runs the script as __main__; importing the module runs nothing
    main()
