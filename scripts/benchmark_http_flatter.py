"""Compara custo CPU de construir e reutilizar opener, sem abrir conexão de rede."""

import argparse
import json
import math
import platform
import statistics
import sys
import time
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web.api import _http_request, _HTTPTransport, _NoRedirect  # noqa: E402
from web.settings import PROJECT_URL  # noqa: E402


class Response:
    """Resposta constante que substitui exclusivamente o envio HTTP do benchmark."""

    status = 200

    def __enter__(self):
        """Abre o recurso fictício sem sockets."""
        return self

    def __exit__(self, *args):
        """Encerra a resposta constante."""
        return False

    def read(self, limit):
        """Entrega JSON válido sem medir qualquer serviço externo."""
        return b"{}"


def summary(samples):
    """Calcula percentis pelo método nearest-rank e separa o primeiro acesso."""
    values = sorted(samples)
    return {"samples": len(samples), "first_ms": samples[0],
            "p50_ms": statistics.median(samples), "p95_ms": values[math.ceil(len(values) * .95) - 1]}


def run(iterations):
    """Mede a mesma request com opener novo e com configuração por instância."""
    arguments = ("GET", PROJECT_URL + "/auth/v1/user", {"Authorization": "Bearer token_ficticio"}, None, 20)
    before, after = [], []
    current = _HTTPTransport()
    with patch.object(urllib.request.OpenerDirector, "open", return_value=Response()):
        for _ in range(iterations):
            start = time.perf_counter()
            _http_request(urllib.request.build_opener(_NoRedirect()), *arguments)
            before.append((time.perf_counter() - start) * 1000)
            start = time.perf_counter()
            current(*arguments)
            after.append((time.perf_counter() - start) * 1000)
    return {
        "environment": {"python": platform.python_version(), "platform": platform.system()},
        "method": "CPU local; envio substituído por resposta constante; não mede TLS remoto nem pooling",
        "before": summary(before), "after": summary(after),
    }


def main():
    """Emite somente métricas, sem headers, tokens ou conteúdo de respostas."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args()
    if not 10 <= args.iterations <= 10000:
        parser.error("Use de 10 a 10.000 repetições.")
    print(json.dumps(run(args.iterations), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
