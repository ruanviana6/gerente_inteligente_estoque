"""Motor de cálculo puro: sem interface, banco de dados ou chamadas ao ERP.

Premissas: vendas diárias em unidades, datas sem horário e estoque físico atual.
Dias de ruptura *comprovada* são excluídos da média; dias sem snapshot continuam
elegíveis, mas a previsão será sinalizada como estimativa sem disponibilidade.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Integral, Real
from typing import Iterable

import pandas as pd

from config import NIVEIS_ALERTA, Configuracao
from dados import hoje_local


@dataclass(frozen=True)
class Reposicao:
    """Resultado auditável de uma decisão de compra sugerida."""

    ponto_pedido: int
    posicao_estoque: int
    estoque_alvo: int
    quantidade: int
    risco_antes_entrega: bool


def _teto_unidades(valor: float) -> int:
    """Aplica o teto sem apagar demandas pequenas por uma tolerância absoluta.

    Uma multiplicação como (29 / 7) * 7 pode exceder 29 apenas pelo erro de
    representação binária. Ajustamos até duas unidades de precisão perto de
    um inteiro positivo; valores positivos próximos de zero continuam em 1.
    """
    inteiro = round(valor)
    if inteiro > 0 and abs(valor - inteiro) <= 2 * math.ulp(valor):
        return inteiro
    return math.ceil(valor)


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
    if any(not isinstance(n, Integral) or isinstance(n, bool) or n < 0 for n in valores):
        raise ValueError("Estoque e parâmetros devem ser inteiros não negativos.")
    if (isinstance(media_diaria, bool) or not isinstance(media_diaria, Real)
            or not math.isfinite(media_diaria) or media_diaria < 0):
        raise ValueError("Demanda diária deve ser finita e não negativa.")
    if cobertura_alvo <= 0:
        raise ValueError("Cobertura-alvo deve ser positiva.")

    consumo_prazo = media_diaria * int(prazo_dias) + int(seguranca)
    consumo_alvo = media_diaria * int(max(prazo_dias, cobertura_alvo)) + int(seguranca)
    if not math.isfinite(consumo_prazo) or not math.isfinite(consumo_alvo):
        raise ValueError("A demanda excede o limite de cálculo suportado.")
    ponto = _teto_unidades(consumo_prazo)
    alvo = _teto_unidades(consumo_alvo)
    posicao = int(estoque) + int(pendentes) - int(reservas)
    sugestao = max(0, alvo - posicao)
    # Reservas afetam disponibilidade imediata, mas pedidos pendentes não são
    # considerados entregues até existir confirmação de recebimento.
    risco = media_diaria > 0 and (int(estoque) - int(reservas)) <= media_diaria * int(prazo_dias)
    return Reposicao(ponto, posicao, alvo, sugestao, risco)


def _normalizar_dia(data: object) -> pd.Timestamp:
    """Usa o dia civil de Fortaleza, sem fuso nas séries de dias do domínio."""
    dia = pd.Timestamp(data)
    if pd.isna(dia):
        raise ValueError("A data de referência precisa ser válida.")
    if dia.tzinfo is not None:
        dia = dia.tz_convert("America/Fortaleza").tz_localize(None)
    return dia.normalize()


def medias_por_produto(
    vendas: pd.DataFrame, snapshots: pd.DataFrame, codigos: list[str],
    hoje: pd.Timestamp, janela: int,
) -> pd.DataFrame:
    """Calcula média por dia elegível e conta dias comprovadamente indisponíveis.

    A base diária é completada com zeros, pois um dia sem venda com produto
    disponível deve contar na média. Saldo zero sem venda positiva no mesmo
    dia indica ruptura segundo o contrato atual. Se houver venda, preservamos
    a observação e sinalizamos o conflito; uma foto não descreve todas as horas
    do dia. Sem snapshot o dia permanece elegível (dado desconhecido).
    """
    if isinstance(janela, bool) or not isinstance(janela, Integral) or janela <= 0:
        raise ValueError("A janela de análise deve ser inteira e positiva.")
    if len(codigos) != len(set(codigos)):
        raise ValueError("Códigos de produto duplicados na análise.")
    hoje = _normalizar_dia(hoje)
    inicio = hoje - pd.Timedelta(days=janela - 1)
    dias = pd.date_range(inicio, hoje, freq="D")
    vend = vendas.loc[vendas["data"].between(inicio, hoje)]
    if not vend.empty:
        soma = vend.groupby(["codigo", "data"])["quantidade"].sum()
    else:
        soma = pd.Series(dtype="float64")
    snap = snapshots.loc[snapshots["data"].between(inicio, hoje)]
    if not snap.empty:
        if snap.duplicated(["codigo", "data"]).any():
            raise ValueError("Snapshots repetidos para SKU e dia.")
        saldo = snap.set_index(["codigo", "data"])["estoque"]
    else:
        saldo = pd.Series(dtype="float64")

    linhas = []
    for codigo in codigos:
        indice = pd.MultiIndex.from_product([[codigo], dias], names=["codigo", "data"])
        vendas_diarias = soma.reindex(indice, fill_value=0)
        estoques = saldo.reindex(indice)
        conflito = estoques.eq(0) & vendas_diarias.gt(0)
        ruptura = estoques.eq(0) & ~conflito
        elegiveis = ~ruptura
        dias_validos = int(elegiveis.sum())
        total = float(vendas_diarias[elegiveis].sum())
        linhas.append({
            "codigo": codigo,
            "media": total / dias_validos if dias_validos else 0.0,
            "dias_ruptura_conhecidos": int(ruptura.sum()),
            "dias_snapshots_conflitantes": int(conflito.sum()),
            "dias_disponibilidade_desconhecida": int(estoques.isna().sum()),
            "dias_elegiveis": dias_validos,
        })
    return pd.DataFrame(linhas, columns=[
        "codigo", "media", "dias_ruptura_conhecidos", "dias_snapshots_conflitantes",
        "dias_disponibilidade_desconhecida", "dias_elegiveis",
    ]).set_index("codigo")


def classificar_tendencia(curta: float, longa: float, limite_pct: float) -> str:
    """Compara média recente com a média longa (que inclui dias recentes)."""
    if any(isinstance(valor, bool) or not isinstance(valor, Real)
           or not math.isfinite(valor) or valor < 0
           for valor in (curta, longa, limite_pct)):
        raise ValueError("Médias e limite devem ser finitos e não negativos.")
    if longa <= 0:
        return "Sem referência"
    variacao = (curta - longa) / longa * 100
    if variacao == 0:
        return "➡ Estável"
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


def somar_valores_financeiros(valores: Iterable[float]) -> float:
    """Soma custos conhecidos sem permitir infinito como indicador monetário.

    Custos ausentes continuam excluídos de um total explicitamente parcial.
    Se a soma exceder a representação numérica, não há valor apresentável.
    """
    conhecidos = [float(valor) for valor in valores if pd.notna(valor)]
    if not conhecidos or not all(math.isfinite(valor) for valor in conhecidos):
        return float("nan")
    try:
        total = math.fsum(conhecidos)
    except OverflowError:
        return float("nan")
    return total if math.isfinite(total) else float("nan")


def _calcular_abc(
    vendas: pd.DataFrame, produtos: pd.DataFrame, hoje: pd.Timestamp,
) -> tuple[pd.Series, bool]:
    """Calcula as classes e informa se houve estouro de representação financeira.

    Usamos custo unitário como aproximação financeira, não faturamento; produtos
    sem custo permanecem 'Não calculável', sem inventar valores monetários.
    Um consumo ou total não finito invalida a participação de todos os SKUs,
    pois retirar silenciosamente esse item mudaria o denominador da Curva ABC.
    """
    hoje = _normalizar_dia(hoje)
    inicio = hoje - pd.Timedelta(days=89)
    unidades = vendas.loc[vendas["data"].between(inicio, hoje)].groupby("codigo")["quantidade"].sum()
    tabela = produtos.set_index("codigo")
    validos = tabela["custo_unitario"].notna()
    if not tabela.loc[validos, "custo_unitario"].map(
        lambda custo: isinstance(custo, Real) and math.isfinite(custo) and custo >= 0
    ).all():
        raise ValueError("Custos da Curva ABC devem ser finitos e não negativos.")
    valores = pd.Series([
        float(quantidade) * float(custo) if pd.notna(custo) else float("nan")
        for quantidade, custo in zip(
            unidades.reindex(tabela.index, fill_value=0), tabela["custo_unitario"],
        )
    ], index=tabela.index, dtype=float)
    classes = pd.Series("Não calculável", index=tabela.index, dtype="object")
    soma = somar_valores_financeiros(valores[validos])
    excedeu_limite = validos.any() and not math.isfinite(soma)
    if excedeu_limite or not validos.any():
        return classes, bool(excedeu_limite)
    if soma <= 0:
        return classes, False
    # O SKU desempata valores iguais para a classificação não mudar só porque
    # o catálogo foi importado em outra ordem.
    ordenados = valores[validos].sort_index().sort_values(ascending=False, kind="stable")
    acumulado_antes = ordenados.cumsum().shift(fill_value=0) / soma
    classes.loc[ordenados.index] = [
        "A" if p < .80 else "B" if p < .95 else "C"
        for p in acumulado_antes
    ]
    return classes, False


def classificar_abc(vendas: pd.DataFrame, produtos: pd.DataFrame, hoje: pd.Timestamp) -> pd.Series:
    """Classifica consumo a custo em 90 dias, sem classes arbitrárias após overflow."""
    return _calcular_abc(vendas, produtos, hoje)[0]


def _aviso_qualidade(linha: object) -> str:
    """Explica o que a base permite concluir e o que exige conferência humana."""
    avisos = []
    if not linha.demanda_estimavel:
        avisos.append("Sem dias elegíveis: demanda não estimável; sugestão exige revisão manual.")
    if linha.sem_historico_vendas:
        avisos.append("Sem venda positiva registrada; isso não comprova produto parado.")
    elif linha.historico_curto:
        avisos.append("Primeira venda recente: confirme se o arquivo cobre toda a janela histórica.")
    if linha.dias_disponibilidade_desconhecida:
        avisos.append("Há dias sem snapshot; disponibilidade desconhecida.")
    if linha.dias_snapshots_conflitantes:
        avisos.append("Saldo zero com venda no mesmo dia: venda preservada, reconcilie o snapshot.")
    if linha.financeiro_limite_excedido:
        avisos.append("Limite numérico financeiro excedido; valores afetados não calculáveis. Revise os custos.")
    if pd.isna(linha.custo_unitario):
        avisos.append("Custo ausente; capital e ABC do produto não calculáveis.")
    elif linha.abc_parcial:
        avisos.append("ABC relativa apenas aos custos conhecidos do catálogo.")
    return " ".join(avisos)


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
    atual = _normalizar_dia(hoje if hoje is not None else hoje_local())
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
    painel["dias_elegiveis"] = curta["dias_elegiveis"]
    painel["dias_snapshots_conflitantes"] = curta["dias_snapshots_conflitantes"]
    painel["demanda_estimavel"] = painel["dias_elegiveis"].gt(0)
    # Cobertura é indefinida quando não há procura observada.
    painel["cobertura_dias"] = [
        e / v if v > 0 else float("nan")
        for e, v in zip(painel["estoque"], painel["venda_media_diaria"])
    ]
    vendas_positivas = vendas.loc[(vendas["data"] <= atual) & vendas["quantidade"].gt(0)]
    ultima = pd.to_datetime(vendas_positivas.groupby("codigo")["data"].max().reindex(painel.index))
    painel["dias_sem_venda"] = (atual - ultima).dt.days
    painel["tendencia"] = [
        classificar_tendencia(media_curta, media_longa, cfg.limite_tendencia_pct)
        for media_curta, media_longa in zip(painel["venda_media_diaria"], painel["media_longa"])
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
    # Zero dias elegíveis é ausência de estimativa, não prova de demanda zero.
    # Mantemos a média numérica em zero por compatibilidade com as fórmulas,
    # mas impedimos um semáforo de normalidade ou uma tendência inventada.
    sem_estimativa = ~painel["demanda_estimavel"]
    painel.loc[sem_estimativa, ["status", "icone", "prioridade"]] = ["Dados insuficientes", "⚪", 7]
    painel.loc[sem_estimativa, "tendencia"] = "Sem referência"
    calculos = [
        calcular_reposicao(
            int(linha.estoque), float(linha.venda_media_diaria),
            int(linha.prazo_entrega_dias), int(linha.estoque_seguranca),
            int(linha.pedidos_pendentes), int(linha.reservas), int(linha.cobertura_alvo_dias),
        )
        for linha in painel.itertuples(index=False)
    ]
    painel["ponto_pedido"] = [r.ponto_pedido for r in calculos]
    painel["posicao_estoque"] = [r.posicao_estoque for r in calculos]
    painel["estoque_alvo"] = [r.estoque_alvo for r in calculos]
    painel["quantidade_sugerida"] = [r.quantidade for r in calculos]
    painel["risco_prazo"] = [r.risco_antes_entrega for r in calculos]
    capital_bruto = pd.Series([
        float(estoque) * float(custo) if pd.notna(custo) else float("nan")
        for estoque, custo in zip(painel["estoque"], painel["custo_unitario"])
    ], index=painel.index, dtype=float)
    capital_finito = capital_bruto.map(math.isfinite)
    capital_excedido = capital_bruto.notna() & ~capital_finito
    painel["capital_estoque"] = capital_bruto.where(capital_finito)
    total_capital = somar_valores_financeiros(painel["capital_estoque"])
    total_excedido = painel["capital_estoque"].notna().any() and not math.isfinite(total_capital)
    painel["classe_abc"], abc_excedido = _calcular_abc(vendas, produtos, atual)
    painel["financeiro_limite_excedido"] = bool(capital_excedido.any() or total_excedido or abc_excedido)
    # Uma primeira venda recente não prova o início de comercialização. É um
    # aviso para verificar a abrangência do CSV; não encurta o denominador.
    primeira = pd.to_datetime(vendas_positivas.groupby("codigo")["data"].min().reindex(painel.index))
    inicio_longo = atual - pd.Timedelta(days=cfg.janela_tendencia - 1)
    painel["historico_curto"] = primeira.notna() & primeira.gt(inicio_longo)
    painel["sem_historico_vendas"] = ultima.isna()
    painel["abc_parcial"] = produtos["custo_unitario"].isna().any()
    painel["requer_revisao"] = (
        sem_estimativa | painel["historico_curto"] | painel["sem_historico_vendas"]
        | painel["dias_disponibilidade_desconhecida"].gt(0)
        | painel["dias_snapshots_conflitantes"].gt(0)
        | painel["financeiro_limite_excedido"]
    )
    painel["aviso_qualidade"] = [_aviso_qualidade(linha) for linha in painel.itertuples(index=False)]
    # Apresentar risco de entrega primeiro, sem adulterar o status por cobertura.
    return painel.sort_values(
        ["risco_prazo", "prioridade", "cobertura_dias", "nome"],
        ascending=[False, True, True, True],
        na_position="last",
    ).reset_index(drop=True)

