"""Páginas operacionais Flatter, com leitura autorizada e conteúdo sob demanda.

Dados e CSVs não permanecem em session_state. O estado contém somente escolhas
de navegação e filtros; cálculos e permissões continuam na fachada e no banco.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

import pandas as pd
import streamlit as st

from dados import hoje_local
from reposicao.apresentacao import projetar_painel
from reposicao.embalagens import ler_embalagens
from web.downloads import csv_export
from web.errors import AuthorizationError
from web.insights import gerar_insights
from web.reports import filtrar_relatorio
from web.theme import metric_card, page_header, status_badge, surface

PERMISSOES = {
    "dashboard.view": "Visão Geral e insights",
    "fiscal.view": "Visualizar solicitações fiscais",
    "fiscal.create_request": "Criar solicitações fiscais DEMO",
    "fiscal.approve_pending": "Autorizar pedidos Em andamento",
    "fiscal.audit": "Consultar auditoria fiscal",
    "inventory.view": "Estoque e histórico",
    "inventory.export": "Exportar estoque",
    "replenishment.view": "Sugestões de reposição",
    "replenishment.export": "Exportar reposição",
    "finance.view": "Custos e indicadores financeiros",
    "trends.view": "Tendências e Curva ABC",
    "inventory.import": "Importar dados",
    "parameters.edit": "Editar parâmetros",
    "decisions.write": "Registrar decisões",
    "audit.view": "Consultar auditoria",
    "reports.view": "Consultar relatórios mensais",
    "reports.export": "Exportar relatórios",
    "reports.receive": "Receber relatórios autorizados",
    "commercial.view": "Inteligência comercial agregada",
    "commercial.customer_detail": "Detalhes comerciais pseudonimizados por cliente",
}

COLUNAS_ESTOQUE = [
    "codigo", "nome", "estoque", "venda_media_diaria", "cobertura_dias", "status",
]
COLUNAS_REPOSICAO = [
    "fornecedor", "codigo", "nome", "status", "estoque", "reservas", "pedidos_pendentes",
    "venda_media_diaria", "cobertura_dias", "prazo_entrega_dias", "estoque_seguranca",
    "ponto_pedido", "posicao_estoque", "estoque_alvo", "risco_prazo", "quantidade_sugerida",
    "demanda_estimavel", "requer_revisao", "aviso_qualidade",
]
ROTULOS = {
    "codigo": "SKU", "nome": "Produto", "fornecedor": "Fornecedor", "status": "Situação",
    "estoque": "Estoque (un.)", "venda_media_diaria": "Demanda/dia", "cobertura_dias": "Cobertura (dias)",
    "prazo_entrega_dias": "Prazo (dias)", "risco_prazo": "Risco antes da entrega",
    "aviso_qualidade": "Qualidade do histórico", "custo_unitario": "Custo unitário (R$)",
    "capital_estoque": "Capital a custo (R$)", "quantidade_sugerida": "Sugestão (un.)",
    "pedidos_pendentes": "Pendentes (un.)", "reservas": "Reservas (un.)",
    "estoque_seguranca": "Segurança (un.)", "ponto_pedido": "Ponto de pedido",
    "posicao_estoque": "Posição (un.)", "estoque_alvo": "Alvo (un.)",
    "demanda_estimavel": "Demanda estimável", "requer_revisao": "Requer revisão",
    "media_longa": "Média de 90 dias", "tendencia": "Tendência", "classe_abc": "ABC a custo",
    "dias_sem_venda": "Dias sem venda", "display_name": "Nome", "role": "Perfil",
    "active": "Conta ativa", "user_id": "Identificador", "created_at": "Registrado em",
    "snapshot_date": "Data do estoque", "period_start": "Início do histórico",
    "period_end": "Fim do histórico", "source": "Origem", "action": "Ação",
    "quantidade": "Quantidade (un.)", "observacao": "Observação",
    "previsao_30": "Demanda em 30 dias", "caixas": "Caixas sugeridas", "unidades": "Unidades sugeridas",
    "solicitar_ate": "Solicitar até", "chegada": "Chegada estimada",
}
TONS_STATUS = {"Ruptura": "danger", "Crítico": "danger", "Baixo": "warning",
               "Excesso": "warning", "Parado": "info", "Saudável": "success"}


def _perfil(contexto: Any, campo: str) -> Any:
    """Lê o perfil validado, sem interpretar metadados editáveis da conta."""
    perfil = contexto.profile
    return perfil.get(campo) if isinstance(perfil, Mapping) else getattr(perfil, campo)


def _moeda(valor: float) -> str:
    """Formata somente capital conhecido, preservando ausência de informação."""
    if pd.isna(valor):
        return "Não disponível"
    return "R$ " + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _navegar(pagina: str, foco: str = "") -> None:
    """Define a próxima tela antes de recriar a navegação, sem guardar dados."""
    st.session_state["_pagina"] = pagina
    st.session_state["_foco_prioridade"] = foco


def _avancar_insight(passo: int) -> None:
    """Altera somente o índice; o rerun seguinte revalida acesso e dados."""
    st.session_state["_flash_indice"] = int(st.session_state.get("_flash_indice", 0)) + passo


def _limpar_filtros(prefixo: str) -> None:
    """Remove escolhas de apresentação, sem tocar em autenticação ou autorização."""
    for sufixo in ("_busca", "_fornecedores", "_status", "_prioridade", "_tabela_pagina"):
        st.session_state.pop(prefixo + sufixo, None)
    st.session_state.pop("_foco_prioridade", None)


def _tabela(tabela: pd.DataFrame, colunas: list[str] | None = None, *, key: str,
            paginar: bool = True, limite: int = 50) -> None:
    """Envia apenas a página visível e as colunas autorizadas ao navegador.

    A paginação limita a renderização, não a consulta histórica no Supabase.
    O backend continua responsável por validar sessão e remover valores negados.
    """
    exibida = tabela if colunas is None else tabela.loc[:, [c for c in colunas if c in tabela]]
    total = len(exibida)
    inicio = 0
    tamanho = limite
    if paginar and total > limite:
        a, b = st.columns(2)
        tamanho = a.selectbox("Itens por página", [50, 100, 250], key=key + "_tamanho")
        paginas = max(1, math.ceil(total / tamanho))
        chave = key + "_pagina"
        if st.session_state.get(chave, 1) > paginas:
            st.session_state[chave] = paginas
        pagina = b.number_input("Página", min_value=1, max_value=paginas, value=1,
                                step=1, key=chave)
        inicio = (pagina - 1) * tamanho
    fatia = exibida.iloc[inicio:inicio + tamanho]
    st.dataframe(fatia, hide_index=True, width="stretch",
                 column_config={c: ROTULOS.get(c, c.replace("_", " ").capitalize()) for c in fatia},
                 key=key)
    if total:
        st.caption(f"Exibindo {inicio + 1}–{min(inicio + tamanho, total)} de {total} registros.")
        if not paginar and total > limite:
            st.caption("A visualização está limitada. Os indicadores consideram o conjunto completo de dados.")


def _origem(dados: dict) -> None:
    """Mantém a data observada e a abrangência disponíveis junto ao resultado."""
    meta = dados.get("metadata") or {}
    st.caption(
        f"Fonte: {meta.get('source', 'Importação autorizada')} · "
        f"Estoque observado em {meta.get('snapshot_date', 'data não informada')} · "
        f"Histórico: {meta.get('period_start', 'não informado')} a {meta.get('period_end', 'não informado')}"
    )


def _validar_contexto(inicial: Any, atual: Any) -> None:
    """Recusa conteúdo cuja autorização mudou durante a operação remota."""
    if atual is None:
        raise AuthorizationError("Não foi possível confirmar a autorização dos dados recebidos.")
    campos = ("user_id", "organization_id", "role", "active")
    if (tuple(_perfil(inicial, campo) for campo in campos)
            != tuple(_perfil(atual, campo) for campo in campos)
            or inicial.permissions != atual.permissions):
        raise AuthorizationError("Seu acesso mudou. Atualize a página antes de consultar os dados.")


def _carregar(servico: Any, modulo: str, contexto: Any) -> dict | None:
    """Obtém uma base autorizada e confirma o contexto antes de renderizar dados."""
    with st.spinner("Carregando os dados autorizados…"):
        dados = servico.load_inventory(modulo)
    _validar_contexto(contexto, dados.get("_access_context"))
    _origem(dados)
    if dados["panel"].empty:
        st.info("Ainda não há produtos. Um usuário com permissão de importação pode carregar os CSVs.")
        return None
    return dados


def _flash(painel: pd.DataFrame, contexto: Any) -> None:
    """Reutiliza os indicadores da página, sem relógio ou consulta remota extra."""
    insights = gerar_insights(painel, contexto.permissions)
    if not insights:
        return
    indice = int(st.session_state.get("_flash_indice", 0)) % len(insights)
    insight = insights[indice]
    with surface("flatter_insight", tone="brand"):
        st.caption(f"LEITURA DO ESTOQUE · {indice + 1} DE {len(insights)}")
        st.subheader(insight.titulo)
        st.write(insight.texto)
        st.caption(insight.explicacao)
        with st.container(horizontal=True, wrap=True, gap="small"):
            st.button("Insight anterior", on_click=_avancar_insight, args=(-1,),
                      disabled=len(insights) < 2, key="insight_anterior")
            st.button("Próximo insight", on_click=_avancar_insight, args=(1,),
                      disabled=len(insights) < 2, key="insight_proximo")
            if contexto.can("replenishment.view") and insight.prioridade in {0, 2}:
                foco = "risco" if insight.prioridade == 0 else ""
                st.button("Conferir reposição", on_click=_navegar, args=("Reposição Inteligente", foco),
                          key="insight_acao", type="primary")
            elif contexto.can("inventory.view"):
                st.button("Conferir produtos", on_click=_navegar, args=("Estoque", ""),
                          key="insight_acao", type="primary")


def _painel(servico: Any, contexto: Any) -> None:
    """Reutiliza a página inicial com leitura exclusiva dos resumos prontos."""
    from web.dashboard_page import visao_geral
    visao_geral(servico, contexto)


def _filtros(painel: pd.DataFrame, prefixo: str) -> tuple[pd.DataFrame, dict]:
    """Mantém busca direta e filtros visíveis, sem alterar o conjunto recebido."""
    estados = sorted(painel["status"].unique().tolist())
    with surface("flatter_filtros_" + prefixo):
        busca = st.text_input("Buscar por nome ou SKU", key=prefixo + "_busca", max_chars=120,
                              placeholder="Digite o código ou o nome do produto").strip()
        status = st.multiselect("Situações", estados, default=estados, key=prefixo + "_status")
        opcoes = {"": "Todos os produtos", "ruptura": "Em ruptura", "risco": "Risco antes da entrega",
                  "qualidade": "Qualidade a revisar"}
        # Um atalho deve substituir a seleção anterior da mesma página. Apenas
        # alterar index não atualiza um widget que já possui valor na sessão.
        foco = st.session_state.pop("_foco_prioridade", None)
        if foco in opcoes:
            st.session_state[prefixo + "_prioridade"] = foco
        filtro = st.selectbox("Prioridade", list(opcoes), format_func=opcoes.get,
                              key=prefixo + "_prioridade")
        st.button("Limpar filtros", on_click=_limpar_filtros, args=(prefixo,),
                  key=prefixo + "_limpar")
    vista = painel.loc[painel["status"].isin(status)].copy()
    if busca:
        vista = vista.loc[vista["codigo"].str.contains(busca, case=False, regex=False)
                          | vista["nome"].str.contains(busca, case=False, regex=False)]
    if filtro == "ruptura":
        vista = vista.loc[vista["status"].eq("Ruptura")]
    elif filtro == "risco":
        vista = vista.loc[vista["risco_prazo"]]
    elif filtro == "qualidade":
        vista = vista.loc[vista["requer_revisao"]]
    return vista, {"status": status, "busca": busca, "prioridade": filtro}


def _estoque(servico: Any, contexto: Any) -> None:
    """Apresenta catálogo paginado e calcula séries somente quando solicitadas."""
    page_header("Estoque e histórico", "Encontre o produto e confira a memória dos indicadores.")
    dados = _carregar(servico, "inventory.view", contexto)
    if dados is None:
        return
    vista, _ = _filtros(dados["panel"], "estoque")
    colunas = COLUNAS_ESTOQUE + (["custo_unitario", "capital_estoque"] if contexto.can("finance.view") else [])
    _tabela(vista, colunas, key="estoque_tabela")
    if contexto.can("inventory.export"):
        csv_export(servico, "inventory", {}, contexto)
    if vista.empty:
        st.info("Nenhum produto corresponde aos filtros.")
        return
    opcoes = vista.set_index("codigo")["nome"].to_dict()
    codigo = st.selectbox("Produto", list(opcoes), format_func=lambda sku: f"{sku} · {opcoes[sku]}")
    item = vista.loc[vista["codigo"].eq(codigo)].iloc[0]
    with surface("flatter_produto"):
        st.subheader(str(item["nome"]))
        st.caption(f"SKU {codigo}")
        status_badge(str(item["status"]), tone=TONS_STATUS.get(str(item["status"]), "neutral"))
        if item["risco_prazo"]:
            status_badge("Risco antes da entrega", tone="danger")
        colunas = st.columns(4, gap="small", wrap=True)
        with colunas[0]:
            metric_card("Estoque", int(item["estoque"]), key="flatter_detalhe_estoque", width="stretch")
        with colunas[1]:
            metric_card("Venda média/dia", f"{item['venda_media_diaria']:.2f}" if item["demanda_estimavel"] else "Não estimável",
                        key="flatter_detalhe_demanda", width="stretch")
        with colunas[2]:
            metric_card("Cobertura", f"{item['cobertura_dias']:.1f} dias" if pd.notna(item["cobertura_dias"]) else "Indefinida",
                        key="flatter_detalhe_cobertura", width="stretch")
        with colunas[3]:
            metric_card("Prazo de reposição", f"{int(item['prazo_entrega_dias'])} dias",
                        key="flatter_detalhe_prazo", width="stretch")
        if item["requer_revisao"]:
            st.warning(str(item["aviso_qualidade"]))
        if contexto.can("replenishment.view"):
            _explicar_reposicao(item)
        if contexto.can("trends.view"):
            st.write("Tendência:", item["tendencia"])
            if contexto.can("finance.view"):
                st.write("Classe ABC a custo:", item["classe_abc"])
    if st.toggle("Mostrar gráficos do produto", value=False, key="estoque_graficos"):
        _graficos_produto(dados, codigo)
    else:
        st.caption("Ative os gráficos para consultar as vendas e os snapshots deste produto.")


def _graficos_produto(dados: dict, codigo: str) -> None:
    """Agrupa apenas a série escolhida, preservando lacunas de disponibilidade."""
    vendas = dados["sales"]
    historico = vendas.loc[vendas["codigo"].eq(codigo)].groupby("data")["quantidade"].sum()
    st.markdown("**Unidades vendidas no histórico importado**")
    if historico.empty:
        st.info("Não há venda registrada para este produto.")
    else:
        st.bar_chart(historico, height=240)
    fotos = dados["snapshots"]
    fotos = fotos.loc[fotos["codigo"].eq(codigo)].sort_values("data")
    st.markdown("**Snapshots registrados**")
    if fotos.empty:
        st.info("Ainda não há snapshots deste produto.")
    else:
        st.scatter_chart(fotos, x="data", y="estoque", height=240)
    st.caption("Cada ponto é uma observação de estoque. Intervalos sem foto não comprovam disponibilidade; "
               "os gráficos não estimam vendas perdidas.")


def _explicar_reposicao(item: pd.Series) -> None:
    """Mostra a fórmula auditada e seus valores sem criar compra automática."""
    st.markdown("**Memória do cálculo de reposição**")
    st.metric("Sugestão (un.)", int(item["quantidade_sugerida"]) if item["demanda_estimavel"] else "Revisar dados")
    st.write(f"Posição = estoque {int(item['estoque'])} + pendentes {int(item['pedidos_pendentes'])} "
             f"− reservas {int(item['reservas'])} = {int(item['posicao_estoque'])} unidades.")
    st.write(f"Prazo: {int(item['prazo_entrega_dias'])} dias; segurança: {int(item['estoque_seguranca'])} unidades. "
             f"Ponto de pedido: {int(item['ponto_pedido'])}; alvo: {int(item['estoque_alvo'])}. "
             "Sugestão = máximo(0, alvo − posição).")
    st.caption("Ponto de pedido = demanda/dia × prazo + segurança. "
               "Alvo = demanda/dia × maior(prazo, cobertura-alvo) + segurança. "
               "Ponto e alvo são arredondados para cima em unidades inteiras.")
    if not item["demanda_estimavel"]:
        st.warning("Os valores numéricos não substituem a revisão: não há demanda estimável para este produto.")
    st.caption("Pendentes não são entregas confirmadas. A sugestão exige conferência humana e não cria pedidos.")


def _reposicao(servico: Any, contexto: Any) -> None:
    """Integra ações à reposição, sem conceder leitura de estoque ao registrador."""
    areas = []
    if contexto.can("replenishment.view"):
        areas.append("Planejamento")
    if contexto.can("decisions.write"):
        areas.append("Acompanhamento de Ações")
    if not areas:
        raise AuthorizationError("Seu acesso não permite consultar reposição.")
    area = st.radio("Área da reposição", areas, horizontal=True, key="reposicao_area")
    if area == "Acompanhamento de Ações":
        _decisoes(servico, contexto)
    else:
        _planejamento_reposicao(servico, contexto)


def _planejamento_reposicao(servico: Any, contexto: Any) -> None:
    """Apresenta ciclos explicáveis sem depender de fornecedores ou alterar o ERP."""
    page_header("Reposição Inteligente", "Planeje solicitações à indústria com revisão humana.")
    modulo = "replenishment.view" if contexto.can("replenishment.view") else "trends.view"
    dados = _carregar(servico, modulo, contexto)
    if dados is None:
        return
    vista, filtros = _filtros(dados["panel"], "reposicao")
    if not contexto.can("replenishment.view"):
        st.caption("Sua conta possui acesso às tendências, sem permissão para consultar sugestões de reposição.")
        _tabela(vista, ["codigo", "nome", "venda_media_diaria", "tendencia"], key="reposicao_tendencias")
        return
    try:
        referencia = pd.Timestamp((dados.get("metadata") or {})["snapshot_date"]).date()
    except (KeyError, ValueError, TypeError):
        st.warning("A data do estoque não está disponível. Atualize os dados antes de simular.")
        return
    regras = servico.replenishment_rules(hoje_local().date())
    if referencia < hoje_local().date():
        st.warning("A simulação usa um estoque de data anterior. Atualize o saldo antes de solicitar à indústria.")
    config = regras["configuration"]
    ciclo = st.radio("Ciclo de reposição", [config["cycle_primary"], config["cycle_alternative"]], horizontal=True)
    espera = st.selectbox("Quando solicitar", [0, config["cycle_primary"], config["cycle_alternative"]],
                         format_func=lambda n: "Hoje" if n == 0 else f"Em {n} dias")
    aceitar = st.checkbox("Revisei as limitações do histórico e aceito uma estimativa com disponibilidade incerta.")
    projecao = projetar_painel(vista, regras, referencia, ciclo, espera, aceitar)
    previsoes = projecao["previsao_30"].dropna()
    caixas = projecao["caixas"].dropna()
    colunas = st.columns(3, gap="small", wrap=True)
    with colunas[0]:
        metric_card("Demanda em 30 dias", f"{previsoes.sum():.1f}" if not previsoes.empty else "Não calculável",
                    key="flatter_demanda_30", note="Soma parcial dos produtos estimáveis", width="stretch")
    with colunas[1]:
        metric_card("Caixas estimadas", int(caixas.sum()) if not caixas.empty else "Não calculável",
                    key="flatter_caixas", note=f"Referência habitual: {config['reference_boxes']} caixas", width="stretch")
    with colunas[2]:
        metric_card("Itens com risco", int(projecao["risco"].sum()), key="flatter_risco_ciclo", tone="warning", width="stretch")
    st.caption("A referência de caixas não é um limite de capacidade. Pendências sem data confirmada não entram na projeção.")
    tabela = projecao.copy()
    for campo in ("previsao_30", "caixas", "unidades", "solicitar_ate"):
        tabela[campo] = tabela[campo].map(lambda valor: "Não calculável" if pd.isna(valor) else str(valor))
    colunas_tabela = ["codigo", "nome", "estoque", "status", "previsao_30", "caixas", "unidades", "solicitar_ate", "observacao"]
    if contexto.can("trends.view"):
        colunas_tabela.insert(4, "tendencia")
    _tabela(tabela, colunas_tabela, key="reposicao_tabela")
    if not projecao.empty:
        opcoes = vista.set_index("codigo")["nome"].to_dict()
        codigo = st.selectbox("Produto para conferir a sugestão", list(opcoes),
                              format_func=lambda sku: f"{sku} · {opcoes[sku]}")
        with st.expander("Por que sugeriu?", expanded=False):
            item = vista.loc[vista["codigo"].eq(codigo)].iloc[0]
            _explicar_reposicao(item)
            st.write(projecao.loc[projecao["codigo"].eq(codigo), ["necessidade_liquida", "chegada", "ruptura", "observacao"]])
            if item["requer_revisao"]:
                st.warning(str(item["aviso_qualidade"]))
    if contexto.can("replenishment.export"):
        filtros.update(ciclo=ciclo, espera=espera, aceitar=aceitar)
        csv_export(servico, "industry", filtros, contexto)
    if contexto.can("decisions.write"):
        elegiveis = projecao.loc[projecao["unidades"].notna() & projecao["unidades"].gt(0)]
        nao_calculaveis = int(projecao["unidades"].isna().sum())
        if nao_calculaveis:
            st.warning(f"{nao_calculaveis} produto(s) sem quantidade calculável não entram na proposta.")
        confirmar = st.checkbox("Revisei as quantidades e os itens sem cálculo; desejo registrar uma proposta interna.")
        if st.button("Registrar proposta interna", disabled=not confirmar or elegiveis.empty):
            _validar_contexto(contexto, servico.context())
            itens = [{"sku": linha["codigo"], "units": int(linha["unidades"]), "boxes": int(linha["caixas"]),
                      "packaging_id": linha["embalagem_id"]} for linha in elegiveis.to_dict("records")]
            servico.save_industry_proposal(ciclo, referencia, itens)
            st.success("Proposta interna registrada. Nenhum pedido foi enviado à indústria ou ao ERP.")


def _tendencias(servico: Any, contexto: Any) -> None:
    """Exibe séries comparáveis e identifica explicitamente a base de custo da ABC."""
    page_header("Tendências e Curva ABC", "Compare demanda recente e histórica sem inventar sazonalidade.")
    dados = _carregar(servico, "trends.view", contexto)
    if dados is None:
        return
    painel = dados["panel"]
    st.caption("Médias de 30 e 90 dias. A comparação não comprova sazonalidade nem usa Machine Learning.")
    colunas = ["codigo", "nome", "venda_media_diaria", "media_longa", "tendencia", "aviso_qualidade"]
    if contexto.can("finance.view"):
        colunas.append("classe_abc")
        st.caption("A Curva ABC mede consumo a custo, não faturamento. Custos ausentes tornam a classificação parcial.")
    else:
        st.info("A Curva ABC a custo exige permissão financeira.")
    _tabela(painel, colunas, key="tendencias_tabela")
    meta = dados.get("metadata") or {}
    inicio = pd.to_datetime(meta.get("period_start"), errors="coerce")
    fim = pd.to_datetime(meta.get("snapshot_date"), errors="coerce")
    completo = pd.notna(inicio) and pd.notna(fim) and (fim - inicio).days >= 89
    if st.toggle("Mostrar gráficos de tendências", value=False, key="tendencias_graficos"):
        if not completo or not painel["venda_media_diaria"].gt(0).any():
            st.info("Os gráficos exigem pelo menos 90 dias de histórico declarado e vendas positivas.")
        else:
            top = painel.sort_values("venda_media_diaria", ascending=False).head(20)
            st.caption("Até 20 produtos com maior média recente. Limitações de disponibilidade continuam aplicáveis.")
            st.bar_chart(top.set_index("nome")[["venda_media_diaria", "media_longa"]], height=300)
            if contexto.can("finance.view"):
                classes = painel.loc[painel["classe_abc"].isin(["A", "B", "C"]), "classe_abc"].value_counts()
                if not classes.empty:
                    st.markdown("**Produtos por classe ABC a custo**")
                    st.bar_chart(classes.rename("Produtos"), height=240)
    st.subheader("Parados e excesso")
    st.caption("Ausência de histórico não é prova de produto parado.")
    colunas = ["codigo", "nome", "status", "estoque", "dias_sem_venda", "cobertura_dias", "aviso_qualidade"]
    if contexto.can("finance.view"):
        colunas.append("capital_estoque")
    _tabela(painel.loc[painel["status"].isin(["Parado", "Excesso"])], colunas, key="tendencias_excesso")


def _relatorios(servico: Any, contexto: Any) -> None:
    """Gera somente o mês solicitado, sem guardar relatório ou bytes na sessão."""
    page_header("Relatório mensal", "Fechamento confiável, comparação explicável e acesso controlado.")
    anterior = hoje_local().replace(day=1) - pd.Timedelta(days=1)
    # A data fica fora de um formulário: preparar CSV deve receber também uma
    # alteração de mês que ainda não foi usada para visualizar o relatório.
    with surface("relatorio_filtros"):
        mes = st.date_input("Mês de referência", value=anterior.date(), max_value=anterior.date()).replace(day=1)
        st.caption("Exige histórico completo do período e fotografia de estoque no último dia do mês.")
        gerar = st.button("Gerar relatório", type="primary")
    # O botão de exportação é independente do resultado momentâneo do formulário.
    # Assim seu clique continua disponível no rerun, sem reter relatório ou CSV.
    if contexto.can("reports.export"):
        csv_export(servico, "monthly", {"month": mes.isoformat()}, contexto)
    if not gerar:
        st.info("Para exibir os indicadores nesta página, selecione um mês fechado e clique em Gerar relatório.")
        return
    with st.spinner("Conferindo o fechamento e preparando os indicadores…"):
        relatorio = servico.monthly_report(mes)
    _validar_contexto(contexto, relatorio.get("_access_context"))
    # Defesa de apresentação adicional ao filtro vigente aplicado pela fachada.
    relatorio = filtrar_relatorio(relatorio, contexto.permissions)
    periodo = relatorio["periodo"]
    st.write(f"Período: {periodo['inicio']} a {periodo['fim']}")
    resumo = relatorio["resumo"]
    variacao = resumo.get("variacao_unidades_pct")
    colunas = st.columns(3 if variacao is not None else 2, gap="small", wrap=True)
    with colunas[0]:
        metric_card("Unidades vendidas", resumo["unidades_vendidas"], key="flatter_mes_vendas", width="stretch")
    with colunas[1]:
        metric_card("Produtos com venda", resumo["produtos_com_venda"], key="flatter_mes_produtos", width="stretch")
    if variacao is not None:
        with colunas[2]:
            metric_card("Variação de unidades", f"{variacao:.1f}%", key="flatter_mes_variacao",
                        note="Frente ao mês anterior", width="stretch")
    for aviso in relatorio.get("cautelas", []):
        st.warning(aviso)
    fonte = relatorio.get("fonte", {})
    st.caption(f"Fonte: {fonte.get('origem', 'Importação')} · Atualização: {fonte.get('atualizado_em', 'não informada')}")
    titulos = {"mais_vendidos": "Mais vendidos por quantidade", "estoque": "Estoque e risco",
               "reposicao": "Oportunidades de reposição", "tendencias": "Tendências"}
    for chave, titulo in titulos.items():
        if chave in relatorio:
            with surface("flatter_relatorio_" + chave):
                st.subheader(titulo)
                _tabela(pd.DataFrame(relatorio[chave]), key="relatorio_" + chave, paginar=False)
    if contexto.can("finance.view") and "financeiro" in relatorio:
        financeiro = relatorio["financeiro"]
        st.subheader("Indicadores a custo")
        colunas_financeiras = st.columns(2, gap="small", wrap=True)
        with colunas_financeiras[0]:
            metric_card("Capital em estoque no fechamento", _moeda(financeiro.get("capital_estoque")),
                        key="flatter_mes_capital", width="stretch")
        with colunas_financeiras[1]:
            metric_card("Capital parado no fechamento", _moeda(financeiro.get("capital_parado")),
                        key="flatter_mes_parado", width="stretch")
        st.caption(financeiro.get("base", "Valores a custo informado; não representam receita."))
        if financeiro.get("custos_parciais"):
            st.warning("Capital parcial: há produtos sem custo informado no fechamento.")
        if financeiro.get("limite_excedido"):
            st.warning("Há valores acima do limite de representação. O capital não é calculável com segurança.")


def _importacao(servico: Any, contexto: Any) -> None:
    """Apresenta importação completa, histórico autorizado e demonstração restrita."""
    page_header("Importar catálogo e vendas", "Valide a fonte antes de atualizar a referência operacional.")
    st.info("A importação é completa. Os lotes anteriores permanecem no histórico; não envie um arquivo parcial como completo.")
    st.caption("Arquivos UTF-8, separador ; e decimal ,. Datas AAAA-MM-DD ou DD/MM/AAAA. Até 10 MiB por arquivo.")
    hoje = hoje_local().date()
    with st.form("importar_csv", clear_on_submit=True):
        produtos = st.file_uploader("produtos.csv", type=["csv"])
        vendas = st.file_uploader("vendas.csv", type=["csv"])
        foto = st.date_input("Data observada do estoque", value=hoje, max_value=hoje)
        a, b = st.columns(2)
        inicio = a.date_input("Início do histórico completo", value=hoje.replace(day=1), max_value=hoje)
        fim = b.date_input("Fim do histórico completo", value=hoje, max_value=hoje)
        confirmado = st.checkbox("Conferi que os dois arquivos e o período informado estão completos.")
        importar = st.form_submit_button("Validar e importar", type="primary")
    if importar:
        if produtos is None or vendas is None or not confirmado:
            st.error("Selecione os dois arquivos e confirme a abrangência do histórico.")
        else:
            with st.spinner("Validando os arquivos e gravando o lote no Supabase…"):
                servico.import_csv(produtos.getvalue(), vendas.getvalue(), foto, inicio, fim)
            st.success("Importação concluída no Supabase. O histórico foi preservado.")
    if contexto.can("inventory.view") and st.button("Consultar histórico de importações"):
        dados = servico.load_inventory("inventory.view")
        _validar_contexto(contexto, dados.get("_access_context"))
        _tabela(pd.DataFrame(dados.get("imports", [])), key="importacoes_historico", paginar=False)
    if _perfil(contexto, "role") == "Master":
        with st.expander("Carregar dados fictícios para validação"):
            st.caption("Cria um lote demonstrativo nesta organização. Não consulta o ERP.")
            referencia = st.date_input("Data da demonstração", value=hoje, max_value=hoje)
            confirmar = st.checkbox("Autorizo substituir a referência operacional por este lote fictício.")
            if st.button("Importar demonstração fictícia", disabled=not confirmar):
                with st.spinner("Preparando o lote fictício…"):
                    servico.import_demo(referencia, referencia - pd.Timedelta(days=119), referencia)
                st.success("Lote fictício importado. Abra o painel para analisar os resultados.")


def _parametros(servico: Any, contexto: Any) -> None:
    """Edita parâmetros de cálculo do gerente, preservando os saldos oficiais."""
    page_header("Regras de Reposição", "Ciclos e embalagens permanecem apenas no Gerente.")
    dados = _carregar(servico, "parameters.edit", contexto)
    if dados is None:
        return
    produtos = dados["products"].set_index("codigo", drop=False)
    hoje = hoje_local().date()
    regras = servico.replenishment_rules(hoje)
    config = regras["configuration"]
    with st.expander("Ciclos e referência do caminhão"):
        with st.form("configuracao_ciclos"):
            primario = st.number_input("Ciclo principal (dias)", min_value=1, max_value=365, value=config["cycle_primary"])
            alternativo = st.number_input("Ciclo alternativo (dias)", min_value=1, max_value=365, value=config["cycle_alternative"])
            referencia = st.number_input("Referência habitual de caixas", min_value=1, max_value=100000, value=config["reference_boxes"])
            st.caption("A referência é informativa; não é um limite físico de carga.")
            if st.form_submit_button("Salvar ciclos"):
                servico.save_replenishment_configuration(primario, alternativo, referencia)
                st.success("Ciclos atualizados.")
    codigo = st.selectbox("Produto para parametrizar", list(produtos.index),
                          format_func=lambda sku: f"{sku} · {produtos.loc[sku, 'nome']}")
    item = produtos.loc[codigo]
    with st.form("parametros_" + codigo):
        prazo = st.number_input("Prazo de produção e transporte (dias)", min_value=0, max_value=365,
                                value=int(item["prazo_entrega_dias"]), step=1)
        seguranca = st.number_input("Estoque de segurança (unidades)", min_value=0, max_value=2147483647,
                                    value=int(item["estoque_seguranca"]), step=1)
        alvo = st.number_input("Cobertura-alvo (dias)", min_value=1, max_value=2147483647,
                              value=int(item["cobertura_alvo_dias"]), step=1)
        st.caption("Salvar altera somente os cálculos do gerente. Nenhum saldo do ERP será editado.")
        if st.form_submit_button("Salvar parâmetros", type="primary"):
            servico.update_parameters(codigo, {"prazo_entrega_dias": prazo,
                                               "estoque_seguranca": seguranca, "cobertura_alvo_dias": alvo})
            st.success("Parâmetros salvos. Os novos valores serão usados na próxima análise.")
    st.subheader("Embalagens por produto")
    atuais = {r["sku"]: r for r in regras.get("rules", [])}
    atual = atuais.get(codigo, {})
    if atual:
        st.caption(f"Embalagem vigente: 1 caixa = {atual['units_per_box']} unidades desde {atual['effective_on']}.")
    else:
        st.info("Cadastro de embalagem necessário para calcular caixas deste produto.")
    with st.form("embalagem_" + codigo):
        unidades = st.number_input("Unidades por caixa", min_value=1, max_value=100000,
                                   value=atual.get("units_per_box"), step=1, placeholder="Informe a conversão")
        vigencia = st.date_input("Data de vigência", value=hoje)
        minimo = st.number_input("Lote mínimo (unidades)", min_value=0, max_value=2147483647,
                                 value=atual.get("minimum_lot", 0), step=1)
        multiplo = st.number_input("Múltiplo de produção (unidades)", min_value=1, max_value=2147483647,
                                   value=atual.get("production_multiple", 1), step=1)
        usar_maximo = st.checkbox("Definir limite máximo de unidades", value=atual.get("maximum_units") is not None)
        maximo = st.number_input("Limite máximo (unidades)", min_value=1, max_value=2147483647,
                                 value=atual.get("maximum_units") or 1, step=1, disabled=not usar_maximo)
        observacao = st.text_area("Observação da embalagem", value=atual.get("note", ""), max_chars=500)
        if st.form_submit_button("Salvar embalagem", type="primary"):
            if unidades is None:
                st.error("Informe a quantidade de unidades por caixa.")
            else:
                servico.save_replenishment_packaging(codigo, vigencia, unidades, minimo, multiplo,
                                                    maximo if usar_maximo else None, observacao,
                                                    regras.get("revisions", {}).get(codigo))
                st.success("Nova versão salva para uso nas próximas simulações.")
    with st.expander("Importar embalagens por CSV"):
        st.caption("Cabeçalhos: sku;unidades_por_caixa;vigencia. Opcional: lote_minimo;multiplo;maximo;observacao.")
        arquivo = st.file_uploader("CSV de embalagens", type=["csv"], key="csv_embalagens")
        if arquivo is not None:
            try:
                linhas = ler_embalagens(arquivo.getvalue(), set(produtos.index), regras.get("revisions", {}))
                digest = hashlib.sha256(json.dumps(linhas, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                if st.button("Preparar prévia de embalagens"):
                    st.session_state["_embalagens_previa"] = digest
                if st.session_state.get("_embalagens_previa") == digest:
                    _tabela(pd.DataFrame(linhas), ["sku", "units_per_box", "effective_on", "minimum_lot", "production_multiple", "maximum_units"],
                            key="embalagens_previa")
                    confirmar = st.checkbox("Conferi as versões e confirmo esta importação interna.")
                    if st.button("Confirmar importação de embalagens", disabled=not confirmar):
                        servico.save_replenishment_packaging_batch(linhas)
                        st.session_state.pop("_embalagens_previa", None)
                        st.success("Embalagens registradas. Nenhuma informação foi enviada ao ERP.")
                elif st.session_state.get("_embalagens_previa"):
                    st.warning("Arquivo ou versões mudaram. Prepare uma nova prévia antes de confirmar.")
            except ValueError as erro:
                st.error(str(erro))


def _decisoes(servico: Any, contexto: Any) -> None:
    """Registra a decisão humana e consulta o histórico somente quando solicitado."""
    page_header("Acompanhamento de Ações", "Registre uma decisão humana, sem criar pedidos no ERP.")
    dados = _carregar(servico, "decisions.write", contexto)
    if dados is None:
        return
    painel = dados["panel"].set_index("codigo", drop=False)
    with st.form("decisao", clear_on_submit=True):
        codigo = st.selectbox("Produto da decisão", list(painel.index),
                              format_func=lambda sku: f"{sku} · {painel.loc[sku, 'nome']}")
        acao = st.selectbox("Decisão", ["Em análise", "Adiada", "Solicitar à indústria", "Descartada", "Concluída"])
        quantidade = st.number_input("Quantidade avaliada", min_value=0, max_value=2147483647, value=0, step=1)
        observacao = st.text_area("Observação opcional", max_chars=500)
        if st.form_submit_button("Registrar decisão", type="primary"):
            servico.save_decision(codigo, acao, quantidade, observacao)
            st.success("Decisão registrada. Nenhum pedido foi criado.")
    if st.button("Consultar decisões anteriores"):
        _tabela(pd.DataFrame(servico.list_decisions()), key="decisoes_historico", paginar=False)
    if contexto.can("replenishment.view") and st.button("Consultar propostas internas"):
        _tabela(pd.DataFrame(servico.list_industry_proposals()), key="propostas_historico", paginar=False)


def _permissoes_formulario(atuais: Mapping[str, bool] | None = None) -> dict[str, bool]:
    """Preserva herança e exceções individuais, sem delegar privilégios de Master."""
    atuais = atuais or {}
    resultado = {}
    st.caption("Herdar usa a permissão do perfil escolhido. Permitir ou negar cria uma exceção individual. Administração é exclusiva do Master.")
    for permissao, descricao in PERMISSOES.items():
        indice = 0 if permissao not in atuais else (1 if atuais[permissao] else 2)
        opcao = st.selectbox(f"{descricao} ({permissao})", ["Herdar do perfil", "Permitir", "Negar"], index=indice)
        if opcao != "Herdar do perfil":
            resultado[permissao] = opcao == "Permitir"
    return resultado


def _editar_usuario(servico: Any, usuarios: list[dict]) -> None:
    """Mantém o formulário de acesso explícito e exige confirmação para desativar."""
    editaveis = {u["user_id"]: u for u in usuarios if u.get("role") != "Master"}
    if not editaveis:
        st.info("Ainda não há outra conta autorizada para editar.")
        return
    alvo = st.selectbox("Usuário para editar", list(editaveis),
                        format_func=lambda uid: str(editaveis[uid].get("display_name", uid)))
    usuario = editaveis[alvo]
    with st.form("editar_usuario_" + alvo):
        nome = st.text_input("Nome de exibição", value=usuario.get("display_name", ""), max_chars=100)
        perfis = ["Administrador", "Operador", "Visualizador"]
        perfil = st.selectbox("Perfil", perfis, index=perfis.index(usuario["role"]))
        ativo = st.checkbox("Conta ativa", value=bool(usuario.get("active")))
        permissoes = usuario.get("permissions", {})
        if isinstance(permissoes, list):
            permissoes = dict.fromkeys(permissoes, True)
        concedidas = _permissoes_formulario(permissoes)
        confirmar = st.checkbox("Confirmo a desativação, caso tenha desmarcado Conta ativa.")
        if st.form_submit_button("Salvar acesso", type="primary"):
            if usuario.get("active") and not ativo and not confirmar:
                st.error("Confirme a desativação antes de retirar o acesso desta conta.")
            else:
                servico.admin_set_user(alvo, nome, perfil, ativo, concedidas)
                st.success("Acesso atualizado. As próximas operações usarão as novas permissões.")


def _autorizar_usuario(servico: Any) -> None:
    """Autoriza uma identidade existente, sem mudar a política de cadastro público."""
    st.caption("Use o UUID da conta criada no painel Supabase. Sem perfil ativo, ela não acessa os dados.")
    with st.form("autorizar_usuario", clear_on_submit=True):
        identificador = st.text_input("UUID do usuário", max_chars=36)
        nome = st.text_input("Nome do usuário", max_chars=100)
        perfil = st.selectbox("Perfil inicial", ["Visualizador", "Operador", "Administrador"])
        concedidas = _permissoes_formulario()
        if st.form_submit_button("Autorizar usuário", type="primary"):
            servico.admin_set_user(identificador, nome, perfil, True, concedidas)
            st.success("Usuário autorizado com as permissões selecionadas.")


def _criar_usuario(servico: Any) -> None:
    """Disponibiliza a operação administrativa já protegida, sem enviar convite."""
    st.caption("Defina uma senha temporária e entregue-a por um canal seguro. Ela vence em 24 horas.")
    with st.form("criar_usuario", clear_on_submit=True):
        email = st.text_input("E-mail do novo usuário", max_chars=254)
        nome = st.text_input("Nome da nova conta", max_chars=100)
        senha = st.text_input("Senha inicial forte", type="password", autocomplete="new-password")
        st.caption("Use 12 a 128 caracteres, com letras, números e símbolos.")
        perfil = st.selectbox("Perfil da nova conta", ["Visualizador", "Operador", "Administrador"])
        concedidas = _permissoes_formulario()
        if st.form_submit_button("Criar usuário autorizado", type="primary"):
            servico.auth.create_user(email, senha, nome, perfil, concedidas)
            st.success("Conta criada. Entregue a senha por canal seguro; a troca será obrigatória no primeiro acesso.")


def _redefinir_usuario(servico: Any, usuarios: list[dict]) -> None:
    """Permite ao Master recuperar um funcionário, confirmando novamente sua identidade."""
    alvos = {u["user_id"]: u for u in usuarios if u.get("role") != "Master" and u.get("active")}
    if not alvos:
        st.info("Não há funcionário ativo disponível para redefinição.")
        return
    with st.form("redefinir_acesso", clear_on_submit=True):
        alvo = st.selectbox("Funcionário", list(alvos),
                            format_func=lambda uid: f"{alvos[uid].get('display_name', '')} · {alvos[uid].get('email', '')}")
        temporaria = st.text_input("Nova senha temporária", type="password", autocomplete="new-password")
        atual = st.text_input("Confirme sua senha Master", type="password", autocomplete="current-password")
        confirmado = st.checkbox("Confirmei a identidade do funcionário e entregarei a senha por canal seguro.")
        if st.form_submit_button("Redefinir acesso", type="primary"):
            if not confirmado:
                st.error("Confirme a identidade do funcionário para continuar.")
            else:
                servico.auth.reset_employee_password(alvo, temporaria, atual)
                st.success("Acesso redefinido. A senha temporária vence em 24 horas e exige troca no próximo acesso.")


ADMIN_PERMISSIONS = frozenset({"inventory.import", "parameters.edit", "integrations.manage", "audit.view", "fiscal.audit", "fiscal.manage"})


def _administracao(servico: Any, contexto: Any) -> None:
    """Agrupa configurações preservando o acesso granular de usuários não Master."""
    from web.fiscal_service import FiscalService
    from web.integration_page import integracoes

    page_header("Administração", "Configurações e operações disponíveis para suas permissões.")
    master = _perfil(contexto, "role") == "Master"
    opcoes = {}
    if master:
        opcoes["Usuários e permissões"] = _usuarios_e_permissoes
    if master and contexto.can("reports.manage"):
        opcoes["Relatórios por e-mail"] = None
    if contexto.can("parameters.edit"):
        opcoes["Regras de Reposição"] = _parametros
    if contexto.can("inventory.import"):
        opcoes["Importação manual / DEMO"] = _importacao
    if master and contexto.can("integrations.manage"):
        opcoes["Integrações"] = integracoes
    if contexto.can("audit.view"):
        opcoes["Auditoria"] = _auditoria
    if master and contexto.can("fiscal.manage"):
        opcoes["Configuração fiscal"] = None
    if contexto.can("fiscal.audit"):
        opcoes["Auditoria fiscal"] = None
    if not opcoes:
        raise AuthorizationError("Nenhuma área administrativa foi autorizada.")
    if st.session_state.get("admin_area") not in opcoes:
        st.session_state["admin_area"] = next(iter(opcoes))
    area = st.radio("Área administrativa", list(opcoes), horizontal=True, key="admin_area")
    if opcoes[area] is not None:
        opcoes[area](servico, contexto)
    elif area == "Relatórios por e-mail":
        _configurar_relatorios(servico, servico.admin_list_users())
    elif area == "Configuração fiscal":
        fiscal = FiscalService(servico.auth)
        config = fiscal.configuracao()
        st.info("Executor: Supabase Edge Functions · Emissão real bloqueada · Sem XML/PDF.")
        habilitado = st.checkbox("Habilitar módulo DEMO", value=config['demo_enabled'])
        if st.button("Salvar configuração fiscal"):
            fiscal.configurar(habilitado)
            st.success("Configuração DEMO salva. A emissão real permanece bloqueada.")
    else:
        fiscal = FiscalService(servico.auth)
        registros = fiscal.auditar()
        _tabela(pd.DataFrame(registros), key="admin_fiscal_requests")
        if registros:
            alvo = st.selectbox("Solicitação fiscal para auditoria", [r['id'] for r in registros], index=None)
            if alvo and st.button("Consultar transições fiscais"):
                _tabela(pd.DataFrame(fiscal.historico(alvo)), key="admin_fiscal_events")


def _usuarios_e_permissoes(servico: Any, contexto: Any) -> None:
    """Carrega apenas a área administrativa escolhida, mantendo o acesso Master."""
    st.subheader("Usuários e permissões")
    if _perfil(contexto, "role") != "Master":
        st.error("A administração de usuários é restrita ao Master.")
        return
    usuarios = servico.admin_list_users()
    _tabela(pd.DataFrame(usuarios), ["display_name", "email", "role", "active"], key="admin_usuarios")
    if not (contexto.can("users.manage") and contexto.can("permissions.manage")):
        st.info("A gestão de acessos exige as permissões administrativas do Master.")
        return
    operacao = st.selectbox("Operação de usuários", ["Editar acessos", "Criar conta", "Redefinir acesso", "Histórico administrativo"])
    if operacao == "Editar acessos":
        _editar_usuario(servico, usuarios)
    elif operacao == "Criar conta":
        _criar_usuario(servico)
    elif operacao == "Redefinir acesso":
        _redefinir_usuario(servico, usuarios)
    elif contexto.can("audit.view"):
        _tabela(pd.DataFrame(servico.admin_audit()), key="admin_historico_acessos", paginar=False)
    else:
        st.info("O histórico exige permissão de auditoria.")


def _configurar_relatorios(servico: Any, usuarios: list[dict]) -> None:
    """Configura a rotina sem habilitar entrega real por uma simples visita à tela."""
    st.subheader("Relatórios mensais por e-mail")
    configuracao = servico.reports_config()
    with st.form("configurar_relatorios"):
        habilitado = st.checkbox("Habilitar rotina mensal", value=bool(configuracao.get("enabled", False)))
        simulado = st.checkbox("Modo de simulação, sem envio de e-mail", value=bool(configuracao.get("dry_run", True)))
        candidatos = {u["user_id"]: u for u in usuarios if u.get("active")}
        selecionados = st.multiselect("Destinatários autorizados", list(candidatos),
                                      default=[u for u in configuracao.get("recipients", []) if u in candidatos],
                                      format_func=lambda uid: str(candidatos[uid].get("display_name", uid)))
        dia = st.number_input("Dia do envio mensal", min_value=1, max_value=28,
                              value=int(configuracao.get("send_day", 1)), step=1)
        blocos = st.multiselect("Blocos autorizados do relatório", ["estoque", "reposicao", "tendencias", "financeiro"],
                               default=configuracao.get("blocks", ["estoque", "reposicao", "tendencias"]))
        st.caption("A execução revalida conta ativa e reports.receive. Valores financeiros não são enviados por e-mail.")
        confirmado = st.checkbox("Autorizo estes destinatários para os envios reais quando o provedor estiver configurado.")
        if st.form_submit_button("Salvar rotina de relatórios", type="primary"):
            if not simulado and not confirmado:
                st.error("Confirme os destinatários antes de habilitar envios reais.")
            else:
                servico.save_reports_config(enabled=habilitado, dry_run=simulado, recipients=selecionados,
                                            blocks=blocos, send_day=int(dia))
                st.success("Configuração salva. O agendador externo executa a rotina mensal.")
    st.caption("O agendador considera o calendário de America/Fortaleza. Provedor e remetente são configurações externas.")
    if st.button("Consultar histórico de envios"):
        _tabela(pd.DataFrame(servico.report_runs()), key="admin_historico_envios", paginar=False)


def _auditoria(servico: Any, contexto: Any) -> None:
    """Apresenta os eventos autorizados em tabela delimitada por página."""
    page_header("Auditoria", "Acompanhe alterações e seus responsáveis.")
    with st.spinner("Consultando a trilha de auditoria…"):
        eventos = pd.DataFrame(servico.admin_audit())
    if eventos.empty:
        st.info("Ainda não há eventos disponíveis para este acesso.")
    _tabela(eventos, key="auditoria_eventos")


PAGES = {
    "Visão Geral": ("dashboard.view", _painel),
    "Estoque": ("inventory.view", _estoque),
    "Reposição Inteligente": ("replenishment.view", _reposicao),
    "Relatórios": ("reports.view", _relatorios),
}
