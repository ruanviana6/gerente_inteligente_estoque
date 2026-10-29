"""Identidade Flatter com tema nativo e componentes de apresentação pequenos.

Somente CSS e lettering estáticos entram em HTML. Nomes de pessoas, produtos,
fornecedores e quaisquer valores do banco passam exclusivamente por widgets
nativos do Streamlit. Este módulo não consulta dados nem guarda estado de sessão.
"""
from __future__ import annotations

import re
from typing import Any, Literal

import streamlit as st

Tone = Literal["neutral", "danger", "warning", "success", "info", "brand"]
Width = int | Literal["stretch", "content"]

PALETTE = {
    "background": "#101720",
    "sidebar": "#151E2B",
    "surface": "#1C2937",
    "brand": "#B55440",
    "brand_text": "#D97963",
    "text": "#F5F7FA",
    "secondary": "#B8C2CE",
    "border": "#6C7A8B",
    "danger": "#FFADAD",
    "warning": "#F8D17B",
    "success": "#8CD5B3",
    "info": "#9FC7FF",
}

_BADGE_COLORS = {
    "neutral": "gray", "danger": "red", "warning": "yellow",
    "success": "green", "info": "blue", "brand": "primary",
}

# As classes st-key são uma API documentada de st.container. Não dependemos de
# data-testid, hashes de CSS, ordem dos elementos ou detalhes internos do DOM.
STYLES = """
<style>
.flatter-wordmark {
    display: inline-flex;
    align-items: center;
    background: #B55440;
    color: #FFFFFF;
    padding: 0.2rem 1.2rem 0.55rem;
    border-radius: 0.8rem;
    font-family: Georgia, Cambria, "Times New Roman", serif;
    font-size: clamp(2.6rem, 8vw, 3.5rem);
    font-weight: 400;
    line-height: 1.1;
    letter-spacing: -0.065em;
    max-width: 100%;
    box-sizing: border-box;
}
.flatter-wordmark--compact {
    font-size: 2.35rem;
    padding: 0.15rem 0.85rem 0.45rem;
}
.flatter-brand-caption {
    color: #B8C2CE;
    font-size: 0.75rem;
    letter-spacing: 0.09em;
    margin-top: 0.6rem;
    margin-bottom: 0.5rem;
    text-transform: uppercase;
}
.st-key-flatter_page_header {
    margin-bottom: 0.2rem;
}
.st-key-flatter_page_header h1 {
    letter-spacing: -0.025em;
    text-wrap: balance;
    overflow-wrap: anywhere;
}
[class*="st-key-flatter_surface_"],
[class*="st-key-flatter_metric_"] {
    background: #1C2937;
    border: 1px solid #334355;
    border-radius: 0.9rem;
    padding: 1rem;
    min-width: 0;
    max-width: 100%;
    box-sizing: border-box;
}
[class*="st-key-flatter_metric_"] {
    border-top-width: 3px;
}
[class*="st-key-flatter_metric_danger_"],
[class*="st-key-flatter_surface_danger_"] { border-top-color: #FFADAD; }
[class*="st-key-flatter_metric_warning_"],
[class*="st-key-flatter_surface_warning_"] { border-top-color: #F8D17B; }
[class*="st-key-flatter_metric_success_"],
[class*="st-key-flatter_surface_success_"] { border-top-color: #8CD5B3; }
[class*="st-key-flatter_metric_info_"],
[class*="st-key-flatter_surface_info_"] { border-top-color: #9FC7FF; }
[class*="st-key-flatter_metric_brand_"],
[class*="st-key-flatter_surface_brand_"] { border-top-color: #D97963; }
[class*="st-key-flatter_"] button,
[class*="st-key-flatter_"] input:not([type="radio"]):not([type="checkbox"]),
[class*="st-key-flatter_"] [role="combobox"] {
    min-height: 2.75rem;
}
[class*="st-key-flatter_"] button {
    min-width: 2.75rem;
}
[class*="st-key-flatter_"] button:focus-visible,
[class*="st-key-flatter_"] input:focus-visible,
[class*="st-key-flatter_"] [role="combobox"]:focus-visible {
    outline: 2px solid #9FC7FF;
    outline-offset: 3px;
}
.st-key-flatter_surface_neutral_login {
    max-width: 34rem;
    margin-inline: auto;
}
.st-key-flatter_navigation label {
    padding-block: 0.4rem;
    min-height: 2.75rem;
    box-sizing: border-box;
}
@media (max-width: 640px) {
    .st-key-flatter_page_header h1 { font-size: 1.65rem; }
    [class*="st-key-flatter_surface_"],
    [class*="st-key-flatter_metric_"] { padding: 0.85rem; }
}
</style>
"""

_BRAND = """
<div class="flatter-wordmark">Flatter</div>
<p class="flatter-brand-caption">Inteligência de estoque</p>
"""
_BRAND_COMPACT = """
<div class="flatter-wordmark flatter-wordmark--compact">Flatter</div>
<p class="flatter-brand-caption">Gerente de estoque</p>
"""


def _component_key(kind: str, key: str, tone: Tone) -> str:
    """Restringe identificadores de apresentação e recusa tons não definidos."""
    if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", key):
        raise ValueError("A chave do componente deve conter letras, números, hífen ou sublinhado.")
    if tone not in _BADGE_COLORS:
        raise ValueError("Tom visual não reconhecido.")
    return f"flatter_{kind}_{tone}_{key}"


def apply_theme() -> None:
    """Complementa o tema TOML com estilos estáticos de componentes identificados."""
    st.html(STYLES)


def brand_header(compact: bool = False) -> None:
    """Exibe a marca em texto; não simula nem substitui um arquivo de logo oficial."""
    st.html(_BRAND_COMPACT if compact else _BRAND)


def page_header(title: str, subtitle: str = "") -> None:
    """Apresenta um cabeçalho contextual, sem interpolar conteúdo em HTML."""
    with st.container(key="flatter_page_header"):
        st.title(title, anchor=False)
        if subtitle:
            st.caption(subtitle)


def surface(key: str, *, tone: Tone = "neutral", width: Width = "stretch") -> Any:
    """Cria uma superfície nativa que pode conter filtros, gráficos ou ações reais."""
    return st.container(border=False, key=_component_key("surface", key, tone), width=width)


def metric_card(
    label: str, value: Any, *, key: str, help: str | None = None,
    note: str | None = None, tone: Tone = "neutral", width: Width = 220,
) -> None:
    """Mostra um indicador já calculado; o rótulo explica o significado da cor."""
    with st.container(border=False, key=_component_key("metric", key, tone), width=width):
        st.metric(label, value, help=help)
        if note:
            st.caption(note)


def status_badge(label: str, *, tone: Tone = "neutral") -> None:
    """Combina texto e cor nativos para que o estado não dependa somente da cor."""
    if tone not in _BADGE_COLORS:
        raise ValueError("Tom visual não reconhecido.")
    st.badge(label, color=_BADGE_COLORS[tone])
