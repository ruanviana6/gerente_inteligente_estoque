"""Localiza número comercial na consulta de vendas do Atacado, só GET.

Sem repasse de clientes, valores, endereço ou notas ao front-end; nenhuma
informação entra no banco. O status não valida elegibilidade fiscal.
"""
from __future__ import annotations

from datetime import timedelta

import streamlit as st

from dados import hoje_local
from fiscal.lojas import validar_lojas_fiscais
from fiscal.order_observation import observar_pedido_fiscal
from integracoes.account_setup import validar_conta
from integracoes.gestaoclick import GestaoClickError
from integracoes.sales_audit import consultar_vendas_integrais
from web.errors import AppError, AuthenticationError, AuthorizationError


def fiscal_order_lookup_controls(
    servico,
    carregar_configuracao,
    conta,
    origem: str | None,
    destino: str | None,
) -> None:
    """Busca consentida e efêmera em até sete dias, sem efeito colateral."""
    st.markdown("**Localizar venda de origem para conferência fiscal**")
    st.caption(
        "Pesquisa o número comercial exato em vendas de produtos. A busca percorre "
        "as páginas do período escolhido (até sete dias e mil documentos) para "
        "não confundir ausência na primeira página com inexistência. "
        "Nenhum cadastro de NF-e é criado."
    )
    hoje = hoje_local().date()
    with st.form("fiscal_order_readonly_lookup"):
        numero = st.text_input(
            "Número da venda na FLT Comércio",
            max_chars=20,
            placeholder="Número exibido no GestãoClick",
            key="fiscal_source_order_number",
        )
        periodo = st.date_input(
            "Período da venda na origem (até sete dias)",
            (hoje - timedelta(days=6), hoje),
            max_value=hoje,
            key="fiscal_source_order_dates",
        )
        autoriza = st.checkbox(
            "Autorizo somente a consulta das vendas deste período na loja de origem",
            key="fiscal_source_order_consent",
        )
        consultar = st.form_submit_button("Localizar venda na origem (somente leitura)")
    if not consultar:
        return
    if not autoriza:
        st.warning("Confirme explicitamente a autorização da consulta de vendas.")
        return
    if len(periodo) != 2:
        st.warning("Escolha as datas inicial e final.")
        return
    try:
        with st.spinner("Conferindo vendas da origem, somente leitura…"):
            with servico.auth.operation():
                servico.auth.require("integrations.manage")
                validar_conta(servico.auth, conta)
                origem_id, destino_id = validar_lojas_fiscais(conta.lojas, origem, destino)
                auditoria = consultar_vendas_integrais(
                    servico.auth, carregar_configuracao, conta,
                    origem_id, *periodo, autorizado=True,
                )
                observacao = observar_pedido_fiscal(
                    auditoria, numero,
                    {x["id"]: x["nome"] for x in conta.situacoes},
                )
                validar_conta(servico.auth, conta)
    except (AuthenticationError, AuthorizationError):
        raise
    except (AppError, GestaoClickError, ValueError, TypeError) as erro:
        st.warning(str(erro))
        return
    st.caption(
        f"Origem solicitada: {origem_id} · Destino selecionado: {destino_id} · "
        f"Janela: {observacao.inicio:%d/%m/%Y} a {observacao.fim:%d/%m/%Y} · "
        f"Documentos consultados: {observacao.documentos_consultados}."
    )
    if not observacao.encontrado_na_janela:
        st.info(
            "O número não foi observado nos documentos devolvidos para esta janela. "
            "Isso NÃO comprova ausência no ERP, em outras datas, lojas ou permissões."
        )
    else:
        st.success("Número comercial encontrado na amostra integral do período.")
        st.write({
            "Número da venda": observacao.numero_solicitado,
            "Data informada": observacao.data_venda.strftime("%d/%m/%Y"),
            "Situação informada": observacao.situacao,
            "Itens (linhas)": observacao.quantidade_linhas,
        })
    st.warning(
        "CONFERÊNCIA, NÃO FATURAMENTO: a loja efetiva do token, o destino CE, "
        "as notas já existentes, os campos fiscais e o efeito do cadastro "
        "NÃO foram comprovados. A busca não libera POST nem emissão de NF-e."
    )
