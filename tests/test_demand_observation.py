"""Testa histórico agregado sem rede, ERP, Supabase ou dados comerciais reais."""
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from analytics.demand_observation import construir_observacao
from analytics.sales_audit import conciliar_vendas_com_catalogo
from integracoes.gestaoclick import ContratoInvalido
from integracoes.native_preview import DocumentoPreviaVenda, ItemPreviaVenda
from integracoes.sales_audit import AuditoriaVendas

SOURCE = "10000000-0000-4000-8000-000000000001"
DATA = date(2026, 11, 12)


def observacao():
    doc1 = DocumentoPreviaVenda(
        "venda-1", "101", DATA, "1", (
            ItemPreviaVenda("101", None, Decimal("2"), "UN"),
            ItemPreviaVenda("102", None, Decimal("3"), "CX"),
            ItemPreviaVenda("101", None, Decimal("2"), None),
        ),
    )
    doc2 = DocumentoPreviaVenda(
        "venda-2", "102", DATA, "0", (
            ItemPreviaVenda("101", None, Decimal("1"), "UN"),
        ),
    )
    doc3 = DocumentoPreviaVenda(
        "venda-3", "103", DATA, "2", (
            ItemPreviaVenda("101", None, Decimal("5"), "UN"),
        ),
    )
    docs = (doc1, doc2, doc3)
    audit = AuditoriaVendas("001", DATA, DATA, docs, 3, 1, 5)
    foto = {"store_id": "001", "product_count": 2, "products": [
        {"id": "101", "estoque": 15},
        {"id": "102", "estoque": 20},
    ]}
    resumo = conciliar_vendas_com_catalogo(
        audit, foto, separacao_id="1", concretizada_id="0",
    )
    return audit, resumo


def test_payload_real_so_agregados_un_e_desabilitado_para_reposicao():
    auditoria, resumo = observacao()
    assert not resumo.ready_for_replenishment
    payload = construir_observacao(auditoria, resumo, SOURCE)
    assert payload == {
        "p_source_id": SOURCE,
        "p_store_id": "001",
        "p_period_start": "2026-11-12",
        "p_period_end": "2026-11-12",
        "p_documents": 3,
        "p_separating": 1,
        "p_completed": 1,
        "p_other": 1,
        "p_items": 5,
        "p_unknown_unit": 1,
        "p_unsupported_unit": 1,
        "p_rows": [{"product_id": "101", "observed_un": "3"}],
        "p_confirmed": True,
    }
    texto = str(payload)
    for forbidden in ("venda-1", "venda-2", "venda-3", "valor", "cliente", "chave_nfe"):
        assert forbidden not in texto


@pytest.mark.parametrize("id_source", [None, "", "123", True, "SECRET"])
def test_fonte_nao_uuid_bloqueia(id_source):
    auditoria, resumo = observacao()
    with pytest.raises(ContratoInvalido):
        construir_observacao(auditoria, resumo, id_source)


def test_rejeita_contagem_alterada():
    auditoria, resumo = observacao()
    with pytest.raises(ContratoInvalido):
        construir_observacao(
            auditoria, replace(resumo, documentos_fechados=4), SOURCE,
        )
    with pytest.raises(ContratoInvalido):
        construir_observacao(
            replace(auditoria, itens=999), resumo, SOURCE,
        )


@pytest.mark.parametrize("identidade", ["=cmd()", "0", "a-b", "", "１２３"])
def test_nao_permite_produto_malformado_ou_formula_csv(identidade):
    auditoria, resumo = observacao()
    with pytest.raises(ContratoInvalido):
        construir_observacao(
            auditoria,
            replace(resumo, agrupamento=((identidade, Decimal("3")),)),
            SOURCE,
        )


def test_quantidade_fora_da_agregacao_bloqueia():
    auditoria, resumo = observacao()
    with pytest.raises(ContratoInvalido):
        construir_observacao(
            auditoria, replace(resumo, agrupamento=(("101", Decimal("4")),)),
            SOURCE,
        )


def test_produto_repetido_bloqueia():
    auditoria, resumo = observacao()
    with pytest.raises(ContratoInvalido):
        construir_observacao(
            auditoria,
            replace(resumo, agrupamento=(("101", Decimal("1")), ("101", Decimal("2"))),
            SOURCE,
        )


def test_inadmissivel_unidade_fracionada_alem_de_seis_casas():
    auditoria, resumo = observacao()
    with pytest.raises(ContratoInvalido):
        construir_observacao(
            auditoria,
            replace(resumo, agrupamento=(("101", Decimal("3.0000001")),)),
            SOURCE,
        )


def test_periodo_alem_da_janela_bloqueia():
    auditoria, resumo = observacao()
    with pytest.raises(ContratoInvalido):
        construir_observacao(
            replace(auditoria, fim=date(2026, 11, 19)), resumo, SOURCE,
        )
