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
from integracoes.fiscal_read_probe import consultar_recurso_nfe
from integracoes.gestaoclick import GestaoClickError
from fiscal.lojas import validar_lojas_fiscais
from web.errors import AuthenticationError, AuthorizationError
from web.integration_catalog_full_page import catalog_full_controls
from web.integration_catalog_preview_page import catalog_preview_controls
from web.integration_native_preview_page import native_preview_controls
from web.integration_sales_audit_page import sales_audit_controls

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
        with st.expander("Conferir lojas da automação fiscal (sem gravação)"):
            st.caption(
                "Selecione separadamente a origem FLT Comércio e o destino "
                "Flagship - Flatter, conforme o cadastro retornado pelo ERP. "
                "Esta conferência não consulta notas ou pedidos, não salva "
                "identificadores e não ativa o cadastro fiscal."
            )
            with st.form("validar_lojas_fiscais"):
                origem = st.selectbox(
                    "Origem fiscal — FLT Comércio (atacado)",
                    list(lojas), index=None, key="fiscal_loja_origem",
                    format_func=lambda valor: f"{lojas[valor]} · ID {valor}",
                    placeholder="Selecione a loja de origem",
                )
                destino = st.selectbox(
                    "Destino fiscal — Flagship - Flatter (varejo)",
                    list(lojas), index=None, key="fiscal_loja_destino",
                    format_func=lambda valor: f"{lojas[valor]} · ID {valor}",
                    placeholder="Selecione a loja de destino",
                )
                conferir_papeis = st.form_submit_button("Conferir origem e destino")
            if conferir_papeis:
                try:
                    validar_conta(servico.auth, conta)
                    id_origem, id_destino = validar_lojas_fiscais(
                        conta.lojas, origem, destino,
                    )
                except (AuthenticationError, AuthorizationError):
                    raise
                except GestaoClickError as erro:
                    st.warning(str(erro))
                else:
                    st.success(
                        f"Papéis conferidos no cadastro: origem {lojas[id_origem]} "
                        f"(ID {id_origem}) → destino {lojas[id_destino]} "
                        f"(ID {id_destino}). Nenhum dado foi alterado."
                    )
                    st.info(
                        "A conferência de nomes não comprova a loja efetiva do token, "
                        "permissão de cadastrar, correspondência fiscal nem ausência "
                        "de emissão automática. O cadastro real continua bloqueado."
                    )

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
            sales_audit_controls(servico, carregar_configuracao, conta, loja_id)

            with st.expander("Verificar leitura de NF-e (sem cadastro e sem emissão)"):
                st.caption(
                    "Teste fiscal independente da auditoria de vendas. Executa apenas um GET "
                    "de até um registro da loja selecionada; descarta dados pessoais e "
                    "não registra, emite, altera ou cancela notas. Resposta 200 não "
                    "comprova que a loja efetiva da credencial seja esta."
                )
                with st.form("fiscal_probe_readonly"):
                    consentimento = st.checkbox(
                        "Autorizo exclusivamente esta consulta de leitura de NF-e, sem gravação",
                        key="fiscal_probe_consentimento",
                    )
                    testar = st.form_submit_button("Testar leitura fiscal")
                if testar:
                    if not consentimento:
                        st.warning("Marque a autorização da consulta de leitura.")
                    else:
                        try:
                            with st.spinner("Testando somente GET de NF-e…"):
                                diagnostico = consultar_recurso_nfe(
                                    servico.auth, carregar_configuracao, conta, loja_id,
                                    autorizado=True,
                                )
                        except (AuthenticationError, AuthorizationError):
                            raise
                        except GestaoClickError as erro:
                            st.warning(str(erro))
                        else:
                            st.success(
                                "A leitura do recurso NF-e respondeu na credencial. "
                                "Nenhuma nota foi cadastrada, emitida, importada ou alterada."
                            )
                            st.write({
                                "Recurso": diagnostico["recurso"],
                                "Loja selecionada (cadastro de lojas)": lojas[loja_id],
                                "Loja solicitada": diagnostico["loja_solicitada"],
                                "Registros vistos no teste": diagnostico["itens_amostra"],
                            })
                            if diagnostico["loja_no_registro_confere"] is False:
                                st.error(
                                    "A nota consultada declarou uma loja DIFERENTE da solicitada. "
                                    "O token pode estar vinculado a outra loja; não permitir cadastro "
                                    "fiscal até corrigir e homologar o escopo."
                                )
                            elif diagnostico["loja_no_registro_confere"] is True:
                                st.info(
                                    "O registro de exemplo declara a loja solicitada. "
                                    "Isso não comprova acesso a todas as notas nem a permissão de cadastrar."
                                )
                            else:
                                st.info(
                                    "A amostra não permitiu confirmar a loja efetivamente usada "
                                    "pela API. É necessário conferir o token e os registros no ERP."
                                )
                            st.warning(
                                "Teste de leitura, NÃO homologação fiscal: o acesso real à loja "
                                "de destino, a proteção contra duplicidade e os efeitos de "
                                "um cadastro ainda precisam de confirmação. Nenhum POST foi liberado."
                            )

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
