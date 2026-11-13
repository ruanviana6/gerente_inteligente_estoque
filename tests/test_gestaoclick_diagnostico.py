"""Verifica o diagnóstico limitado com identidades, Secrets e transporte fictícios.

A fronteira Auth reutiliza o harness da interface. A suíte bloqueia a criação
de conexões reais e não consulta nem persiste dados em serviços externos.
"""

import json
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import Mock

import pytest
from test_integration_interface import AuthRPCFicticia

from integracoes.diagnostico import diagnosticar_conexao
from integracoes.gestaoclick import (
    ContratoInvalido,
    GestaoClickError,
    IntegracaoDesabilitada,
    RespostaGestaoClick,
)
from web.auth import AccessContext
from web.errors import AuthorizationError

ORGANIZACAO = "10000000-0000-4000-8000-000000000001"
OUTRA_ORGANIZACAO = "10000000-0000-4000-8000-000000000002"
ACCESS_TOKEN = "TOKEN-ACESSO-APENAS-FICTICIO"
SECRET_TOKEN = "TOKEN-SECRETO-APENAS-FICTICIO"
DADO_PRIVADO = "nome-ficticio-nao-exibir@example.invalid"
URL = "https://api.gestaoclick.com/lojas?pagina=1&limite=1"


def resposta(data=None, **values):
    """Cria um envelope fictício que pode anunciar páginas nunca consultadas."""
    payload = {"code": 200, "status": "success", "data": [] if data is None else data, **values}
    return RespostaGestaoClick(200, json.dumps(payload).encode())


class TransporteFicticio:
    """Disponibiliza somente GET e registra cada tentativa sem nenhuma rede."""

    def __init__(self, result=None, depois=None):
        """Permite simular resposta, falha ou mudança de autorização durante o GET."""
        self.result = result if result is not None else resposta()
        self.depois = depois
        self.calls = []

    def get(self, url, headers, timeout):
        """Registra a única operação disponível e executa a mudança após o envio."""
        self.calls.append((url, dict(headers), timeout))
        if self.depois:
            self.depois()
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture(autouse=True)
def bloquear_rede(monkeypatch):
    """Uma regressão para o transporte real deve falhar antes de qualquer conexão."""
    monkeypatch.setattr("urllib.request.build_opener", Mock(side_effect=AssertionError("Rede não autorizada.")))


@pytest.fixture
def auth():
    """Reutiliza a autorização da interface com uma organização UUID fictícia."""
    session = AuthRPCFicticia()
    session.context = AccessContext(replace(session.context.profile, organization_id=ORGANIZACAO),
                                    session.context.permissions)
    session.rpc = Mock(side_effect=AssertionError("Diagnóstico não deve executar RPC."))
    return session


@pytest.fixture
def configuracao():
    """Fornece apenas os Secrets do diagnóstico, sem loja, contrato ou fonte analítica."""
    return {"GESTAOCLICK_ENABLED": True,
            "GESTAOCLICK_BASE_URL": "https://api.gestaoclick.com",
            "GESTAOCLICK_ORGANIZATION_ID": ORGANIZACAO,
            "GESTAOCLICK_ACCESS_TOKEN": ACCESS_TOKEN,
            "GESTAOCLICK_SECRET_ACCESS_TOKEN": SECRET_TOKEN}


def alterar_acesso(auth, campo):
    """Altera uma dimensão da autorização, inclusive concessão adicional de permissão."""
    if campo == "generation":
        auth.generation += 1
    elif campo == "permission":
        auth.context = AccessContext(auth.context.profile, frozenset())
    elif campo == "permissions_changed":
        auth.context = AccessContext(auth.context.profile,
                                     auth.context.permissions | frozenset({"trends.view"}))
    else:
        values = {"user_id": "outro-usuario-ficticio", "organization_id": OUTRA_ORGANIZACAO,
                  "role": "Administrador", "active": False}
        auth.context = AccessContext(replace(auth.context.profile, **{campo: values[campo]}),
                                     auth.context.permissions)


def assert_sem_dados_privados(value):
    """Confere que retornos e erros não carregam a resposta nem tokens fictícios."""
    text = str(value)
    for private in (ACCESS_TOKEN, SECRET_TOKEN, DADO_PRIVADO):
        assert private not in text


def test_diagnostico_realiza_um_get_fixo_sem_paginacao_importacao_ou_rpc(auth, configuracao):
    """Metadados de continuação não transformam a verificação em carga analítica."""
    body = resposta([{"id": "loja-ficticia", "email": DADO_PRIVADO, "token": SECRET_TOKEN}],
                    meta={"pagina": 1, "total_paginas": 20, "total_registros": 2000},
                    links={"next": "https://outro-destino.invalid/roubar-segredos"})
    transporte = TransporteFicticio(body)
    carregar = Mock(return_value=configuracao)

    result = diagnosticar_conexao(auth, carregar, autorizado=True, transporte=transporte)

    assert result == {"authenticated": True, "resource": "lojas", "records_received": 1,
                      "coverage_confirmed": False, "imported": False}
    assert transporte.calls == [(URL, {"access-token": ACCESS_TOKEN, "secret-access-token": SECRET_TOKEN,
                                      "Accept": "application/json"}, 15)]
    carregar.assert_called_once_with()
    auth.rpc.assert_not_called()
    assert not auth.pages
    assert not auth.consolidated
    assert_sem_dados_privados(result)


def test_consulta_vazia_nao_comprova_cobertura_ou_importacao(auth, configuracao):
    """Autenticação aceita com lista vazia não comprova disponibilidade do histórico."""
    transporte = TransporteFicticio()
    result = diagnosticar_conexao(auth, lambda: configuracao, autorizado=True, transporte=transporte)
    assert result == {"authenticated": True, "resource": "lojas", "records_received": 0,
                      "coverage_confirmed": False, "imported": False}
    assert len(transporte.calls) == 1
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("consentimento", [False, None, 0, 1, "true", "false"])
def test_sem_consentimento_booleano_nao_le_secrets_nem_faz_get(auth, consentimento):
    """Somente uma confirmação explicitamente verdadeira autoriza ler a configuração."""
    carregar = Mock(side_effect=AssertionError("Leitura prematura de Secrets."))
    transporte = TransporteFicticio()
    with pytest.raises(AuthorizationError):
        diagnosticar_conexao(auth, carregar, autorizado=consentimento, transporte=transporte)
    carregar.assert_not_called()
    assert not transporte.calls
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("perfil,permissoes,ativo", [
    ("Administrador", {"integrations.manage"}, True),
    ("Operador", {"integrations.manage"}, True),
    ("Visualizador", {"integrations.manage"}, True),
    ("Master", set(), True),
    ("Master", {"integrations.manage"}, False),
])
def test_master_ativo_e_permissao_sao_obrigatorios_antes_dos_secrets(auth, perfil, permissoes, ativo):
    """Nem o papel nem a permissão isolados substituem todas as condições de acesso."""
    auth.context = AccessContext(replace(auth.context.profile, role=perfil, active=ativo), frozenset(permissoes))
    carregar = Mock(side_effect=AssertionError("Leitura indevida de Secrets."))
    transporte = TransporteFicticio()
    with pytest.raises(AuthorizationError):
        diagnosticar_conexao(auth, carregar, autorizado=True, transporte=transporte)
    carregar.assert_not_called()
    assert not transporte.calls


@pytest.mark.parametrize("ativacao", [False, "false", None])
def test_configuracao_desativada_ou_ausente_nao_transmite(auth, configuracao, ativacao):
    """A confirmação do usuário não substitui a ativação protegida do servidor."""
    if ativacao is None:
        configuracao.pop("GESTAOCLICK_ENABLED")
    else:
        configuracao["GESTAOCLICK_ENABLED"] = ativacao
    transporte = TransporteFicticio()
    with pytest.raises(IntegracaoDesabilitada):
        diagnosticar_conexao(auth, lambda: configuracao, autorizado=True, transporte=transporte)
    assert not transporte.calls
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("organizacao", [None, "", "org-inventada", [], 42])
def test_uuid_protegido_ausente_ou_invalido_impede_rede(auth, configuracao, organizacao):
    """A vinculação da conta a uma organização precisa existir nos Secrets."""
    if organizacao is None:
        configuracao.pop("GESTAOCLICK_ORGANIZATION_ID")
    else:
        configuracao["GESTAOCLICK_ORGANIZATION_ID"] = organizacao
    transporte = TransporteFicticio()
    with pytest.raises(ContratoInvalido) as erro:
        diagnosticar_conexao(auth, lambda: configuracao, autorizado=True, transporte=transporte)
    assert not transporte.calls
    assert_sem_dados_privados(erro.value)


def test_master_de_outra_organizacao_nao_utiliza_credenciais_globais(auth, configuracao):
    """Uma credencial válida não autoriza contas de outra organização."""
    configuracao["GESTAOCLICK_ORGANIZATION_ID"] = OUTRA_ORGANIZACAO
    transporte = TransporteFicticio()
    with pytest.raises(AuthorizationError):
        diagnosticar_conexao(auth, lambda: configuracao, autorizado=True, transporte=transporte)
    assert not transporte.calls
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("values", [None, [], "secrets-ficticios", 42])
def test_configuracao_protegida_precisa_ser_mapeamento(auth, values):
    """Um carregador inválido falha sem transformar seu conteúdo em mensagem pública."""
    transporte = TransporteFicticio()
    with pytest.raises(ContratoInvalido):
        diagnosticar_conexao(auth, lambda: values, autorizado=True, transporte=transporte)
    assert not transporte.calls


@pytest.mark.parametrize("fase", ["secrets", "http"])
@pytest.mark.parametrize("campo", ["user_id", "organization_id", "generation", "permission",
                                    "permissions_changed", "role", "active"])
def test_mudanca_de_acesso_durante_a_operacao_impede_resultado(auth, configuracao, fase, campo):
    """Revalida cada dimensão da sessão após carregar Secrets e após consultar o ERP."""
    def carregar():
        """Simula mudança de contexto enquanto a configuração protegida é carregada."""
        if fase == "secrets":
            alterar_acesso(auth, campo)
        return configuracao

    def ao_responder():
        """Simula revogação ou troca de sessão enquanto a resposta externa trafega."""
        if fase == "http":
            alterar_acesso(auth, campo)

    transporte = TransporteFicticio(resposta([{"email": DADO_PRIVADO}]), depois=ao_responder)
    with pytest.raises(AuthorizationError) as erro:
        diagnosticar_conexao(auth, carregar, autorizado=True, transporte=transporte)
    assert len(transporte.calls) == (1 if fase == "http" else 0)
    auth.rpc.assert_not_called()
    assert_sem_dados_privados(erro.value)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 401, 403, 429, 500, 503])
def test_http_recusado_nao_repete_nem_expoe_corpo_ou_headers(auth, configuracao, status):
    """Redirect, bloqueio de acesso e falha temporária terminam com uma única tentativa."""
    body = f"{ACCESS_TOKEN} {SECRET_TOKEN} {DADO_PRIVADO}".encode()
    transporte = TransporteFicticio(RespostaGestaoClick(status, body, {
        "Location": "https://outro-destino.invalid/" + SECRET_TOKEN, "Retry-After": "0"}))
    with pytest.raises(GestaoClickError) as erro:
        diagnosticar_conexao(auth, lambda: configuracao, autorizado=True, transporte=transporte)
    assert len(transporte.calls) == 1
    assert transporte.calls[0][0] == URL
    auth.rpc.assert_not_called()
    assert_sem_dados_privados(erro.value)


@pytest.mark.parametrize("falha", [TimeoutError, OSError])
def test_falha_de_transporte_nao_repete_nem_expoe_excecao(auth, configuracao, falha):
    """O diagnóstico não herda retentativas nem mensagens privadas do transporte."""
    transporte = TransporteFicticio(falha(f"{SECRET_TOKEN} {DADO_PRIVADO}"))
    with pytest.raises(GestaoClickError, match="não respondeu") as erro:
        diagnosticar_conexao(auth, lambda: configuracao, autorizado=True, transporte=transporte)
    assert len(transporte.calls) == 1
    assert_sem_dados_privados(erro.value)


@pytest.mark.parametrize("raw", [
    ("{json-invalido " + SECRET_TOKEN + " " + DADO_PRIVADO).encode(),
    ('{"code":200,"code":200,"status":"success","data":[],"segredo":"' + SECRET_TOKEN + '"}').encode(),
    ('{"code":200,"status":"success","data":[{"email":"' + DADO_PRIVADO + '","email":"repetido"}]}').encode(),
    b'{"code":200,"status":"success","data":[{"valor":NaN}]}',
    b'{"code":200,"status":"success","data":[{"valor":"\\ud800"}]}',
    b'[]',
    b'\xff',
])
def test_json_invalido_ou_duplicado_nao_entrega_dados(auth, configuracao, raw):
    """JSON ambíguo em qualquer nível é recusado por mensagem fixa e segura."""
    transporte = TransporteFicticio(RespostaGestaoClick(200, raw))
    with pytest.raises(ContratoInvalido) as erro:
        diagnosticar_conexao(auth, lambda: configuracao, autorizado=True, transporte=transporte)
    assert str(erro.value) == "JSON GestãoClick inválido ou ambíguo."
    assert len(transporte.calls) == 1
    assert_sem_dados_privados(erro.value)


@pytest.mark.parametrize("mudancas", [
    {"code": True}, {"code": "200"}, {"code": 401}, {"status": "error"},
    {"data": None}, {"data": {}}, {"data": [DADO_PRIVADO]}, {"data": [{}] * 101},
])
def test_http_200_exige_envelope_de_sucesso_e_lista_limitada(auth, configuracao, mudancas):
    """HTTP 200 isolado não comprova autenticação nem o formato esperado de lojas."""
    envelope = {"code": 200, "status": "success", "data": [], "message": DADO_PRIVADO, **mudancas}
    transporte = TransporteFicticio(RespostaGestaoClick(200, json.dumps(envelope).encode()))
    with pytest.raises(ContratoInvalido) as erro:
        diagnosticar_conexao(auth, lambda: configuracao, autorizado=True, transporte=transporte)
    assert len(transporte.calls) == 1
    assert_sem_dados_privados(erro.value)


def test_operacao_mantem_escopo_da_sessao_durante_secrets_e_http(auth, configuracao):
    """A mesma operação envolve autorização, configuração e resposta da consulta."""
    em_operacao = False

    @contextmanager
    def operacao():
        """Representa o escopo serializado da sessão sem depender de locks globais."""
        nonlocal em_operacao
        assert not em_operacao
        em_operacao = True
        try:
            yield
        finally:
            em_operacao = False

    def carregar():
        """Confere que a configuração não é lida fora da operação autorizada."""
        assert em_operacao
        return configuracao

    def durante_http():
        """Confere que a resposta ainda pertence ao mesmo escopo de operação."""
        assert em_operacao

    auth.operation = operacao
    diagnosticar_conexao(auth, carregar, autorizado=True, transporte=TransporteFicticio(depois=durante_http))
    assert not em_operacao


def test_transporte_padrao_constroi_get_sem_corpo_e_bloqueia_redirecionamento(auth, configuracao, monkeypatch):
    """Exercita a construção HTTP real com um opener local que jamais usa sockets."""
    recebido = Mock()
    recebido.__enter__ = Mock(return_value=recebido)
    recebido.__exit__ = Mock(return_value=False)
    recebido.status = 200
    recebido.headers = {}
    recebido.read.return_value = resposta().body
    opener = Mock()
    opener.open.return_value = recebido
    construir = Mock(return_value=opener)
    monkeypatch.setattr("urllib.request.build_opener", construir)

    diagnosticar_conexao(auth, lambda: configuracao, autorizado=True)

    construir.assert_called_once()
    handler = construir.call_args.args[0]
    assert handler.redirect_request(None, None, 302, "", {}, "https://outro-destino.invalid") is None
    opener.open.assert_called_once()
    request = opener.open.call_args.args[0]
    assert request.get_method() == "GET"
    assert request.full_url == URL
    assert request.data is None
    assert request.get_header("Access-token") == ACCESS_TOKEN
    assert request.get_header("Secret-access-token") == SECRET_TOKEN
    assert opener.open.call_args.kwargs == {"timeout": 15}
    auth.rpc.assert_not_called()


def test_revogacao_posterior_impede_nova_leitura_sem_reutilizar_sucesso(auth, configuracao):
    """Um diagnóstico anterior não autoriza a próxima ação após revogação no backend."""
    transporte = TransporteFicticio()
    carregar = Mock(return_value=configuracao)
    diagnosticar_conexao(auth, carregar, autorizado=True, transporte=transporte)
    alterar_acesso(auth, "permission")
    with pytest.raises(AuthorizationError):
        diagnosticar_conexao(auth, carregar, autorizado=True, transporte=transporte)
    carregar.assert_called_once_with()
    assert len(transporte.calls) == 1
