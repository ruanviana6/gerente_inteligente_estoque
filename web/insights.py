"""Insights determinísticos a partir de indicadores permitidos ao usuário."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from motor import somar_valores_financeiros


@dataclass(frozen=True)
class Insight:
    """Mensagem curta e memória de cálculo para o flash do dashboard."""

    titulo: str
    texto: str
    explicacao: str
    prioridade: int = 5


def gerar_insights(painel: pd.DataFrame, permissoes: Iterable[str]) -> list[Insight]:
    """Produz avisos reproduzíveis sem acessar rede, cache ou dados de outra sessão."""
    acessos = set(permissoes)
    if "dashboard.view" not in acessos:
        return []
    if painel.empty:
        return [Insight(
            "Ainda não há dados", "Importe um catálogo e histórico completos para começar.",
            "Nenhum indicador foi calculado sem uma fonte de dados.", 9,
        )]
    insights = []
    riscos = int(painel["risco_prazo"].sum())
    if riscos:
        insights.append(Insight(
            "Risco antes da entrega", f"{riscos} produto(s) podem faltar durante o prazo do fornecedor.",
            "Saldo menos reservas comparado à demanda diária multiplicada pelo prazo; "
            "compras pendentes ainda não contam como entregues.", 0,
        ))
    incertos = int((~painel["demanda_estimavel"]).sum())
    if incertos:
        insights.append(Insight(
            "Demanda não estimável", f"{incertos} produto(s) não têm dias elegíveis na janela.",
            "Ruptura comprovada é excluída da média; sem denominador não há previsão confiável.", 1,
        ))
    if "replenishment.view" in acessos:
        sugeridos = painel.loc[painel["quantidade_sugerida"].gt(0)]
        if not sugeridos.empty:
            insights.append(Insight(
                "Revisar reposição", f"{len(sugeridos)} produto(s) têm quantidade sugerida para conferência.",
                "Sugestão = máximo entre zero e estoque-alvo menos posição. "
                "A posição inclui pendentes e desconta reservas; a decisão de compra é humana.", 2,
            ))
    if "inventory.view" in acessos:
        parados = int(painel["status"].eq("Parado").sum())
        excessos = int(painel["status"].eq("Excesso").sum())
        if parados or excessos:
            insights.append(Insight(
                "Estoque sem giro ou em excesso", f"{parados} parado(s) e {excessos} em excesso.",
                "Parado exige venda anterior registrada; excesso depende da cobertura e da demanda observada.", 3,
            ))
    if "trends.view" in acessos:
        crescendo = int(painel["tendencia"].eq("⬆ Crescendo").sum())
        if crescendo:
            insights.append(Insight(
                "Demanda recente em alta", f"{crescendo} produto(s) apresentam média recente maior.",
                "O motor compara médias de 30 e 90 dias; isso não comprova sazonalidade.", 4,
            ))
    if "finance.view" in acessos:
        total = somar_valores_financeiros(painel["capital_estoque"])
        if math.isfinite(total):
            insights.append(Insight(
                "Capital em estoque", f"R$ {total:,.2f} a custo informado.",
                "Soma de saldo × custo unitário conhecido. Custos ausentes tornam o total parcial; "
                "o indicador não representa receita.", 5,
            ))
    if painel["dias_disponibilidade_desconhecida"].gt(0).any():
        insights.append(Insight(
            "Conferir qualidade do histórico", "Há dias sem fotografia do estoque.",
            "Sem snapshot, a disponibilidade é desconhecida. O sistema não inventa vendas perdidas.", 6,
        ))
    if not insights:
        insights.append(Insight(
            "Indicadores disponíveis", f"{len(painel)} produto(s) analisados.",
            "Os resultados usam as janelas e parâmetros do motor; reveja a qualidade antes de decidir.", 8,
        ))
    return sorted(insights, key=lambda insight: insight.prioridade)
