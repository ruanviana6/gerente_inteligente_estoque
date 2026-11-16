"""Bordas da janela histórica ampliada, sem transporte externo nem banco."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from sincronizacao.periodos import MAX_HISTORY_DAYS, validar_periodo


@pytest.mark.parametrize("days", [1, 366, 367, 731, 732])
def test_janela_inclusiva_aceita_ate_732_dias(days):
    """A janela ampliada aceita ambos os extremos, inclusive em ano bissexto."""
    first = date(2020, 1, 1)
    last = first + timedelta(days=days - 1)
    assert validar_periodo(first.isoformat(), last, today=last) == (first, last)
    assert MAX_HISTORY_DAYS == 732


def test_733_dias_sao_recusados():
    """O dia adicional não atravessa o limite por confusão entre delta e contagem."""
    first = date(2020, 1, 1)
    last = first + timedelta(days=732)
    with pytest.raises(ValueError, match="732 dias"):
        validar_periodo(first, last, today=last)


@pytest.mark.parametrize("first,last", [
    ("2020-01-02", "2020-01-01"),
    ("2020-01-01", "2020-02-01"),
    ("20200101", "2020-01-31"),
    ("01/01/2020", "2020-01-31"),
    ("2020-01-01T00:00:00Z", "2020-01-31"),
    (datetime(2020, 1, 1), "2020-01-31"),
    (None, "2020-01-31"),
    (True, "2020-01-31"),
    ("2020-01-01", "2020-02-30"),
])
def test_janela_invalida_recusada(first, last):
    """Recusa inversão, futuro, entradas inválidas ou horários sem expor valores."""
    with pytest.raises(ValueError):
        validar_periodo(first, last, today=date(2020, 1, 31))


def test_relogio_operacional_usa_data_de_fortaleza():
    """Meia-noite UTC não libera o dia seguinte antes da virada local."""
    instant = datetime(2020, 2, 1, 1, tzinfo=timezone.utc)
    with patch("sincronizacao.periodos.datetime") as clock:
        clock.now.side_effect = lambda zone: instant.astimezone(zone)
        assert validar_periodo("2020-01-31", "2020-01-31") == (date(2020, 1, 31),) * 2
        with pytest.raises(ValueError, match="futuras"):
            validar_periodo("2020-01-31", "2020-02-01")
