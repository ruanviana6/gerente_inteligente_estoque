"""Contratos explícitos para painéis e CSVs, sem liberação implícita de indicadores."""
from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

COLUNAS_REPOSICAO = (
    "codigo", "nome", "fornecedor", "estoque", "prazo_entrega_dias", "estoque_seguranca",
    "pedidos_pendentes", "reservas", "cobertura_alvo_dias", "venda_media_diaria",
    "cobertura_dias", "dias_ruptura_conhecidos", "dias_disponibilidade_desconhecida",
    "dias_elegiveis", "dias_snapshots_conflitantes", "demanda_estimavel", "dias_sem_venda",
    "status", "icone", "prioridade", "ponto_pedido", "posicao_estoque", "estoque_alvo",
    "quantidade_sugerida", "risco_prazo", "historico_curto", "sem_historico_vendas",
    "requer_revisao", "aviso_qualidade",
)
COLUNAS_TENDENCIAS = ("media_longa", "tendencia")
COLUNAS_FINANCEIRAS = ("custo_unitario", "capital_estoque", "financeiro_limite_excedido")
COLUNAS_ABC = ("classe_abc", "abc_parcial")


def _qualidade_operacional(linha: object, financeiro: bool, tendencias: bool) -> str:
    """Reconstrói avisos por flags permitidas, sem reaproveitar texto restrito do motor."""
    avisos = []
    if not getattr(linha, "demanda_estimavel", True):
        avisos.append("Sem dias elegíveis: demanda não estimável; sugestão exige revisão manual.")
    if getattr(linha, "sem_historico_vendas", False):
        avisos.append("Sem venda positiva registrada; isso não comprova produto parado.")
    elif getattr(linha, "historico_curto", False):
        avisos.append("Primeira venda recente: confirme se o arquivo cobre toda a janela histórica.")
    if getattr(linha, "dias_disponibilidade_desconhecida", 0):
        avisos.append("Há dias sem snapshot; disponibilidade desconhecida.")
    if getattr(linha, "dias_snapshots_conflitantes", 0):
        avisos.append("Saldo zero com venda no mesmo dia: venda preservada, reconcilie o snapshot.")
    if financeiro:
        if getattr(linha, "financeiro_limite_excedido", False):
            avisos.append("Limite numérico financeiro excedido; valores afetados não calculáveis. Revise os custos.")
        if pd.isna(getattr(linha, "custo_unitario", None)):
            avisos.append("Custo ausente; capital não calculável.")
        if tendencias and getattr(linha, "abc_parcial", False):
            avisos.append("ABC relativa apenas aos custos conhecidos do catálogo.")
    return " ".join(avisos)


def _colunas_permitidas(financeiro: bool, tendencias: bool) -> list[str]:
    """Define o contrato comum do painel sem inferir acesso pelo nome da coluna."""
    colunas = list(COLUNAS_REPOSICAO)
    if tendencias:
        colunas.extend(COLUNAS_TENDENCIAS)
    if financeiro:
        colunas.extend(COLUNAS_FINANCEIRAS)
    if financeiro and tendencias:
        colunas.extend(COLUNAS_ABC)
    return colunas


def filtrar_painel(tabela: pd.DataFrame, financeiro: bool, tendencias: bool = True) -> pd.DataFrame:
    """Reduz campos e avisos à autorização final sem exigir permissão de exportar.

    Reconstrói a qualidade de dados a partir de flags explícitas para não manter
    referências financeiras ou ABC em mensagens preparadas antes de uma revogação.
    Novos indicadores não entram automaticamente na apresentação.
    """
    colunas = [coluna for coluna in _colunas_permitidas(financeiro, tendencias) if coluna in tabela]
    segura = tabela.loc[:, colunas].copy(deep=True)
    if segura.empty:
        return segura
    segura["aviso_qualidade"] = [
        _qualidade_operacional(linha, financeiro, tendencias) for linha in tabela.itertuples(index=False)
    ]
    revisao = (~tabela.get("demanda_estimavel", pd.Series(True, index=tabela.index)).fillna(True).astype(bool))
    for campo in ("historico_curto", "sem_historico_vendas", "dias_disponibilidade_desconhecida",
                  "dias_snapshots_conflitantes"):
        if campo in tabela:
            revisao |= tabela[campo].fillna(False).astype(bool)
    if financeiro and "financeiro_limite_excedido" in tabela:
        revisao |= tabela["financeiro_limite_excedido"].fillna(False).astype(bool)
    segura["requer_revisao"] = revisao
    return segura


def filtrar_reposicao(tabela: pd.DataFrame, permissoes: Iterable[str]) -> pd.DataFrame:
    """Seleciona colunas autorizadas e conserva cabeçalhos mesmo sem produtos.

    ABC exige simultaneamente tendências e finanças. A lista vazia conserva o
    contrato dos cabeçalhos, sem inserir indicadores fictícios na interface.
    """
    acessos = set(permissoes)
    if not {"replenishment.view", "replenishment.export"}.issubset(acessos):
        raise PermissionError("Você não tem permissão para exportar a reposição.")
    financeiro, tendencias = "finance.view" in acessos, "trends.view" in acessos
    return filtrar_painel(tabela, financeiro, tendencias).reindex(
        columns=_colunas_permitidas(financeiro, tendencias),
    )
