"""Interface Master para diagnóstico da consulta de devoluções."""
from datetime import timedelta

import streamlit as st

from dados import hoje_local
from integracoes.gestaoclick import GestaoClickError
from integracoes.returns_read_probe import consultar_recurso_devolucoes
from web.errors import AuthenticationError, AuthorizationError


def returns_probe_controls(servico, carregar_configuracao, conta, loja_id: str) -> None:
    with st.expander("Conferir trocas e devoluções (somente leitura)"):
        st.caption(
            "Uma consulta de até um registro, pela data de registro da devolução. "
            "Mostra apenas presença e tipos de campos candidatos; não importa vendas "
            "ou devoluções nem comprova o histórico."
        )
        hoje = hoje_local().date()
        with st.form("returns_probe_readonly"):
            datas = st.date_input(
                "Período de registro da devolução",
                (hoje - timedelta(days=6), hoje),
                max_value=hoje, key="returns_probe_dates",
            )
            consentimento = st.checkbox(
                "Autorizo exclusivamente a consulta de devoluções sem gravação",
                key="returns_probe_consent",
            )
            executar = st.form_submit_button("Testar leitura de devoluções")
        if not executar:
            return
        if not consentimento or not isinstance(datas, (tuple, list)) or len(datas) != 2:
            st.warning("Confirme a autorização e informe as duas datas.")
            return
        try:
            with servico.auth.operation():
                servico.auth.require("integrations.manage")
                with st.spinner("Conferindo retorno sem gravar registros…"):
                    resultado = consultar_recurso_devolucoes(
                        servico.auth, carregar_configuracao, conta, loja_id,
                        *datas, autorizado=True,
                    )
        except (AuthenticationError, AuthorizationError):
            raise
        except GestaoClickError as erro:
            st.warning(str(erro))
            return
        st.success("Consulta de diagnóstico respondeu. Nenhum registro foi alterado.")
        st.write({"Registros da amostra": resultado["itens_amostra"]})
        if resultado["loja_no_registro_confere"] is False:
            st.error("O registro declarou loja diferente da solicitada.")
        elif resultado["loja_no_registro_confere"] is True:
            st.info("O registro declara a loja solicitada; não comprova abrangência.")
        else:
            st.info("A amostra não confirma a loja efetiva nem ausência de devoluções.")
        st.json(resultado["tipos_campos_candidatos"], expanded=False)
        st.warning("O vínculo à venda original, situação e quantidades ainda exigem homologação. "
                   "Reposição e NF-e permanecem bloqueadas.")
