"""What every page shares (UI revision, docs/phase09_interface/ui_revision.md): the page settings with the
Taxy icon, the logo, the page links at the top of the sidebar and the profile block at its bottom.

Profile block (UI-14):

- signed in: "Signed in as ..." from the running session's identity, as plain text with no Markdown, so an
  email is never turned into a link (the display text is never stored, D-163), "Delete my history" (a
  confirmation dialog, then the same `erase_history` call and sign-out as the My questions page) and
  "Log out" (`services.extra["sign_out"]`, which Phase 11 wires to `st.logout()`). My questions is not
  repeated here: it is already in the page links at the top of the sidebar;
- signed out on a hosted deployment: "Sign in with Google" (`services.extra["sign_in"]`, wired to `st.login`
  in Phase 11; until then the button is disabled and the "later phase" note is shown);
- local and signed out: not shown.

Every text comes from `app/copy.py`. No CSS, HTML or JavaScript: the block is the last item of the sidebar.
"""
from __future__ import annotations

import logging
from pathlib import Path

import streamlit as st

from app import copy as ui

LOG = logging.getLogger("taxy.app")
ASSETS = Path(__file__).resolve().parent / "assets"
LOGO = str(ASSETS / "taxy_logo.svg")   # the wide badge with the name, for the sidebar
ICON = str(ASSETS / "taxy_icon.svg")   # the square icon: browser tab, closed sidebar, answer avatar
MAIN_PAGE = "main.py"                  # page paths relative to the entry point, app/main.py
HISTORY_PAGE = "pages/my_questions.py"
PROFILE_DONE = "profile_deleted"


def page_setup(title: str) -> None:
    """Page settings and the logo; the sidebar starts closed on a phone and open on a desktop (UI-10)."""
    st.set_page_config(page_title=title, page_icon=ICON, layout="centered", initial_sidebar_state="auto")
    st.logo(LOGO, icon_image=ICON, size="large")


def nav_links() -> None:
    """The two pages by name (UI-9); the file-name list is hidden in `.streamlit/config.toml`."""
    with st.sidebar:
        st.page_link(MAIN_PAGE, label=ui.NAV_ASK, icon=":material/chat:")
        st.page_link(HISTORY_PAGE, label=ui.HISTORY_TITLE, icon=":material/history:")


PROFILE_CONFIRM = "profile_confirm_delete"


def ask_delete() -> None:
    st.session_state[PROFILE_CONFIRM] = True


def cancel_delete() -> None:
    st.session_state.pop(PROFILE_CONFIRM, None)


@st.dialog(ui.DELETE_BUTTON, on_dismiss=cancel_delete)
def delete_dialog(services, identity) -> None:
    """Open while the confirm flag is set: closing it, Cancel or a finished delete clears the flag."""
    st.warning(ui.DELETE_CONFIRM)
    left, right = st.columns(2)
    with left:
        confirmed = st.button(ui.DELETE_CONFIRM_BUTTON, key="profile-delete-yes", type="primary")
    with right:
        st.button(ui.DELETE_CANCEL_BUTTON, key="profile-delete-no", on_click=cancel_delete)
    if not confirmed:
        return
    cancel_delete()
    try:
        n = services.service.erase_history(identity)
    except Exception as exc:  # noqa: BLE001
        LOG.error("erase failed (%s)", type(exc).__name__)
        st.error(ui.DELETE_FAILED)
        return
    st.session_state[PROFILE_DONE] = n
    st.session_state.pop("history_open", None)
    sign_out = services.extra.get("sign_out")
    if sign_out is not None:
        sign_out()
    st.rerun()


def profile_block(services, identity) -> None:
    """The last item of the sidebar (UI-14). Call it after everything else the page puts in the sidebar."""
    deleted = st.session_state.pop(PROFILE_DONE, None)
    if deleted is not None:
        st.sidebar.success(ui.DELETE_DONE.format(n=deleted))
    if not identity.signed_in and services.deployment == "local":
        return
    with st.sidebar:
        st.divider()
        with st.container(key="profile"):
            if identity.signed_in:
                st.text(ui.SIDEBAR_SIGNED_IN_AS.format(display=identity.display or ""))  # no Markdown: no mailto
                st.button(ui.DELETE_BUTTON, key="profile-delete", icon=":material/delete:", on_click=ask_delete)
                if st.session_state.get(PROFILE_CONFIRM):
                    delete_dialog(services, identity)
                sign_out = services.extra.get("sign_out")
                if st.button(ui.LOG_OUT_BUTTON, key="profile-log-out", icon=":material/logout:",
                             disabled=sign_out is None):
                    sign_out()
            else:
                sign_in = services.extra.get("sign_in")
                if st.button(ui.SIGN_IN_BUTTON, key="profile-sign-in", icon=":material/login:",
                             disabled=sign_in is None):
                    sign_in()
                if sign_in is None:
                    st.caption(ui.SIDEBAR_SIGN_IN)
