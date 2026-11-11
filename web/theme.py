"""Identidade Flatter com tema nativo e componentes de apresentação pequenos.

Somente CSS estático entra em HTML. Nomes de pessoas, produtos,
fornecedores e quaisquer valores do banco passam exclusivamente por widgets
nativos do Streamlit. Este módulo não consulta dados nem guarda estado de sessão.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import streamlit as st

Tone = Literal["neutral", "danger", "warning", "success", "info", "brand"]
Width = int | Literal["stretch", "content"]

PALETTE = {
    "background": "#FBF8F6",
    "sidebar": "#F4ECE8",
    "surface": "#FFFFFF",
    "brand": "#9C4438",
    "brand_text": "#8B332E",
    "text": "#2D1C25",
    "secondary": "#66575F",
    "border": "#8A727A",
    "danger": "#AA2737",
    "warning": "#875900",
    "success": "#1D705A",
    "info": "#69417C",
    "plum": "#61334D",
}

LOGO_PATH = Path(__file__).resolve().parents[1] / "assets" / "flatter-logo.jpg"

_BADGE_COLORS = {
    "neutral": "gray", "danger": "red", "warning": "yellow",
    "success": "green", "info": "blue", "brand": "primary",
}

# As classes st-key são uma API documentada de st.container. Não dependemos de
# data-testid, hashes de CSS, ordem dos elementos ou detalhes internos do DOM.
STYLES = """
<style>
.st-key-flatter_brand_logo, .st-key-flatter_brand_logo_compact {
    background: #9C4438;
    border-radius: 0.7rem;
    width: fit-content;
    max-width: 100%;
    overflow: hidden;
}
.st-key-flatter_brand_logo img, .st-key-flatter_brand_logo_compact img {
    height: 4.5rem;
    object-fit: cover;
    filter: grayscale(1) invert(1) brightness(4);
    mix-blend-mode: screen;
}
.st-key-flatter_brand_logo_compact img { height: 3.5rem; }
.st-key-flatter_brand_caption, .st-key-flatter_brand_caption_compact {
    margin-top: 0.15rem;
    margin-bottom: 0.4rem;
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
    background: #FFFFFF;
    border: 1px solid #E7DAD8;
    border-radius: 0.8rem;
    padding: 1rem;
    min-width: 0;
    max-width: 100%;
    box-sizing: border-box;
}
[class*="st-key-flatter_metric_"] {
    border-top-width: 3px;
}
[class*="st-key-flatter_metric_danger_"],
[class*="st-key-flatter_surface_danger_"] { border-top-color: #AA2737; }
[class*="st-key-flatter_metric_warning_"],
[class*="st-key-flatter_surface_warning_"] { border-top-color: #875900; }
[class*="st-key-flatter_metric_success_"],
[class*="st-key-flatter_surface_success_"] { border-top-color: #1D705A; }
[class*="st-key-flatter_metric_info_"],
[class*="st-key-flatter_surface_info_"] { border-top-color: #69417C; }
[class*="st-key-flatter_metric_brand_"],
[class*="st-key-flatter_surface_brand_"] { border-top-color: #9C4438; }
[class*="st-key-flatter_"] button,
[class*="st-key-flatter_"] input:not([type="radio"]):not([type="checkbox"]),
[class*="st-key-flatter_"] [role="combobox"] {
    min-height: 44px;
}
[class*="st-key-flatter_"] button {
    min-width: 44px;
}
[class*="st-key-flatter_"] button:focus-visible,
[class*="st-key-flatter_"] input:focus-visible,
[class*="st-key-flatter_"] [role="combobox"]:focus-visible {
    outline: 2px solid #69417C;
    outline-offset: 3px;
}
.st-key-flatter_surface_neutral_login {
    max-width: 34rem;
    margin-inline: auto;
    border-top: 4px solid #61334D;
    box-shadow: 0 12px 32px rgba(71, 33, 50, 0.06);
}
.st-key-flatter_navigation label {
    padding-block: 0.4rem;
    min-height: 44px;
    box-sizing: border-box;
}
@media (max-width: 640px) {
    .st-key-flatter_page_header h1 { font-size: 1.65rem; }
    [class*="st-key-flatter_surface_"],
    [class*="st-key-flatter_metric_"] { padding: 0.85rem; }
}
</style>
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
    """Mostra imagem local da marca com texto alternativo e dimensões responsivas."""
    sufixo = "_compact" if compact else ""
    with st.container(key="flatter_brand_logo" + sufixo, width="content"):
        st.image(str(LOGO_PATH), width=125 if compact else 170, alt="Logotipo da Flatter")
    with st.container(key="flatter_brand_caption" + sufixo):
        st.caption("GERENTE DE ESTOQUE" if compact else "INTELIGÊNCIA DE ESTOQUE")


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
