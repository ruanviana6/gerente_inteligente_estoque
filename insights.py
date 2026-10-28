"""Gera destaques com base nos indicadores já calculados pelo motor.

Não utiliza IA generativa: todos os números vêm de linhas do painel.
"""
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Insight:
    """Dados exibidos no flash inicial, incluindo a justificativa."""

    icone: str
    titulo: str
    resumo: str
    motivo: str


def gerar_insights(painel: pd.DataFrame) -> list[Insight]:
    """Mostra riscos, sugestões, produtos parados e tendências, nessa ordem.

    Não cria compras nem altera qualquer informação. Produtos e números
    são extraídos da análise fornecida pelo motor.py.
    """
    if painel.empty:
        return [Insight("📦", "Nenhum produto para analisar",
                        "Ainda não há dados para mostrar destaques.",
                        "Carregue um catálogo para iniciar.")]

    destaques = []
    riscos = painel.loc[painel["risco_prazo"]]
    if not riscos.empty:
        produto = riscos.iloc[0]
        cobertura = produto["cobertura_dias"]
        texto_cobertura = f"{cobertura:.1f}" if pd.notna(cobertura) else "não disponível"
        destaques.append(Insight(
            "🔴", "Risco antes da entrega",
            f"{produto['nome']} pode faltar antes da próxima entrega.",
            f"Cobertura: {texto_cobertura} dias. "
            f"Prazo do fornecedor: {int(produto['prazo_entrega_dias'])} dias.",
        ))

    compras = painel.loc[painel["quantidade_sugerida"] > 0]
    if not compras.empty:
        produto = compras.iloc[0]
        destaques.append(Insight(
            "🛒", "Sugestão de compra",
            f"Avalie pedir {int(produto['quantidade_sugerida'])} unidades de {produto['nome']}.",
            f"Estoque-alvo: {int(produto['estoque_alvo'])} unidades. "
            f"Posição atual: {int(produto['posicao_estoque'])}. "
            "A compra não é realizada automaticamente.",
        ))

    lentos = painel.loc[painel["status"].isin(["Parado", "Excesso"])]
    if not lentos.empty:
        destaques.append(Insight(
            "📦", "Estoque parado ou em excesso",
            f"{len(lentos)} produto(s) merecem revisão de giro ou cobertura.",
            "Confira o histórico e a necessidade de reposição antes de agir.",
        ))

    crescimento = painel.loc[painel["tendencia"].eq("⬆ Crescendo")]
    if not crescimento.empty:
        produto = crescimento.iloc[0]
        destaques.append(Insight(
            "📈", "Vendas em crescimento",
            f"{produto['nome']} teve uma média recente de vendas maior.",
            f"Últimos 30 dias: {produto['venda_media_diaria']:.2f} un./dia. "
            f"Últimos 90 dias: {produto['media_longa']:.2f} un./dia.",
        ))

    if not destaques:
        destaques.append(Insight(
            "🟢", "Sem destaques prioritários",
            "Nenhum alerta foi selecionado pelas regras atuais.",
            "Isso não substitui a conferência física do estoque.",
        ))
    return destaques
