"""Identificação por produto/loja sem inferir rótulos e sem chamadas externas."""
from copy import deepcopy
from decimal import Decimal

import pytest

from analytics.observational_review import conferir_observacao, exportar_conferencia_csv
from integracoes.catalog_full import CatalogoCompleto
from integracoes.catalog_preview import _projetar
from integracoes.gestaoclick import ContratoInvalido
from web.real_catalog_stock_page import validar_fotografia
from web.real_replenishment_triage import priorizar_conferencia


def catalogo(nome="Perfume Original 100 ml", grupo="Perfumaria"):
    return {"code": 200, "status": "success", "meta": {"pagina_atual": 1, "total_registros": 1},
            "data": [{"id": "62951783", "codigo_interno": "PFF33", "nome": nome,
                      "nome_grupo": grupo, "estoque": 10, "variacoes": []}]}


def test_parser_captura_nome_categoria_e_snapshot_na_mesma_chave_de_produto():
    produtos, quantidade, total = _projetar(catalogo())
    assert (quantidade, total) == (1, 1)
    assert produtos[0].nome == "Perfume Original 100 ml"
    assert produtos[0].categoria == "Perfumaria"
    completo = CatalogoCompleto("416023", produtos, 1, 1, Decimal(10), 0, 0, 0, 0, 0, "teste")
    row = completo.linhas_snapshot()[0]
    assert (row["id"], row["nome"], row["categoria"]) == (
        "62951783", "Perfume Original 100 ml", "Perfumaria",
    )
    assert row["estoque"] == "10"


def test_grupo_em_objeto_na_api_e_utilizado_sem_inventar_categoria():
    raw = catalogo()
    raw["data"][0].pop("nome_grupo")
    raw["data"][0]["grupo"] = {"id": "5", "nome": "Cosméticos"}
    produtos, _, _ = _projetar(raw)
    assert produtos[0].categoria == "Cosméticos"
    raw["data"][0]["grupo"] = {"id": "5"}
    produtos, _, _ = _projetar(raw)
    assert produtos[0].categoria is None


@pytest.mark.parametrize("field,value", [
    ("nome", "<script>alert(1)</script>"),
    ("nome_grupo", "Categoria\nInjetada"),
    ("nome", "A" * 201),
    ("nome", {"texto": "inválido"}),
])
def test_parser_rejeita_identificacao_invalida(field, value):
    raw = catalogo()
    raw["data"][0][field] = value
    with pytest.raises(ContratoInvalido):
        _projetar(raw)


def foto():
    return {"found": True, "store_id": "416023", "created_at": "2026-11-22T20:22:50-03:00",
            "product_count": 1, "total_stock": "10", "products": [{
                "id": "62951783", "codigo_interno": "PFF33",
                "nome": "Perfume Original 100 ml", "categoria": "Perfumaria", "estoque": 10,
            }]}


def test_estoque_real_prioriza_rotulos_e_preserva_id_e_saldo():
    result = validar_fotografia(foto(), "416023")
    assert result["rows"] == [{
        "Produto": "Perfume Original 100 ml", "Categoria": "Perfumaria",
        "Código interno": "PFF33", "Produto (ID)": "62951783",
        "Saldo na fotografia (não atual)": 10.0,
    }]


def test_fotografia_legada_sem_rotulos_nao_inventa_nome():
    raw = foto()
    raw["products"][0].pop("nome")
    raw["products"][0].pop("categoria")
    result = validar_fotografia(raw, "416023")
    assert result["rows"][0]["Produto"] == "Não informado"
    assert result["rows"][0]["Categoria"] == "Não informada"


def test_obs_reposicao_csv_usam_mesma_identificacao_e_escopo():
    raw = {
        "found": True, "store_id": "416023", "observed_at": "2026-11-22T18:47:31+00:00",
        "period_start": "2026-11-16", "period_end": "2026-11-22",
        "documents": 2, "closed_documents": 2, "other_documents": 0,
        "product_count": 1, "total_observed_un": 3, "stock_snapshot_at": "2026-11-22T18:00:00+00:00",
        "stock_snapshot_products": 245,
        "stock_is_live": False, "ready_for_replenishment": False, "fiscal_real_enabled": False,
        "rows": [{"product_id": "62951783", "internal_code": "PFF33",
                  "product_name": "Perfume Original 100 ml", "category": "Perfumaria",
                  "observed_un": 3, "stock_snapshot": 10}],
    }
    dados = conferir_observacao(raw, "416023")
    assert dados.rows[0].product_name == "Perfume Original 100 ml"
    linhas = priorizar_conferencia(dados)
    assert linhas[0]["Categoria"] == "Perfumaria"
    assert linhas[0]["Produto"] == "Perfume Original 100 ml"
    csv = exportar_conferencia_csv(dados).decode("utf-8-sig")
    assert "nome_produto;categoria" in csv
    assert "Perfume Original 100 ml;Perfumaria" in csv
    bad = deepcopy(raw)
    bad["rows"][0]["product_name"] = "<script>"
    with pytest.raises(ContratoInvalido):
        conferir_observacao(bad, "416023")


def test_csv_protege_formula_se_nomes_externos_a_trouxerem():
    from analytics.observational_review import _csv_rotulo

    assert _csv_rotulo("=1+2") == "'=1+2"
    assert _csv_rotulo("Perfumaria") == "Perfumaria"
