"""Verifica leitura paginada e negações na única área de inteligência comercial."""

from copy import deepcopy

import pytest
from test_sales_interface import SOURCE, STORE, AuthVendasFicticio, ServicoVendasFicticio
from test_web_interface import abrir

from web.commercial_page import comercial
from web.errors import AuthorizationError
from web.service import Service


class AuthComercialFicticio(AuthVendasFicticio):
    """Registra RPC de resultados prontos; qualquer consulta bruta falha no teste."""

    def rpc(self, name, payload, permission):
        """Responde com dados fictícios sem treinamento, HTTP ou consultas ao ERP."""
        self.require(permission)
        self.calls.append((name, deepcopy(payload), permission))
        if name == "commercial_sources":
            result = [{"id": SOURCE, "name": "Fonte DEMO", "stores": [STORE], "last_sync": "2026-09-30"}]
        elif name == "commercial_insights":
            result = self.servico.insights
        elif name == "storage_history" or name == "commercial_model_status":
            result = []
        else:
            raise AssertionError("RPC inesperada; a navegação só pode consultar resultados prontos.")
        if self.after_rpc:
            self.after_rpc(name)
        return deepcopy(result)


class ServicoComercialFicticio(ServicoVendasFicticio):
    """Injeta transporte mantendo a fachada verdadeira de autorização comercial."""

    commercial_sources = Service.commercial_sources
    commercial_insights = Service.commercial_insights
    storage_history = Service.storage_history
    commercial_model_status = Service.commercial_model_status

    def __init__(self, permissoes=("commercial.view", "commercial.customer_detail")):
        """Cria uma amostra comprovadamente fictícia com evidência numérica."""
        super().__init__(permissoes)
        self.auth = AuthComercialFicticio(self)
        self.insights = [{"dimension": "customer", "alert_code": "volume_reduzido", "customer_ref": "a"*64,
                          "period_start": "2026-09-01", "period_end": "2026-09-30", "current_units": 5,
                          "previous_units": 20, "current_orders": 2, "previous_orders": 4,
                          "median_gap_days": 7, "recency_days": 14, "mix_similarity": 0.3,
                          "computed_at": "2026-10-01T10:00:00Z", "city_basis": "delivery"}]


def abrir_comercial(monkeypatch, servico):
    """Executa a interface inteira, com login e contexto por sessão do teste."""
    import web.ui

    monkeypatch.setattr(web.ui, "PAGES", {"Inteligência Comercial": ("commercial.view", comercial)})
    return abrir(monkeypatch, servico)[0]


def test_uma_area_usa_apenas_resultados_paginados(monkeypatch):
    servico = ServicoComercialFicticio()
    app = abrir_comercial(monkeypatch, servico)
    assert not app.exception
    assert next(r for r in app.radio if r.label == "Visão comercial").options == ["Clientes", "Cidades", "Produtos Juntos"]
    assert [c[0] for c in servico.auth.calls] == ["commercial_sources", "commercial_insights"]
    assert any("regra estatística" in c.value for c in app.caption)
    next(r for r in app.number_input if r.label == "Página de insights").set_value(2).run()
    assert not app.exception
    assert servico.auth.calls[-1][1]["p_offset"] == 10
    assert not any(c[0] in {"sales_payload", "sync_begin", "save_commercial_model"} for c in servico.auth.calls)


def test_sem_permissao_de_cliente_nao_busca_resultados(monkeypatch):
    servico = ServicoComercialFicticio(("commercial.view",))
    app = abrir_comercial(monkeypatch, servico)
    assert not app.exception
    assert not servico.auth.calls
    with pytest.raises(AuthorizationError):
        servico.commercial_insights(SOURCE, STORE, "customer")


@pytest.mark.parametrize("mudanca", ["sessao", "permissao", "usuario"])
def test_revogacao_durante_leitura_nao_apresenta_insights(monkeypatch, mudanca):
    servico = ServicoComercialFicticio()
    def alterar(name):
        """Altera o acesso imediatamente após a resposta de dados do banco."""
        if name != "commercial_insights":
            return
        if mudanca == "sessao":
            servico.auth.generation += 1
        elif mudanca == "permissao":
            servico.permissoes = frozenset()
        else:
            servico.usuario = "00000000-0000-4000-8000-000000000999"
    servico.auth.after_rpc = alterar
    app = abrir_comercial(monkeypatch, servico)
    assert not app.exception
    assert not any("Volume de compras diminuiu" in item.value for item in app.markdown)
    assert any(b.label == "Entrar" for b in app.button)


def test_ocupacao_nao_pode_ser_lida_por_nome_de_perfil_ou_permissao_forjada():
    servico = ServicoComercialFicticio(("commercial.manage",))
    with pytest.raises(AuthorizationError):
        servico.storage_history()
    assert not servico.auth.calls
