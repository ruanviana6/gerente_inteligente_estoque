"""Regressões da amostra fictícia: datas seguras e linhas estáveis entre consultas."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from analytics.demo import gerar_vendas_ficticias
from analytics.sales import analisar_vendas, normalizar_linhas
from dados import hoje_local

SOURCE = "fonte-exclusivamente-demo"
STORE = "loja-exclusivamente-demo"


@pytest.fixture(autouse=True)
def relogio_operacional():
    """Fixa somente a fronteira de relógio, preservando a validação produtiva."""
    instante = datetime(2026, 11, 15, 8, tzinfo=timezone.utc)
    with patch("sincronizacao.periodos.datetime") as relogio:
        relogio.now.side_effect = lambda zona: instante.astimezone(zona)
        yield relogio


@pytest.mark.parametrize("dias", [1, 366, 367, 731, 732])
def test_demo_respeita_janela_inclusiva_compartilhada(dias):
    """A demonstração aceita a mesma janela ampliada do coordenador de sincronização."""
    inicio = date(2024, 1, 1)
    fim = inicio + timedelta(days=dias - 1)
    linhas = gerar_vendas_ficticias(SOURCE, STORE, inicio, fim)
    assert len(linhas) == dias * 5
    assert len({item["document_id"] for item in linhas}) == dias * 3
    assert linhas[0]["sale_date"] == inicio.isoformat()
    assert linhas[-1]["sale_date"] == fim.isoformat()
    assert len({(item["document_id"], item["line_id"]) for item in linhas}) == len(linhas)


@pytest.mark.parametrize("inicio,fim", [
    (date(2024, 1, 1), date(2024, 1, 1) + timedelta(days=732)),
    (date(2024, 1, 2), date(2024, 1, 1)),
    (date(2026, 11, 15), date(2026, 11, 16)),
    (datetime(2024, 1, 1), date(2024, 1, 1)),
    (None, date(2024, 1, 1)),
    ("20240101", "2024-01-01"),
])
def test_demo_recusa_periodo_excessivo_invertido_futuro_ou_ambiguo(inicio, fim):
    """Entradas inválidas são recusadas antes de produzir qualquer lote fictício."""
    with pytest.raises(ValueError):
        gerar_vendas_ficticias(SOURCE, STORE, inicio, fim)


@pytest.mark.parametrize("inicio,fim,recorte_inicio,recorte_fim", [
    (date(2026, 8, 1), date(2026, 9, 30), date(2026, 9, 1), date(2026, 9, 30)),
    (date(2024, 2, 1), date(2024, 3, 31), date(2024, 2, 28), date(2024, 3, 3)),
    (date(2024, 12, 1), date(2025, 1, 31), date(2024, 12, 31), date(2025, 1, 15)),
])
def test_periodos_sobrepostos_mantem_payload_inteiro_e_versao(inicio, fim, recorte_inicio, recorte_fim):
    """Consultar a mesma linha em outra janela não inventa uma alteração da origem."""
    ampla = gerar_vendas_ficticias(SOURCE, STORE, inicio, fim)
    recorte = gerar_vendas_ficticias(SOURCE, STORE, recorte_inicio, recorte_fim)
    assert recorte == [item for item in ampla
                       if recorte_inicio.isoformat() <= item["sale_date"] <= recorte_fim.isoformat()]
    normalizadas, duplicadas = normalizar_linhas(ampla + recorte)
    assert len(normalizadas) == len(ampla)
    assert duplicadas == len(recorte)


@pytest.mark.parametrize("instante", [
    datetime(2026, 11, 15, 2, 59, 59, tzinfo=timezone.utc),
    datetime(2026, 11, 15, 3, tzinfo=timezone.utc),
    datetime(2026, 11, 15, 8, tzinfo=timezone.utc),
    datetime(2026, 11, 15, 17, 54, tzinfo=timezone.utc),
    datetime(2026, 11, 15, 23, 59, 59, tzinfo=timezone.utc),
])
def test_dia_atual_nao_gera_timestamp_futuro_em_qualquer_horario(instante, relogio_operacional):
    """O horário de origem é válido desde a meia-noite local, antes das antigas 18h UTC."""
    relogio_operacional.now.side_effect = lambda zona: instante.astimezone(zona)
    with patch("dados.datetime") as relogio_da_interface:
        relogio_da_interface.now.side_effect = lambda zona: instante.astimezone(zona)
        hoje = hoje_local().date()
    assert hoje == instante.astimezone(ZoneInfo("America/Fortaleza")).date()
    linhas = gerar_vendas_ficticias(SOURCE, STORE, hoje - timedelta(days=1), hoje)
    assert linhas[-1]["sale_date"] == hoje.isoformat()
    assert all(datetime.fromisoformat(item["source_updated_at"]) <= instante for item in linhas)
    assert all(item["source_updated_at"] == f'{item["sale_date"]}T03:00:00+00:00' for item in linhas)
    with pytest.raises(ValueError, match="futuras"):
        gerar_vendas_ficticias(SOURCE, STORE, hoje, hoje + timedelta(days=1))


def test_janela_366_dias_nao_deixa_lote_final_com_instante_futuro():
    """As últimas 30 das 1.830 linhas também passam pela guarda temporal do staging."""
    instante = datetime(2026, 11, 15, 8, tzinfo=timezone.utc)
    hoje = instante.astimezone(ZoneInfo("America/Fortaleza")).date()
    linhas = gerar_vendas_ficticias(SOURCE, STORE, hoje - timedelta(days=365), hoje)
    assert len(linhas) == 1830
    assert len(linhas[1800:]) == 30
    assert all(datetime.fromisoformat(item["source_updated_at"]) <= instante + timedelta(minutes=5)
               for item in linhas[1800:])


def test_demo_preserva_cestas_amostras_e_variacao_mensal():
    """O ciclo fictício continua útil para demonstrar crescimento e produtos associados."""
    inicio, fim = date(2026, 8, 1), date(2026, 9, 30)
    linhas = gerar_vendas_ficticias(SOURCE, STORE, inicio, fim)
    assert linhas == gerar_vendas_ficticias(SOURCE, STORE, inicio.isoformat(), fim.isoformat())
    agosto = [item["quantity"] for item in linhas if item["product_id"] == "DEMO-A"
              and item["sale_date"].startswith("2026-08")]
    setembro = [item["quantity"] for item in linhas if item["product_id"] == "DEMO-A"
                and item["sale_date"].startswith("2026-09")]
    assert min(setembro) > max(agosto) > 0
    resultado = analisar_vendas(linhas, date(2026, 9, 1), fim, coverage_start=inicio, coverage_end=fim)
    assert resultado["resumo"]["pedidos_concluidos"] == 90
    assert resultado["cestas"]["pares"]


def test_ciclo_ficticio_reinicia_no_calendario_sem_mudar_versoes():
    """A queda de dezembro para janeiro é uma propriedade declarada da amostra."""
    linhas = gerar_vendas_ficticias(SOURCE, STORE, date(2024, 12, 31), date(2025, 1, 1))
    dezembro = [item["quantity"] for item in linhas if item["product_id"] == "DEMO-A"
                and item["sale_date"] == "2024-12-31"]
    janeiro = [item["quantity"] for item in linhas if item["product_id"] == "DEMO-A"
               and item["sale_date"] == "2025-01-01"]
    assert dezembro == [12, 12]
    assert janeiro == [1, 1]
