"""Triagem operacional de produtos reais, sem calcular compra nem alterar o ERP."""
from __future__ import annotations

from decimal import Decimal

import streamlit as st

from analytics.observational_review import ConferenciaObservada, exportar_conferencia_csv
from integracoes.gestaoclick import ContratoInvalido, GestaoClickError
from web.errors import AppError, AuthenticationError, AuthorizationError
from web.real_home_status import consultar_status_real
from web.real_store_selector import escolher_loja_real


def priorizar_conferencia(dados: ConferenciaObservada) -> list[dict]:
    """Ordena apenas conferências de saldos históricos, nunca sugestões de pedido."""
    if not isinstance(dados, ConferenciaObservada):
        raise ContratoInvalido("A triagem exige uma observação previamente validada.")
    linhas = []
    for row in dados.rows:
        saldo = row.stock_snapshot
        if saldo is None:
            ordem, etiqueta = 0, "Saldo não informado na fotografia"
        elif saldo == 0:
            ordem, etiqueta = 1, "Saldo zero na fotografia"
        elif saldo <= Decimal(10):
            ordem, etiqueta = 2, "Saldo de 1 a 10 na fotografia"
        elif saldo <= Decimal(50):
            ordem, etiqueta = 3, "Saldo de 11 a 50 na fotografia"
        else:
            ordem, etiqueta = 4, "Conferir saldo atual"
        linhas.append({
            "_ordem": ordem,
            "Produto": row.product_name or "Não informado",
            "Categoria": row.category or "Não informada",
            "Produto (ID)": row.product_id,
            "Código interno": row.internal_code or "Não informado",
            "Situação da fotografia": etiqueta,
            "UN comerciais observadas": float(row.observed_un),
            "Saldo na fotografia (não atual)": float(saldo) if saldo is not None else None,
        })
    linhas.sort(key=lambda x: (x["_ordem"], -x["UN comerciais observadas"], int(x["Produto (ID)"])))
    for item in linhas:
        del item["_ordem"]
    return linhas


def real_triage_controls(servico, contexto, loja_id: str | None = None):
    """Consulta histórica só com clique explícito, após verificar Master."""
    if (contexto.profile.role != "Master" or not contexto.can("integrations.manage")
            or loja_id is None):
        return
    with st.expander("Triagem real de reposição — conferir no GestãoClick"):
        st.caption(
            "Somente os dados reais salvos no Supabase. A triagem não calcula "
            "cobertura, previsão ou quantidade a pedir e NÃO cria pedidos."
        )
        loja_id = escolher_loja_real(servico.auth)
        if loja_id is None:
            return
        if not st.button("Consultar produtos para conferência", key="triagem_real_reposicao"):
            return
        try:
            with st.spinner("Lendo a última observação e fotografia autorizadas…"):
                dados = consultar_status_real(servico.auth, loja_id)
        except (AuthenticationError, AuthorizationError):
            raise
        except (AppError, GestaoClickError, TypeError, ValueError):
            st.warning("Não foi possível conferir os dados reais da loja. Nenhuma ação foi executada.")
            return
        if dados is None:
            st.info("Não há observação real autorizada. Consulte Administração → Integrações.")
            return
        linhas = priorizar_conferencia(dados)
        st.warning(
            "Saldo HISTÓRICO, não atual. Venda observada não equivale a venda "
            "consolidada. Confira estoque, unidades por caixa, prazo e devoluções "
            "no GestãoClick antes de propor qualquer compra."
        )
        st.caption(
            f"Loja {dados.store_id} · Vendas observadas "
            f"{dados.period_start:%d/%m/%Y}–{dados.period_end:%d/%m/%Y} · "
            f"Fotografia de estoque {dados.stock_snapshot_at:%d/%m/%Y %H:%M} (fuso da fonte)."
        )
        st.dataframe(linhas, hide_index=True, width="stretch")
        st.download_button(
            "Exportar conferência real em CSV",
            data=exportar_conferencia_csv(dados),
            file_name=f"triagem_estoque_{dados.store_id}_{dados.period_start}_{dados.period_end}.csv",
            mime="text/csv; charset=utf-8",
            on_click="ignore",
            key="triagem_real_csv",
        )
        st.info(
            "A Reposição Inteligente operacional continua separada. Não use estes "
            "totais para gerar automaticamente quantidades de compra."
        )
