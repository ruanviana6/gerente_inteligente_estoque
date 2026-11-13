"""Exercita a página Streamlit de integração com coordenador real e RPC fictícia.

O harness chama a página verdadeira e representa apenas a fronteira Auth/RPC.
Nenhuma credencial ou conexão externa é utilizada. A integridade SQL é testada
separadamente no PostgreSQL descartável, não é inferida destes fakes.
"""

import json
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from integracoes.gestaoclick import RespostaGestaoClick
from web.auth import AccessContext, Profile
from web.errors import AuthorizationError

SOURCE = "30000000-0000-4000-8000-000000000001"
RUN = "50000000-0000-4000-8000-000000000001"
TOKEN = "60000000-0000-4000-8000-000000000001"
ORGANIZACAO_DIAGNOSTICO = "70000000-0000-4000-8000-000000000001"
CONFIRMACAO_DIAGNOSTICO = "Autorizo uma consulta real de lojas para verificar a conexão"
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


def texto_interface(app):
    """Reúne somente o conteúdo renderizado para detectar dados protegidos na tela."""
    tipos = ("caption", "code", "error", "info", "markdown", "success", "text", "warning")
    return " ".join(str(item.value) for tipo in tipos for item in app.get(tipo))


@pytest.fixture
def diagnostico_ficticio(monkeypatch):
    """Mantém a página e o serviço reais, substituindo apenas Secrets e transporte."""
    import web.integration_page

    estado = SimpleNamespace(
        leituras=0,
        consultas=[],
        apos_consulta=None,
        falha=None,
        resposta=RespostaGestaoClick(200, b'{"code":200,"status":"success","data":[]}'),
        configuracao={
            "GESTAOCLICK_ENABLED": True,
            "GESTAOCLICK_ORGANIZATION_ID": ORGANIZACAO_DIAGNOSTICO,
            "GESTAOCLICK_BASE_URL": "https://api.gestaoclick.com",
            "GESTAOCLICK_ACCESS_TOKEN": "ACCESS-FICTICIO-PROTEGIDO",
            "GESTAOCLICK_SECRET_ACCESS_TOKEN": "SECRET-FICTICIO-PROTEGIDO",
            "GESTAOCLICK_SOURCE_ID": "FONTE-FICTICIA-PROTEGIDA",
            "GESTAOCLICK_STORE_ID": "LOJA-FICTICIA-PROTEGIDA",
        },
    )

    def carregar():
        """Conta leituras para provar que a navegação não abre configuração protegida."""
        estado.leituras += 1
        return dict(estado.configuracao)

    def consultar(self, url, headers, timeout):
        """Registra o GET sem rede e permite simular revogação durante a resposta."""
        estado.consultas.append((url, dict(headers), timeout))
        if estado.apos_consulta:
            estado.apos_consulta()
        if estado.falha:
            raise estado.falha
        return estado.resposta

    monkeypatch.setattr(web.integration_page, "_secrets", carregar)
    monkeypatch.setattr("integracoes.gestaoclick.TransporteHTTPS.__init__", lambda self: None)
    monkeypatch.setattr("integracoes.gestaoclick.TransporteHTTPS.get", consultar)
    app, auth = abrir()
    auth.context = AccessContext(
        replace(auth.context.profile, organization_id=ORGANIZACAO_DIAGNOSTICO), auth.context.permissions,
    )
    app.run()
    estado.app = app
    estado.auth = auth
    return estado


def verificar_diagnostico(estado):
    """Executa a ação visível depois de marcar a autorização específica da consulta."""
    checkbox(estado.app, CONFIRMACAO_DIAGNOSTICO).check()
    botao(estado.app, "Verificar conexão").click().run()
    assert not estado.app.exception


def test_diagnostico_navegacao_e_checkbox_nao_leem_secrets_nem_consultam(diagnostico_ficticio):
    """Navegar, reexecutar e marcar a confirmação ainda não executam o diagnóstico."""
    estado = diagnostico_ficticio
    assert not estado.app.exception
    assert not checkbox(estado.app, CONFIRMACAO_DIAGNOSTICO).value
    estado.app.run()
    checkbox(estado.app, CONFIRMACAO_DIAGNOSTICO).check().run()
    assert not estado.app.exception
    assert estado.leituras == 0
    assert estado.consultas == []
    assert estado.auth.calls == []
    assert not estado.app.success


def test_diagnostico_clique_sem_confirmacao_nao_le_secrets(diagnostico_ficticio):
    """Clicar sem consentimento explícito é negado antes de abrir configuração."""
    estado = diagnostico_ficticio
    botao(estado.app, "Verificar conexão").click().run()
    assert not estado.app.exception
    assert estado.app.error or estado.app.warning
    assert estado.leituras == 0
    assert estado.consultas == []
    assert estado.auth.calls == []
    assert not estado.app.success


def test_diagnostico_desabilitado_nao_faz_get(diagnostico_ficticio):
    """A autorização visual não substitui a ativação explícita do servidor."""
    estado = diagnostico_ficticio
    estado.configuracao["GESTAOCLICK_ENABLED"] = False
    verificar_diagnostico(estado)
    assert estado.leituras == 1
    assert estado.consultas == []
    assert estado.auth.calls == []
    assert any("não habilitada" in item.value for item in estado.app.warning)
    assert not estado.app.success


@pytest.mark.parametrize("vazia", [False, True])
def test_diagnostico_limita_get_descarta_dados_e_nao_importa(diagnostico_ficticio, vazia):
    """Autenticação aceita não comprova cobertura nem revela dados recebidos do ERP."""
    estado = diagnostico_ficticio
    dados_pessoais = "PESSOA-FICTICIA-NAO-EXIBIR"
    estado.resposta = RespostaGestaoClick(200, json.dumps({
        "code": 200, "status": "success",
        "data": [] if vazia else [{"nome": dados_pessoais, "email": "ficticio@example.invalid"}],
        "meta": {"proxima_pagina": "https://fora-do-contrato.invalid/segredo", "total_paginas": 999},
    }).encode())
    verificar_diagnostico(estado)
    assert estado.leituras == 1
    assert len(estado.consultas) == 1
    url, headers, timeout = estado.consultas[0]
    assert url == "https://api.gestaoclick.com/lojas?pagina=1&limite=1"
    assert set(headers) == {"access-token", "secret-access-token", "Accept"}
    assert 0 < timeout <= 15
    assert estado.auth.calls == []
    assert not estado.auth.pages
    assert not estado.auth.consolidated
    assert not estado.app.dataframe
    assert not estado.app.json
    assert "_sync_preview" not in estado.app.session_state
    assert "_sync_attempt" not in estado.app.session_state
    assert any("Nenhum dado foi importado" in item.value for item in estado.app.success)
    texto = texto_interface(estado.app)
    assert "não confirma o histórico completo" in texto
    assert "abrangência do período" in texto
    assert bool(estado.app.warning) is vazia
    if vazia:
        assert any("veio vazia" in item.value for item in estado.app.warning)
    for nome, valor in estado.configuracao.items():
        if nome not in {"GESTAOCLICK_ENABLED", "GESTAOCLICK_ORGANIZATION_ID"}:
            assert valor not in texto
    assert dados_pessoais not in texto
    assert "ficticio@example.invalid" not in texto
    assert "fora-do-contrato.invalid" not in texto
    assert not estado.app.text_area
    assert {item.label for item in estado.app.text_input} == {
        "UUID da fonte para consultar", "Identificador da loja para consultar",
    }
    estado.app.run()
    assert estado.leituras == 1
    assert len(estado.consultas) == 1
    assert not estado.app.success


@pytest.mark.parametrize("status", [401, 403, 429, 503, 302])
def test_diagnostico_erros_http_nao_exibem_corpo_nem_repetem_get(diagnostico_ficticio, status):
    """Erros e redirects têm mensagem segura sem resposta comercial ou repetição."""
    estado = diagnostico_ficticio
    sentinela = "RESPOSTA-PII-FICTICIA-NAO-EXIBIR"
    estado.resposta = RespostaGestaoClick(status, sentinela.encode(), {
        "Location": "https://destino-ficticio.invalid/" + sentinela, "Retry-After": "0",
    })
    verificar_diagnostico(estado)
    assert len(estado.consultas) == 1
    assert estado.app.warning
    assert not estado.app.success
    assert not estado.app.dataframe
    assert sentinela not in texto_interface(estado.app)
    assert estado.auth.calls == []


@pytest.mark.parametrize("corpo", [
    b'{"code":200,"status":"success","data":[],"data":[{"nome":"PII-FICTICIA"}]}',
    b'{"code":200,"status":"success","data":[{"nome":"PII-FICTICIA","nome":"duplicado"}]}',
    b'{"code":200,"status":"error","message":"PII-FICTICIA","data":[]}',
    b'PII-FICTICIA-JSON-INVALIDO',
])
def test_diagnostico_json_invalido_nao_exibe_resposta(diagnostico_ficticio, corpo):
    """Duplicatas, erros e JSON inválido nunca viram sucesso nem conteúdo de tela."""
    estado = diagnostico_ficticio
    estado.resposta = RespostaGestaoClick(200, corpo)
    verificar_diagnostico(estado)
    assert len(estado.consultas) == 1
    assert estado.app.warning
    assert not estado.app.success
    assert not estado.app.dataframe
    assert "PII-FICTICIA" not in texto_interface(estado.app)
    assert estado.auth.calls == []


def test_diagnostico_timeout_nao_repete_nem_exibe_excecao(diagnostico_ficticio):
    """A mensagem interna de transporte não chega ao usuário nem causa retry."""
    estado = diagnostico_ficticio
    sentinela = "TOKEN-FICTICIO-NAO-EXIBIR"
    estado.falha = TimeoutError(sentinela)
    verificar_diagnostico(estado)
    assert len(estado.consultas) == 1
    assert estado.app.warning
    assert not estado.app.success
    assert sentinela not in texto_interface(estado.app)
    assert estado.auth.calls == []


@pytest.mark.parametrize("alteracao", ["usuario", "organizacao", "geracao", "permissao", "ativa", "papel"])
def test_diagnostico_revogacao_durante_get_nao_exibe_sucesso(diagnostico_ficticio, alteracao):
    """A revalidação descarta o diagnóstico recebido após alteração de acesso."""
    estado = diagnostico_ficticio

    def revogar():
        """Altera apenas o contexto autenticado durante a resposta HTTP fictícia."""
        auth = estado.auth
        if alteracao == "geracao":
            auth.generation += 1
        elif alteracao == "permissao":
            auth.context = AccessContext(auth.context.profile, frozenset())
        else:
            campos = {
                "usuario": {"user_id": "outro-usuario-ficticio"},
                "organizacao": {"organization_id": "80000000-0000-4000-8000-000000000001"},
                "ativa": {"active": False},
                "papel": {"role": "Administrador"},
            }
            auth.context = AccessContext(replace(auth.context.profile, **campos[alteracao]),
                                         auth.context.permissions)

    estado.apos_consulta = revogar
    verificar_diagnostico(estado)
    assert len(estado.consultas) == 1
    assert estado.app.error
    assert not estado.app.success
    assert not estado.app.dataframe
    assert estado.auth.calls == []
    assert "_sync_preview" not in estado.app.session_state
