"""My questions: a signed-in user's own saved questions and answers (interface design, Section 7.3; D-164).

The list comes from `user_history(user_key)`, a database function that returns only the rows of the key
it is given. The key is derived server-side from the running session's sign-in (`services.identity()`),
never from a URL, a query parameter or a form field, and the page has no input that could carry one.
Opening an answer shows the stored rendered text with chips for its supporting passages and records the
delivery `shown_in_history`. "Delete my history" asks for confirmation, then calls `erase_user`.

Phase 9 builds and tests this with a fake identity; Phase 11 wires the real sign-in. The page settings, the
logo, the page links and the profile block come from `app/chrome.py` (UI revision); the page itself is unchanged.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
for _path in (str(ROOT / "python"), str(ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app import cards  # noqa: E402
from app import chrome  # noqa: E402
from app import copy as ui  # noqa: E402
from app.services import get_services  # noqa: E402

LOG = logging.getLogger("taxy.app")


def open_item(query_id: str) -> None:
    st.session_state["history_open"] = query_id
    st.session_state.pop("history_delivered", None)


def ask_delete() -> None:
    st.session_state["confirm_delete"] = True


def cancel_delete() -> None:
    st.session_state.pop("confirm_delete", None)


def main() -> None:
    chrome.page_setup(f"{ui.HISTORY_TITLE} · {ui.APP_NAME}")
    services = get_services()
    chrome.nav_links()
    st.title(ui.HISTORY_TITLE)
    st.info(ui.DISCLAIMER)
    if not services.ok:
        st.error(ui.UNAVAILABLE_TEXT)
        return
    identity = services.identity()
    chrome.profile_block(services, identity)
    if not identity.signed_in:
        st.info(ui.HISTORY_NOT_SIGNED_IN)
        return

    deleted = st.session_state.pop("history_deleted", None)
    if deleted is not None:
        st.success(ui.DELETE_DONE.format(n=deleted))

    try:
        items = services.service.history(identity)
    except Exception as exc:  # noqa: BLE001
        LOG.error("history failed (%s)", type(exc).__name__)
        st.error(ui.HISTORY_UNAVAILABLE)
        return
    if not items:
        st.caption(ui.HISTORY_EMPTY)
    reader = cards.make_reader(st, services.chunks)
    for item in items:
        label = ui.HISTORY_ITEM.format(reference=item.reference, when=item.received_at.strftime("%Y-%m-%d %H:%M"),
                                       status=ui.STATUS_LINES.get(item.status, item.status))
        with st.container(border=True):
            st.markdown(f"**{label}**")
            st.write(item.question)
            st.button(ui.HISTORY_ANSWER, key=f"open-{item.query_id}", on_click=open_item, args=(item.query_id,))
            if st.session_state.get("history_open") == item.query_id:
                card = cards.card_from_history(item, services.chunks)
                if not st.session_state.get("history_delivered"):
                    try:
                        services.service.open_history_item(identity, item)
                    except Exception as exc:  # noqa: BLE001
                        LOG.error("%s: the history delivery failed (%s)", item.reference, type(exc).__name__)
                    st.session_state["history_delivered"] = True
                cards.render_card(st, card, key=f"hist-{item.query_id}", reader=reader)

    st.divider()
    if st.session_state.get("confirm_delete"):
        st.warning(ui.DELETE_CONFIRM)
        left, right = st.columns(2)
        with left:
            confirmed = st.button(ui.DELETE_CONFIRM_BUTTON, key="delete-yes", type="primary")
        with right:
            st.button(ui.DELETE_CANCEL_BUTTON, key="delete-no", on_click=cancel_delete)
        if confirmed:
            try:
                n = services.service.erase_history(identity)
            except Exception as exc:  # noqa: BLE001
                LOG.error("erase failed (%s)", type(exc).__name__)
                st.error(ui.DELETE_FAILED)
                return
            st.session_state.pop("confirm_delete", None)
            st.session_state.pop("history_open", None)
            st.session_state["history_deleted"] = n
            sign_out = services.extra.get("sign_out")  # Phase 11 wires st.logout() here
            if sign_out is not None:
                sign_out()
            st.rerun()
    else:
        st.button(ui.DELETE_BUTTON, key="delete-ask", on_click=ask_delete)


if __name__ == "__main__":  # Streamlit runs the script as __main__; importing the module runs nothing
    main()
