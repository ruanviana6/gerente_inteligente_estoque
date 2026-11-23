"""Quadro real de consulta por loja: saldos GET + vendas observadas separadamente.

O quadro é uma conferência, nunca uma previsão ou um pedido à indústria.
Vendas observadas não são descontadas do saldo da API.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from analytics.observational_review import ConferenciaObservada
from integracoes.catalog_full import CatalogoCompleto
from integracoes.gestaoclick import ContratoInvalido


@dataclass(frozen=True)
class QuadroReposicaoReal:
    loja_id: str
    consultado_em: datetime
    periodo_inicio: date | None
    periodo_fim: date | None
    produtos: int
    produtos_com_saldo: int
    produtos_com_venda_observada: int
    observados_sem_catalogo: int
    linhas: tuple[dict, ...]


def construir_quadro_real(
    catalogo: CatalogoCompleto, observacao: ConferenciaObservada | None,
    loja_id: str, consultado_em: datetime,
) -> QuadroReposicaoReal:
    """Junta somente IDs idênticos da mesma loja, preservando ausência de dados."""
    if (not isinstance(catalogo, CatalogoCompleto)
            or catalogo.loja_solicitada != loja_id
            or not isinstance(loja_id, str) or not loja_id.isascii()
            or not loja_id.isdigit()
            or catalogo.total_informado != len(catalogo.produtos)
            or not isinstance(consultado_em, datetime)
            or consultado_em.tzinfo is None
            or consultado_em.utcoffset() is None):
        raise ContratoInvalido("O catálogo consultado não corresponde à loja autorizada.")
    if observacao is not None and (
            not isinstance(observacao, ConferenciaObservada)
            or observacao.store_id != loja_id):
        raise ContratoInvalido("Observação de vendas de outra loja ou com formato incompatível.")
    vistos: set[str] = set()
    observadas = {x.product_id: x.observed_un for x in observacao.rows} if observacao else {}
    linhas = []
    for produto in catalogo.produtos:
        if (not isinstance(produto.id, str) or not produto.id.isascii()
                or not produto.id.isdigit() or produto.id in vistos):
            raise ContratoInvalido("ID de produto inválido ou duplicado na leitura.")
        vistos.add(produto.id)
        saldo = produto.estoque
        if saldo is not None and (
                not isinstance(saldo, Decimal) or not saldo.is_finite() or saldo < 0):
            raise ContratoInvalido("O saldo retornado pelo catálogo é inválido.")
        observado = observadas.get(produto.id)
        if observado is not None and (
                not isinstance(observado, Decimal)
                or not observado.is_finite() or observado <= 0):
            raise ContratoInvalido("A observação de vendas não pode ser conciliada.")
        linhas.append({
            "Produto": produto.nome or "Não informado pelo ERP",
            "Categoria": produto.categoria or "Não informada",
            "Código interno": produto.codigo_interno or "Não informado",
            "Produto (ID)": produto.id,
            "Saldo GET da loja (UN; não ao vivo)": format(saldo, "f") if saldo is not None else "Não informado",
            "Vendas fechadas observadas (UN)": (
                format(observado, "f") if observado is not None else "Não observado no período"
            ),
            "Variações": "Conferir unidades/variações" if produto.variacoes else "Sem variações no retorno",
        })
    linhas.sort(key=lambda row: (
        row["Vendas fechadas observadas (UN)"] == "Não observado no período",
        -observadas.get(row["Produto (ID)"], Decimal(0)),
        int(row["Produto (ID)"]),
    ))
    return QuadroReposicaoReal(
        loja_id, consultado_em,
        observacao.period_start if observacao else None,
        observacao.period_end if observacao else None,
        len(linhas),
        sum(p.estoque is not None for p in catalogo.produtos),
        sum(k in vistos for k in observadas),
        sum(k not in vistos for k in observadas),
        tuple(linhas),
    )


def exportar_quadro_real_csv(quadro: QuadroReposicaoReal) -> bytes:
    """CSV sem PII, com rótulos protegidos contra fórmula do Excel."""
    if not isinstance(quadro, QuadroReposicaoReal):
        raise TypeError("É necessário um quadro real validado.")
    arquivo = io.StringIO(newline="")
    w = csv.writer(arquivo, delimiter=";", lineterminator="\r\n")
    cabecalhos = (
        "Loja (ID)", "Saldo consultado em (UTC)", "Período observado início",
        "Período observado fim", "Produto", "Categoria", "Código interno",
        "Produto (ID)", "Saldo GET (UN, consulta pontual)",
        "Vendas fechadas observadas (UN, período parcial)", "Variações",
    )
    w.writerow(cabecalhos)
    for row in quadro.linhas:
        dados = [
            quadro.loja_id, quadro.consultado_em.isoformat(),
            quadro.periodo_inicio.isoformat() if quadro.periodo_inicio else "",
            quadro.periodo_fim.isoformat() if quadro.periodo_fim else "",
            *(row[k] for k in (
                "Produto", "Categoria", "Código interno", "Produto (ID)",
                "Saldo GET da loja (UN; não ao vivo)",
                "Vendas fechadas observadas (UN)", "Variações",
            )),
        ]
        w.writerow([
            "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v
            for v in dados
        ])
    return arquivo.getvalue().encode("utf-8-sig")
