"""Confere publicação minimizada, disponibilidade e custo de ocupação."""

from datetime import date, timedelta

import pytest
from test_comercial import linha

from analytics.commercial_pipeline import preparar_publicacao, preparar_serie_regional
from analytics.occupancy import avisos_ocupacao


def test_publicacao_idempotente_preserva_evidencias_sem_detalhes_pessoais():
    linhas = [linha(dia, "cliente", str(i), quantidade=q) for i, (dia, q) in enumerate([
        ("2026-01-05", 5), ("2026-01-12", 5), ("2026-01-19", 5), ("2026-01-26", 5), ("2026-02-02", 2)])]
    args = dict(source="origem-demo", store="loja-demo", coverage_start=date(2026, 1, 1),
                coverage_end=date(2026, 2, 28), city_basis="delivery")
    a = preparar_publicacao(linhas, date(2026, 2, 1), date(2026, 2, 28), **args)
    b = preparar_publicacao(list(reversed(linhas)), date(2026, 2, 1), date(2026, 2, 28), **args)
    assert a == b
    item = next(r for r in a["items"] if r["alert_code"] == "volume_reduzido")
    assert item["current_units"] == 2 and item["previous_units"] == 20
    assert item["median_gap_days"] == 7
    assert "document_id" not in str(a) and "cliente" not in str(a)
    with pytest.raises(ValueError, match="origem definidas"):
        preparar_publicacao(linhas, date(2026, 2, 1), date(2026, 2, 28), **(args | {"city_basis": "unknown"}))


def test_pipeline_nao_mistura_fontes_nem_devolve_resultado_parcial():
    args = dict(source="origem-demo", store="loja-demo", coverage_start=date(2026, 1, 1),
                coverage_end=date(2026, 2, 28), city_basis="delivery")
    with pytest.raises(ValueError, match="outra fonte"):
        preparar_publicacao([linha("2026-02-02", "c", "d") | {"store_id": "outra"}], date(2026, 2, 1), date(2026, 2, 28), **args)
    with pytest.raises(ValueError, match="abrangência"):
        preparar_publicacao([], date(2026, 2, 1), date(2026, 2, 28), **(args | {"coverage_start": date(2026, 2, 1)}))


def test_serie_regional_nao_transforma_indisponibilidade_em_zero():
    inicio, fim = date(2025, 1, 1), date(2025, 6, 29)
    dias = {inicio + timedelta(days=i) for i in range(180)}
    linhas = [linha(dia.isoformat(), str(c), f"{dia}-{c}") for dia in sorted(dias) for c in range(3)]
    args = dict(source="origem-demo", store="loja-demo", city_code="2304400", sku="001",
                coverage_start=inicio, coverage_end=fim)
    serie = preparar_serie_regional(linhas, inicio, fim, dias_disponiveis=dias, **args)
    assert len(serie) == 180 and all(v == 6 for _, v in serie)
    with pytest.raises(ValueError, match="Disponibilidade"):
        preparar_serie_regional(linhas, inicio, fim, dias_disponiveis=dias - {inicio}, **args)
    with pytest.raises(ValueError, match="privacidade"):
        preparar_serie_regional([r for r in linhas if r["document_id"].endswith("-0")], inicio, fim,
                               dias_disponiveis=dias, **args)


def test_ocupacao_desconhecida_nao_e_zero_e_nao_dispara_alerta_inventado():
    assert avisos_ocupacao(13_080_243, None) == []
    avisos = avisos_ocupacao(450_000_000, None)
    assert avisos == [{"resource": "PostgreSQL", "fraction": 0.9, "threshold": 0.85}]
    with pytest.raises(ValueError):
        avisos_ocupacao(True, 0)
