"""Confere fotografia real da loja sem extrapolar a cobertura dos dados."""
import csv
import io
from copy import deepcopy

import pytest

from integracoes.gestaoclick import ContratoInvalido
from web.real_catalog_stock_page import exportar_fotografia_csv, validar_fotografia


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


def test_csv_real_contem_nomes_categorias_e_loja_da_fotografia():
    payload = exemplo()
    payload["products"][0].update(
        nome="Perfume Verão 100 ml", categoria="Perfumaria", codigo_interno="FL001",
    )
    foto = validar_fotografia(payload, "416023")
    linhas = list(csv.reader(
        io.StringIO(exportar_fotografia_csv(foto).decode("utf-8-sig")), delimiter=";",
    ))
    assert len(linhas) == 3
    assert linhas[0] == [
        "Loja (ID)", "Fotografia em (ISO)", "Produto", "Categoria",
        "Código interno", "Produto (ID)", "Saldo histórico (não atual)",
    ]
    assert linhas[1][:6] == [
        "416023", "2026-11-22T20:22:50-03:00", "Perfume Verão 100 ml",
        "Perfumaria", "FL001", "62951783",
    ]
    assert linhas[1][6] == "10.0"
    assert linhas[2][2:5] == ["Não informado", "Não informada", "Não informado"]


def test_csv_real_impede_formula_externa_em_nome_categoria_e_codigo():
    payload = exemplo()
    payload["products"][0].update(
        nome="=1+1", categoria="+SOMA(A1:A2)", codigo_interno="@comando",
    )
    foto = validar_fotografia(payload, "416023")
    content = exportar_fotografia_csv(foto).decode("utf-8-sig")
    linhas = list(csv.reader(io.StringIO(content), delimiter=";"))
    assert linhas[1][2:5] == ["'=1+1", "'+SOMA(A1:A2)", "'@comando"]
