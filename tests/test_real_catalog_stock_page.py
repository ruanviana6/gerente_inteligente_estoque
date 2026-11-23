"""Confere fotografia real da loja sem extrapolar a cobertura dos dados."""
from copy import deepcopy

import pytest

from integracoes.gestaoclick import ContratoInvalido
from web.real_catalog_stock_page import validar_fotografia


def exemplo():
    return {
        "found": True, "store_id": "416023",
        "created_at": "2026-11-22T20:22:50-03:00", "product_count": 2,
        "total_stock": "12.000000", "products": [
            {"id": "62951783", "estoque": "10.000000"},
            {"id": "99587110", "estoque": "2.000000"},
        ],
    }


def test_foto_reconciliada_apresenta_estoque_historico_sem_descontos():
    got = validar_fotografia(exemplo(), "416023")
    assert got["store_id"] == "416023"
    assert str(got["total_stock"]) == "12.000000"
    assert [r["Produto (ID)"] for r in got["rows"]] == ["62951783", "99587110"]
    assert sum(r["Saldo na fotografia (não atual)"] for r in got["rows"]) == 12


@pytest.mark.parametrize("change", [
    lambda p: p.update(store_id="424409"),
    lambda p: p.update(product_count=3),
    lambda p: p.update(total_stock="13"),
    lambda p: p["products"][1].update(id="62951783"),
    lambda p: p["products"][1].update(estoque="-1"),
    lambda p: p["products"][1].update(estoque="NaN"),
    lambda p: p.update(created_at="2026-11-22T20:22:50"),
    lambda p: p["products"][0].update(id="=SUM(A1)"),
])
def test_foto_invalida_e_recusada(change):
    p = deepcopy(exemplo())
    change(p)
    with pytest.raises(ContratoInvalido):
        validar_fotografia(p, "416023")


def test_ausencia_de_foto_nao_e_estoque_zero():
    assert validar_fotografia({"found": False, "store_id": "416023"}, "416023") is None
