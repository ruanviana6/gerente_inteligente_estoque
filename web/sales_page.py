"""Área de vendas Flatter: dados sincronizados, filtros e cálculos explicáveis."""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import streamlit as st

from dados import hoje_local
from web.sales_service import SalesService, access_stamp
from web.theme import metric_card, page_header


def vendas(servico, contexto):
    """Gera a análise por ação explícita, sem chamar o GestãoClick nos reruns."""
    page_header("Inteligência de Vendas", "Vendas concluídas, variações e combinações observadas.")
    assinatura = access_stamp(contexto, servico.auth.generation)
    fontes = SalesService(servico.auth).sources()
    if assinatura != access_stamp(
            servico.auth.require("trends.view"), servico.auth.generation):
        from web.errors import AuthorizationError

        raise AuthorizationError("O acesso mudou durante a consulta.")
    if not fontes:
        st.info("Nenhuma fonte sincronizada. O Master pode preparar uma carga fictícia em Integrações.")
        return
    por_id = {f["id"]: f for f in fontes if f.get("active")}
    if not por_id:
        st.info("Não há fonte ativa autorizada.")
        return
    fonte = st.selectbox("Fonte autorizada", list(por_id), format_func=lambda k: por_id[k]["name"])
    if not por_id[fonte].get("stores"):
        st.info("A fonte não tem lojas autorizadas. Procure o Master.")
        return
    loja = st.selectbox("Loja", por_id[fonte]["stores"])
    st.caption("Cada análise usa uma única fonte e loja. Não há consolidação implícita nem valores financeiros.")
    ultima = por_id[fonte].get("last_sync")
    if ultima:
        st.caption(f"Última sincronização da fonte (pode corresponder a outra loja): {ultima.get('finished_at', 'não informada')}. "
                   f"Linhas: {ultima.get('rows', 0)}. Duração: {ultima.get('duration_seconds', 'indisponível')} s.")
    with st.form("filtros_vendas"):
        datas = st.date_input("Período de vendas", (hoje_local().date() - timedelta(days=29), hoje_local().date()),
                             max_value=hoje_local().date())
        produto = st.text_input("Identificador do produto (opcional)", max_chars=120)
        categoria = st.text_input("Categoria (opcional)", max_chars=300)
        buscar = st.text_input("Buscar produto ou SKU na tabela", max_chars=120)
        granularidade = st.radio("Evolução", ("diaria", "semanal", "mensal"), horizontal=True)
        analisar = st.form_submit_button("Analisar vendas", type="primary")
    if not analisar:
        st.info("Escolha o período e clique em Analisar vendas. Os dados são lidos do Supabase.")
        return
    if len(datas) != 2:
        st.warning("Selecione as duas datas.")
        return
    with st.spinner("Validando acesso e calculando indicadores…"):
        resultado = SalesService(servico.auth).analyze(fonte, loja, *datas,
                            product_id=produto or None, category=categoria or None)
    if assinatura != access_stamp(
            servico.auth.require("trends.view"), servico.auth.generation):
        from web.errors import AuthorizationError

        raise AuthorizationError("O acesso mudou durante a análise.")
    resumo = resultado["resumo"]
    for coluna, titulo, chave in zip(st.columns(3, wrap=True),
            ("Unidades líquidas", "Pedidos concluídos", "Produtos vendidos"),
            ("unidades", "pedidos_concluidos", "produtos_vendidos")):
        with coluna:
            metric_card(titulo, str(resumo[chave]), key="vendas_" + chave)
    comparacao = resultado["comparacao"]
    variacao = comparacao.get("variacao_percentual")
    st.caption("Variação frente ao período anterior equivalente: " +
               (f"{variacao:.1f}%" if variacao is not None else "indisponível; consulte as limitações."))
    with st.expander("Origem, cálculo e limitações", expanded=True):
        st.write({"fonte": por_id[fonte]["name"], "loja": loja, "período": resultado["periodo"],
                  "comparação": comparacao})
        for aviso in resultado["limitacoes"]:
            st.warning(aviso)
    serie = pd.DataFrame(resultado["series"][granularidade])
    if not serie.empty:
        st.line_chart(serie.set_index("data")[["unidades"]])
    st.subheader("Produtos e crescimento observado")
    produtos = pd.DataFrame(resultado["produtos"])
    if not produtos.empty:
        if buscar:
            produtos = produtos[produtos["sku"].str.contains(buscar, case=False, regex=False) |
                                produtos["nome"].str.contains(buscar, case=False, regex=False)]
        st.dataframe(produtos, hide_index=True, width="stretch")
    else:
        st.info("Não há vendas concluídas para os filtros selecionados.")
    with st.expander("Categoria, fornecedor e loja"):
        for chave in ("categorias", "fornecedores", "lojas"):
            st.write(chave.capitalize())
            st.dataframe(pd.DataFrame(resultado[chave]), hide_index=True, width="stretch")
    with st.expander("Variação no calendário e sazonalidade"):
        sazonal = resultado["sazonalidade"]
        st.write(sazonal["classificacao"])
        st.info(sazonal["limitacao"])
        st.dataframe(pd.DataFrame(sazonal["mes"]), hide_index=True, width="stretch")
        for chave in ("semana", "dia_semana", "trimestre", "recorrencias"):
            st.write(chave.replace("_", " ").capitalize())
            st.dataframe(pd.DataFrame(sazonal[chave]), hide_index=True, width="stretch")
    with st.expander("Produtos comprados juntos"):
        cestas = resultado["cestas"]
        st.caption(cestas["explicacao"])
        if not cestas["disponivel"]:
            st.info(cestas["motivo"])
        else:
            st.dataframe(pd.DataFrame(cestas["pares"]), hide_index=True, width="stretch")
    for insight in resultado["insights"]:
        with st.expander(insight["titulo"]):
            st.write(insight["explicacao"])
            st.write(insight["limitacoes"])
