"""Conferência simultânea do saldo GET da loja e das vendas observadas.

O quadro é aberto somente pelo Master, com consentimento e um GET completo
por clique. Os dados ficam por cinco minutos apenas na sessão do Streamlit.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import streamlit as st

from integracoes.account_setup import consultar_conta, validar_conta
from integracoes.catalog_full import consultar_catalogo_completo
from integracoes.gestaoclick import ContratoInvalido, GestaoClickError
from reposicao.quadro_real import construir_quadro_real, exportar_quadro_real_csv
from web.errors import AppError, AuthenticationError, AuthorizationError
from web.integration_page import _secrets
from web.real_confirmed_batch_page import lote_conferido_controls
from web.real_home_status import consultar_status_real
from web.sales_service import access_stamp

_QUADRO_KEY = "_quadro_reposicao_get_por_loja"
_TTL_SECONDS = 300


def consultar_catalogo_para_quadro(
    auth, loja_id: str, *, autorizado: bool, carregar_configuracao=_secrets,
):
    """GET completo da loja, conferindo conta, autorização e integridade."""
    if autorizado is not True:
        raise AuthorizationError("Autorize explicitamente a consulta do catálogo.")
    with auth.operation():
        acesso = auth.require("integrations.manage")
        if acesso.profile.role != "Master":
            raise AuthorizationError("A conferência de saldos reais exige Master.")
        stamp = access_stamp(acesso, auth.generation)
        conta = consultar_conta(auth, carregar_configuracao, autorizado=True)
        if loja_id not in {x["id"] for x in conta.lojas}:
            raise AuthorizationError("A loja não pertence à conta autorizada.")
        catalogo = consultar_catalogo_completo(
            auth, carregar_configuracao, conta, loja_id, autorizado=True,
        )
        validar_conta(auth, conta)
        if stamp != access_stamp(auth.require("integrations.manage"), auth.generation):
            raise AuthorizationError("O acesso mudou durante a conferência.")
        if catalogo.loja_solicitada != loja_id:
            raise ContratoInvalido("A resposta não corresponde à loja solicitada.")
        return catalogo, datetime.now(timezone.utc)


def _mostrar_detalhe_produto(linhas: list[dict], loja_id: str) -> None:
    """No celular mostra saldo e vendas sem depender de rolar colunas laterais."""
    if not linhas:
        st.info("Nenhum produto corresponde aos filtros. Limpe a busca para consultar outros itens.")
        return
    por_id = {row["Produto (ID)"]: row for row in linhas}
    if len(por_id) != len(linhas):
        raise ContratoInvalido("Há produtos repetidos nesta conferência.")
    selecionado = st.selectbox(
        "Produto — visualizar saldo e vendas",
        list(por_id),
        index=0,
        format_func=lambda product_id: (
            f'{por_id[product_id]["Produto"]} · ID {product_id}'
        ),
        key=f"detalhe_quadro_reposicao_{loja_id}",
    )
    linha = por_id[selecionado]
    st.write(linha["Produto"])
    st.caption(
        f'Categoria: {linha["Categoria"]} · '
        f'Código interno: {linha["Código interno"]} · '
        f'Produto ID: {selecionado}'
    )
    with st.container(border=True):
        saldo = linha["Saldo GET (unidade do ERP não conferida)"]
        st.metric("Saldo na consulta ao GestãoClick", saldo if saldo != "Não informado" else "—")
        st.caption("Quantidade na unidade do ERP, ainda a conferir; saldo pontual, não ao vivo.")
        observado = linha["Vendas fechadas observadas (UN)"]
        st.metric(
            "Vendas fechadas observadas (UN)",
            observado if observado != "Não observado no período" else "—",
        )
        if observado == "Não observado no período":
            st.caption("Não observado na amostra; NÃO significa zero vendas.")
        if linha["Variações"] == "Conferir unidades/variações":
            st.warning("Produto com variações: confira as unidades antes de calcular reposição.")


def quadro_reposicao_real_controls(servico, contexto, loja_id: str | None) -> None:
    """Exibe saldos e vendas reais sem fabricar previsão ou mover estoque."""
    if (contexto.profile.role != "Master" or not contexto.can("integrations.manage")
            or not contexto.can("replenishment.view") or loja_id is None):
        return
    auth = servico.auth
    acesso = auth.require("integrations.manage")
    stamp = access_stamp(acesso, auth.generation)
    with st.expander("Conferir todos os produtos — saldo GET e vendas observadas"):
        st.caption(
            "Uma consulta manual por loja, com até 10 páginas do GestãoClick. "
            "Os saldos são do instante da consulta, não um acompanhamento contínuo; "
            "vendas observadas são parciais e não são subtraídas do saldo."
        )
        entrada = st.session_state.get(_QUADRO_KEY)
        if entrada is not None and (
                entrada.get("loja_id") != loja_id or entrada.get("stamp") != stamp
                or time.monotonic() >= entrada.get("expira", 0)):
            st.session_state.pop(_QUADRO_KEY, None)
            entrada = None
        with st.form(f"conferir_quadro_erp_{loja_id}"):
            consentiu = st.checkbox(
                "Autorizo consultar somente os produtos e saldos da loja escolhida "
                "pela API GestãoClick e cruzar com a última observação do Supabase."
            )
            consultar = st.form_submit_button("Consultar todos os saldos da loja")
        if consultar:
            st.session_state.pop(_QUADRO_KEY, None)
            entrada = None
            if not consentiu:
                st.warning("Marque a autorização de leitura antes da consulta.")
                return
            try:
                with st.spinner("Conferindo o catálogo integral da loja por GET…"):
                    catalogo, momento = consultar_catalogo_para_quadro(
                        auth, loja_id, autorizado=True,
                    )
                indisponivel = False
                try:
                    observacao = consultar_status_real(auth, loja_id)
                except (AuthenticationError, AuthorizationError):
                    raise
                except (AppError, GestaoClickError, TypeError, ValueError):
                    observacao = None
                    indisponivel = True
                quadro = construir_quadro_real(
                    catalogo, observacao, loja_id, momento,
                )
                if stamp != access_stamp(
                    auth.require("integrations.manage"), auth.generation,
                ):
                    raise AuthorizationError("A sessão mudou durante a consulta.")
                entrada = {
                    "stamp": stamp, "loja_id": loja_id,
                    "expira": time.monotonic() + _TTL_SECONDS,
                    "quadro": quadro, "observacao_indisponivel": indisponivel,
                }
                st.session_state[_QUADRO_KEY] = entrada
            except (AuthenticationError, AuthorizationError):
                raise
            except (AppError, GestaoClickError, ValueError, TypeError):
                st.warning(
                    "Não foi possível obter um catálogo completo e coerente da loja. "
                    "Nenhum saldo foi apresentado nem importado."
                )
                return
        if entrada is None:
            return
        quadro = entrada["quadro"]
        st.subheader(f"Loja {quadro.loja_id} · consulta pontual")
        st.caption(
            f"Saldo lido em {quadro.consultado_em:%d/%m/%Y %H:%M:%S} UTC. "
            "Válido somente no instante da leitura, podendo mudar no ERP."
        )
        a, b, c = st.columns(3)
        a.metric("Produtos consultados", quadro.produtos)
        b.metric("Com saldo informado", quadro.produtos_com_saldo)
        c.metric("Com venda observada", quadro.produtos_com_venda_observada)
        if quadro.periodo_inicio is None:
            st.info("Não há observação disponível para esta loja. Vendas não foram consideradas zero.")
        else:
            st.caption(
                f"Vendas fechadas observadas: {quadro.periodo_inicio:%d/%m/%Y} "
                f"a {quadro.periodo_fim:%d/%m/%Y}. "
                "A amostra ainda não comprova vendas excluídas, devoluções ou cobertura da API."
            )
        if entrada["observacao_indisponivel"]:
            st.warning("A consulta da observação falhou. Saldos GET apresentados sem dados de vendas.")
        if quadro.observados_sem_catalogo:
            st.warning(
                f"{quadro.observados_sem_catalogo} produto(s) observados em vendas não "
                "correspondem ao catálogo GET desta loja. Não foram incorporados à tabela."
            )
        st.caption(
            "Conferência, não pedido: saldo pontual do ERP; vendas parciais não "
            "são descontadas do saldo nem geram compra. É necessário validar "
            "devoluções, reservas, unidades por caixa e prazo."
        )
        busca = st.text_input(
            "Buscar nome, categoria, código ou ID",
            key=f"busca_quadro_real_{loja_id}",
        ).strip().casefold()
        somente_venda = st.checkbox(
            "Mostrar somente produtos com venda observada",
            key=f"quadro_real_somente_observado_{loja_id}",
        )
        linhas = list(quadro.linhas)
        if busca:
            linhas = [x for x in linhas if any(
                busca in str(x[k]).casefold()
                for k in ("Produto", "Categoria", "Código interno", "Produto (ID)")
            )]
        if somente_venda:
            linhas = [x for x in linhas if x["Vendas fechadas observadas (UN)"] != "Não observado no período"]
        st.caption(f"{len(linhas)} produto(s) encontrados nesta conferência.")
        _mostrar_detalhe_produto(linhas, loja_id)
        with st.expander("Ver tabela completa — deslize para as colunas à direita"):
            st.dataframe(linhas, hide_index=True, width="stretch")
        if contexto.can("replenishment.export") and contexto.can("inventory.export"):
            st.download_button(
                "Exportar quadro completo (CSV)",
                data=exportar_quadro_real_csv(quadro),
                file_name=f"conferencia_reposicao_loja_{loja_id}.csv",
                mime="text/csv; charset=utf-8",
                key=f"quadro_real_exportar_{loja_id}", on_click="ignore",
            )
        lote_conferido_controls(
            quadro, exportacao=contexto.can("replenishment.export") and contexto.can("inventory.export"),
        )
