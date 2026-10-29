"""Tempos opcionais sem conteúdo de usuário, identidade, credenciais ou custos.

As etapas são inclusivas: o total de uma operação já contém as subetapas.
Cada escopo pertence à execução atual e não armazena dados entre sessões.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

STAGES = frozenset({
    "auth_user", "app_context", "load_inventory", "motor", "export", "render", "bootstrap", "rerun",
})
LABELS = frozenset({"bootstrap", "rerun", "export", "watcher"})
LOGGER = logging.getLogger("gerente.desempenho")


@dataclass
class _Measurement:
    """Acumula apenas números e nomes de etapas previamente aprovados."""

    seconds: Counter = field(default_factory=Counter)
    counts: Counter = field(default_factory=Counter)


_current: ContextVar[_Measurement | None] = ContextVar("gerente_performance", default=None)


@contextmanager
def measure(stage: str) -> Iterator[None]:
    """Cronometra uma etapa fixa somente quando um escopo habilitado está ativo."""
    if stage not in STAGES:
        raise ValueError("Etapa de desempenho desconhecida.")
    current = _current.get()
    if current is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        current.seconds[stage] += time.perf_counter() - started
        current.counts[stage] += 1


@contextmanager
def performance_scope(
    label: str = "rerun", *, enabled: bool | None = None,
    sink: Callable[[dict], None] | None = None,
) -> Iterator[None]:
    """Registra métricas sanitizadas; GERENTE_PERFORMANCE_LOG=true habilita logs.

    O destino injetável serve aos testes. Escopos aninhados compartilham somente
    os contadores da execução corrente. Não se registram mensagens de exceção,
    argumentos de funções, URL, conteúdo exportado nem identificadores.
    """
    if label not in LABELS:
        raise ValueError("Operação de desempenho desconhecida.")
    active = os.environ.get("GERENTE_PERFORMANCE_LOG", "").lower() == "true" if enabled is None else enabled
    if not active or _current.get() is not None:
        yield
        return
    current = _Measurement()
    token = _current.set(current)
    started = time.perf_counter()
    try:
        yield
    finally:
        _current.reset(token)
        record = {
            "operation": label,
            "total_ms": round((time.perf_counter() - started) * 1000, 3),
            "stages": {
                stage: {"count": current.counts[stage], "ms": round(seconds * 1000, 3)}
                for stage, seconds in sorted(current.seconds.items())
            },
        }
        if sink is not None:
            sink(record)
        else:
            LOGGER.warning("desempenho %s", json.dumps(record, sort_keys=True))
