"""Limites comuns da janela histórica consultável, sem acessar banco ou ERP."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

MAX_HISTORY_DAYS = 732


def _data_civil(value: str | date) -> date:
    """Recusa horários e formatos ambíguos antes de qualquer leitura externa."""
    if type(value) is date:
        return value
    if not isinstance(value, str):
        raise ValueError("Informe uma data civil em AAAA-MM-DD, sem horário.")
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise ValueError("Informe uma data civil em AAAA-MM-DD, sem horário.") from None
    if result.isoformat() != value:
        raise ValueError("Informe uma data civil em AAAA-MM-DD, sem horário.")
    return result


def validar_periodo(start: str | date, end: str | date, *, today: date | None = None) -> tuple[date, date]:
    """Valida até 732 dias inclusivos, na mesma data civil usada pela operação.

    Uma janela que começa e termina no mesmo dia contém um dia. O limite
    temporal não amplia os limites de linhas, páginas ou tempo da API; uma
    carga volumosa precisa ser dividida em lotes menores pelo executor.
    O relógio injetável permite testar a virada do dia sem realizar consultas.
    """
    first, last = _data_civil(start), _data_civil(end)
    current = datetime.now(ZoneInfo("America/Fortaleza")).date() if today is None else _data_civil(today)
    if first > last:
        raise ValueError("A data inicial deve ser anterior ou igual à data final.")
    if last > current:
        raise ValueError("O período não pode incluir datas futuras.")
    if (last - first).days + 1 > MAX_HISTORY_DAYS:
        raise ValueError(f"Escolha um período de até {MAX_HISTORY_DAYS} dias, incluindo as datas inicial e final.")
    return first, last
