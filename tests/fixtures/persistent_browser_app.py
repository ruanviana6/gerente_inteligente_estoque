"""Interface verdadeira e sessão cifrada com transporte exclusivamente fictício."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(Path(__file__).parent))


def executar():
    """Não pode ser ativado por URL, Secrets ou caminho do app.py produtivo."""
    if os.environ.get("GERENTE_PERSISTENT_BROWSER_FIXTURE") != "1":
        raise RuntimeError("Entrada exclusiva do teste descartável.")
    import streamlit as st
    from persistent_browser_state import BACKEND, transport
    from test_persistent_sessions import BACKEND_SECRET

    from web.api import SupabaseAPI
    from web.auth import AuthService
    from web.service import Service
    from web.settings import PROJECT_URL, Settings
    from web.ui import run_app

    def factory(state):
        """Cada conexão tem AuthService próprio; só o banco fictício é compartilhado."""
        if "_service" not in state:
            config = Settings(PROJECT_URL, "sb_publishable_exclusivamente_ficticia", backend_secret=BACKEND_SECRET)
            state["_service"] = Service(AuthService(SupabaseAPI(config, transport), lambda: BACKEND.now))
        return state["_service"]

    run_app(service_factory=factory)
    total = sum(c["path"].endswith("grant_type=password") for c in BACKEND.calls)
    st.caption(f"DEMO: entradas por senha = {total}")


if __name__ == "__main__":
    executar()
