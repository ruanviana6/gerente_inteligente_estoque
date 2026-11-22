"""A triagem nunca calcula compra; usa somente observações verificadas."""
from dataclasses import replace
from decimal import Decimal

import pytest
from test_observational_review import payload

from analytics.observational_review import (
    LinhaConferencia,
    conferir_observacao,
)
from integracoes.gestaoclick import ContratoInvalido
from web.real_replenishment_triage import priorizar_conferencia


def dados_ficticios():
    return conferir_observacao(payload(), "LOJA_FICTICIA")


def test_prioriza_ausencia_saldo_zero_e_saldo_baixo():
    base = dados_ficticios()
    linhas = (
        LinhaConferencia("101", "COD101", Decimal("3"), Decimal("0")),
        LinhaConferencia("102", "COD102", Decimal("200"), Decimal("7")),
        LinhaConferencia("103", "COD103", Decimal("1"), None),
        LinhaConferencia("104", "COD104", Decimal("50"), Decimal("100")),
        LinhaConferencia("105", "COD105", Decimal("35"), Decimal("30")),
    )
    triagem = priorizar_conferencia(replace(base, rows=linhas, product_count=len(linhas)))
    assert [x["Produto (ID)"] for x in triagem] == ["103", "101", "102", "105", "104"]
    assert triagem[1]["Situação da fotografia"] == "Saldo zero na fotografia"
    assert triagem[2]["Código interno"] == "COD102"
    assert all("suger" not in str(x).lower() and "pedido" not in str(x).lower()
               for x in triagem)


def test_dados_reais_prevalidam_total_e_escopo():
    base = dados_ficticios()
    triagem = priorizar_conferencia(base)
    assert len(triagem) == 2
    assert all(x["Saldo na fotografia (não atual)"] is None
               or x["Saldo na fotografia (não atual)"] >= 0 for x in triagem)


def test_rejeita_objeto_nao_homologado():
    for input_data in (None, {}, [], "LOJA_FICTICIA"):
        with pytest.raises(ContratoInvalido):
            priorizar_conferencia(input_data)
