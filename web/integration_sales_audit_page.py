"""Consulta integral de vendas e conciliação somente de leitura com o catálogo salvo."""
from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import streamlit as st

from analytics.sales_audit import (conciliar_vendas_com_catalogo, csv_demanda_observada,
                                   rotular_agrupamento)
from dados import hoje_local
from integracoes.account_setup import validar_conta
from integracoes.gestaoclick import ContratoInvalido, GestaoClickError
from integracoes.sales_audit import VendaIntegralInvalida, consultar_vendas_integrais
from web.errors import AppError, AuthenticationError, AuthorizationError

_LOJA_STATUS_CONFERIDOS = {
    # IDs provenientes da amostra real conferida na loja 416023;
    # nunca reutilizar automaticamente em outra loja ou conta.
    "416023": {"separacao": "6548068", "concretizada": "6548070"},
}


def _id_fonte(auth, loja: str) -> str | None:
    """Usa apenas fontes ativas já registradas nesta organização."""
    sources = auth.rpc("integration_sources", {}, "trends.view")
    if not isinstance(sources, list) or len(sources) > 100:
        raise ContratoInvalido("As fontes disponíveis não têm formato válido.")
    ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ContratoInvalido("A fonte não tem formato válido.")
        try:
            source_id = str(UUID(source["id"]))
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ContratoInvalido("A fonte não tem ID válido.") from None
        stores = source.get("stores")
        if (type(source.get("active")) is not bool or not isinstance(stores, list) or
                any(not isinstance(x, str) for x in stores)):
            raise ContratoInvalido("A fonte não possui lojas verificáveis.")
        if source["active"] and loja in stores:
            ids.add(source_id)
    if len(ids) > 1:
        raise ContratoInvalido(
            "Há várias fontes registradas para a mesma loja. Identifique a fonte correta antes de continuar."
        )
    return next(iter(ids), None)


def sales_audit_controls(servico, carregar_configuracao, conta, loja_id: str) -> None:
    auth = servico.auth
    with st.expander("Auditar todas as vendas do período e conciliar produtos"):
        st.caption(
            "Consulta somente vendas de produtos (sem PDV) e cruza os IDs com a "
            "fotografia já salva desta loja. Limite de sete dias e até 1.000 documentos; "
            "nenhuma venda é importada e nenhum estoque é descontado."
        )
        situacoes = {row["id"]: row["nome"] for row in conta.situacoes}
        ids = list(situacoes)
        padrao = _LOJA_STATUS_CONFERIDOS.get(loja_id, {})
        hoje = hoje_local().date()
        def indice(valor):
            return ids.index(valor) if valor in ids else None

        with st.form("sales_audit_complete"):
            datas = st.date_input(
                "Período da auditoria",
                (hoje - timedelta(days=6), hoje),
                max_value=hoje,
                key="sales_audit_dates",
            )
            separacao = st.selectbox(
                "Situação comercial: Em separação",
                ids, index=indice(padrao.get("separacao")),
                format_func=lambda x: f"{situacoes[x]} · ID {x}",
                placeholder="Confirme o ID desta loja", key="sales_audit_separacao",
            )
            concretizada = st.selectbox(
                "Situação comercial: Concretizada",
                ids, index=indice(padrao.get("concretizada")),
                format_func=lambda x: f"{situacoes[x]} · ID {x}",
                placeholder="Confirme o ID desta loja", key="sales_audit_concretizada",
            )
            confirmado = st.checkbox(
                "Conferi no ERP a loja e as duas situações fechadas; autorizo "
                "somente a consulta integral e conciliação sem gravação",
                key="sales_audit_consent",
            )
            executar = st.form_submit_button("Conferir vendas e conciliar com catálogo")
        if not executar:
            return
        if not confirmado or separacao is None or concretizada is None or separacao == concretizada:
            st.warning("Confirme a loja e selecione duas situações diferentes.")
            return
        if len(datas) != 2:
            st.warning("Informe o início e o fim do período.")
            return
        try:
            with st.spinner("Conferindo páginas, produtos e unidades sem alterar dados…"):
                with auth.operation():
                    auth.require("integrations.manage")
                    validar_conta(auth, conta)
                    source_id = _id_fonte(auth, loja_id)
                    if source_id is None:
                        raise ContratoInvalido("Registre antes a fonte analítica desta loja.")
                    foto = auth.rpc("catalog_stock_latest", {
                        "p_source_id": source_id, "p_store_id": loja_id,
                    }, "integrations.manage")
                    if not isinstance(foto, dict) or foto.get("found") is not True:
                        raise ContratoInvalido("Nenhuma fotografia de catálogo encontrada para esta fonte e loja.")
                    if foto.get("store_id") != loja_id:
                        raise AuthorizationError("A fotografia pertence a outra loja.")
                    auditoria = consultar_vendas_integrais(
                        auth, carregar_configuracao, conta,
                        loja_id, *datas, autorizado=True,
                    )
                    resumo = conciliar_vendas_com_catalogo(
                        auditoria, foto,
                        separacao_id=separacao, concretizada_id=concretizada,
                    )
                    validar_conta(auth, conta)
        except (AuthenticationError, AuthorizationError):
            raise
        except (AppError, GestaoClickError) as erro:
            st.warning(str(erro))
            if isinstance(erro, VendaIntegralInvalida):
                st.json(erro.diagnostico, expanded=False)
            return

        st.success(
            f"Consulta de leitura concluída: {auditoria.paginas} página(s), "
            f"{auditoria.total_informado} documentos e {auditoria.itens} itens. "
            "Nenhuma venda foi importada."
        )
        st.caption(
            f"Loja: {loja_id} · Fotografia: {foto['product_count']} produtos · "
            f"Período: {auditoria.inicio:%d/%m/%Y} a {auditoria.fim:%d/%m/%Y}."
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Pedidos fechados observados", resumo.documentos_fechados)
        c2.metric("Em separação", resumo.documentos_separacao)
        c3.metric("Concretizados", resumo.documentos_concretizados)
        st.caption(
            f"Outras situações não classificadas: {resumo.documentos_nao_classificados}. "
            f"IDs de produtos vistos: {resumo.produtos_vistos}; "
            f"IDs conciliados: {resumo.produtos_correspondentes}."
        )
        if resumo.produtos_sem_correspondencia:
            st.warning(
                f"{len(resumo.produtos_sem_correspondencia)} produto(s) das vendas "
                "não constam na fotografia desta loja; nenhum deles foi incorporado à demanda."
            )
            st.dataframe(
                [{"Produto sem correspondência (ID)": x} for x in resumo.produtos_sem_correspondencia[:20]],
                hide_index=True, width="stretch",
            )
        if resumo.linhas_unidade_nao_confirmada or resumo.linhas_unidade_nao_suportada:
            st.warning(
                f"Itens com unidade não informada: {resumo.linhas_unidade_nao_confirmada}. "
                f"Itens em caixa, peso ou outra unidade: {resumo.linhas_unidade_nao_suportada}. "
                "Esses itens não foram convertidos nem somados às unidades UN."
            )
        st.caption(
            f"Quantidade observada de itens UN em vendas fechadas, com produto conciliado: "
            f"{format(resumo.unidades_fechadas_un, 'f')}. "
            "Este número NÃO é movimentação de estoque nem previsão de demanda válida."
        )
        st.dataframe(
            list(rotular_agrupamento(resumo, foto)[:25]),
            hide_index=True, width="stretch",
        )
        st.caption(
            "A tabela exibe até 25 produtos com a identificação existente na fotografia. "
            "O CSV inclui todos os produtos com UN observada, nome, categoria e código "
            "quando informados pelo ERP; não contém clientes, preços nem saldo."
        )
        st.download_button(
            "Exportar todos os produtos observados (CSV)",
            data=csv_demanda_observada(resumo, foto),
            file_name=f"auditoria_vendas_loja_{loja_id}_{datas[0]}_{datas[1]}.csv",
            mime="text/csv; charset=utf-8",
            key="sales_audit_export",
            on_click="ignore",
        )
        st.warning(
            "AUDITORIA, NÃO SINCRONIZAÇÃO: a API ainda não comprovou ID estável "
            "da linha de venda, horário de alteração, devoluções nem exclusões. "
            "A paginação completa não comprova que todos os documentos da empresa "
            "são visíveis pela chave. Não usar estes números em reposição, "
            "não subtrair do saldo do GestãoClick e não declarar pedidos ausentes como cancelados."
        )
