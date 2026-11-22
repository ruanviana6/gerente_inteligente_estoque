"""Leitura fiscal real na própria aba NF-e, sem acionar cadastro ou emissão.

Usa a mesma configuração server-side do conector comprovado na Administração.
Nenhuma credencial ou resposta fiscal com clientes é guardada no navegador.
"""
from __future__ import annotations

import streamlit as st

from fiscal.lojas import validar_lojas_fiscais
from integracoes.account_setup import consultar_conta, validar_conta
from integracoes.fiscal_read_probe import consultar_recurso_nfe
from integracoes.gestaoclick import GestaoClickError
from web.errors import AuthenticationError, AuthorizationError
from web.fiscal_origin_lookup_page import fiscal_order_lookup_controls
from web.integration_page import _secrets

_REAL_ACCOUNT_KEY = "_fiscal_real_account_readonly"


def fiscal_real_read_controls(servico, contexto):
    """Exibe consultas fiscais reais só ao Master, sem misturar o fluxo DEMO."""
    if contexto.profile.role != "Master" or not contexto.can("integrations.manage"):
        return
    with st.expander("API real GestãoClick — consultar vendas e NF-e (somente leitura)", expanded=True):
        st.caption(
            "Conexão única, origem FLT Comércio e destino Flagship - Flatter. "
            "A consulta real é separada da simulação. Nenhum botão desta área "
            "cadastra, edita, transmite ou cancela NF-e."
        )
        if st.button("Atualizar lojas e situações do ERP (GET)", key="fiscal_real_refresh_account"):
            st.session_state.pop(_REAL_ACCOUNT_KEY, None)
            try:
                with st.spinner("Consultando lojas e situações autorizadas…"):
                    conta = consultar_conta(servico.auth, _secrets, autorizado=True)
                st.session_state[_REAL_ACCOUNT_KEY] = conta
                st.success("Lojas e situações reais consultadas. Nenhum dado do ERP foi alterado.")
            except (AuthenticationError, AuthorizationError):
                raise
            except GestaoClickError:
                st.warning("O GestãoClick não respondeu à consulta autorizada. Verifique a conexão.")
                return
        conta = st.session_state.get(_REAL_ACCOUNT_KEY)
        if conta is None:
            st.info(
                "Consulte as lojas acima uma vez por sessão para preparar a conferência fiscal real."
            )
            return
        try:
            validar_conta(servico.auth, conta)
        except (AuthenticationError, AuthorizationError):
            st.session_state.pop(_REAL_ACCOUNT_KEY, None)
            raise
        except GestaoClickError:
            st.session_state.pop(_REAL_ACCOUNT_KEY, None)
            st.warning("A consulta venceu ou a configuração mudou. Atualize as lojas novamente.")
            return
        lojas = {x["id"]: x["nome"] for x in conta.lojas}
        if len(lojas) != len(conta.lojas):
            st.warning("IDs de lojas repetidos na API. Consulte o ERP.")
            return
        with st.form("fiscal_real_roles_readonly"):
            origem = st.selectbox(
                "Loja de origem (atacado)", list(lojas), index=None,
                format_func=lambda x: f"{lojas[x]} · {x}",
                placeholder="FLT Comércio", key="fiscal_real_origem",
            )
            destino = st.selectbox(
                "Loja de destino (varejo)", list(lojas), index=None,
                format_func=lambda x: f"{lojas[x]} · {x}",
                placeholder="Flagship - Flatter", key="fiscal_real_destino",
            )
            conferir = st.form_submit_button("Conferir os papéis das lojas")
        if origem is None or destino is None:
            st.info("Selecione ambas as lojas do catálogo para continuar.")
            return
        try:
            validar_conta(servico.auth, conta)
            origem_id, destino_id = validar_lojas_fiscais(conta.lojas, origem, destino)
        except (AuthenticationError, AuthorizationError):
            raise
        except GestaoClickError:
            st.warning("As lojas selecionadas não correspondem ao fluxo fiscal autorizado.")
            return
        if conferir:
            st.success(
                f"Origem {lojas[origem_id]} ({origem_id}) → "
                f"Destino {lojas[destino_id]} ({destino_id}). "
                "Essa conferência não confirma o escopo efetivo do token."
            )
        with st.form("fiscal_real_get_probe"):
            permitido = st.checkbox(
                "Autorizo uma consulta de NF-e existentes na loja de destino, apenas GET",
                key="fiscal_real_probe_consent",
            )
            verificar = st.form_submit_button("Verificar acesso às NF-e da Flagship")
        if verificar:
            if not permitido:
                st.warning("Confirme a autorização de consulta somente leitura.")
            else:
                try:
                    resultado = consultar_recurso_nfe(
                        servico.auth, _secrets, conta, destino_id, autorizado=True,
                    )
                except (AuthenticationError, AuthorizationError):
                    raise
                except GestaoClickError:
                    st.warning("A consulta fiscal foi recusada. Nenhuma nota foi modificada.")
                else:
                    st.success(
                        "Recurso notas_fiscais_produtos respondeu por GET. "
                        "Nenhuma nota foi criada ou emitida."
                    )
                    comparacao = resultado["loja_no_registro_confere"]
                    if comparacao is False:
                        st.error(
                            "A nota observada pertence a outra loja! "
                            "Cadastro real BLOQUEADO; confira o token no ERP."
                        )
                    elif comparacao is True:
                        st.info(
                            "A nota da amostra declara a loja solicitada. "
                            "Isso não comprova permissão de criação nem ausência de nota duplicada."
                        )
                    else:
                        st.warning(
                            "A loja efetiva do token não pôde ser comprovada na amostra. "
                            "Não liberar cadastro fiscal."
                        )
        with st.expander("Localizar venda real da FLT Comércio por número (GET)"):
            fiscal_order_lookup_controls(
                servico, _secrets, conta, origem_id, destino_id,
            )
        st.warning(
            "CADASTRO REAL AINDA BLOQUEADO: faltam validação fiscal completa "
            "(destinatário, UF CE, NCM, CFOP, CST/CSOSN e impostos), nota anterior, "
            "efeitos no estoque e envio_automatico. Emissão continua manual no ERP."
        )
