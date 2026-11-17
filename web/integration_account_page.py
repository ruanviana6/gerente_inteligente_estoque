"""Conferência explícita de lojas e situações sem expor credenciais do servidor."""

from __future__ import annotations

import json

import streamlit as st

from integracoes.account_setup import (
    PaginacaoSituacoesInvalida,
    consultar_conta,
    registrar_fonte,
    validar_conta,
)
from integracoes.gestaoclick import GestaoClickError
from web.errors import AuthenticationError, AuthorizationError
from web.integration_catalog_preview_page import catalog_preview_controls
from web.integration_catalog_full_page import catalog_full_controls
from web.integration_native_preview_page import native_preview_controls

_ACCOUNT_KEY = "_gestaoclick_account_setup"
_SOURCE_KEY = "_gestaoclick_registered_source"


def account_setup_controls(servico, carregar_configuracao):
    """Consulta só após confirmação e mantém o catálogo restrito à sessão Master.

    IDs vêm da API. Registrar uma fonte escreve apenas no Supabase; não altera
    a loja no ERP, não importa vendas e não ativa o agendador. O objeto assinado
    expira e é invalidado quando a identidade ou a autorização muda.
    """
    with st.expander("Conferir lojas e situações da conta"):
        st.caption("Este passo identifica as lojas e situações acessíveis à sua chave. "
                   "Não exige preencher mapas JSON e não importa vendas.")
        with st.form("conferir_conta_gestaoclick"):
            autorizado = st.checkbox("Autorizo consultar as lojas e situações da minha conta")
            consultar = st.form_submit_button("Consultar lojas e situações")
        if consultar:
            st.session_state.pop(_ACCOUNT_KEY, None)
            st.session_state.pop(_SOURCE_KEY, None)
            if not autorizado:
                st.warning("Marque a autorização antes da consulta de leitura.")
                return
            try:
                with st.spinner("Conferindo lojas e situações autorizadas…"):
                    conta = consultar_conta(servico.auth, carregar_configuracao, autorizado=True)
            except (AuthenticationError, AuthorizationError):
                raise
            except GestaoClickError as erro:
                st.warning(str(erro))
                if isinstance(erro, PaginacaoSituacoesInvalida):
                    st.caption("Diagnóstico técnico: somente nomes e tipos dos metadados, sem valores da conta.")
                    st.json(erro.diagnostico, expanded=False)
                return
            st.session_state[_ACCOUNT_KEY] = conta
            st.success("Lojas e situações consultadas. Nenhuma venda foi importada.")

        conta = st.session_state.get(_ACCOUNT_KEY)
        if conta is None:
            return
        try:
            validar_conta(servico.auth, conta)
        except (AuthenticationError, AuthorizationError):
            st.session_state.pop(_ACCOUNT_KEY, None)
            st.session_state.pop(_SOURCE_KEY, None)
            raise
        except GestaoClickError:
            st.session_state.pop(_ACCOUNT_KEY, None)
            st.session_state.pop(_SOURCE_KEY, None)
            st.info("A conferência expirou. Consulte as lojas novamente para continuar.")
            return

        lojas = {loja["id"]: loja["nome"] for loja in conta.lojas}
        if not lojas:
            st.warning("A chave não retornou lojas. Confira as permissões no GestãoClick.")
            return
        loja_id = st.selectbox("Loja para a cópia analítica", list(lojas), index=None,
                               placeholder="Escolha a loja conferida",
                               format_func=lambda valor: f"{lojas[valor]} · ID {valor}",
                               key="gestaoclick_setup_store")
        st.caption("Escolha a loja pelo nome e ID retornados. A ordem da lista não define varejo ou atacado.")
        if conta.situacoes:
            st.dataframe([{"ID": situacao["id"], "Situação no ERP": situacao["nome"]}
                          for situacao in conta.situacoes], hide_index=True, width="stretch")
        else:
            st.warning("Nenhuma situação de venda foi retornada; o mapeamento ainda não pode ser validado.")
        st.caption("Os nomes e códigos são específicos da conta. Eles não são classificados "
                   "automaticamente como venda concluída, devolução ou cancelamento.")

        if loja_id is not None:
            native_preview_controls(servico, carregar_configuracao, conta, loja_id)
            catalog_preview_controls(servico, carregar_configuracao, conta, loja_id)
            catalog_full_controls(servico, carregar_configuracao, conta, loja_id)

        if st.button("Registrar fonte analítica desta loja", disabled=loja_id is None,
                     key="gestaoclick_setup_register"):
            try:
                fonte = registrar_fonte(servico.auth, conta, loja_id)
            except (AuthenticationError, AuthorizationError):
                raise
            except GestaoClickError as erro:
                st.warning(str(erro))
                return
            st.session_state[_SOURCE_KEY] = {"source_id": fonte, "store_id": loja_id}
            st.success("Fonte registrada no Gerente. Nenhuma alteração foi enviada ao ERP.")
        fonte = st.session_state.get(_SOURCE_KEY)
        if fonte and fonte["store_id"] == loja_id:
            st.code("GESTAOCLICK_SOURCE_ID = " + json.dumps(fonte["source_id"]) + "\n"
                    "GESTAOCLICK_STORE_ID = " + json.dumps(loja_id), language="toml", wrap_lines=True)
            st.info("Estes são os identificadores para os Secrets. O registro da fonte ainda não "
                    "valida campos de vendas, devoluções nem ativa a sincronização automática.")
