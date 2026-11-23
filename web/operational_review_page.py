"""Conferência operacional de leitura: observação semanal vs fotografia de estoque.

Nunca altera o GestãoClick, não soma janelas e não calcula pedidos.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import streamlit as st

from analytics.observational_review import conferir_observacao, exportar_conferencia_csv
from integracoes.account_setup import validar_conta
from integracoes.gestaoclick import ContratoInvalido, GestaoClickError
from web.errors import AppError, AuthenticationError, AuthorizationError
from web.integration_sales_audit_page import _id_fonte

_FUSO = ZoneInfo("America/Fortaleza")


def operational_review_controls(servico, conta, loja_id):
    """Apenas Master e sessão autorizada; uma leitura RPC sob demanda."""
    with st.expander("Conferência para o trabalho — vendas observadas × saldo da fotografia"):
        st.caption(
            "Consulte a última observação salva e a última fotografia de estoque "
            "da mesma loja. É uma lista para conferir no ERP, NÃO uma previsão, "
            "pedido de compra nem estoque atual."
        )
        if not st.button("Consultar conferência operacional", key="conferencia_operacional_btn"):
            return
        try:
            with st.spinner("Conferindo o histórico e a fotografia já salvos…"):
                with servico.auth.operation():
                    servico.auth.require("integrations.manage")
                    validar_conta(servico.auth, conta)
                    fonte = _id_fonte(servico.auth, loja_id)
                    if fonte is None:
                        raise ContratoInvalido("Registre a fonte analítica desta loja.")
                    bruto = servico.auth.rpc(
                        "demand_observation_review",
                        {"p_source_id": fonte, "p_store_id": loja_id},
                        "integrations.manage",
                    )
                    validar_conta(servico.auth, conta)
                    dados = conferir_observacao(bruto, loja_id)
        except (AuthenticationError, AuthorizationError):
            raise
        except (AppError, GestaoClickError, ValueError, TypeError):
            st.warning(
                "Não foi possível conferir os registros. Nenhuma venda, saldo ou nota "
                "foi alterada. Revise fonte, loja e histórico salvo."
            )
            return

        if dados is None:
            st.info("Ainda não há observações salvas para esta loja. Use o histórico acima.")
            return
        horario_estoque = dados.stock_snapshot_at.astimezone(_FUSO)
        horario_venda = dados.observed_at.astimezone(_FUSO)
        dias_foto = (datetime.now(timezone.utc) - dados.stock_snapshot_at).total_seconds() / 86400
        st.write(
            f"**Loja solicitada:** {dados.store_id} · "
            f"**Período observado:** {dados.period_start:%d/%m/%Y}–"
            f"{dados.period_end:%d/%m/%Y} · "
            f"**Observação salva:** {horario_venda:%d/%m/%Y %H:%M}"
        )
        st.warning(
            f"**O saldo não é ao vivo.** Última fotografia: "
            f"{horario_estoque:%d/%m/%Y às %H:%M}. "
            "As vendas e o estoque podem ter datas diferentes; NÃO subtraia "
            "um do outro nem use esta tela para calcular quantidades a pedir."
        )
        if dias_foto > 1:
            st.error(
                "A fotografia de estoque tem mais de 24 horas. "
                "Confira os saldos atuais diretamente no GestãoClick antes de decidir."
            )
        a, b, c = st.columns(3)
        a.metric("Vendas fechadas observadas", dados.closed_documents)
        b.metric("Produtos com movimento UN observado", dados.product_count)
        c.metric("Unidades UN observadas no período", f"{dados.total_observed_un:,.0f}".replace(",", "."))
        st.caption(
            "Ordenado por unidades comercialmente observadas no período; o código interno pertence à fotografia salva, não é uma consulta atual ao ERP. "
            "sem somar outras janelas ou reconsultas. Saldo da fotografia "
            "é apenas referência histórica para checagem manual."
        )
        linhas = []
        for item in dados.rows:
            linhas.append({
                "Produto": item.product_name or "Não informado",
                "Categoria": item.category or "Não informada",
                "Produto (ID)": item.product_id,
                "Código interno (fotografia)": item.internal_code or "Não informado",
                "UN observadas no período": float(item.observed_un),
                "Saldo na fotografia (não atual)": (
                    float(item.stock_snapshot) if item.stock_snapshot is not None else None
                ),
                "Conferir no ERP": (
                    "Sem saldo na foto" if item.stock_snapshot is None else
                    "Saldo zero na foto" if item.stock_snapshot == 0 else
                    "Verificar saldo atual"
                ),
            })
        st.dataframe(linhas, hide_index=True, width="stretch")
        st.download_button(
            "Exportar conferência em CSV",
            data=exportar_conferencia_csv(dados),
            file_name=f"conferencia_loja_{loja_id}_{dados.period_start}_{dados.period_end}.csv",
            mime="text/csv; charset=utf-8",
            on_click="ignore",
            key="conferencia_operacional_csv",
        )
        st.info(
            "Uso de amanhã: confira no GestãoClick os saldos dos produtos destacados "
            "e use este histórico para organizar prioridades de revisão. "
            "Reposição automática e cadastro real de NF-e continuam desativados."
        )
