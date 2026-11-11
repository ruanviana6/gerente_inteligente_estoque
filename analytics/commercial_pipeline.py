"""Processamento comercial explícito, separado da navegação e da consulta ao ERP."""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

from analytics.commercial import analisar_comercial, normalizar_linhas_comerciais

CITY_BASES = {"delivery", "registration", "billing", "unknown"}


def preparar_publicacao(linhas, inicio: date, fim: date, *, source: str, store: str,
                        coverage_start: date, coverage_end: date, city_basis: str) -> dict:
    """Produz no máximo 500 indicadores sem PII, com períodos e evidências tipadas.

    A escolha de cidade deve vir do contrato autorizado. Nenhum campo real do
    GestãoClick é mapeado por esta função. A abrangência declarada precisa ser
    conferida no processo de carga antes da chamada.
    """
    if city_basis not in CITY_BASES:
        raise ValueError("Defina a origem da cidade antes de analisar.")
    normalizadas, _ = normalizar_linhas_comerciais(linhas)
    if any(r["source_id"] != source or r["store_id"] != store for r in normalizadas):
        raise ValueError("O processo não aceita linhas de outra fonte ou loja.")
    if city_basis == "unknown" and any(r["city_code"] for r in normalizadas):
        raise ValueError("A cidade não pode ser usada sem finalidade e origem definidas.")
    resultado = analisar_comercial(normalizadas, inicio, fim, source=source, store=store,
                                  coverage_start=coverage_start, coverage_end=coverage_end)
    if resultado["avisos"]:
        raise ValueError("Não publique comparações sem abrangência completa.")
    base = {"period_start": inicio.isoformat(), "period_end": fim.isoformat(),
            "current_units": 0, "previous_units": 0, "current_orders": 0, "previous_orders": 0,
            "group_customers": 0, "previous_customers": 0, "total_orders": 0,
            "customer_ref": None, "city_code": None, "city_name": None, "state": None,
            "sku_a": None, "sku_b": None, "support": None, "confidence": None, "lift": None,
            "median_gap_days": None, "recency_days": None, "mix_similarity": None,
            "top_skus": [], "previous_top_skus": []}
    items = []
    for cliente in resultado["clientes"]:
        for alerta in cliente["alertas"]:
            items.append(base | {"dimension": "customer", "alert_code": alerta,
                "customer_ref": cliente["customer_ref"], "current_units": cliente["unidades_atuais"],
                "previous_units": cliente["unidades_anteriores"], "current_orders": cliente["pedidos_atuais"],
                "previous_orders": cliente["pedidos_anteriores"], "median_gap_days": cliente["intervalo_mediano_dias"],
                "recency_days": cliente["dias_desde_ultimo"], "mix_similarity": cliente["similaridade_mix"]})
    for cidade in resultado["cidades"]:
        variacao = cidade["variacao_pct"]
        if variacao is None or abs(variacao) < 25:
            continue
        items.append(base | {"dimension": "city", "alert_code": "cidade_crescimento" if variacao > 0 else "cidade_queda",
            "city_code": cidade["city_code"], "city_name": cidade["city_name"], "state": cidade["state"],
            "current_units": cidade["unidades_atuais"], "previous_units": cidade["unidades_anteriores"],
            "current_orders": cidade["pedidos_atuais"], "previous_orders": cidade["pedidos_anteriores"],
            "group_customers": cidade["clientes_atuais"], "previous_customers": cidade["clientes_anteriores"]})
        items[-1].update(top_skus=[s for s, _ in cidade["produtos_mais_pedidos"]],
                         previous_top_skus=[s for s, _ in cidade["produtos_anteriores"]])
    cestas = resultado["cestas"] or {}
    if cestas.get("disponivel"):
        for par in cestas["pares"]:
            items.append(base | {"dimension": "pair", "alert_code": "produtos_juntos", "sku_a": par["sku_a"],
                "sku_b": par["sku_b"], "current_orders": par["pedidos_juntos"], "total_orders": cestas["total_pedidos"],
                "support": par["suporte"], "confidence": par["confianca_a_b"], "lift": par["lift"]})
    items.sort(key=lambda r: (-abs(r["current_units"] - r["previous_units"]), -r["current_orders"],
                             r["dimension"], r["customer_ref"] or r["city_code"] or r["sku_a"] or "", r["alert_code"]))
    # O limite limita o custo de leitura. O detalhe de pedidos continua fora do
    # resumo e nunca é apagado para caber neste limite.
    contagens = {"customer": 0, "city": 0, "pair": 0}
    selecionados = []
    for item in items:
        if contagens[item["dimension"]] < 150:
            selecionados.append(item)
            contagens[item["dimension"]] += 1
    items = selecionados
    payload = {"source_id": source, "store_id": store, "city_basis": city_basis, "items": items,
               "period_start": inicio.isoformat(), "period_end": fim.isoformat()}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return payload | {"content_hash": hashlib.sha256(canonical.encode()).hexdigest()}


def preparar_serie_regional(linhas, inicio: date, fim: date, *, source: str, store: str,
                           city_code: str, sku: str | None, coverage_start: date, coverage_end: date,
                           dias_disponiveis: set[date]) -> list[tuple[date, float]]:
    """Constrói série diária somente com cobertura e disponibilidade comprovadas.

    O mínimo de três clientes é verificado em cada janela de 30 dias. Ausência
    de venda só vira zero quando a carga e a disponibilidade foram confirmadas.
    """
    if (type(inicio) is not date or type(fim) is not date or not 180 <= (fim-inicio).days + 1 <= 1825
            or coverage_start > inicio or coverage_end < fim):
        raise ValueError("Histórico regional completo insuficiente.")
    dias = [inicio + timedelta(days=i) for i in range((fim-inicio).days + 1)]
    if not isinstance(dias_disponiveis, set) or not set(dias) <= dias_disponiveis:
        raise ValueError("Disponibilidade regional incompleta; nenhum zero será inventado.")
    normalizadas, _ = normalizar_linhas_comerciais(linhas)
    selecionadas = [r for r in normalizadas if r["source_id"] == source and r["store_id"] == store
                    and r["city_code"] == city_code and (sku is None or r["sku"] == sku)
                    and inicio.isoformat() <= r["sale_date"] <= fim.isoformat() and r["status"] == "completed"
                    and r["quantity"] > r["returned_quantity"]]
    for indice in range(0, len(dias), 30):
        janela = set(d.isoformat() for d in dias[indice:indice+30])
        if len({r["customer_ref"] for r in selecionadas if r["sale_date"] in janela}) < 3:
            raise ValueError("Grupo regional insuficiente para preservar a privacidade.")
    unidades = dict.fromkeys(dias, 0)
    for row in selecionadas:
        unidades[date.fromisoformat(row["sale_date"])] += row["quantity"] - row["returned_quantity"]
    return [(dia, float(unidades[dia])) for dia in dias]
