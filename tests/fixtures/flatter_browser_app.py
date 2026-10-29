"""Hospeda a UI verdadeira com transporte fictício, exclusivamente para Playwright.

Esta entrada nunca é importada por app.py e recusa execução sem o marcador de
teste. O navegador não recebe Secrets, tokens válidos ou dados de uma empresa.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).parent))


def executar() -> None:
    """Injeta somente no teste uma fábrica que preserva AuthService e Service reais."""
    if os.environ.get("GERENTE_BROWSER_FIXTURE") != "1":
        raise RuntimeError("Entrada exclusiva dos testes de navegador. Use app.py na aplicação.")

    import streamlit as st
    from flatter_data import Scenario

    from web.ui import run_app

    def fabrica(estado):
        """Mantém cenário e cliente exclusivos de cada sessão fictícia do navegador."""
        if "_flatter_test_service" in estado:
            return estado["_flatter_test_service"]
        if "_flatter_browser_scenario" not in estado:
            perfil = st.query_params.get("perfil", "Master")
            if perfil not in {"Master", "Administrador", "Operador", "Visualizador"}:
                raise ValueError("Perfil de teste desconhecido.")
            scenario = Scenario(size=100, role=perfil, authenticated=False)
            permissoes_iniciais = set(scenario.permissions)
            transporte = scenario.transport

            def transporte_controlado(method, url, headers, payload, timeout):
                """Simula revogação externa sem botões ou atalhos no código produtivo."""
                controle = json.loads(Path(os.environ["GERENTE_BROWSER_CONTROL"]).read_text())
                scenario.active = perfil not in controle.get("desativados", [])
                scenario.permissions = set(permissoes_iniciais)
                if perfil in controle.get("sem_financeiro", []):
                    scenario.permissions.discard("finance.view")
                if perfil in controle.get("sem_exportacao", []):
                    scenario.permissions.difference_update({"reports.export", "replenishment.export"})
                return transporte(method, url, headers, payload, timeout)

            scenario.transport = transporte_controlado
            estado["_flatter_browser_scenario"] = scenario
        return estado["_flatter_browser_scenario"].service_factory(estado)

    run_app(service_factory=fabrica)


if __name__ == "__main__":
    executar()
