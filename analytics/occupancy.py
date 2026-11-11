"""Avisos sobre medições reais, sem inferir espaço livre a partir de contagens."""

from __future__ import annotations


def avisos_ocupacao(database_bytes: int, storage_bytes: int | None, *, database_limit: int = 500_000_000,
                   storage_limit: int = 1_000_000_000, thresholds=(0.60, 0.75, 0.85)) -> list[dict]:
    """Compara bytes conhecidos com referências Free, que devem ser conferidas no painel."""
    if (any(type(v) is not int or v <= 0 for v in (database_limit, storage_limit))
            or type(database_bytes) is not int or database_bytes < 0
            or (storage_bytes is not None and (type(storage_bytes) is not int or storage_bytes < 0))
            or not thresholds or any(isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 < t <= 1 for t in thresholds)):
        raise ValueError("Medição, cotas ou limiares inválidos.")
    result = []
    for name, used, limit in (("PostgreSQL", database_bytes, database_limit), ("Storage", storage_bytes, storage_limit)):
        if used is not None:
            crossed = [t for t in thresholds if used / limit >= t]
            if crossed:
                result.append({"resource": name, "fraction": used / limit, "threshold": max(crossed)})
    return result
