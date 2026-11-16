"""Conferência limitada de vendas nativas, sem persistir a amostra na sessão."""

from __future__ import annotations

from datetime import timedelta

import streamlit as st

from dados import hoje_local
from integracoes.account_setup import validar_conta
from integracoes.gestaoclick import GestaoClickError
from integracoes.native_preview import consultar_previa_vendas
from web.errors import AuthenticationError, AuthorizationError

_TIPOS = {"produto": "Produtos", "vendas_balcao": "PDV / vendas de balcão"}


def native_preview_controls(servico, carregar_configuracao, conta, loja_id: str) -> None:
    """Consulta somente no envio autorizado e descarta os registros a cada rerun.

    A amostra usa o contrato nativo documentado, independente de mapas JSON ou
    fonte registrada. Ela não passa pelo coordenador de sincronização nem pode
    alterar indicadores. O catálogo assinado é revalidado antes da exibição.
    """
    st.markdown("**Conferir uma amostra real de vendas**")
    st.caption("Esta consulta usa os campos documentados da API, sem preencher mapas JSON. "
               "Exibe até cinco documentos para conferência, sem importar vendas.")
    hoje = hoje_local().date()
    with st.form("conferir_amostra_nativa_gestaoclick"):
        datas = st.date_input("Período da amostra de vendas", (hoje - timedelta(days=6), hoje),
                             max_value=hoje, key="gestaoclick_native_preview_dates")
        st.caption("A amostra aceita até 31 dias, incluindo início e fim. "
                   "O limite de 732 dias da sincronização permanece separado.")
        tipo = st.selectbox("Tipo de venda da amostra", list(_TIPOS),
                            format_func=_TIPOS.get, key="gestaoclick_native_preview_type")
        autorizado = st.checkbox("Autorizo consultar uma amostra real de vendas desta loja",
                                 key="gestaoclick_native_preview_consent")
        consultar = st.form_submit_button("Conferir amostra de vendas")
    if not consultar:
        return
    if not autorizado:
        st.warning("Marque a autorização antes de consultar a amostra real de vendas.")
        return
    if len(datas) != 2:
        st.warning("Escolha as datas inicial e final da amostra de vendas.")
        return
    try:
        with st.spinner("Conferindo uma amostra limitada de vendas…"):
            previa = consultar_previa_vendas(servico.auth, carregar_configuracao, conta,
                                            loja_id, *datas, tipo, autorizado=True)
        # O resultado não fica no session_state; uma sessão alterada no retorno
        # também não pode receber os registros recém-consultados.
        validar_conta(servico.auth, conta)
    except (AuthenticationError, AuthorizationError):
        raise
    except GestaoClickError as erro:
        st.warning(str(erro))
        return

    st.success("Amostra consultada. Nenhuma venda foi importada e nenhum indicador foi alterado.")
    st.caption(f"Fonte: GestãoClick · loja solicitada: {previa.loja_solicitada} · "
               f"Período: {previa.inicio:%d/%m/%Y} a {previa.fim:%d/%m/%Y} · "
               f"Tipo: {_TIPOS[previa.tipo]}.")
    st.caption(f"Amostra parcial: {len(previa.documentos)} documento(s) exibido(s). "
               f"A API informou {previa.total_informado} no período e devolveu "
               f"{previa.documentos_recebidos} nesta página. Essas contagens não comprovam "
               "a abrangência de todas as vendas da empresa.")
    st.info("O filtro de loja solicitado não confirma a loja efetiva da chave. "
            "Confira os documentos no ERP antes de autorizar uma carga.")
    st.caption("SKU, identificador da linha, devoluções e instante de alteração não foram comprovados. "
               "Dados desconhecidos não são preenchidos com zero. A amostra não é usada nos cálculos "
               "de estoque, não é salva e não ativa a sincronização automática.")
    st.caption("Quantidade e unidade são apresentadas como informadas pela origem. "
               "Unidade ausente aparece como ‘Não informada’; não há conversão automática entre caixas e unidades.")
    if not previa.documentos:
        st.info("Nenhum documento foi retornado nesta consulta. Isso não comprova ausência de vendas "
                "fora do período, do tipo ou das permissões da chave.")
        return
    situacoes = {situacao["id"]: situacao["nome"] for situacao in conta.situacoes}
    linhas = []
    for documento in previa.documentos:
        cabecalho = {"Documento (ID)": documento.id, "Número": documento.codigo or "Não informado",
                     "Data": documento.data.strftime("%d/%m/%Y"),
                     "Situação": situacoes.get(documento.situacao_id, "Não conferida"),
                     "Situação (ID)": documento.situacao_id}
        for item in documento.itens:
            linhas.append({**cabecalho, "Produto (ID)": item.produto_id,
                           "Variação (ID)": item.variacao_id or "Não informada",
                           "Quantidade": format(item.quantidade, "f"),
                           "Unidade": item.sigla_unidade or "Não informada"})
        if not documento.itens:
            linhas.append({**cabecalho, "Produto (ID)": "Sem itens retornados",
                           "Variação (ID)": "Não informada", "Quantidade": "Não informada",
                           "Unidade": "Não informada"})
    st.dataframe(linhas, hide_index=True, width="stretch")
