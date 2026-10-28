"""Relatórios mensais explicáveis, sem persistir conteúdo além da autorização."""
from __future__ import annotations

import calendar
import copy
import json
import math
from datetime import date, datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from dados import (
    COLUNAS_SNAPSHOTS,
    COLUNAS_VENDAS,
    exportar_csv_seguro,
    validar_produtos,
    validar_snapshots,
    validar_vendas,
)
from motor import gerar_painel, somar_valores_financeiros

FUSO = ZoneInfo("America/Fortaleza")
BLOCOS_PERMISSOES = {
    "estoque": "inventory.view",
    "reposicao": "replenishment.view",
    "tendencias": "trends.view",
    "financeiro": "finance.view",
}


def mes_anterior(agora: datetime | date | None = None) -> date:
    """Retorna o primeiro dia do mês anterior no calendário de Fortaleza."""
    atual = agora or datetime.now(FUSO)
    if isinstance(atual, datetime):
        atual = atual.replace(tzinfo=FUSO) if atual.tzinfo is None else atual.astimezone(FUSO)
        atual = atual.date()
    return (atual.replace(day=1) - timedelta(days=1)).replace(day=1)


def _dia(valor: object) -> date:
    """Normaliza datas civis sem aceitar horários como datas de fechamento."""
    if isinstance(valor, datetime):
        raise ValueError("Informe uma data civil, sem horário.")
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor))
    except ValueError as erro:
        raise ValueError("A data do relatório é inválida.") from erro


def _periodo(mes: date | str) -> tuple[date, date]:
    """Define os limites reais do mês, incluindo fevereiro e anos bissextos."""
    inicio = _dia(mes).replace(day=1)
    return inicio, inicio.replace(day=calendar.monthrange(inicio.year, inicio.month)[1])


def _exigir(permissoes: set[str], *nomes: str) -> None:
    """Nega a operação quando falta qualquer concessão necessária."""
    if not set(nomes).issubset(permissoes):
        raise PermissionError("Você não tem permissão para acessar este relatório.")


def _validar_fechamento(payload: dict, inicio: date, fim: date) -> dict:
    """Exige metadados verificáveis e saldo observado no fechamento do mês."""
    metadados = payload.get("metadata") or {}
    if metadados.get("month_complete") is not True:
        raise ValueError("O mês não possui fechamento completo e confiável.")
    if (_dia(metadados.get("snapshot_date")) != fim
            or _dia(metadados.get("period_start")) > inicio
            or _dia(metadados.get("period_end")) < fim):
        raise ValueError("O mês exige histórico completo e snapshot no último dia.")
    return metadados


def _registros(tabela: pd.DataFrame, colunas: list[str]) -> list[dict[str, Any]]:
    """Seleciona campos permitidos e preserva a precisão dos indicadores."""
    selecionada = tabela.loc[:, colunas].replace([math.inf, -math.inf], float("nan"))
    registros = selecionada.astype(object).where(pd.notna(selecionada), None).to_dict(orient="records")
    # to_json do pandas arredonda casas decimais, inclusive coberturas pequenas.
    # JSON padrão conserva o float calculado; ausentes e infinitos viram null.
    return json.loads(json.dumps(registros, allow_nan=False))


def _numero_finito(valor: float) -> float | None:
    """Mantém indicador desconhecido como ausência, jamais infinito ou zero inventado."""
    return float(valor) if math.isfinite(valor) else None


def gerar_relatorio_mensal(
    payload: dict,
    mes: date | str,
    permissoes: Iterable[str],
    anterior: dict | None = None,
    hoje: date | None = None,
) -> dict[str, Any]:
    """Calcula um mês fechado com os mesmos indicadores do dashboard.

    O lote precisa declarar abrangência completa e conter o saldo do último
    dia. Custos são descartados antes do motor quando não autorizados. Os
    blocos de estoque, reposição e tendências respeitam concessões distintas.
    Não há estimativa de receita: as vendas importadas contêm apenas unidades.
    """
    acessos = set(permissoes)
    _exigir(acessos, "reports.view")
    inicio, fim = _periodo(mes)
    atual = hoje or datetime.now(FUSO).date()
    if fim >= atual:
        raise ValueError("O relatório exige um mês civil já encerrado.")
    metadados = _validar_fechamento(payload, inicio, fim)
    bruto = pd.DataFrame(payload.get("products", [])).copy(deep=True)
    if "finance.view" not in acessos:
        bruto = bruto.drop(columns=["custo_unitario"], errors="ignore")
    produtos = validar_produtos(bruto)
    vendas = validar_vendas(pd.DataFrame(payload.get("sales", []), columns=COLUNAS_VENDAS), produtos)
    fotos = validar_snapshots(
        pd.DataFrame(payload.get("snapshots", []), columns=COLUNAS_SNAPSHOTS), produtos,
    )
    fechamento = fotos.loc[fotos["data"].eq(pd.Timestamp(fim))].set_index("codigo")["estoque"]
    saldo = produtos.set_index("codigo")["estoque"]
    if not fechamento.reindex(saldo.index).eq(saldo).all():
        raise ValueError("O saldo dos produtos deve corresponder ao snapshot de fechamento.")
    painel = gerar_painel(produtos, vendas, fotos, hoje=fim)
    periodo = vendas.loc[vendas["data"].between(pd.Timestamp(inicio), pd.Timestamp(fim))]
    positivas = periodo.loc[periodo["quantidade"].gt(0)]
    agrupadas = positivas.groupby("codigo", as_index=False)["quantidade"].sum()
    melhores = agrupadas.merge(produtos[["codigo", "nome"]], on="codigo").sort_values(
        ["quantidade", "codigo"], ascending=[False, True],
    )
    resumo = {
        "unidades_vendidas": sum(int(valor) for valor in periodo["quantidade"]),
        "produtos_com_venda": int(positivas["codigo"].nunique()),
        "variacao_unidades_pct": None,
    }
    cautelas = [
        "Vendas expressas em unidades; o relatório não estima faturamento ou margem.",
        "A abrangência do histórico foi declarada na importação e deve ser conferida na origem.",
    ]
    if anterior is not None:
        inicio_anterior, fim_anterior = _periodo(inicio - timedelta(days=1))
        try:
            _validar_fechamento(anterior, inicio_anterior, fim_anterior)
            catalogo_anterior = pd.DataFrame(anterior.get("products", []))
            if "finance.view" not in acessos:
                catalogo_anterior = catalogo_anterior.drop(columns=["custo_unitario"], errors="ignore")
            vendas_anteriores = validar_vendas(
                pd.DataFrame(anterior.get("sales", []), columns=COLUNAS_VENDAS),
                validar_produtos(catalogo_anterior),
            )
            anteriores = vendas_anteriores.loc[vendas_anteriores["data"].between(
                pd.Timestamp(inicio_anterior), pd.Timestamp(fim_anterior),
            ), "quantidade"]
            total_anterior = sum(int(valor) for valor in anteriores)
            if total_anterior > 0:
                resumo["variacao_unidades_pct"] = (
                    (resumo["unidades_vendidas"] - total_anterior) / total_anterior * 100
                )
            else:
                cautelas.append("Mês anterior sem vendas positivas; variação percentual indefinida.")
        except (ValueError, TypeError, KeyError):
            cautelas.append("Mês anterior incompleto; comparação percentual indisponível.")
    else:
        cautelas.append("Sem fechamento comparável do mês anterior.")
    if painel["dias_disponibilidade_desconhecida"].gt(0).any():
        cautelas.append("Há dias sem snapshot; disponibilidade desconhecida não comprova ruptura.")
    if painel["dias_snapshots_conflitantes"].gt(0).any():
        cautelas.append("Existem vendas com saldo zero no mesmo dia; as vendas foram preservadas.")
    if painel["demanda_estimavel"].eq(False).any():
        cautelas.append("Há produtos sem dias elegíveis; as recomendações exigem revisão humana.")
    if _dia(metadados["period_start"]) > fim - timedelta(days=89):
        cautelas.append("O histórico não cobre 90 dias; tendências e ABC têm referência limitada.")
    resultado: dict[str, Any] = {
        "periodo": {"inicio": inicio.isoformat(), "fim": fim.isoformat()},
        "fonte": {
            "batch_id": metadados.get("batch_id"),
            "atualizado_em": metadados.get("created_at"),
            "origem": metadados.get("source", "Importação CSV"),
        },
        "resumo": resumo,
        "mais_vendidos": _registros(melhores, ["codigo", "nome", "quantidade"]),
        "cautelas": cautelas,
    }
    if "inventory.view" in acessos:
        resultado["estoque"] = _registros(painel, [
            "codigo", "nome", "estoque", "status", "cobertura_dias", "risco_prazo", "dias_sem_venda",
        ])
    if "replenishment.view" in acessos:
        resultado["reposicao"] = _registros(painel.loc[painel["quantidade_sugerida"].gt(0)], [
            "codigo", "nome", "fornecedor", "estoque", "venda_media_diaria", "prazo_entrega_dias",
            "estoque_seguranca", "reservas", "pedidos_pendentes", "posicao_estoque", "ponto_pedido",
            "estoque_alvo", "quantidade_sugerida", "demanda_estimavel", "risco_prazo",
        ])
    if "trends.view" in acessos:
        colunas = ["codigo", "nome", "venda_media_diaria", "media_longa", "tendencia"]
        if "finance.view" in acessos:
            colunas.append("classe_abc")
        resultado["tendencias"] = _registros(painel, colunas)
    if "finance.view" in acessos:
        parados = painel.loc[painel["status"].eq("Parado")]
        resultado["financeiro"] = {
            "base": "Custo informado na importação do fechamento, não receita de vendas.",
            "capital_estoque": _numero_finito(somar_valores_financeiros(painel["capital_estoque"])),
            "capital_parado": 0.0 if parados.empty else _numero_finito(
                somar_valores_financeiros(parados["capital_estoque"]),
            ),
            "custos_parciais": bool(produtos["custo_unitario"].isna().any()),
            "limite_excedido": bool(painel["financeiro_limite_excedido"].any()),
        }
    return resultado


def filtrar_relatorio(relatorio: dict, permissoes: Iterable[str]) -> dict:
    """Reaplica permissões atuais sem reutilizar blocos revogados em memória."""
    acessos = set(permissoes)
    _exigir(acessos, "reports.view")
    permitido = {"periodo", "fonte", "resumo", "mais_vendidos", "cautelas"}
    permitido.update(bloco for bloco, permissao in BLOCOS_PERMISSOES.items() if permissao in acessos)
    seguro = copy.deepcopy({chave: valor for chave, valor in relatorio.items() if chave in permitido})
    if "finance.view" not in acessos:
        for linha in seguro.get("tendencias", []):
            linha.pop("classe_abc", None)
    return seguro


def exportar_relatorio_csv(relatorio: dict, permissoes: Iterable[str]) -> bytes:
    """Exporta os blocos vigentes com proteção contra fórmulas de planilhas."""
    acessos = set(permissoes)
    _exigir(acessos, "reports.view", "reports.export")
    seguro = filtrar_relatorio(relatorio, acessos)
    linhas = []
    for bloco, valores in seguro.items():
        if isinstance(valores, list):
            for item in valores:
                if isinstance(item, dict):
                    linhas.append({"bloco": bloco, **item})
                else:
                    linhas.append({"bloco": bloco, "observacao": item})
        elif isinstance(valores, dict):
            for chave, valor in valores.items():
                linhas.append({"bloco": bloco, "indicador": chave, "valor": valor})
    return exportar_csv_seguro(pd.DataFrame(linhas))
