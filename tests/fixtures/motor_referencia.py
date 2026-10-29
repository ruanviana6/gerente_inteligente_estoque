"""Oráculo congelado da base publicada 5f485638 para regressão de desempenho.

Esta cópia pequena preserva o cálculo anterior por SKU e dia, exclusivamente
nos testes diferenciais. O aplicativo importa somente o motor de produção.
"""
from numbers import Integral

import pandas as pd

from motor import _normalizar_dia


def medias_referencia(
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
