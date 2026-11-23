"""Simulação de reposição com catálogo real e entradas verificadas pelo Master."""
from __future__ import annotations

import csv
import io

import streamlit as st

from analytics.observational_review import ConferenciaObservada
from dados import hoje_local
from integracoes.gestaoclick import GestaoClickError
from reposicao.cenario_manual_real import PreviaReposicaoReal, preparar_reposicao_real
from web.errors import AppError, AuthenticationError, AuthorizationError
from web.real_balance_refresh_page import mostrar_saldo_erp_reposicao
from web.real_catalog_stock_page import consultar_catalogo_real
from web.real_home_status import consultar_status_real


def exportar_cenario_manual_csv(previa: PreviaReposicaoReal) -> bytes:
    """Gera uma linha para conferência humana, protegendo rótulos vindos do ERP."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow([
        "Loja (ID)", "Produto (ID)", "Produto", "Categoria", "Fotografia em",
        "Conferido em", "Saldo atual conferido (UN)", "Reservas conferidas (UN)",
        "Demanda média conferida (UN/dia)", "Unidades por caixa", "Prazo (dias)",
        "Segurança (UN)", "Ciclo (dias)", "Necessidade líquida (UN)",
        "Solicitar (UN)", "Solicitar (CX)", "Chegada estimada",
        "Primeira ruptura projetada", "Aviso", "Origem da demanda",
    ])
    c = previa.resultado
    valores = [
        previa.loja_id, previa.produto_id, previa.produto_nome, previa.categoria,
        previa.fotografia_em.isoformat(), previa.conferencia_em.isoformat(),
        previa.estoque_conferido, previa.reservas_conferidas,
        str(previa.media_diaria_conferida), previa.unidades_por_caixa_conferidas,
        previa.prazo_dias_conferido, previa.seguranca_conferida,
        previa.ciclo_dias, c.necessidade_liquida, c.unidades, c.caixas,
        c.data_chegada.isoformat(), c.primeira_ruptura.isoformat() if c.primeira_ruptura else "",
        c.observacao, "Informada e conferida manualmente pelo operador",
    ]
    writer.writerow([
        "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
        else value for value in valores
    ])
    return buffer.getvalue().encode("utf-8-sig")


def produtos_reais_identificados(foto: dict) -> tuple[dict[str, dict], int]:
    """Não apresenta uma lista de IDs anônimos como catálogo pronto para operação."""
    linhas = foto["rows"]
    identificados = {
        row["Produto (ID)"]: row for row in linhas
        if row["Produto"] != "Não informado"
    }
    return identificados, len(linhas) - len(identificados)


def rotulo_produto_real(row: dict) -> str:
    """Identificação e saldo datado, nunca quantidade atual."""
    saldo = row["Saldo na fotografia (não atual)"]
    saldo_legivel = f"{saldo:.6f}".rstrip("0").rstrip(".")
    return (
        f'{row["Produto"]} · {row["Categoria"]} · ID {row["Produto (ID)"]} '
        f'· Saldo histórico: {saldo_legivel} UN'
    )


def observacao_produto_real(
    dados: ConferenciaObservada | None, loja_id: str, produto_id: str,
):
    """Somente observação exata da mesma loja; ausente não significa venda zero."""
    if dados is None:
        return None
    if not isinstance(dados, ConferenciaObservada) or dados.store_id != loja_id:
        raise ValueError("A observação não pertence à loja da simulação.")
    if not isinstance(produto_id, str) or not produto_id.isascii() or not produto_id.isdigit():
        raise ValueError("É necessário identificar um produto autorizado.")
    return next((linha for linha in dados.rows if linha.product_id == produto_id), None)


def _abrir_estoque_pelo_menu() -> None:
    """Troca o radio de navegação antes de o Streamlit instanciar o widget.

    on_click roda no início do rerun seguinte, evitando
    StreamlitWidgetAlreadyInstantiatedError no menu com key="_pagina".
    """
    st.session_state["_pagina"] = "Estoque"


def avisar_identificacao_incompleta(foto: dict, loja_id: str) -> None:
    """Diferencia ausência de nome (precisa reler) de categoria opcional ausente."""
    sem_nome = sum(row["Produto"] == "Não informado" for row in foto["rows"])
    sem_categoria = sum(row["Categoria"] == "Não informada" for row in foto["rows"])
    if sem_nome:
        st.warning(
            f"{sem_nome} produto(s) ainda sem nome na fotografia desta loja. "
            "Consulte novamente o catálogo no GestãoClick antes de usar os itens "
            "sem identificação; nenhum nome será inferido do ID."
        )
        st.button(
            "Abrir Estoque e histórico para atualizar o catálogo real",
            key=f"repor_abrir_estoque_atualizacao_{loja_id}",
            on_click=_abrir_estoque_pelo_menu,
        )
    if sem_categoria:
        st.info(
            f"{sem_categoria} produto(s) sem categoria na fotografia. "
            "Isso não impede o cálculo para produtos com nome identificado. "
            "Se o GestãoClick não fornece a categoria, uma nova fotografia "
            "não preenche esse campo; confira o cadastro no ERP."
        )


def reposicao_real_manual_controls(servico, contexto, loja_id: str | None) -> None:
    """Somente o Master acessa a simulação com um produto da loja escolhida."""
    if (contexto.profile.role != "Master" or not contexto.can("integrations.manage")
            or not contexto.can("replenishment.view") or loja_id is None):
        return
    with st.expander("Planejar reposição com produto real e dados conferidos"):
        st.caption(
            "Use o produto real da última fotografia salva e confira no GestãoClick "
            "o saldo ATUAL, as reservas e a saída média em unidades. Esta simulação "
            "NÃO usa histórico fictício, não consulta o ERP automaticamente e não cria pedido."
        )
        if not st.toggle("Abrir simulador real por produto", key=f"repor_real_abrir_{loja_id}"):
            return
        try:
            with st.spinner("Consultando a fotografia da loja no Supabase…"):
                foto = consultar_catalogo_real(servico.auth, loja_id)
        except (AuthenticationError, AuthorizationError):
            raise
        except (AppError, GestaoClickError, TypeError, ValueError):
            st.warning("Não foi possível validar a fotografia desta loja no Supabase.")
            return
        if foto is None:
            st.info("Não existe fotografia nesta loja. Salve uma na aba Estoque.")
            return
        st.caption(
            f'Loja {loja_id} · Fotografia histórica: {foto["created_at"]:%d/%m/%Y %H:%M %Z}. '
            "O saldo da fotografia NÃO será usado como saldo atual do cálculo."
        )
        opcoes, sem_nome = produtos_reais_identificados(foto)
        avisar_identificacao_incompleta(foto, loja_id)
        if not opcoes:
            st.error(
                "Nenhum produto desta fotografia possui nome. O simulador está "
                "bloqueado até salvar uma fotografia com identificação real."
            )
            st.dataframe([
                {
                    "Produto (ID)": row["Produto (ID)"],
                    "Código interno": row["Código interno"],
                    "Saldo na fotografia (histórico, UN)": row["Saldo na fotografia (não atual)"],
                }
                for row in foto["rows"][:20]
            ], hide_index=True, width="stretch")
            st.caption(
                "Acima: até 20 registros existentes no Supabase, com saldos históricos. "
                "Na aba Estoque e histórico: autorize a leitura completa por GET, "
                "confira a quantidade de nomes/categorias retornados e salve a nova fotografia. "
                "Se a própria API não fornecer nomes, não prossiga como se os dados fossem completos."
            )
            return
        selecionado = st.selectbox(
            "Produto real identificado", list(opcoes), index=None,
            format_func=lambda codigo: rotulo_produto_real(opcoes[codigo]),
            placeholder="Selecione um produto com nome informado pelo ERP",
            key=f"reposicao_produto_real_{loja_id}",
        )
        if selecionado is not None:
            selecionado_row = opcoes[selecionado]
            st.caption(
                f'Saldo da fotografia: {selecionado_row["Saldo na fotografia (não atual)"]:g} UN. '
                "Este número é histórico, não é a quantidade atual; "
                "confira o saldo disponível hoje no GestãoClick antes de simular."
            )
            mostrar_saldo_erp_reposicao(servico, loja_id, selecionado, selecionado_row)
            with st.container(border=True):
                st.caption(
                    "Consulta somente a última observação agregada já salva no Supabase. "
                    "Não consulta o ERP, não registra venda e não preenche automaticamente "
                    "a média diária do cálculo. Vendas excluídas ou devolvidas podem não estar cobertas."
                )
                if st.button(
                    "Consultar observação histórica do produto",
                    key=f"reposicao_volume_observado_{loja_id}",
                ):
                    try:
                        dados_observados = consultar_status_real(servico.auth, loja_id)
                        linha_observada = observacao_produto_real(
                            dados_observados, loja_id, selecionado,
                        )
                    except (AuthenticationError, AuthorizationError):
                        raise
                    except (AppError, GestaoClickError, ValueError, TypeError):
                        st.warning("Não foi possível conferir a observação desta loja.")
                    else:
                        if dados_observados is None:
                            st.info("Nenhuma observação real foi salva para a loja selecionada.")
                        elif linha_observada is None:
                            st.info(
                                "Este produto não consta da última observação do período. "
                                "Isso não comprova que teve zero vendas."
                            )
                        else:
                            st.metric(
                                "UN em vendas fechadas observadas (não demanda validada)",
                                format(linha_observada.observed_un, "f"),
                            )
                            st.caption(
                                f"Período observado: {dados_observados.period_start:%d/%m/%Y} "
                                f"a {dados_observados.period_end:%d/%m/%Y}; "
                                f"loja {loja_id}. Total parcial, não previsão de compras."
                            )
        with st.form(f"reposicao_real_conferencia_{loja_id}", clear_on_submit=True):
            a, b = st.columns(2)
            with a:
                saldo = st.text_input(
                    "Saldo atual conferido no GestãoClick (UN)",
                    max_chars=12,
                    placeholder="Obrigatório; digite ou copie de uma consulta GET recente",
                    key=f"reposicao_saldo_atual_{loja_id}_{selecionado}",
                )
                reservas = st.text_input("Reservas atuais conferidas (UN)", max_chars=12,
                                         placeholder="Digite 0 apenas se confirmou zero")
                media = st.text_input("Média diária conferida de saída (UN/dia)", max_chars=16,
                                      placeholder="Ex.: 2,5 (não é histórico automático)")
            with b:
                caixa = st.text_input("Unidades reais por caixa", max_chars=12,
                                      placeholder="Ex.: 12")
                prazo = st.text_input("Prazo total da indústria até a entrega (dias)", max_chars=12,
                                      placeholder="Ex.: 5")
                seguranca = st.text_input("Estoque de segurança conferido (UN)", max_chars=12,
                                          placeholder="Digite 0 se não houver reserva de segurança")
            ciclo = st.radio("Ciclo de solicitação", (14, 21), horizontal=True)
            st.caption("Cobertura alvo: 30 dias; a simulação considera o maior entre alvo e ciclo.")
            conferi = st.checkbox(
                "Conferi saldo, reservas, média diária, embalagem e prazo no GestãoClick "
                "ou em registros operacionais confiáveis para ESTE produto e loja."
            )
            simular = st.form_submit_button("Calcular reposição deste produto", type="primary")
        if not simular:
            return
        try:
            previa = preparar_reposicao_real(
                fotografia=foto, produto_id=selecionado, estoque=saldo, reservas=reservas,
                media_diaria=media, unidades_por_caixa=caixa, prazo_dias=prazo,
                seguranca=seguranca, ciclo_dias=ciclo, referencia=hoje_local().date(),
                conferencia_explicita=conferi,
            )
        except ValueError as erro:
            st.warning(str(erro))
            return
        resultado = previa.resultado
        st.subheader(f'{previa.produto_nome} · Loja {previa.loja_id}')
        a, b, c = st.columns(3)
        a.metric("Necessidade líquida (UN)", resultado.necessidade_liquida)
        b.metric("Quantidade arredondada (UN)", resultado.unidades)
        c.metric("Caixas", resultado.caixas)
        st.caption(
            f"Solicitação: {resultado.data_solicitacao:%d/%m/%Y} · "
            f"Chegada estimada: {resultado.data_chegada:%d/%m/%Y} · "
            f"Média informada: {previa.media_diaria_conferida} UN/dia."
        )
        if resultado.risco_antes_chegada:
            st.error("Risco de ruptura antes da chegada estimada. Confira prioridade no ERP.")
        if resultado.primeira_ruptura is not None:
            st.caption(f"Ruptura projetada: {resultado.primeira_ruptura:%d/%m/%Y}.")
        st.info(resultado.observacao)
        st.warning(
            "CENÁRIO MANUAL, NÃO PEDIDO: depende dos números conferidos hoje. "
            "Não soma vendas observadas nem promove a fotografia antiga ao motor; "
            "confira a necessidade na indústria antes de solicitar."
        )
        if contexto.can("replenishment.export"):
            st.download_button(
                "Exportar esta simulação em CSV", data=exportar_cenario_manual_csv(previa),
                file_name=f'reposicao_conferida_loja_{loja_id}_produto_{previa.produto_id}.csv',
                mime="text/csv; charset=utf-8",
                key=f'reposicao_manual_exportar_{loja_id}', on_click="ignore",
            )
