"""Testes do painel de conferência de leitura, sem ERP nem Supabase."""
from copy import deepcopy
from decimal import Decimal

import pytest

from analytics.observational_review import (
    conferir_observacao,
    exportar_conferencia_csv,
)
from integracoes.gestaoclick import ContratoInvalido


def payload():
    return {
        "found": True,
        "store_id": "LOJA_FICTICIA",
        "observed_at": "2026-11-22T18:47:31+00:00",
        "period_start": "2026-11-16",
        "period_end": "2026-11-22",
        "documents": 4,
        "closed_documents": 3,
        "other_documents": 1,
        "product_count": 2,
        "total_observed_un": 5,
        "stock_snapshot_at": "2026-11-18T01:07:47+00:00",
        "stock_snapshot_products": 245,
        "stock_is_live": False,
        "ready_for_replenishment": False,
        "fiscal_real_enabled": False,
        "rows": [
            {"product_id": "101", "internal_code": "FL-101", "observed_un": 3, "stock_snapshot": 0},
            {"product_id": "102", "internal_code": None, "observed_un": 2, "stock_snapshot": None},
        ],
    }


def test_conferencia_valida_ultima_fotografia_sem_calcular_saldo():
    out = conferir_observacao(payload(), "LOJA_FICTICIA")
    assert out.closed_documents == 3
    assert out.total_observed_un == Decimal("5")
    assert out.stock_snapshot_products == 245
    assert [x.product_id for x in out.rows] == ["101", "102"]
    assert out.rows[0].internal_code == "FL-101"
    assert out.rows[0].stock_snapshot == Decimal(0)
    assert out.rows[1].stock_snapshot is None


def test_sem_observacao_retorna_ausencia_explicita():
    assert conferir_observacao({"found": False, "store_id": "LOJA_FICTICIA"}, "LOJA_FICTICIA") is None


@pytest.mark.parametrize("altera", [
    lambda p: p.update(store_id="OUTRA_LOJA"),
    lambda p: p.update(ready_for_replenishment=True),
    lambda p: p.update(stock_is_live=True),
    lambda p: p.update(fiscal_real_enabled=True),
    lambda p: p.update(total_observed_un="999"),
    lambda p: p.update(product_count=5),
    lambda p: p.update(closed_documents=4),
    lambda p: p.update(stock_snapshot_at="2026-11-18"),
    lambda p: p["rows"][0].update(product_id="=1+1"),
    lambda p: p["rows"][0].update(product_id="102"),
    lambda p: p["rows"][0].update(internal_code="=SUM(1,1)"),
    lambda p: p["rows"][0].update(internal_code="+PAYLOAD"),
    lambda p: p["rows"][0].update(internal_code="  @CMD"),
    lambda p: p["rows"][0].update(internal_code="a\\nb"),
    lambda p: p["rows"][0].update(observed_un=-1),
    lambda p: p["rows"][0].update(stock_snapshot=-1),
    lambda p: p["rows"][0].update(observed_un="NaN"),
    lambda p: p["rows"][0].update(observed_un="1.0000001"),
])
def test_rejeita_dados_inconsistentes(altera):
    raw = deepcopy(payload())
    altera(raw)
    with pytest.raises(ContratoInvalido):
        conferir_observacao(raw, "LOJA_FICTICIA")


def test_csv_so_observacao_e_fotografia_sem_execucao():
    data = conferir_observacao(payload(), "LOJA_FICTICIA")
    csv = exportar_conferencia_csv(data)
    assert csv.startswith(bytes((0xEF, 0xBB, 0xBF)))
    texto = csv.decode("utf-8-sig")
    assert "101;FL-101;3;0;" in texto
    assert "102;;2;;" in texto
    for proibido in ("cliente", "cpf", "cnpj", "chave_nfe", "valor", "sugerir"):
        assert proibido not in texto.lower()


def test_rejeita_cobertura_falsa_no_payload():
    raw = payload()
    raw["rows"].append({"product_id": "103", "internal_code": "FL-103", "observed_un": 1, "stock_snapshot": 4})
    with pytest.raises(ContratoInvalido):
        conferir_observacao(raw, "LOJA_FICTICIA")
