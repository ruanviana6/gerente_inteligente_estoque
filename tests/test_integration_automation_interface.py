"""Exercita controles automáticos reais com configuração e RPC inteiramente fictícias."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_gestaoclick_configuration import ORGANIZATION, configuracao_ficticia
from test_integration_interface import abrir, botao, checkbox, texto_interface

from web.auth import AccessContext
from web.errors import AppError

APPROVAL = "Conferi uma amostra real, loja, campos e situações; autorizo a atualização automática"
ROTATION = "Gerar nova credencial do executor e invalidar a anterior"
WORKER_SECRET = "credencial-exclusivamente-ficticia-do-executor-123456"


@pytest.fixture
def automacao_ficticia(monkeypatch):
    """Isola fronteiras sem substituir validação de configuração, UI ou autorização."""
    state = SimpleNamespace(values=configuracao_ficticia(), reads=0, after_load=None,
                            after_rpc=None, failure=None, response={"enabled": True, "secret": WORKER_SECRET})

    def load():
        """Devolve uma cópia apenas após a ação explícita e permite simular revogação."""
        state.reads += 1
        result = deepcopy(state.values)
        if state.after_load:
            state.after_load()
        return result

    def no_network(*args, **kwargs):
        """Uma configuração, consulta de estado ou ativação não lê o ERP."""
        raise AssertionError("A UI administrativa não deve executar leitura do ERP nesta ação.")

    monkeypatch.setattr("web.integration_page._secrets", load)
    monkeypatch.setattr("urllib.request.build_opener", no_network)
    monkeypatch.setattr("integracoes.gestaoclick.GestaoClickClient.consultar", no_network)
    app, auth = abrir()
    auth.context = AccessContext(replace(auth.context.profile, organization_id=ORGANIZATION), auth.context.permissions)
    original_rpc = auth.rpc

    def rpc(name, payload, permission):
        """Simula somente novas RPCs; as funções manuais usam a fronteira existente."""
        if name.startswith("sync_automation_"):
            auth.require(permission)
            auth.calls.append((name, deepcopy(payload)))
            if state.failure:
                raise state.failure
            result = deepcopy(state.response)
            if state.after_rpc:
                state.after_rpc()
            return result
        return original_rpc(name, payload, permission)

    auth.rpc = rpc
    state.app, state.auth = app, auth
    app.run()
    return state


def rendered(app):
    """Inclui JSONs visíveis na inspeção de vazamentos da interface."""
    return texto_interface(app) + " ".join(str(item.value) for item in app.json)


def test_navegacao_e_selecao_nao_abrem_secrets_nem_executam_erp(automacao_ficticia):
    """Abrir a aba, mudar opções e rerodar não inicia o agendador nem faz consultas."""
    state = automacao_ficticia
    app = state.app
    assert not app.exception
    assert not checkbox(app, "Usar somente dados fictícios identificados como DEMO").value
    assert not checkbox(app, APPROVAL).value
    checkbox(app, APPROVAL).check().run()
    app.selectbox[0].select(48).run()
    app.run()
    assert not app.exception
    assert state.reads == 0
    assert state.auth.calls == []
    assert not app.success


def test_ativacao_sem_aprovacao_nao_abre_configuracao(automacao_ficticia):
    """O botão sem aceite não prepara nenhuma credencial ou operação no banco."""
    state = automacao_ficticia
    botao(state.app, "Ativar atualização automática").click().run()
    assert not state.app.exception
    assert any("marque a autorização" in item.value for item in state.app.warning)
    assert state.reads == 0
    assert state.auth.calls == []


def test_configuracao_incompleta_mostra_campos_sem_segredos(automacao_ficticia):
    """O usuário recebe nomes de ajustes necessários, em vez de resposta técnica bruta."""
    state = automacao_ficticia
    state.values = {"GESTAOCLICK_ACCESS_TOKEN": "NAO-EXIBIR-ACCESS",
                    "GESTAOCLICK_SECRET_ACCESS_TOKEN": "NAO-EXIBIR-SECRET"}
    botao(state.app, "Verificar configuração da sincronização").click().run()
    assert not state.app.exception
    text = rendered(state.app)
    assert "GESTAOCLICK_SOURCE_ID" in text
    assert "GESTAOCLICK_STORE_ID" in text
    assert "GESTAOCLICK_READ_CONTRACT_JSON" in text
    assert "NAO-EXIBIR" not in text
    assert state.auth.calls == []
    assert not state.app.success


def test_ativacao_incompleta_orienta_conferir_conta_sem_gravar(automacao_ficticia):
    """A autorização marcada não substitui fonte/loja/contrato, nem acusa o UUID errado."""
    state = automacao_ficticia
    state.values.pop("GESTAOCLICK_SOURCE_ID")
    checkbox(state.app, APPROVAL).check()
    botao(state.app, "Ativar atualização automática").click().run()
    assert not state.app.exception
    assert any("Primeiro confira as lojas e situações" in item.value for item in state.app.warning)
    assert not state.auth.calls and not state.app.success
    assert "Configure os UUIDs válidos" not in rendered(state.app)


def test_configuracao_completa_valida_fonte_sem_get(automacao_ficticia):
    """Conferência local não é anunciada como autenticação real ou carga concluída."""
    state = automacao_ficticia
    botao(state.app, "Verificar configuração da sincronização").click().run()
    assert not state.app.exception
    assert [name for name, _ in state.auth.calls] == ["sync_authorize_source"]
    assert any("Nenhuma consulta ao ERP" in item.value for item in state.app.success)
    assert "validar uma amostra real" in rendered(state.app)


def test_ativacao_salva_apenas_contrato_e_exibe_credencial_uma_vez(automacao_ficticia):
    """A aprovação persiste escopo e hash; ERP e tokens não são encaminhados à RPC."""
    state = automacao_ficticia
    checkbox(state.app, APPROVAL).check()
    botao(state.app, "Ativar atualização automática").click().run()
    assert not state.app.exception
    assert [name for name, _ in state.auth.calls] == ["sync_automation_configure"]
    payload = state.auth.calls[0][1]
    assert payload["p_approved"] is True
    assert payload["p_enabled"] is True
    assert payload["p_rotate_secret"] is False
    assert payload["p_interval_hours"] == 24
    assert len(payload["p_contract_hash"]) == 64
    assert "access_token" not in repr(payload)
    assert "secret_access_token" not in repr(payload)
    assert WORKER_SECRET in rendered(state.app)
    assert "precisa estar configurado" in rendered(state.app)
    state.app.run()
    assert WORKER_SECRET not in rendered(state.app)
    assert len(state.auth.calls) == 1
    assert state.reads == 1


def test_rotacao_e_opcao_explicita_na_configuracao(automacao_ficticia):
    """Gerar uma nova chave exige a opção separada, além da aprovação do contrato."""
    state = automacao_ficticia
    checkbox(state.app, APPROVAL).check()
    checkbox(state.app, ROTATION).check()
    botao(state.app, "Ativar atualização automática").click().run()
    assert not state.app.exception
    assert state.auth.calls[0][1]["p_rotate_secret"] is True


def test_status_ignora_campos_fora_da_allowlist(automacao_ficticia):
    """Consultar estado não apresenta credencial mesmo se a fronteira retornar campo extra."""
    state = automacao_ficticia
    state.response = {"enabled": True, "phase": "backfill", "last_success": None,
                      "secret": WORKER_SECRET, "unexpected_data": "PII-FICTICIA"}
    botao(state.app, "Consultar estado da atualização automática").click().run()
    assert not state.app.exception
    assert [name for name, _ in state.auth.calls] == ["sync_automation_status"]
    assert WORKER_SECRET not in rendered(state.app)
    assert "PII-FICTICIA" not in rendered(state.app)
    assert "não comprova executor configurado" in rendered(state.app)


def test_pausa_funciona_com_contrato_ou_chaves_do_erp_ausentes(automacao_ficticia):
    """A interrupção continua possível após configuração externa ficar incompleta."""
    state = automacao_ficticia
    state.values = {name: state.values[name] for name in (
        "GESTAOCLICK_ORGANIZATION_ID", "GESTAOCLICK_SOURCE_ID", "GESTAOCLICK_STORE_ID",
    )}
    botao(state.app, "Pausar atualização automática").click().run()
    assert not state.app.exception
    assert [name for name, _ in state.auth.calls] == ["sync_automation_pause"]
    assert any("histórico foi preservado" in item.value for item in state.app.success)


@pytest.mark.parametrize("action", ["Ativar atualização automática", "Consultar estado da atualização automática",
                                    "Pausar atualização automática", "Verificar configuração da sincronização"])
def test_escopo_de_outra_organizacao_nao_envia_rpc(automacao_ficticia, action):
    """Todas as ações amarram a configuração protegida à organização autenticada."""
    state = automacao_ficticia
    state.values["GESTAOCLICK_ORGANIZATION_ID"] = "10000000-0000-4000-8000-000000000002"
    checkbox(state.app, APPROVAL).check()
    botao(state.app, action).click().run()
    assert not state.app.exception
    assert state.app.warning
    assert not state.app.success
    assert state.auth.calls == []


@pytest.mark.parametrize("boundary", ["load", "rpc"])
@pytest.mark.parametrize("change", ["permission", "role", "active", "generation", "organization"])
def test_revogacao_descarta_credencial_e_sucesso(automacao_ficticia, boundary, change):
    """Mudança de acesso durante Secrets/RPC impede exibir token de trabalho ou sucesso."""
    state = automacao_ficticia

    def revoke():
        """Altera só o contexto fictício para representar revogação concorrente."""
        auth = state.auth
        if change == "permission":
            auth.context = replace(auth.context, permissions=frozenset())
        elif change == "generation":
            auth.generation += 1
        else:
            changes = {"role": {"role": "Operador"}, "active": {"active": False},
                       "organization": {"organization_id": "10000000-0000-4000-8000-000000000002"}}
            auth.context = replace(auth.context, profile=replace(auth.context.profile, **changes[change]))

    if boundary == "load":
        state.after_load = revoke
    else:
        state.after_rpc = revoke
    checkbox(state.app, APPROVAL).check()
    botao(state.app, "Ativar atualização automática").click().run()
    assert not state.app.exception
    assert state.app.error
    assert not state.app.success
    assert WORKER_SECRET not in rendered(state.app)
    assert len(state.auth.calls) == (1 if boundary == "rpc" else 0)


def test_recusa_do_banco_nao_vaza_excecao_interna(automacao_ficticia):
    """A falha administrativa mostra uma orientação fixa e não reenvia automaticamente."""
    state = automacao_ficticia
    state.failure = AppError("DETALHE-PRIVADO-FICTICIO")
    checkbox(state.app, APPROVAL).check()
    botao(state.app, "Ativar atualização automática").click().run()
    assert not state.app.exception
    assert "DETALHE-PRIVADO" not in rendered(state.app)
    assert state.app.warning
    assert not state.app.success
    state.app.run()
    assert len(state.auth.calls) == 1
