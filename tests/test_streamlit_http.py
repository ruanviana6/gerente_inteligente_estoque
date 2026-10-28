"""Inicialização real do servidor somente em loopback, sem publicar o aplicativo."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


def test_streamlit_inicia_e_responde_http(tmp_path):
    """Confirma processo e saúde HTTP; a navegação é exercitada pelo AppTest."""
    raiz = Path(__file__).resolve().parents[1]
    with socket.socket() as reserva:
        reserva.bind(("127.0.0.1", 0))
        porta = reserva.getsockname()[1]
    ambiente = os.environ.copy()
    ambiente["PYTHONPATH"] = str(raiz)
    # O servidor e o cliente ficam na mesma máquina de teste, sem tráfego externo.
    with (tmp_path / "streamlit.log").open("w+") as log:
        processo = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", str(raiz / "app.py"),
             "--server.address=127.0.0.1", f"--server.port={porta}",
             "--server.headless=true", "--browser.gatherUsageStats=false"],
            cwd=tmp_path, env=ambiente, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            cliente = build_opener(ProxyHandler({}))
            limite = time.monotonic() + 30
            while time.monotonic() < limite:
                if processo.poll() is not None:
                    raise AssertionError("Streamlit terminou antes de responder ao teste HTTP.")
                try:
                    with cliente.open(f"http://127.0.0.1:{porta}/_stcore/health", timeout=1) as resposta:
                        if resposta.status == 200 and resposta.read().strip() == b"ok":
                            break
                except (URLError, TimeoutError):
                    time.sleep(0.2)
            else:
                raise AssertionError("Streamlit não respondeu dentro do prazo do teste.")
            with cliente.open(f"http://127.0.0.1:{porta}/", timeout=3) as resposta:
                assert resposta.status == 200
                assert b"Streamlit" in resposta.read()
        finally:
            processo.terminate()
            try:
                processo.wait(timeout=5)
            except subprocess.TimeoutExpired:
                processo.kill()
                processo.wait(timeout=5)
