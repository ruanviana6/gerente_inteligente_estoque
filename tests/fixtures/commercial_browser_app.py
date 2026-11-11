"""Entrada exclusiva do navegador de testes; não é um modo público do aplicativo."""

from __future__ import annotations

import os
import sys
from contextlib import nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class SessaoComercialDEMO:
    """Entrega resultados fixos e fictícios através da fachada comercial verdadeira."""

    generation = 1

    def __init__(self):
        """Prepara uma identidade de teste sem token ou chave administrativa."""
        from web.auth import AccessContext, Profile

        self.ctx = AccessContext(Profile("00000000-0000-4000-8000-000000000011",
            "00000000-0000-4000-8000-000000000012", "Pessoa DEMO", "Operador", True),
            frozenset({"commercial.view", "commercial.customer_detail"}))

    def context(self):
        """Não consulta rede; os testes Auth/PostgREST são executados separadamente."""
        return self.ctx

    def require(self, permission):
        """Mantém o contrato de autorização da fachada sem bypass no app.py."""
        if not self.ctx.can(permission):
            from web.errors import AuthorizationError
            raise AuthorizationError("Acesso DEMO negado.")
        return self.ctx

    def operation(self):
        """Fornece o escopo do fake, que não usa threads nem sessão remota."""
        return nullcontext()

    def rpc(self, name, payload, permission):
        """Recusa qualquer RPC de dados brutos, treino, sincronização ou ERP."""
        self.require(permission)
        if name == "commercial_sources":
            return [{"id": "30000000-0000-4000-8000-000000000011", "name": "Fonte DEMO fictícia",
                     "stores": ["DEMO"], "last_sync": "2026-10-01T10:00:00Z"}]
        if name != "commercial_insights":
            raise AssertionError("A navegação chamou uma rotina que não é leitura de resumo.")
        base = {"period_start": "2026-09-01", "period_end": "2026-09-30", "current_units": 10,
                "previous_units": 50, "current_orders": 2, "previous_orders": 6,
                "computed_at": "2026-10-01T10:00:00Z", "city_basis": "delivery"}
        if payload["p_dimension"] == "customer":
            return [base | {"customer_ref": "a"*64, "alert_code": "volume_reduzido", "median_gap_days": 7,
                            "recency_days": 14, "mix_similarity": 0.3}]
        if payload["p_dimension"] == "city":
            return [base | {"city_code": "2304400", "city_name": "Cidade DEMO", "state": "CE",
                            "alert_code": "cidade_queda", "group_customers": 5, "previous_customers": 6}]
        return [base | {"sku_a": "DEMO-A", "sku_b": "DEMO-B", "alert_code": "produtos_juntos", "current_orders": 12,
                        "total_orders": 30, "support": 0.4, "confidence": 0.6, "lift": 1.2}]


def executar():
    """Renderiza o componente de produção somente se o runner isolado foi ativado."""
    if os.environ.get("GERENTE_COMMERCIAL_BROWSER_FIXTURE") != "1":
        raise RuntimeError("Entrada exclusiva de teste; utilize app.py no Streamlit Cloud.")
    import streamlit as st

    from web.commercial_page import comercial
    from web.service import Service
    from web.theme import apply_theme, brand_header

    st.set_page_config(page_title="DEMO comercial", layout="wide")
    apply_theme()
    brand_header()
    st.caption("DEMONSTRAÇÃO DE TESTE: todos os dados desta página são fictícios.")
    if "_sessao_comercial_demo" not in st.session_state:
        st.session_state["_sessao_comercial_demo"] = SessaoComercialDEMO()
    servico = Service(st.session_state["_sessao_comercial_demo"])
    comercial(servico, servico.context())


if __name__ == "__main__":
    executar()
