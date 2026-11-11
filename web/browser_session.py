"""Cookie opaco do navegador; não contém senha ou tokens do Supabase."""

from __future__ import annotations

import json
from uuid import uuid4

import streamlit as st
from streamlit.components.v1 import html

from web.persistent_sessions import COOKIE_DAYS, COOKIE_NAME, split_ticket

STATE_KEYS = frozenset({"_browser_cookie_action", "_skip_session_restore", "_restore_attempted", "_persistence_notice"})


def set_browser_cookie(ticket: str | None) -> None:
    """Agenda escrita ou remoção na mesma origem, preservada até o próximo reload."""
    if ticket is not None:
        split_ticket(ticket)
    st.session_state["_browser_cookie_action"] = (ticket or "", uuid4().hex)
    if ticket is None:
        st.session_state["_skip_session_restore"] = True


def render_browser_cookie() -> None:
    """Usa iframe local para cookie Secure/SameSite; nunca carrega scripts externos.

    Streamlit não fornece Set-Cookie HttpOnly para cookies próprios. Somente o
    identificador aleatório é visível ao JavaScript; os tokens ficam cifrados no
    backend. Não interpolar dados de negócio ou qualquer outra credencial aqui.
    """
    action = st.session_state.get("_browser_cookie_action")
    if not action:
        return
    ticket, operation = action
    if ticket:
        split_ticket(ticket)
    age = COOKIE_DAYS * 86400 if ticket else 0
    cookie = f"{COOKIE_NAME}={ticket}; Path=/; Max-Age={age}; Secure; SameSite=Strict"
    html("<script>\n" + f"const operation = {json.dumps(operation)};\n"
         + "try { window.parent.document.cookie = " + json.dumps(cookie) + "; } catch (_) {}\n"
         + "</script>", height=0, scrolling=False)


def incoming_ticket() -> str | None:
    """Lê exclusivamente o cookie próprio recebido na conexão inicial do Streamlit."""
    if st.session_state.get("_skip_session_restore") or st.session_state.get("_restore_attempted"):
        return None
    st.session_state["_restore_attempted"] = True
    value = st.context.cookies.get(COOKIE_NAME)
    if value:
        split_ticket(value)
    return value
