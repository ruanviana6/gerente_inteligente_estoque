"""Projeção rigorosa de conferência manual entre duas fotografias de datas diferentes.

Não calcula cobertura, saldo corrigido, previsão nem pedido de compra.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from integracoes.gestaoclick import ContratoInvalido


@dataclass(frozen=True)
class LinhaConferencia:
    product_id: str
    observed_un: Decimal
    stock_snapshot: Decimal | None


@dataclass(frozen=True)
class ConferenciaObservada:
    store_id: str
    period_start: date
    period_end: date
    observed_at: datetime
    stock_snapshot_at: datetime
    documents: int
    closed_documents: int
    other_documents: int
    product_count: int
    stock_snapshot_products: int
    total_observed_un: Decimal
    rows: tuple[LinhaConferencia, ...]


def _decimal(valor: object, *, permite_nulo: bool = False) -> Decimal | None:
    if valor is None and permite_nulo:
        return None
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str, Decimal)):
        raise ContratoInvalido("Quantidade numérica inválida no diagnóstico.")
    try:
        qtd = Decimal(str(valor))
    except InvalidOperation:
        raise ContratoInvalido("Quantidade numérica inválida no diagnóstico.") from None
    if (not qtd.is_finite() or qtd < 0 or qtd > Decimal("1000000000000")
            or max(0, -qtd.as_tuple().exponent) > 6):
        raise ContratoInvalido("Quantidade fora do limite da conferência.")
    return qtd


def _instante(valor: object) -> datetime:
    if not isinstance(valor, str):
        raise ContratoInvalido("Data da fotografia inválida.")
    try:
        instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        raise ContratoInvalido("Data da fotografia inválida.") from None
    if instante.tzinfo is None or instante.utcoffset() is None:
        raise ContratoInvalido("A fotografia não informa fuso horário.")
    return instante


def conferir_observacao(payload: object, loja: str) -> ConferenciaObservada | None:
    """Confere integridade antes de qualquer exibição ou exportação."""
    if not isinstance(payload, dict) or payload.get("store_id") != loja:
        raise ContratoInvalido("A resposta não corresponde à loja autorizada.")
    if payload.get("found") is False and set(payload) == {"found", "store_id"}:
        return None
    if (payload.get("found") is not True or
            payload.get("stock_is_live") is not False or
            payload.get("ready_for_replenishment") is not False or
            payload.get("fiscal_real_enabled") is not False):
        raise ContratoInvalido("A resposta não é uma conferência exclusivamente de leitura.")
    linhas_brutas = payload.get("rows")
    if not isinstance(linhas_brutas, list) or len(linhas_brutas) > 1000:
        raise ContratoInvalido("A lista da conferência está incompleta ou excessiva.")
    try:
        inicio = date.fromisoformat(payload["period_start"])
        fim = date.fromisoformat(payload["period_end"])
        docs = payload["documents"]
        fechados = payload["closed_documents"]
        outros = payload["other_documents"]
        n_prod = payload["product_count"]
        n_foto = payload["stock_snapshot_products"]
        observacao = _instante(payload["observed_at"])
        foto = _instante(payload["stock_snapshot_at"])
    except (KeyError, TypeError, ValueError):
        raise ContratoInvalido("Metadados da observação inválidos.") from None
    if (not 0 <= (fim - inicio).days <= 6 or
            any(type(x) is not int or x < 0 for x in (docs, fechados, outros, n_prod, n_foto)) or
            fechados + outros != docs or docs > 1000 or n_prod != len(linhas_brutas) or
            n_foto > 1000):
        raise ContratoInvalido("Totais da observação não conciliados.")
    total = _decimal(payload.get("total_observed_un"))
    linhas = []
    ids = set()
    soma = Decimal(0)
    for item in linhas_brutas:
        if not isinstance(item, dict) or set(item) != {
            "product_id", "observed_un", "stock_snapshot"
        }:
            raise ContratoInvalido("Linha da observação com formato inesperado.")
        pid = item["product_id"]
        if (not isinstance(pid, str) or not re.fullmatch(r"[0-9]{1,30}", pid) or
                int(pid) == 0 or pid in ids):
            raise ContratoInvalido("ID de produto inválido ou repetido.")
        ids.add(pid)
        quantidade = _decimal(item["observed_un"])
        saldo = _decimal(item["stock_snapshot"], permite_nulo=True)
        if quantidade == 0 or (saldo is not None and saldo > 1000000000):
            raise ContratoInvalido("Quantidade inesperada na conferência.")
        soma += quantidade
        linhas.append(LinhaConferencia(pid, quantidade, saldo))
    if soma != total:
        raise ContratoInvalido("Soma das quantidades observadas não confere.")
    linhas.sort(key=lambda r: (-r.observed_un, int(r.product_id)))
    return ConferenciaObservada(
        loja, inicio, fim, observacao, foto,
        docs, fechados, outros, n_prod, n_foto, total, tuple(linhas),
    )


def exportar_conferencia_csv(dados: ConferenciaObservada) -> bytes:
    """CSV Excel pt-BR de observações, com somente IDs numéricos e totais."""
    if not isinstance(dados, ConferenciaObservada):
        raise TypeError("É necessária uma conferência validada.")
    arquivo = io.StringIO(newline="")
    w = csv.writer(arquivo, delimiter=";", lineterminator="\n")
    w.writerow(("produto_id", "unidades_observadas", "saldo_ultima_fotografia",
                "data_ultima_fotografia_utc"))
    for linha in dados.rows:
        w.writerow((linha.product_id, format(linha.observed_un, "f").replace(".", ","),
                    "" if linha.stock_snapshot is None else
                    format(linha.stock_snapshot, "f").replace(".", ","),
                    dados.stock_snapshot_at.isoformat()))
    return arquivo.getvalue().encode("utf-8-sig")
