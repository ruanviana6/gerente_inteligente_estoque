"""Seletor compartilhado de loja para fotografias reais; nunca soma lojas."""
from __future__ import annotations

import streamlit as st

from integracoes.gestaoclick import GestaoClickError
from web.errors import AppError, AuthenticationError, AuthorizationError
from web.real_home_status import consultar_fontes_reais

_CHAVE = "_loja_real_selecionada"


def escolher_loja_real(auth) -> str | None:
    """Oferece apenas lojas ativas permitidas pela RPC na sessão atual."""
    try:
        fontes = consultar_fontes_reais(auth)
    except (AuthenticationError, AuthorizationError):
        raise
    except (AppError, GestaoClickError, TypeError, ValueError):
        st.warning("Não foi possível validar as lojas reais. Confira a integração.")
        return None
    lojas = [loja for _, loja in fontes]
    if not lojas:
        st.info("Nenhuma fonte real ativa. Registre uma loja em Administração → Integrações.")
        return None
    if len(lojas) == 1:
        return lojas[0]
    # Alterações na autorização ou fontes invalidam seleções persistidas.
    if st.session_state.get(_CHAVE) not in lojas:
        st.session_state.pop(_CHAVE, None)
    return st.selectbox(
        "Loja dos dados reais (atacado e varejo não são somados)",
        lojas,
        index=None,
        placeholder="Escolha a loja pelo ID conferido no GestãoClick",
        key=_CHAVE,
    )
