"""Exercita a página Streamlit de integração com coordenador real e RPC fictícia.

O harness chama a página verdadeira e representa apenas a fronteira Auth/RPC.
Nenhuma credencial ou conexão externa é utilizada. A integridade SQL é testada
separadamente no PostgreSQL descartável, não é inferida destes fakes.
"""

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from web.auth import AccessContext, Profile
from web.errors import AuthorizationError

SOURCE = "30000000-0000-4000-8000-000000000001"
RUN = "50000000-0000-4000-8000-000000000001"
TOKEN = "60000000-0000-4000-8000-000000000001"
SCRIPT = '''
import streamlit as st
from web.integration_page import integracoes
from web.errors import AppError
servico = st.session_state['_integration_service']
try:
    integracoes(servico, servico.auth.context)
except AppError:
    st.error('Acesso negado. Entre novamente ou consulte o Master.')
'''


class AuthRPCFicticia:
    """Mantém staging separado dos dados consolidados e autoriza cada RPC de teste."""

    def __init__(self):
        """Cria uma identidade Master fictícia sem token nem senha."""
        self.context = AccessContext(Profile("user-ficticio", "org-ficticia", "Master fictício", "Master", True),
                                     frozenset({"integrations.manage"}))
        self.generation = 1
        self.calls = []
        self.run = None
        self.pages = {}
        self.consolidated = []
        self.after_rpc = None

    def operation(self):
        """Reproduz a interface de escopo sem introduzir locks globais no teste."""
        return nullcontext()

    def require(self, permission):
        """Nega por padrão, incluindo perfis desativados."""
        if not self.context.profile.active or not self.context.can(permission):
            raise AuthorizationError("Acesso fictício negado.")
        return self.context

    def rpc(self, name, payload, permission):
        """Simula a persistência interna; nenhum método chama o ERP ou o Supabase."""
        self.require(permission)
        if self.context.profile.role != "Master":
            raise AuthorizationError("Somente o Master pode sincronizar.")
        self.calls.append((name, deepcopy(payload)))
        if name == "integration_register_source":
            result = {"id": SOURCE}
        elif name == "sync_authorize_source":
            result = {"id": SOURCE}
        elif name == "sync_begin":
            if self.run and self.run["request_key"] == payload["p_request_key"]:
                result = dict(self.run)
            else:
                self.pages = {}
                self.run = {"id": RUN, "claim_token": TOKEN, "status": "staging", "checkpoint": 0,
                            "request_key": payload["p_request_key"], "row_count": payload["p_expected_rows"],
                            "net_quantity": payload["p_expected_quantity"],
                            "period_start": payload["p_period_start"], "period_end": payload["p_period_end"]}
                result = dict(self.run)
        elif name == "sync_stage":
            self.pages[payload["p_page"]] = deepcopy(payload["p_rows"])
            self.run["checkpoint"] = max(self.pages)
            result = dict(self.run)
        elif name == "sync_preview":
            result = {**self.run, "sample": [row for rows in self.pages.values() for row in rows][:5],
                      "changed_rows": self.run["row_count"]}
        elif name == "sync_commit":
            assert payload["p_coverage_confirmed"] is True
            self.consolidated = [row for rows in self.pages.values() for row in rows]
            self.run["status"] = "completed"
            result = dict(self.run)
        elif name == "sync_cancel":
            self.pages = {}
            self.run["status"] = "cancelled"
            result = dict(self.run)
        else:
            raise AssertionError("RPC fora do contrato fictício.")
        if self.after_rpc:
            self.after_rpc(name)
        return result


def abrir():
    """Entrega uma página e sua sessão exclusiva, usando o SyncCoordinator verdadeiro."""
    auth = AuthRPCFicticia()
    app = AppTest.from_string(SCRIPT, default_timeout=30)
    app.session_state["_integration_service"] = SimpleNamespace(auth=auth)
    app.run()
    return app, auth


def botao(app, label):
    """Localiza um controle pela ação que o usuário realmente vê."""
    return next(item for item in app.button if item.label == label)


def checkbox(app, label):
    """Localiza a confirmação explícita sem depender da ordem de widgets."""
    return next(item for item in app.checkbox if item.label == label)


def preparar_demo(app):
    """Solicita a prévia por clique; não confirma a sincronização."""
    botao(app, "Preparar prévia").click().run()
    assert not app.exception
    return app


def test_demo_so_prepara_apos_clique_e_nao_consolida_automaticamente():
    """Abrir a página não extrai dados; prévia precisa de uma segunda ação para commit."""
    app, auth = abrir()
    assert not app.exception
    assert auth.calls == []
    preparar_demo(app)
    assert not auth.consolidated
    assert auth.pages
    assert app.dataframe
    assert "sync_commit" not in [name for name, _ in auth.calls]
    assert botao(app, "Confirmar sincronização interna").disabled
    assert all(row["store_id"] == "DEMO" for rows in auth.pages.values() for row in rows)
    assert all(row["document_id"].startswith("DEMO-") for rows in auth.pages.values() for row in rows)


def test_demo_confirma_somente_com_checkbox_e_clique_separados():
    """A confirmação visual isolada ainda não envia a operação sensível ao banco."""
    app, auth = abrir()
    preparar_demo(app)
    checkbox(app, "Conferi a amostra, quantidades, totais e abrangência completa do período").check().run()
    assert not auth.consolidated
    assert not botao(app, "Confirmar sincronização interna").disabled
    botao(app, "Confirmar sincronização interna").click().run()
    assert not app.exception
    assert auth.consolidated
    assert [name for name, _ in auth.calls].count("sync_commit") == 1
    assert "_sync_preview" not in app.session_state
    assert "_sync_attempt" not in app.session_state
    app.run()
    assert [name for name, _ in auth.calls].count("sync_commit") == 1


def test_cancelar_previa_preserva_copia_consolidada_e_limpa_estado():
    """Cancelar descarta apenas staging e não apaga resultados anteriormente confirmados."""
    app, auth = abrir()
    previous = [{"document_id": "VENDA-ANTERIOR-FICTICIA"}]
    auth.consolidated = deepcopy(previous)
    preparar_demo(app)
    botao(app, "Cancelar esta prévia").click().run()
    assert not app.exception
    assert not auth.pages
    assert auth.consolidated == previous
    assert "sync_commit" not in [name for name, _ in auth.calls]
    assert [name for name, _ in auth.calls].count("sync_cancel") == 1
    assert "_sync_preview" not in app.session_state


@pytest.mark.parametrize("which", ["user", "organization", "generation", "permission"])
def test_troca_de_contexto_descarta_previa_antes_de_nova_leitura(which):
    """Uma prévia anterior não é apresentada em outra identidade, organização ou sessão."""
    app, auth = abrir()
    preparar_demo(app)
    before = len(auth.calls)
    if which == "generation":
        auth.generation += 1
    elif which == "permission":
        auth.context = AccessContext(auth.context.profile, frozenset({"integrations.manage", "trends.view"}))
    else:
        field = "user_id" if which == "user" else "organization_id"
        auth.context = AccessContext(replace(auth.context.profile, **{field: "outra-identidade-ficticia"}),
                                     auth.context.permissions)
    app.run()
    assert not app.exception
    assert not app.dataframe
    assert len(auth.calls) == before
    assert "_sync_preview" not in app.session_state


@pytest.mark.parametrize("role", ["Administrador", "Operador", "Visualizador"])
def test_nao_master_nao_recebe_interface_mesmo_com_permissao_concedida(role):
    """O papel Master é obrigatório além da permissão granular."""
    app, auth = abrir()
    auth.context = AccessContext(replace(auth.context.profile, role=role), auth.context.permissions)
    app.run()
    assert not app.exception
    assert app.error
    assert not app.button
    assert not app.dataframe
    assert auth.calls == []


def test_integracao_real_desabilitada_nao_faz_rede_nem_exibe_segredos(monkeypatch):
    """Desmarcar DEMO não contorna enabled=false nem apresenta configuração protegida."""
    import web.integration_page

    sentinela = "SEGREDO-FICTICIO-NAO-EXIBIR"
    monkeypatch.setattr(web.integration_page, "_secrets", lambda: {
        "GESTAOCLICK_ENABLED": False, "GESTAOCLICK_ACCESS_TOKEN": sentinela,
        "GESTAOCLICK_SECRET_ACCESS_TOKEN": sentinela,
    })
    def bloquear(*args, **kwargs):
        """Falha se o fluxo desabilitado tentar construir uma conexão HTTP."""
        raise AssertionError("Rede indevida no modo desabilitado.")
    monkeypatch.setattr("urllib.request.build_opener", bloquear)
    app, auth = abrir()
    checkbox(app, "Usar somente dados fictícios identificados como DEMO").uncheck()
    checkbox(app, "Confirmo autorização da empresa, contrato e permissões de leitura (consulta real)").check()
    botao(app, "Preparar prévia").click().run()
    assert not app.exception
    assert any("não habilitada" in item.value for item in app.warning)
    assert auth.calls == []
    assert not app.dataframe
    text = " ".join(item.value for kind in (app.warning, app.error, app.markdown, app.info) for item in kind)
    assert sentinela not in text
    assert not app.text_input


def test_real_exige_autorizacao_antes_de_ler_configuracao(monkeypatch):
    """O opt-in real exige confirmação explícita além da configuração protegida."""
    import web.integration_page

    def bloquear():
        """Não deixa sequer ler os Secrets antes da autorização na ação."""
        raise AssertionError("Leitura prematura de configuração.")
    monkeypatch.setattr(web.integration_page, "_secrets", bloquear)
    app, auth = abrir()
    checkbox(app, "Usar somente dados fictícios identificados como DEMO").uncheck()
    botao(app, "Preparar prévia").click().run()
    assert not app.exception
    assert any("exige autorização" in item.value for item in app.warning)
    assert auth.calls == []


@pytest.mark.parametrize("period", [(date(2020, 1, 1), date(2022, 1, 1)), (date(2020, 1, 1),)])
def test_periodo_invalido_impede_qualquer_rpc(period):
    """Janela incompleta ou excessiva não registra fonte, staging nem confirmação."""
    app, auth = abrir()
    app.date_input[0].set_value(period)
    botao(app, "Preparar prévia").click().run()
    assert not app.exception
    assert any("período" in item.value for item in app.warning)
    assert auth.calls == []


def test_revogacao_durante_previa_nao_exibe_amostra():
    """A revalidação após a RPC impede apresentação com uma sessão que mudou no percurso."""
    app, auth = abrir()
    def alterar(name):
        """Troca a vida da sessão no retorno da primeira prévia do coordenador."""
        if name == "sync_preview":
            auth.generation += 1
    auth.after_rpc = alterar
    preparar_demo(app)
    assert app.error
    assert not app.dataframe
    assert "sync_commit" not in [name for name, _ in auth.calls]


def test_duas_sessoes_master_nao_compartilham_previa_ou_staging():
    """O estado da primeira página não funciona como cache global para outra conta."""
    first, first_auth = abrir()
    preparar_demo(first)
    second, second_auth = abrir()
    second_auth.context = AccessContext(replace(second_auth.context.profile, user_id="outro-master-ficticio"),
                                       second_auth.context.permissions)
    second.run()
    assert not second.exception
    assert "_sync_preview" not in second.session_state
    assert not second.dataframe
    assert second_auth.calls == []
    assert first_auth.pages
    assert not second_auth.pages


def test_conta_desativada_nao_prepara_ou_confirma_previa():
    """Desativação no backend impede até renderizar os controles de sincronização."""
    app, auth = abrir()
    auth.context = AccessContext(replace(auth.context.profile, active=False), auth.context.permissions)
    app.run()
    assert not app.exception
    assert app.error
    assert not app.button
    assert not app.dataframe
    assert auth.calls == []
