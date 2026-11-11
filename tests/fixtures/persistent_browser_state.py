"""Banco de transporte fictício, mantido só no processo descartável do navegador."""

import json
import os
from pathlib import Path

from test_persistent_sessions import PersistentTransport

BACKEND = PersistentTransport()


def transport(method, url, headers, payload, timeout):
    """Aplica somente controles de teste; nunca chama rede ou recebe Secrets reais."""
    control = json.loads(Path(os.environ["GERENTE_PERSISTENT_CONTROL"]).read_text())
    for context in BACKEND.contexts.values():
        context["profile"]["active"] = not control.get("deactivated", False)
    return BACKEND(method, url, headers, payload, timeout)
