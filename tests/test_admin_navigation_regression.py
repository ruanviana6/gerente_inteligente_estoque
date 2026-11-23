"""Regressões da composição administrativa real, com Auth/RPC sem rede.

O teste mantém os cabeçalhos, formulários, roteamento e coordenador produtivos.
Somente as fronteiras de dados e transporte recebem respostas fictícias.
"""

from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
from test_integration_interface import (
    CONFIRMACAO_DIAGNOSTICO,
    ORGANIZACAO_DIAGNOSTICO,
    AuthRPCFicticia,
    botao,
    checkbox,
    texto_interface,
)

from integracoes.gestaoclick import RespostaGestaoClick
from web.auth import AccessContext

SCRIPT = '''
import streamlit as st
from web.errors import AppError
from web.pages import _administracao
servico = st.session_state['_admin_service']
try:
    _administracao(servico, servico.auth.context)
except AppError:
    st.error('Acesso negado. Entre novamente ou consulte o Master.')
'''
PERMISSOES = frozenset({
    "integrations.manage", "inventory.import", "inventory.view", "parameters.edit", "audit.view",
})
AREAS = (
    ("Integrações", "Integrações"),
    ("Importação CSV", "Importar catálogo e vendas"),
    ("Regras de Reposição", "Regras de Reposição"),
    ("Auditoria", "Auditoria"),
)


class ServicoAdministracaoFicticio:
    """Isola os dados internos mantendo renderização e autorização verificáveis."""

    def __init__(self, *, papel="Master", permissoes=PERMISSOES):
        """Prepara uma organização e um produto declaradamente fictícios."""
        self.auth = AuthRPCFicticia()
        self.auth.context = AccessContext(replace(self.auth.context.profile, role=papel), permissoes)
        self.leituras = []
        self.importacoes = []

    def admin_list_users(self):
        """Fornece uma página inicial vazia sem acessar diretório de usuários."""
        self.leituras.append("usuarios")
        return []

    def load_inventory(self, permission):
        """Retorna o contrato mínimo de catálogo autorizado e seu contexto atual."""
        self.auth.require(permission)
        self.leituras.append(permission)
        produtos = pd.DataFrame([{
            "codigo": "DEMO-001", "nome": "Produto fictício", "prazo_entrega_dias": 10,
            "estoque_seguranca": 5, "cobertura_alvo_dias": 30,
        }])
        return {
            "products": produtos, "panel": produtos, "imports": [],
            "metadata": {"source": "DEMO fictícia"}, "_access_context": self.auth.context,
        }

    def replenishment_rules(self, referencia):
        """Entrega ciclos fictícios para exercitar todos os formulários de regras."""
        self.auth.require("parameters.edit")
        self.leituras.append("regras")
        return {"configuration": {"cycle_primary": 30, "cycle_alternative": 60,
                                  "reference_boxes": 100}, "rules": []}

    def admin_audit(self):
        """Entrega um evento fictício apenas quando a área autorizada for aberta."""
        self.auth.require("audit.view")
        self.leituras.append("auditoria")
        return [{"action": "consulta_ficticia", "created_at": "2026-11-01T12:00:00Z"}]

    def import_demo(self, referencia, inicio, fim):
        """Conta a importação interna fictícia depois da confirmação do Master."""
        self.auth.require("inventory.import")
        assert self.auth.context.profile.role == "Master"
        self.importacoes.append((referencia, inicio, fim))


@pytest.fixture(autouse=True)
def bloquear_fronteiras_externas(monkeypatch):
    """Falha se qualquer cenário tentar abrir Secrets ou um transporte real."""
    def bloquear(*args, **kwargs):
        """Nega acesso externo inclusive por caminhos não previstos no harness."""
        raise AssertionError("A regressão administrativa deve usar somente fronteiras fictícias.")

    monkeypatch.setattr("web.integration_page._secrets", bloquear)
    monkeypatch.setattr("urllib.request.build_opener", bloquear)
    monkeypatch.setattr("socket.create_connection", bloquear)


def abrir_administracao(area="Integrações", *, papel="Master", permissoes=PERMISSOES):
    """Executa a composição real com estado de navegação e sessão independentes."""
    servico = ServicoAdministracaoFicticio(papel=papel, permissoes=permissoes)
    app = AppTest.from_string(SCRIPT, default_timeout=30)
    app.session_state["_admin_service"] = servico
    app.session_state["admin_area"] = area
    app.run()
    return app, servico


@pytest.mark.parametrize(("area", "titulo"), AREAS)
def test_subarea_real_renderiza_dois_cabecalhos_sem_colisao(area, titulo):
    """A composição mantém cabeçalhos pai e filho no mesmo rerun sem chave repetida."""
    app, servico = abrir_administracao(area)
    assert not app.exception
    assert [item.value for item in app.title] == ["Administração", titulo]
    assert app.radio(key="admin_area").value == area
    assert servico.auth.calls == []
    assert servico.importacoes == []


def test_trocar_areas_e_reexecutar_preserva_titulos_e_nao_dispara_operacoes():
    """A mesma sessão percorre as subáreas duas vezes sem executar ações implícitas."""
    app, servico = abrir_administracao()
    assert not app.exception
    for area, titulo in (*AREAS[1:], *AREAS):
        app.radio(key="admin_area").set_value(area).run()
        assert not app.exception
        assert [item.value for item in app.title] == ["Administração", titulo]
        app.run()
        assert not app.exception
        assert [item.value for item in app.title] == ["Administração", titulo]
    assert "regras" in servico.leituras
    assert "auditoria" in servico.leituras
    assert "usuarios" not in servico.leituras
    assert "inventory.view" not in servico.leituras
    assert servico.auth.calls == []
    assert servico.importacoes == []


def test_producao_nao_oferece_geracao_de_vendas_demo():
    """Master visualiza somente integração real e não pode recriar DEMO."""
    app, servico = abrir_administracao()
    assert not app.exception
    assert not any("dados fictícios identificados como DEMO" in x.label for x in app.checkbox)
    assert not any(x.label == "Importar demonstração fictícia" for x in app.button)
    assert botao(app, "Preparar prévia")
    app.run()
    assert not servico.auth.calls
    assert not servico.importacoes

def test_importacao_manual_valida_formulario_sem_arquivos():
    """O formulário completo continua acessível e recusa submissão incompleta."""
    app, servico = abrir_administracao("Importação CSV")
    assert not app.exception
    assert [item.label for item in app.get("file_uploader")] == ["produtos.csv", "vendas.csv"]
    botao(app, "Validar e importar").click().run()
    assert not app.exception
    assert any("Selecione os dois arquivos" in item.value for item in app.error)
    assert servico.importacoes == []
    assert servico.auth.calls == []


def test_importacao_nao_possui_opcao_de_criar_lote_ficticio():
    """A rotina produtiva CSV não permite gerar exemplos."""
    app, servico = abrir_administracao("Importação CSV")
    assert not app.exception
    assert not any("demonstração fictícia" in x.label.lower() for x in app.button)
    assert not any("lote fictício" in x.label.lower() for x in app.checkbox)
    app.run()
    assert not app.exception
    assert not servico.importacoes

@pytest.mark.parametrize("papel", ["Administrador", "Operador", "Visualizador"])
@pytest.mark.parametrize(("permissao", "area", "titulo"), [
    ("inventory.import", "Importação CSV", "Importar catálogo e vendas"),
    ("parameters.edit", "Regras de Reposição", "Regras de Reposição"),
    ("audit.view", "Auditoria", "Auditoria"),
])
def test_nao_master_preserva_area_granular_e_oculta_integracoes(papel, permissao, area, titulo):
    """Mesmo com concessão indevida, integração Master não surge para outros perfis."""
    app, servico = abrir_administracao(
        papel=papel, permissoes=frozenset({permissao, "integrations.manage"}),
    )
    assert not app.exception
    assert app.radio(key="admin_area").options == [area]
    assert app.radio(key="admin_area").value == area
    assert [item.value for item in app.title] == ["Administração", titulo]
    assert not any(item.label in {"Verificar conexão", "Preparar prévia", "Importar demonstração fictícia"}
                   for item in app.button)
    assert servico.auth.calls == []
    assert servico.importacoes == []
    app.run()
    assert not app.exception
    assert app.radio(key="admin_area").value == area


def test_master_sem_permissao_de_integracao_nao_recebe_diagnostico():
    """Uma seleção antiga não substitui a permissão granular exigida pelo Master."""
    app, servico = abrir_administracao(permissoes=frozenset({"audit.view"}))
    assert not app.exception
    assert app.radio(key="admin_area").options == ["Usuários e permissões", "Auditoria"]
    assert app.radio(key="admin_area").value == "Usuários e permissões"
    assert not any(item.label == "Verificar conexão" for item in app.button)
    app.radio(key="admin_area").set_value("Auditoria").run()
    assert not app.exception
    assert [item.value for item in app.title] == ["Administração", "Auditoria"]
    assert servico.auth.calls == []


@pytest.mark.parametrize("alteracao", ["permissao", "papel"])
def test_revogacao_reseleciona_area_sem_exibir_ou_confirmar_previa(alteracao):
    """Revogação desativa a integração e não oferece importação simulada."""
    app, servico = abrir_administracao()
    assert not app.exception
    chamadas = list(servico.auth.calls)
    contexto = servico.auth.context
    if alteracao == "permissao":
        servico.auth.context = replace(contexto, permissions=frozenset({"audit.view"}))
        destino = "Usuários e permissões"
    else:
        servico.auth.context = replace(contexto, profile=replace(contexto.profile, role="Operador"))
        destino = "Regras de Reposição"
    app.run()
    assert not app.exception
    assert app.radio(key="admin_area").value == destino
    assert "Integrações" not in app.radio(key="admin_area").options
    assert not any(item.label in {"Confirmar sincronização interna", "Verificar conexão"}
                   for item in app.button)
    assert servico.auth.calls == chamadas
    assert not servico.auth.consolidated

@pytest.fixture
def diagnostico_administrativo(monkeypatch):
    """Substitui apenas configuração e GET, mantendo o diagnóstico e a UI reais."""
    estado = SimpleNamespace(leituras=0, consultas=[])

    def carregar():
        """Entrega configuração exclusivamente fictícia apenas após consentimento."""
        estado.leituras += 1
        return {
            "GESTAOCLICK_ENABLED": True,
            "GESTAOCLICK_ORGANIZATION_ID": ORGANIZACAO_DIAGNOSTICO,
            "GESTAOCLICK_BASE_URL": "https://api.gestaoclick.com",
            "GESTAOCLICK_ACCESS_TOKEN": "ACCESS-FICTICIO-PROTEGIDO",
            "GESTAOCLICK_SECRET_ACCESS_TOKEN": "SECRET-FICTICIO-PROTEGIDO",
        }

    def consultar(self, url, headers, timeout):
        """Registra a chamada simulada sem criar socket nem retornar dados reais."""
        estado.consultas.append(url)
        return RespostaGestaoClick(200, b'{"code":200,"status":"success","data":[{}]}')

    monkeypatch.setattr("web.integration_page._secrets", carregar)
    monkeypatch.setattr("integracoes.gestaoclick.TransporteHTTPS.__init__", lambda self: None)
    monkeypatch.setattr("integracoes.gestaoclick.TransporteHTTPS.get", consultar)
    app, servico = abrir_administracao("Auditoria")
    servico.auth.context = replace(
        servico.auth.context,
        profile=replace(servico.auth.context.profile, organization_id=ORGANIZACAO_DIAGNOSTICO),
    )
    app.radio(key="admin_area").set_value("Integrações").run()
    assert not app.exception
    estado.app, estado.servico = app, servico
    return estado


def test_diagnostico_por_administracao_nega_clique_sem_consentimento(diagnostico_administrativo):
    """A ação de diagnóstico recusa o clique desautorizado antes de ler Secrets."""
    estado = diagnostico_administrativo
    app = estado.app
    botao(app, "Verificar conexão").click().run()
    assert not app.exception
    assert app.error
    assert estado.leituras == 0
    assert estado.consultas == []
    assert estado.servico.auth.calls == []
    assert estado.servico.importacoes == []


def test_diagnostico_por_administracao_exige_consentimento_e_clique(diagnostico_administrativo):
    """Entrar, reexecutar e consentir não consultam; o botão executa um GET limitado."""
    estado = diagnostico_administrativo
    app = estado.app
    app.run()
    assert not app.exception
    assert estado.leituras == 0
    assert estado.consultas == []
    checkbox(app, CONFIRMACAO_DIAGNOSTICO).check().run()
    assert not app.exception
    assert estado.leituras == 0
    assert estado.consultas == []
    # AppTest não envia alterações do formulário em um rerun sem submissão.
    checkbox(app, CONFIRMACAO_DIAGNOSTICO).check()
    botao(app, "Verificar conexão").click().run()
    assert not app.exception
    assert any("aceitou a autenticação" in item.value for item in app.success), texto_interface(app)
    assert estado.leituras == 1
    assert estado.consultas == ["https://api.gestaoclick.com/lojas?pagina=1&limite=1"]
    assert not app.dataframe
    assert "ACCESS-FICTICIO-PROTEGIDO" not in texto_interface(app)
    assert "SECRET-FICTICIO-PROTEGIDO" not in texto_interface(app)
    app.radio(key="admin_area").set_value("Auditoria").run()
    assert not app.exception
    app.radio(key="admin_area").set_value("Integrações").run()
    assert not app.exception
    app.run()
    assert not app.exception
    assert estado.leituras == 1
    assert len(estado.consultas) == 1
    assert estado.servico.auth.calls == []
    assert estado.servico.importacoes == []
    assert "_sync_preview" not in app.session_state
