"""Transforma dados já autorizados em projeções para revisão humana."""

from datetime import date

import pandas as pd

from reposicao.planejamento import RegraProduto, simular_cenario


def projetar_painel(painel: pd.DataFrame, regras: dict, referencia: date, ciclo: int,
                    espera: int = 0, aceitar_incerteza: bool = False) -> pd.DataFrame:
    """Calcula por SKU, sem pendências sem data nem suposições de embalagem."""
    if type(referencia) is not date or type(aceitar_incerteza) is not bool:
        raise ValueError("Referência ou confirmação inválida.")
    versoes = {r["sku"]: r for r in regras.get("rules", [])}
    linhas = []
    for item in painel.to_dict("records"):
        regra = versoes.get(item["codigo"], {})
        parametros = RegraProduto(
            prazo_dias=int(item["prazo_entrega_dias"]), seguranca=int(item["estoque_seguranca"]),
            unidades_por_caixa=regra.get("units_per_box"), lote_minimo=regra.get("minimum_lot", 0),
            multiplo=regra.get("production_multiple", 1), maximo=regra.get("maximum_units"),
        )
        suficiente = bool(item.get("demanda_estimavel", False)) and not bool(item.get("sem_historico_vendas", True))
        if item.get("requer_revisao", False) and not aceitar_incerteza:
            suficiente = False
        resultado = simular_cenario(referencia=referencia, estoque=int(item["estoque"]),
            reservas=int(item["reservas"]), media_diaria=item.get("venda_media_diaria"),
            demanda_confiavel=suficiente, regra=parametros, ciclo_dias=ciclo, esperar_dias=espera)
        linha = {"codigo": item["codigo"], "nome": item["nome"], "estoque": item["estoque"],
                 "status": item["status"], "prioridade": item.get("prioridade", 99),
                 "previsao_30": float(resultado.demanda_30_dias) if resultado.demanda_30_dias is not None else None,
                 "caixas": resultado.caixas, "unidades": resultado.unidades,
                 "necessidade_liquida": resultado.necessidade_liquida,
                 "solicitar_ate": resultado.data_limite.isoformat() if resultado.data_limite else None,
                 "chegada": resultado.data_chegada.isoformat(), "ruptura": resultado.primeira_ruptura.isoformat() if resultado.primeira_ruptura else None,
                 "risco": resultado.risco_antes_chegada, "observacao": resultado.observacao,
                 "embalagem_id": regra.get("id"), "unidades_por_caixa": regra.get("units_per_box")}
        if "tendencia" in item:
            linha["tendencia"] = item["tendencia"]
        linhas.append(linha)
    colunas = ["codigo", "nome", "estoque", "status", "prioridade", "previsao_30", "caixas", "unidades",
               "necessidade_liquida", "solicitar_ate", "chegada", "ruptura", "risco", "observacao",
               "embalagem_id", "unidades_por_caixa"]
    if not linhas:
        return pd.DataFrame(columns=colunas)
    return pd.DataFrame(linhas).sort_values(["risco", "prioridade", "nome"], ascending=[False, True, True])
