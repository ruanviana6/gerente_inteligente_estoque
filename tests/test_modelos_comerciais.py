"""Exercita guardrails temporais e comparação local com séries fictícias."""

from datetime import date, timedelta

import pytest

from analytics.commercial_models import (
    _features,
    _validar_serie,
    atualizar_previsao,
    avaliar_alertas_clientes,
    avaliar_modelo_temporal,
    inferir_alertas_clientes,
    monitorar_previsao,
)


def serie_ficticia():
    return [(date(2025, 1, 1) + timedelta(days=i), float((i % 7) + 1)) for i in range(210)]


def test_features_utilizam_somente_historico_passado():
    valores = list(range(30))
    atributos = _features(valores, date(2026, 1, 1))
    assert atributos[0] == 29 and atributos[1] == 23
    assert atributos[2] == sum(range(23, 30)) / 7


def test_modelo_recusa_lacunas_e_disponibilidade_desconhecida():
    serie = serie_ficticia()
    with pytest.raises(ValueError, match="disponibilidade"):
        _validar_serie(serie, False)
    with pytest.raises(ValueError, match="lacunas"):
        _validar_serie(serie[:80] + serie[81:], True)


def test_backtest_temporal_compara_baseline_em_quatro_horizontes():
    pytest.importorskip("sklearn")
    resultado = avaliar_modelo_temporal(serie_ficticia(), disponibilidade_comprovada=True)
    assert set(resultado["metricas"]) == {7, 14, 21, 30}
    assert resultado["selecionado"] in {"arvore", "media_7_dias"}
    assert len(resultado["cortes"]) == 3
    assert all(m["amostras"] == 3*h for h, m in resultado["metricas"].items())
    assert all(m["mae_baseline"] >= 0 and m["mae_modelo"] >= 0 for m in resultado["metricas"].values())


def test_serie_zero_nao_inventa_wape_nem_adota_modelo():
    pytest.importorskip("sklearn")
    serie = [(dia, 0.0) for dia, _ in serie_ficticia()]
    resultado = avaliar_modelo_temporal(serie, disponibilidade_comprovada=True)
    assert resultado["selecionado"] == "media_7_dias"
    assert all(m["wape_baseline"] is None for m in resultado["metricas"].values())


def _amostra_cliente(indice, base, observado=False):
    return {"customer_ref": f"{indice:064x}", "feature_end": base, "label_end": base + timedelta(days=30),
            "cadence_ratio": 2.0 if observado else 1.0, "frequency_ratio": 0.4 if observado else 1.0,
            "volume_ratio": 0.2 if observado else 1.0, "mix_similarity": 0.2 if observado else 0.9,
            "observado": observado}


def test_alertas_sem_rotulo_ou_amostra_usam_regra():
    resultado = avaliar_alertas_clientes([], [], rotulos_confirmados=False)
    assert resultado["selecionado"] == "regra"
    assert "probabilidade" not in resultado


def test_alertas_exigem_separacao_de_clientes_e_datas():
    treino = [_amostra_cliente(i, date(2025, 1, 1)) for i in range(50)]
    validacao = [_amostra_cliente(i, date(2025, 3, 1)) for i in range(20)]
    with pytest.raises(ValueError, match="separar clientes"):
        avaliar_alertas_clientes(treino, validacao, rotulos_confirmados=True)


def test_alertas_com_rotulos_ficticios_comparam_modelo_e_baseline():
    pytest.importorskip("sklearn")
    treino = [_amostra_cliente(i, date(2025, 1, 1), i % 10 == 0) for i in range(60)]
    validacao = [_amostra_cliente(i + 100, date(2025, 3, 1), i % 4 == 0) for i in range(24)]
    resultado = avaliar_alertas_clientes(treino, validacao, rotulos_confirmados=True)
    assert resultado["selecionado"] in {"regra", "isolation_forest"}
    assert resultado["baseline"]["precisao"] == 1
    assert "abandono" in resultado["limitacao"]


def test_inferencia_simples_funciona_sem_treino_e_nao_cria_probabilidade():
    atual = {k: v for k, v in _amostra_cliente(100, date(2026, 1, 1), True).items()
             if k not in {"label_end", "observado"}}
    resultado = inferir_alertas_clientes([], [], [atual], rotulos_confirmados=False)
    assert resultado["alerts"][0]["alert"] is True
    assert resultado["alerts"][0]["method"] == "regra"
    assert "probabilidade" not in str(resultado)


def test_atualizacao_temporal_gera_manifesto_compacto_e_30_previsoes():
    pytest.importorskip("sklearn")
    resultado = atualizar_previsao(serie_ficticia(), disponibilidade_comprovada=True)
    assert len(resultado["predictions"]) == 30
    assert resultado["predictions"][0]["date"] == "2025-07-30"
    assert len(resultado["data_hash"]) == 64
    assert "pickle" not in resultado and "model_object" not in resultado


def test_monitoramento_calcula_erro_observado_sem_usar_previsao_como_venda():
    resultado = monitorar_previsao([{"date": "2026-01-01", "units": 8}, {"date": "2026-01-02", "units": 12}],
                                  [(date(2026, 1, 1), 10), (date(2026, 1, 2), 10)])
    assert resultado == {"samples": 2, "mae": 2, "wape": 0.2, "bias": 0}
    assert monitorar_previsao([{"date": "2026-01-01", "units": 8}], []) == {
        "samples": 0, "mae": None, "wape": None, "bias": None}
