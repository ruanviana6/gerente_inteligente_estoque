"""Atualização explícita da fotografia real a partir da própria aba Estoque.

A consulta continua exclusiva do Master e usa a integração GET já homologada;
nenhuma função envia escrita ao GestãoClick ou promove saldo ao motor.
"""
from __future__ import annotations

import streamlit as st

from integracoes.account_setup import consultar_conta, validar_conta
from integracoes.gestaoclick import GestaoClickError
from web.errors import AuthenticationError, AuthorizationError
from web.integration_catalog_full_page import catalog_full_controls
from web.integration_page import _secrets

_CONTA_ESTOQUE = "_estoque_conta_consultada"


def atualizar_fotografia_estoque(servico, contexto, loja_id: str) -> None:
    """Reusa a conferência do catálogo, fixando a loja da fotografia existente."""
    if contexto.profile.role != "Master" or not contexto.can("integrations.manage"):
        return
    with st.container(border=True):
        st.markdown("**Atualizar produtos, categorias e saldos da loja**")
        st.caption(
            "Esta operação consulta somente o GestãoClick por GET, mediante sua autorização, "
            "e permite salvar uma nova fotografia histórica no Supabase. Não altera o ERP, "
            "não faz baixa de estoque e não gera pedido."
        )
        with st.form("estoque_conferir_conta"):
            consentimento = st.checkbox(
                "Autorizo consultar lojas e situações do GestãoClick para atualizar esta fotografia"
            )
            iniciar = st.form_submit_button("Conferir conexão e loja do estoque")
        if iniciar:
            st.session_state.pop(_CONTA_ESTOQUE, None)
            if not consentimento:
                st.warning("Autorize a consulta das lojas antes de continuar.")
                return
            try:
                with st.spinner("Conferindo lojas e situações reais…"):
                    conta = consultar_conta(servico.auth, _secrets, autorizado=True)
            except (AuthenticationError, AuthorizationError):
                raise
            except GestaoClickError:
                st.warning("Não foi possível conferir as lojas. Nenhum dado foi alterado.")
                return
            st.session_state[_CONTA_ESTOQUE] = conta

        conta = st.session_state.get(_CONTA_ESTOQUE)
        if conta is None:
            return
        try:
            validar_conta(servico.auth, conta)
        except (AuthenticationError, AuthorizationError):
            st.session_state.pop(_CONTA_ESTOQUE, None)
            raise
        except GestaoClickError:
            st.session_state.pop(_CONTA_ESTOQUE, None)
            st.warning("A conferência da conta expirou. Consulte novamente.")
            return
        lojas = {loja["id"]: loja["nome"] for loja in conta.lojas}
        if loja_id not in lojas:
            st.error(
                "A loja desta fotografia não apareceu na consulta autorizada. "
                "Não é seguro ler ou salvar o catálogo com outra loja."
            )
            return
        st.info(
            f"Loja da fotografia: {lojas[loja_id]} (ID {loja_id}). "
            "A loja está fixa para impedir a mistura de estoques."
        )
        if catalog_full_controls(servico, _secrets, conta, loja_id):
            # Rerun do Streamlit, não reload do navegador: refaz a leitura da
            # fotografia salva sem apagar a sessão e sem nova consulta ao ERP.
            st.rerun()
