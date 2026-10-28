"""Motor de cálculo puro: sem interface, banco de dados ou chamadas ao ERP.

Premissas: vendas diárias em unidades, datas sem horário e estoque físico atual.
Dias de ruptura *comprovada* são excluídos da média; dias sem snapshot continuam
elegíveis, mas a previsão será sinalizada como estimativa sem disponibilidade.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd

from config import Configuracao, NIVEIS_ALERTA


@dataclass(frozen=True)
class Reposicao:
    """Resultado auditável de uma decisão de compra sugerida."""

    ponto_pedido: int
    posicao_estoque: int
    estoque_alvo: int
    quantidade: int
    risco_antes_entrega: bool


def calcular_reposicao(
    estoque: int, media_diaria: float, prazo_dias: int,
    seguranca: int, pendentes: int = 0, reservas: int = 0,
    cobertura_alvo: int = 30,
) -> Reposicao:
    """Recomenda até a cobertura-alvo, respeitando o prazo e o estoque de segurança.

    1) Ponto de pedido = consumo durante entrega + segurança.
    2) Posição = saldo físico + encomendas pendentes - reservas.
    3) Estoque-alvo = consumo do maior entre prazo e cobertura-alvo + segurança.
    4) Sugestão = máximo(0, alvo - posição), arredondado para unidades inteiras.
    O risco por prazo é conservador: não conhece a data de chegada dos pendentes.
    """
    valores = (estoque, prazo_dias, seguranca, pendentes, reservas, cobertura_alvo)
    if any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in valores):
        raise ValueError("Estoque e parâmetros devem ser inteiros não negativos.")
    if not math.isfinite(media_diaria) or media_diaria < 0:
        raise ValueError("Demanda diária deve ser finita e não negativa.")
    if cobertura_alvo <= 0:
        raise ValueError("Cobertura-alvo deve ser positiva.")

    ponto = math.ceil(media_diaria * prazo_dias + seguranca - 1e-9)
    alvo = math.ceil(media_diaria * max(prazo_dias, cobertura_alvo) + seguranca - 1e-9)
    posicao = estoque + pendentes - reservas
    sugestao = max(0, alvo - posicao)
    # Reservas afetam disponibilidade imediata, mas pedidos pendentes não são
    # considerados entregues até existir confirmação de recebimento.
    risco = media_diaria > 0 and (estoque - reservas) <= media_diaria * prazo_dias
    return Reposicao(ponto, posicao, alvo, sugestao, risco)


def _normalizar_dia(data: object) -> pd.Timestamp:
    """Remove o horário para que as janelas usem dias civis completos."""
    return pd.Timestamp(data).normalize()


def medias_por_produto(
    vendas: pd.DataFrame, snapshots: pd.DataFrame, codigos: list[str],
    hoje: pd.Timestamp, janela: int,
) -> pd.DataFrame:
    """Calcula média por dia elegível e conta dias comprovadamente indisponíveis.

    A base diária é completada com zeros, pois um dia sem venda com produto
    disponível deve contar na média. Só snapshots com saldo zero comprovam
    ruptura. Sem snapshot o dia permanece elegível (dado desconhecido).
    """
    if janela <= 0:
        raise ValueError("A janela de análise deve ser positiva.")
    inicio = hoje - pd.Timedelta(days=janela - 1)
    dias = pd.date_range(inicio, hoje, freq="D")
    vend = vendas.loc[vendas["data"].between(inicio, hoje)]
    if not vend.empty:
        soma = vend.groupby(["codigo", "data"])["quantidade"].sum()
    else:
        soma = pd.Series(dtype="float64")
    snap = snapshots.loc[snapshots["data"].between(inicio, hoje)]
    if not snap.empty:
        snap = snap.drop_duplicates(["codigo", "data"], keep="last")
        saldo = snap.set_index(["codigo", "data"])["estoque"]
    else:
        saldo = pd.Series(dtype="float64")

    linhas = []
    for codigo in codigos:
        indice = pd.MultiIndex.from_product([[codigo], dias], names=["codigo", "data"])
        vendas_diarias = soma.reindex(indice, fill_value=0)
        estoques = saldo.reindex(indice)
        ruptura = estoques.eq(0)
        elegiveis = ~ruptura
        dias_validos = int(elegiveis.sum())
        total = float(vendas_diarias[elegiveis].sum())
        linhas.append({
            "codigo": codigo,
            "media": total / dias_validos if dias_validos else 0.0,
            "dias_ruptura_conhecidos": int(ruptura.sum()),
            "dias_disponibilidade_desconhecida": int(estoques.isna().sum()),
            "dias_elegiveis": dias_validos,
        })
    return pd.DataFrame(linhas).set_index("codigo")


def classificar_tendencia(curta: float, longa: float, limite_pct: float) -> str:
    """Compara média recente com a média longa (que inclui dias recentes)."""
    if longa <= 0:
        return "Sem referência"
    variacao = (curta - longa) / longa * 100
    if variacao >= limite_pct:
        return "⬆ Crescendo"
    if variacao <= -limite_pct:
        return "⬇ Caindo"
    return "➡ Estável"


def classificar_status(
    estoque: int, media: float, cobertura: float,
    dias_sem_venda: float, config: Configuracao,
) -> tuple[str, str, int]:
    """Aplica o semáforo da cobertura; risco do prazo é indicador separado.

    Parado exige histórico real de última venda; sem histórico fica distinto
    de produto sem movimento comprovado. NaN indica cobertura não estimável.
    """
    if estoque == 0 and media > 0:
        return "Ruptura", "🔴", 0
    if estoque > 0 and pd.notna(dias_sem_venda) and dias_sem_venda >= config.dias_parado:
        return "Parado", "⚪", 6
    if media == 0:
        return "Sem demanda recente", "⚪", 7
    for prioridade, (nome, limite, icone) in enumerate(NIVEIS_ALERTA, start=1):
        if cobertura <= limite:
            return nome, icone, prioridade
    if cobertura > config.excesso_dias:
        return "Excesso", "🔵", 5
    return "OK", "🟢", 4


def classificar_abc(vendas: pd.DataFrame, produtos: pd.DataFrame, hoje: pd.Timestamp) -> pd.Series:
    """Classifica por participação no valor estimado vendido nos últimos 90 dias.

    Usamos custo unitário como aproximação financeira, não faturamento; produtos
    sem custo permanecem 'Não calculável', sem inventar valores monetários.
    """
    inicio = hoje - pd.Timedelta(days=89)
    unidades = vendas.loc[vendas["data"].between(inicio, hoje)].groupby("codigo")["quantidade"].sum()
    tabela = produtos.set_index("codigo")
    validos = tabela["custo_unitario"].notna()
    valores = unidades.reindex(tabela.index, fill_value=0) * tabela["custo_unitario"]
    classes = pd.Series("Não calculável", index=tabela.index, dtype="object")
    soma = valores[validos].sum()
    if soma <= 0:
        return classes
    ordenados = valores[validos].sort_values(ascending=False)
    acumulado_antes = ordenados.cumsum().shift(fill_value=0) / soma
    classes.loc[ordenados.index] = [
        "A" if p < .80 else "B" if p < .95 else "C"
        for p in acumulado_antes
    ]
    return classes


def gerar_painel(
    produtos: pd.DataFrame, vendas: pd.DataFrame,
    snapshots: pd.DataFrame | None = None, hoje: object | None = None,
    config: Configuracao | None = None,
) -> pd.DataFrame:
    """Consolida indicadores sem alterar os DataFrames originais.

    Executa médias de 30/90 dias, tendência, cobertura, classificação de risco,
    reposição, posição de estoque, capital aproximado e Curva ABC.
    """
    cfg = config or Configuracao()
    atual = _normalizar_dia(hoje if hoje is not None else pd.Timestamp.today())
    snapshots = snapshots if snapshots is not None else pd.DataFrame(columns=["codigo", "data", "estoque"])
    painel = produtos.set_index("codigo", drop=False).copy()
    if painel.empty:
        return painel
    codigos = painel.index.tolist()
    curta = medias_por_produto(vendas, snapshots, codigos, atual, cfg.janela_venda)
    longa = medias_por_produto(vendas, snapshots, codigos, atual, cfg.janela_tendencia)
    painel["venda_media_diaria"] = curta["media"]
    painel["media_longa"] = longa["media"]
    painel["dias_ruptura_conhecidos"] = curta["dias_ruptura_conhecidos"]
    painel["dias_disponibilidade_desconhecida"] = curta["dias_disponibilidade_desconhecida"]
    # Cobertura é indefinida quando não há procura observada.
    painel["cobertura_dias"] = [
        e / v if v > 0 else float("nan")
        for e, v in zip(painel["estoque"], painel["venda_media_diaria"])
    ]
    ultima = vendas.loc[vendas["data"] <= atual].groupby("codigo")["data"].max()
    painel["dias_sem_venda"] = (atual - ultima.reindex(painel.index)).dt.days
    painel["tendencia"] = [
        classificar_tendencia(c, l, cfg.limite_tendencia_pct)
        for c, l in zip(painel["venda_media_diaria"], painel["media_longa"])
    ]
    status = [
        classificar_status(int(e), float(v), float(c), d, cfg)
        for e, v, c, d in zip(
            painel["estoque"], painel["venda_media_diaria"],
            painel["cobertura_dias"], painel["dias_sem_venda"]
        )
    ]
    painel["status"] = [s[0] for s in status]
    painel["icone"] = [s[1] for s in status]
    painel["prioridade"] = [s[2] for s in status]
    calculos = [
        calcular_reposicao(
            int(l.estoque), float(l.venda_media_diaria),
            int(l.prazo_entrega_dias), int(l.estoque_seguranca),
            int(l.pedidos_pendentes), int(l.reservas), int(l.cobertura_alvo_dias),
        )
        for l in painel.itertuples(index=False)
    ]
    painel["ponto_pedido"] = [r.ponto_pedido for r in calculos]
    painel["posicao_estoque"] = [r.posicao_estoque for r in calculos]
    painel["estoque_alvo"] = [r.estoque_alvo for r in calculos]
    painel["quantidade_sugerida"] = [r.quantidade for r in calculos]
    painel["risco_prazo"] = [r.risco_antes_entrega for r in calculos]
    painel["capital_estoque"] = painel["estoque"] * painel["custo_unitario"]
    painel["classe_abc"] = classificar_abc(vendas, produtos, atual)
    # Apresentar risco de entrega primeiro, sem adulterar o status por cobertura.
    return painel.sort_values(
        ["risco_prazo", "prioridade", "cobertura_dias", "nome"],
        ascending=[False, True, True, True],
        na_position="last",
    ).reset_index(drop=True)
