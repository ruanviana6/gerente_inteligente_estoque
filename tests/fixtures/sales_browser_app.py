"""Páginas reais com dados DEMO, exclusivamente para Playwright e sem rede externa."""
from __future__ import annotations

import os
import sys
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
SOURCE = "30000000-0000-4000-8000-000000000001"
RUN = "50000000-0000-4000-8000-000000000001"
TOKEN = "60000000-0000-4000-8000-000000000001"


class SessaoDEMO:
    """Fornece apenas RPC fictícia; serviços, páginas e cálculos são os produtivos."""

    def __init__(self):
        """Cria staging e identidade isolados, sem credenciais."""
        from web.auth import AccessContext, Profile

        self.context = AccessContext(Profile("master-demo", "org-demo", "Master DEMO", "Master", True),
                                    frozenset({"trends.view", "integrations.manage"}))
        self.generation = 1
        self.run = None
        self.pages = {}
        self.commits = 0
        self.cancels = 0

    def require(self, permission):
        """Recusa qualquer permissão não incluída no cenário de teste."""
        from web.errors import AuthorizationError

        if not self.context.can(permission):
            raise AuthorizationError("Permissão DEMO negada.")
        return self.context

    def operation(self):
        """Mantém o contrato por sessão sem cache global."""
        return nullcontext()

    def rpc(self, name, payload, permission):
        """Responde somente aos endpoints usados pelas duas páginas, sem HTTP."""
        from analytics.demo import gerar_vendas_ficticias

        self.require(permission)
        if name == "integration_sources":
            return [{"id": SOURCE, "name": "DEMO: vendas inteiramente fictícias", "active": True,
                     "stores": ["DEMO"]}]
        if name == "sales_payload":
            first, last = date.fromisoformat(payload["p_start"]), date.fromisoformat(payload["p_end"])
            return {"source_id": SOURCE, "store_id": "DEMO",
                    "rows": gerar_vendas_ficticias(SOURCE, "DEMO", first, last), "metadata": {
                "coverage_start": first.isoformat(), "coverage_end": last.isoformat(), "coverage_verified": True,
            }}
        if name in {"integration_register_source", "sync_authorize_source"}:
            return {"id": SOURCE}
        if name == "sync_begin":
            self.pages = {}
            self.run = {"id": RUN, "claim_token": TOKEN, "status": "staging", "checkpoint": 0,
                        "row_count": payload["p_expected_rows"], "net_quantity": payload["p_expected_quantity"],
                        "period_start": payload["p_period_start"], "period_end": payload["p_period_end"]}
        elif name == "sync_stage":
            self.pages[payload["p_page"]] = list(payload["p_rows"])
            self.run["checkpoint"] = max(self.pages)
        elif name == "sync_preview":
            return {**self.run, "changed_rows": self.run["row_count"],
                    "sample": [row for rows in self.pages.values() for row in rows][:5]}
        elif name == "sync_commit":
            assert payload["p_coverage_confirmed"] is True
            self.commits += 1
            self.run["status"] = "completed"
        elif name == "sync_cancel":
            self.cancels += 1
            self.pages = {}
            self.run["status"] = "cancelled"
        else:
            raise AssertionError("RPC fora do contrato exclusivo do navegador DEMO.")
        return dict(self.run)


def executar():
    """Monta as páginas verdadeiras com o mesmo contêiner de tema da aplicação."""
    if os.environ.get("GERENTE_SALES_BROWSER_FIXTURE") != "1":
        raise RuntimeError("Entrada exclusiva de teste. Use app.py na aplicação.")
    import streamlit as st

    from web.integration_page import integracoes
    from web.sales_page import vendas
    from web.theme import apply_theme, brand_header

    st.set_page_config(page_title="DEMO fictícia: vendas e integrações", layout="wide")
    apply_theme()
    if "_sales_browser_auth" not in st.session_state:
        st.session_state["_sales_browser_auth"] = SessaoDEMO()
    auth = st.session_state["_sales_browser_auth"]
    with st.sidebar:
        brand_header(compact=True)
        page = st.radio("Navegação DEMO", ["Inteligência de Vendas", "Integrações"])
    with st.container(key="flatter_app"):
        st.caption("DEMO EXCLUSIVA DE TESTE: dados fictícios, RPC simulada e nenhuma chamada a ERP ou Supabase.")
        service = SimpleNamespace(auth=auth)
        if page == "Inteligência de Vendas":
            vendas(service, auth.context)
        else:
            integracoes(service, auth.context)
        st.caption(f"Controle DEMO: confirmações={auth.commits}; cancelamentos={auth.cancels}")


if __name__ == "__main__":
    executar()
