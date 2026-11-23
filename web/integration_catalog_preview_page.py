"""Conferência protegida do catálogo e saldo do GestãoClick para uma loja."""

from __future__ import annotations

import streamlit as st

from integracoes.account_setup import validar_conta
from integracoes.catalog_preview import (
    CodigoCatalogoInvalido,
    IdentificadorCatalogoInvalido,
    consultar_previa_catalogo,
)
from integracoes.gestaoclick import GestaoClickError
from web.errors import AuthenticationError, AuthorizationError


def catalog_preview_controls(servico, carregar_configuracao, conta, loja_id: str) -> None:
    """Lê a primeira página somente com clique Master, sem guardar a resposta."""
    st.markdown("**Conferir catálogo e estoque da loja**")
    st.caption("Prévia de até cinco produtos. Consulta exclusivamente de leitura: "
               "não importa saldos, não altera o GestãoClick e não atualiza indicadores.")
    with st.form("conferir_amostra_catalogo_gestaoclick"):
        autorizado = st.checkbox("Autorizo consultar uma amostra real do catálogo desta loja")
        consultar = st.form_submit_button("Conferir catálogo e estoque")
    if not consultar:
        return
    if not autorizado:
        st.warning("Autorize a leitura do catálogo antes da consulta.")
        return
    try:
        with st.spinner("Consultando uma amostra limitada do catálogo…"):
            previa = consultar_previa_catalogo(servico.auth, carregar_configuracao, conta,
                                               loja_id, autorizado=True)
        validar_conta(servico.auth, conta)
    except (AuthenticationError, AuthorizationError):
        raise
    except GestaoClickError as erro:
        st.warning(str(erro))
        if isinstance(erro, (IdentificadorCatalogoInvalido, CodigoCatalogoInvalido)):
            st.caption("Diagnóstico do campo: somente nome, tipo e motivo; "
                       "nenhum valor da conta é exibido.")
            st.json(erro.diagnostico, expanded=False)
        return

    st.success("Catálogo consultado. Nenhum saldo foi importado ou modificado.")
    st.caption(f"Loja solicitada: {previa.loja_solicitada}. "
               f"{len(previa.produtos)} produto(s) exibido(s), "
               f"{previa.recebidos} retornado(s) nesta página. "
               "Total informado: "
               f"{previa.total_informado if previa.total_informado is not None else 'não confirmado'}.")
    st.info("O estoque exibido corresponde à loja resolvida pela API, não necessariamente "
            "ao estoque da empresa inteira. Confira a loja no ERP. "
            "IDs das variações do catálogo ainda não estão associados automaticamente "
            "aos itens das vendas.")
    if not previa.produtos:
        st.info("Nenhum produto retornado. Isso não comprova catálogo vazio nem estoque zerado.")
        return
    sem_id = sum(variacao.id is None for produto in previa.produtos for variacao in produto.variacoes)
    if sem_id:
        st.warning(f"{sem_id} linha(s) de variação vieram sem ID de estoque. "
                   "O saldo dessas linhas foi ocultado e não será importado. "
                   "O retorno não corresponde integralmente ao contrato documentado do GestãoClick; "
                   "confira a loja e a resposta da API antes de sincronizar estoque.")

    sem_codigo_produto = sum(produto.codigo_interno is None for produto in previa.produtos)
    sem_codigo_variacao = sum(variacao.codigo is None
                             for produto in previa.produtos for variacao in produto.variacoes)
    if sem_codigo_produto or sem_codigo_variacao:
        st.warning(
            f"Na amostra exibida, {sem_codigo_produto} produto(s) estão sem código interno "
            f"e {sem_codigo_variacao} linha(s) de variação estão sem código. "
            "A prévia mostra 'Não informado', sem inventar códigos. "
            "Confirme os códigos no GestãoClick antes de associar itens de vendas "
            "ou sincronizar estoque; nenhuma carga será feita aqui."
        )

    def valor(quantidade):
        return format(quantidade, "f") if quantidade is not None else "Não informado"

    linhas = []
    for produto in previa.produtos:
        if not produto.variacoes:
            linhas.append({
                "Produto": produto.nome or "Não informado",
                "Categoria": produto.categoria or "Não informada",
                "Produto (ID)": produto.id,
                "Código interno": produto.codigo_interno or "Não informado",
                "Estoque do produto (loja)": valor(produto.estoque),
                "Linha de estoque (ID)": "Não informada",
                "Código da variação": "Não informado",
                "Estoque da variação": "Não informado",
                "Combinação API": "Não informada",
            })
        for variacao in produto.variacoes:
            linhas.append({
                "Produto": produto.nome or "Não informado",
                "Categoria": produto.categoria or "Não informada",
                "Produto (ID)": produto.id,
                "Código interno": produto.codigo_interno or "Não informado",
                "Estoque do produto (loja)": valor(produto.estoque),
                "Linha de estoque (ID)": variacao.id or "Não informado",
                "Código da variação": variacao.codigo or "Não informado",
                "Estoque da variação": valor(variacao.estoque),
                "Combinação API": variacao.variacao_api_id or "Não informada",
            })
    st.dataframe(linhas, hide_index=True, width="stretch")
    st.caption("Somente amostra: não foi verificada a correspondência entre o ID de "
               "variação de uma venda e a linha de estoque do catálogo, nem a "
               "abrangência completa de produtos, lotes ou unidades. "
               "Nenhum resultado é salvo ou usado na reposição.")
