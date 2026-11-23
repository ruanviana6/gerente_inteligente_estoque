"""Consulta e salva um histórico agregado real, independente do motor de reposição."""
from __future__ import annotations

import time
from datetime import timedelta

import streamlit as st

from analytics.demand_observation import construir_observacao
from analytics.sales_audit import rotular_agrupamento
from analytics.sales_audit import conciliar_vendas_com_catalogo
from dados import hoje_local
from integracoes.account_setup import validar_conta
from integracoes.gestaoclick import ContratoInvalido, GestaoClickError
from integracoes.sales_audit import consultar_vendas_integrais
from web.errors import AppError, AuthenticationError, AuthorizationError
from web.integration_sales_audit_page import _LOJA_STATUS_CONFERIDOS, _id_fonte
from web.sales_service import access_stamp

_KEY = "_gc_demanda_observada_previa"
_TTL = 300


def _stamp(auth):
    return access_stamp(auth.require("integrations.manage"), auth.generation)


def _fonte_fotografia(auth, loja):
    source_id = _id_fonte(auth, loja)
    if source_id is None:
        raise ContratoInvalido("Registre a fonte analítica desta loja antes da observação.")
    foto = auth.rpc(
        "catalog_stock_latest",
        {"p_source_id": source_id, "p_store_id": loja}, "integrations.manage",
    )
    if (not isinstance(foto, dict) or foto.get("found") is not True or
            foto.get("store_id") != loja or
            not isinstance(foto.get("snapshot_id"), str)):
        raise ContratoInvalido("Confirme primeiro uma fotografia do catálogo desta loja.")
    return source_id, foto


def demand_observation_controls(servico, carregar_configuracao, conta, loja_id: str):
    """Dois atos separados: consulta real GET e confirmação da gravação interna."""
    auth = servico.auth
    with st.expander("Histórico de demanda observada (sem reposição automática)"):
        st.caption(
            "Registra somente uma fotografia agregada de vendas fechadas observadas "
            "no período de até sete dias. Não grava pedidos ou clientes, não move "
            "estoque e NÃO alimenta vendas consolidadas, reposição ou notas fiscais."
        )
        situacoes = {item["id"]: item["nome"] for item in conta.situacoes}
        ids = list(situacoes)
        padrao = _LOJA_STATUS_CONFERIDOS.get(loja_id, {})
        hoje = hoje_local().date()

        def indice(id_situacao):
            return ids.index(id_situacao) if id_situacao in ids else None

        with st.form("observar_demanda_get"):
            dias = st.date_input(
                "Período da observação", (hoje - timedelta(days=6), hoje),
                max_value=hoje, key="observacao_demanda_datas",
            )
            separacao = st.selectbox(
                "Situação Em separação", ids, index=indice(padrao.get("separacao")),
                format_func=lambda valor: f"{situacoes[valor]} · ID {valor}",
                placeholder="Confirme a situação no ERP",
                key="observacao_demanda_separacao",
            )
            concretizada = st.selectbox(
                "Situação Concretizada", ids, index=indice(padrao.get("concretizada")),
                format_func=lambda valor: f"{situacoes[valor]} · ID {valor}",
                placeholder="Confirme a situação no ERP",
                key="observacao_demanda_concretizada",
            )
            autoriza_get = st.checkbox(
                "Autorizo consultar as vendas reais somente por GET, sem importá-las",
                key="observacao_demanda_consent_get",
            )
            consultar = st.form_submit_button("Preparar observação real")
        if consultar:
            st.session_state.pop(_KEY, None)
            if (autoriza_get is not True or not isinstance(dias, (list, tuple)) or
                    len(dias) != 2 or separacao is None or concretizada is None or
                    separacao == concretizada):
                st.warning("Confirme o período, as duas situações diferentes e a autorização GET.")
                return
            try:
                with st.spinner("Consultando e conciliando sem modificar o ERP…"):
                    with auth.operation():
                        auth.require("integrations.manage")
                        validar_conta(auth, conta)
                        source_id, foto = _fonte_fotografia(auth, loja_id)
                        auditoria = consultar_vendas_integrais(
                            auth, carregar_configuracao, conta,
                            loja_id, *dias, autorizado=True,
                        )
                        conciliado = conciliar_vendas_com_catalogo(
                            auditoria, foto, separacao_id=separacao,
                            concretizada_id=concretizada,
                        )
                        dados = construir_observacao(auditoria, conciliado, source_id)
                        validar_conta(auth, conta)
                        st.session_state[_KEY] = {
                            "stamp": _stamp(auth), "store": loja_id, "source_id": source_id,
                            "expires": time.monotonic() + _TTL,
                            "snapshot_id": foto["snapshot_id"], "data": dados,
                            "products": rotular_agrupamento(conciliado, foto, loja_id),
                            "unmatched": len(conciliado.produtos_sem_correspondencia),
                        }
            except (AuthenticationError, AuthorizationError):
                raise
            except (AppError, GestaoClickError, TypeError, ValueError):
                st.warning(
                    "A observação não pôde ser conciliada. Nenhum dado foi gravado. "
                    "Confira catálogo, situações e abrangência da consulta."
                )
                return

        previa = st.session_state.get(_KEY)
        if previa:
            if (previa["stamp"] != _stamp(auth) or
                    previa["store"] != loja_id or
                    time.monotonic() >= previa["expires"]):
                st.session_state.pop(_KEY, None)
                st.info("A observação expirou ou mudou de contexto. Refaça a leitura.")
                return
            validar_conta(auth, conta)
            dados = previa["data"]
            st.write({
                "Loja": loja_id,
                "Período": f"{dados['p_period_start']} a {dados['p_period_end']}",
                "Vendas observadas": dados["p_documents"],
                "Em separação": dados["p_separating"],
                "Concretizadas": dados["p_completed"],
                "Outras situações": dados["p_other"],
                "Itens consultados": dados["p_items"],
                "Produtos com unidades UN": len(dados["p_rows"]),
                "Produtos fora do catálogo": previa["unmatched"],
                "Itens com unidade não informada": dados["p_unknown_unit"],
                "Itens com unidade não suportada": dados["p_unsupported_unit"],
            })
            if previa["products"]:
                st.dataframe(
                    list(previa["products"][:25]), hide_index=True, width="stretch",
                )
                st.caption(
                    "Até 25 produtos com nome, categoria e código da fotografia desta loja. "
                    "Os saldos não são alterados, e a gravação armazena somente os agregados."
                )
            st.warning(
                "A cobertura da loja, as exclusões, devoluções e alterações dos "
                "pedidos não foram comprovadas. Gravar esta observação NÃO "
                "autoriza uso na Reposição Inteligente."
            )
            with st.form("confirmar_observacao"):
                conferi = st.checkbox(
                    "Conferi no GestãoClick o período, loja e totais exibidos",
                    key="observacao_demanda_conferida",
                )
                autoriza_gravar = st.checkbox(
                    "Autorizo apenas armazenar o histórico agregado no Supabase, "
                    "sem atualizar estoque ou recomendações",
                    key="observacao_demanda_consent_store",
                )
                salvar = st.form_submit_button("Salvar observação histórica no Supabase")
            if salvar:
                if not conferi or not autoriza_gravar:
                    st.warning("As duas confirmações são obrigatórias.")
                    return
                try:
                    with auth.operation():
                        auth.require("integrations.manage")
                        validar_conta(auth, conta)
                        if (previa["stamp"] != _stamp(auth) or
                                time.monotonic() >= previa["expires"]):
                            raise AuthorizationError("A prévia perdeu validade.")
                        source_id, foto_atual = _fonte_fotografia(auth, loja_id)
                        if (source_id != previa["source_id"] or
                                foto_atual["snapshot_id"] != previa["snapshot_id"]):
                            raise ContratoInvalido("O catálogo mudou desde a conferência.")
                        resultado = auth.rpc(
                            "demand_observation_save", dados, "integrations.manage",
                        )
                        validar_conta(auth, conta)
                        if (previa["stamp"] != _stamp(auth) or
                                not isinstance(resultado, dict) or
                                resultado.get("motor_atualizado") is not False or
                                resultado.get("ready_for_replenishment") is not False or
                                resultado.get("fiscal_real_enabled") is not False):
                            raise AuthorizationError("O retorno do histórico não foi validado.")
                except (AuthenticationError, AuthorizationError):
                    raise
                except (AppError, GestaoClickError, TypeError, ValueError):
                    st.warning(
                        "Não foi possível confirmar o salvamento. Consulte o histórico "
                        "antes de tentar novamente. O ERP não foi alterado."
                    )
                    return
                st.session_state.pop(_KEY, None)
                st.success(
                    "Observação histórica armazenada de forma idempotente no Supabase. "
                    "Nenhuma venda foi consolidada e nenhum saldo foi alterado."
                )
                st.caption(
                    f"Produtos com UN observada: {resultado['product_count']}. "
                    f"Reutilizou observação: {'sim' if resultado.get('reused') else 'não'}."
                )

        if st.button("Consultar histórico de observações", key="historico_demanda_observada"):
            try:
                with auth.operation():
                    auth.require("integrations.manage")
                    validar_conta(auth, conta)
                    source_id, _ = _fonte_fotografia(auth, loja_id)
                    historico = auth.rpc(
                        "demand_observation_history",
                        {"p_source_id": source_id, "p_store_id": loja_id},
                        "integrations.manage",
                    )
                    validar_conta(auth, conta)
                if not isinstance(historico, list):
                    raise ContratoInvalido("Histórico incompleto.")
                if historico:
                    st.dataframe(historico, hide_index=True, width="stretch")
                else:
                    st.info("Ainda não há observações salvas nesta loja.")
            except (AuthenticationError, AuthorizationError):
                raise
            except (AppError, GestaoClickError):
                st.warning("O histórico não está disponível. Confira as permissões.")
