"""Confere fotografia real da loja sem extrapolar a cobertura dos dados."""
import csv
import io
from copy import deepcopy
from types import SimpleNamespace

import pytest

from integracoes.gestaoclick import ContratoInvalido
from web.real_catalog_stock_page import (
    consultar_catalogo_real,
    exportar_fotografia_csv,
    real_catalog_controls,
    validar_fotografia,
)


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


def test_consulta_catalogo_exige_selecao_quando_ha_duas_lojas():
    from test_real_home_status import AuthFake

    auth = AuthFake()
    outra_id = "10000000-0000-4000-8000-000000000003"
    fontes = [
        {"id": "10000000-0000-4000-8000-000000000001",
         "name": "GestãoClick: loja 416023", "stores": ["416023"], "active": True},
        {"id": outra_id, "name": "GestãoClick: loja 424409",
         "stores": ["424409"], "active": True},
    ]

    def rpc(name, args, permission):
        auth.calls.append((name, deepcopy(args), permission))
        if name == "integration_sources":
            return deepcopy(fontes)
        if name == "catalog_stock_latest":
            assert args == {"p_source_id": outra_id, "p_store_id": "424409"}
            payload = exemplo()
            payload["store_id"] = "424409"
            return payload
        raise AssertionError("RPC fora do escopo")

    auth.rpc = rpc
    assert consultar_catalogo_real(auth) is None
    assert [x[0] for x in auth.calls] == ["integration_sources"]
    auth.calls.clear()
    assert consultar_catalogo_real(auth, "424409")["store_id"] == "424409"
    assert [x[0] for x in auth.calls] == ["integration_sources", "catalog_stock_latest"]
    auth.calls.clear()
    assert consultar_catalogo_real(auth, "loja-nao-autorizada") is None
    assert [x[0] for x in auth.calls] == ["integration_sources"]


def test_fotografia_de_outra_loja_e_bloqueada_antes_da_exibicao():
    from test_real_home_status import AuthFake

    auth = AuthFake()

    def rpc(name, args, permission):
        auth.calls.append((name, deepcopy(args), permission))
        if name == "integration_sources":
            return [{"id": "10000000-0000-4000-8000-000000000001",
                     "name": "GestãoClick: loja 416023", "stores": ["416023"], "active": True}]
        if name == "catalog_stock_latest":
            payload = exemplo()
            payload["store_id"] = "424409"
            return payload
        raise AssertionError("RPC fora do escopo")

    auth.rpc = rpc
    with pytest.raises(ContratoInvalido):
        consultar_catalogo_real(auth, "416023")


def test_primeira_fotografia_pode_ser_iniciada_na_aba_estoque(monkeypatch):
    """A ausência de foto não pode esconder a atualização via GET."""
    from web import real_catalog_stock_page as tela
    from web import real_stock_refresh_page as atualizacao

    chamadas = []
    monkeypatch.setattr(tela, "escolher_loja_real", lambda _auth: "416023")
    monkeypatch.setattr(tela, "consultar_catalogo_real", lambda _auth, _loja: None)
    monkeypatch.setattr(tela.st, "subheader", lambda _text: None)
    monkeypatch.setattr(tela.st, "info", lambda _text: chamadas.append("informativo"))
    monkeypatch.setattr(
        atualizacao, "atualizar_fotografia_estoque",
        lambda servico, contexto, loja: chamadas.append(loja),
    )
    perfil = SimpleNamespace(role="Master")
    contexto = SimpleNamespace(profile=perfil, can=lambda permissao: permissao == "integrations.manage")
    servico = SimpleNamespace(auth=object())
    real_catalog_controls(servico, contexto)
    assert chamadas == ["416023", "informativo"]
