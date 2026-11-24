"""Interface de planejamento em lote a partir do quadro GET da loja."""
from __future__ import annotations

import hashlib

import streamlit as st

from dados import hoje_local
from reposicao.lote_conferido import (
    MAX_PRODUTOS_LOTE,
    calcular_lote_conferido,
    exportar_lote_conferido,
)
from reposicao.quadro_real import QuadroReposicaoReal


def lote_conferido_controls(quadro: QuadroReposicaoReal, *, exportacao: bool) -> None:
    """Calcula vários itens sem transformar observação parcial em histórico validado."""
    st.divider()
    if not st.toggle("Planejar reposição em lote", key=f"lote_conferido_{quadro.loja_id}"):
        return
    st.caption(
        "Selecione os produtos e confira os dados uma única vez na tabela. "
        "O cálculo usa saída líquida média e saldo disponível em UN informados por você, "
        "não subtrai vendas do saldo do ERP e não envia pedidos."
    )
    opcoes = {
        row["Produto (ID)"]: row for row in quadro.linhas
        if row["Produto"] != "Não informado pelo ERP" and row["Variações"] == "Sem variações no retorno"
    }
    if not opcoes:
        st.info("Nenhum produto identificado sem variações nesta consulta.")
        return
    chave = hashlib.sha256(
        f"{quadro.loja_id}:{quadro.consultado_em.isoformat()}".encode()
    ).hexdigest()[:16]
    selecionados = st.multiselect(
        "Produtos para planejar", list(opcoes),
        format_func=lambda codigo: f'{opcoes[codigo]["Produto"]} · {codigo}',
        max_selections=MAX_PRODUTOS_LOTE, key=f"lote_produtos_{chave}",
    )
    if not selecionados:
        return
    ident = hashlib.sha256("|".join(selecionados).encode()).hexdigest()[:12]
    tabela = []
    for codigo in selecionados:
        row = opcoes[codigo]
        tabela.append({
            "Produto (ID)": codigo, "Produto": row["Produto"],
            "Saldo GET (a conferir)": row["Saldo GET (unidade do ERP não conferida)"],
            "Vendas observadas (UN)": row["Vendas fechadas observadas (UN)"],
            "Disponível (UN)": "", "Saída líquida (UN/dia)": "", "UN/CX": "", "Segurança (UN)": "",
        })
    st.caption(
        "Saldo GET e vendas observadas são referências, não entradas automáticas. "
        "Preencha o saldo já líquido de reservas, média diária líquida conferida, "
        "embalagem e segurança. Zero somente quando confirmado."
    )
    with st.form(f"lote_calculo_{chave}_{ident}"):
        entradas = st.data_editor(
            tabela, num_rows="fixed", hide_index=True, width="stretch",
            disabled=["Produto (ID)", "Produto", "Saldo GET (a conferir)", "Vendas observadas (UN)"],
            key=f"lote_dados_{chave}_{ident}",
        )
        prazo = st.text_input("Prazo de entrega da indústria (dias)", max_chars=3)
        ciclo = st.radio("Ciclo da solicitação", (14, 21), horizontal=True)
        conferi = st.checkbox(
            "Confirmei cada saldo disponível em UN, saída líquida média, segurança e embalagem, "
            "e que não há recebimentos em trânsito a considerar neste cenário."
        )
        calcular = st.form_submit_button("Calcular reposição dos produtos", type="primary")
    if not calcular:
        return
    try:
        linhas = calcular_lote_conferido(
            quadro, entradas, referencia=hoje_local().date(), prazo_dias=prazo,
            ciclo_dias=ciclo, confirmado=conferi,
        )
    except ValueError as erro:
        st.warning(str(erro))
        return
    st.success(f"{len(linhas)} produto(s) calculados para revisão humana.")
    st.dataframe(linhas, hide_index=True, width="stretch")
    if all(linha["Sugestão (CX)"] is not None for linha in linhas):
        st.metric("Total de caixas sugeridas", sum(linha["Sugestão (CX)"] for linha in linhas))
    st.caption("Cenário com cobertura de 30 dias após chegada; nenhuma compra é enviada ao ERP.")
    if exportacao:
        st.download_button(
            "Exportar planejamento em lote (CSV)", exportar_lote_conferido(linhas),
            file_name=f"reposicao_conferida_{quadro.loja_id}.csv",
            mime="text/csv; charset=utf-8", key=f"lote_export_{chave}_{ident}", on_click="ignore",
        )
